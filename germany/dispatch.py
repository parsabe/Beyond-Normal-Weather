"""Risk-aware rolling hydrogen inventory and transfer decisions."""

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix


class Plant:
    def __init__(self, config):
        self.parameters = config["plant"]
        zones = config["zone"]
        self.zones = [zone["name"] for zone in zones]
        if len(set(self.zones)) != len(zones):
            raise ValueError("Zone names must be unique")
        self.share = np.array([zone["contract_share"] for zone in zones], dtype=float)
        self.power_mw = np.array([zone["electrolyzer_mw"] for zone in zones], dtype=float)
        self.demand = np.array([zone["delivery_kg"] for zone in zones], dtype=float)
        self.import_limit = np.array([zone["import_limit_kg"] for zone in zones], dtype=float)
        self.capacity = self.demand * self.parameters["days_of_storage"]
        self.reserve = self.demand * self.parameters["terminal_reserve_days"]
        self.links = np.array([[self.zones.index(name) for name in link["zones"]]
                               for link in config["link"]], dtype=int).reshape(-1, 2)
        self.link_capacity = np.array([link["capacity_kg"] for link in config["link"]], dtype=float)
        if ((self.demand <= 0).any() or (self.import_limit < 0).any() or (self.import_limit > self.demand).any()
                or (self.reserve < 0).any() or (self.reserve > self.capacity).any()
                or not 0 < self.parameters["cvar_level"] < 1 or self.parameters["kwh_per_kg"] <= 0):
            raise ValueError("Invalid demand, reserve, import limit, CVaR level or conversion efficiency")
        self.production_cost = self.parameters["renewable_eur_per_mwh"] * self.parameters["kwh_per_kg"] / 1000

    def availability(self, generation):
        contracted_mwh = np.asarray(generation).sum(axis=-1) * self.share
        return 1000 * np.minimum(contracted_mwh, self.power_mw * 24) / self.parameters["kwh_per_kg"]

    def cost(self, production, imported, transferred, unmet):
        cash = (production.sum() * self.production_cost + imported.sum() * self.parameters["import_eur_per_kg"]
                + transferred.sum() * self.parameters["transfer_eur_per_kg"])
        return cash, cash + unmet.sum() * self.parameters["unmet_eur_per_kg"]


def sparse_rows(rows, columns):
    r, c, v = [], [], []
    for index, pairs in enumerate(rows):
        for column, value in pairs:
            r.append(index)
            c.append(column)
            v.append(value)
    return coo_matrix((v, (r, c)), shape=(len(rows), columns)).tocsr()


class DispatchProblem:
    """Linear scenario program with first-day import commitments shared by all paths."""

    def __init__(self, plant, members, days):
        self.plant = plant
        self.members = members
        self.days = days
        zones = len(plant.zones)
        links = len(plant.links)
        self.shape = (members, days, zones)
        size = 0

        def block(shape):
            nonlocal size
            count = int(np.prod(shape))
            indices = np.arange(size, size + count).reshape(shape)
            size += count
            return indices

        self.production = block(self.shape)
        self.imported = block(self.shape)
        self.unmet = block(self.shape)
        self.stock = block(self.shape)
        self.flow = block((members, days, links, 2))
        self.reserve_gap = block((members, zones))
        risk = members > 1 and plant.parameters["cvar_weight"] > 0
        if risk:
            eta = block((1,))[0]
            excess = block((members,))

        objective = np.zeros(size)
        objective[self.production] = plant.production_cost / members
        objective[self.imported] = plant.parameters["import_eur_per_kg"] / members
        objective[self.unmet] = plant.parameters["unmet_eur_per_kg"] / members
        objective[self.flow] = plant.parameters["transfer_eur_per_kg"] / members
        objective[self.reserve_gap] = plant.parameters["reserve_gap_eur_per_kg"] / members
        objective[self.stock] = 0.002 / members
        if risk:
            objective[eta] = plant.parameters["cvar_weight"]
            objective[excess] = plant.parameters["cvar_weight"] / (members * (1 - plant.parameters["cvar_level"]))
        self.objective = objective

        balance_rows = []
        for s, t, z in np.ndindex(self.shape):
            index = (s, t, z)
            coefficients = [(self.stock[index], 1), (self.production[index], -1),
                            (self.imported[index], -1), (self.unmet[index], -1)]
            if t > 0:
                coefficients.append((self.stock[s, t - 1, z], -1))
            for edge, (left, right) in enumerate(plant.links):
                direction = int(z == left) - int(z == right)
                if direction:
                    coefficients.extend([(self.flow[s, t, edge, 0], direction),
                                         (self.flow[s, t, edge, 1], -direction)])
            balance_rows.append(coefficients)
        shared = [[(self.imported[s, 0, z], 1), (self.imported[0, 0, z], -1)]
                  for s in range(1, members) for z in range(zones)]
        self.eq = sparse_rows(balance_rows + shared, size)
        self.shared_count = len(shared)

        flows = [[(a, 1), (b, 1)] for a, b in self.flow.reshape(-1, 2)]
        reserves = [[(self.stock[s, -1, z], -1), (self.reserve_gap[s, z], -1)]
                    for s in range(members) for z in range(zones)]
        tails = []
        if risk:
            tails = [[(value, 1) for value in self.unmet[s].ravel()] + [(eta, -1), (excess[s], -1)]
                     for s in range(members)]
        self.ub = sparse_rows(flows + reserves + tails, size)
        self.tail_count = len(tails)

        self.bounds = np.zeros((size, 2))
        self.bounds[:, 1] = np.inf
        self.bounds[self.stock, 1] = plant.capacity
        self.bounds[self.unmet, 1] = plant.demand

    def solve(self, availability, stock, import_limit, reserve, booked=None):
        if availability.shape != self.shape or not np.isfinite(availability).all() or (availability < 0).any():
            raise ValueError("Invalid renewable supply tensor")
        extremes = self.bounds.copy()
        extremes[self.production, 1] = availability
        extremes[self.imported, 1] = import_limit
        if booked is not None:
            extremes[self.imported[:, 0], 0] = booked
            extremes[self.imported[:, 0], 1] = booked
        balance = -np.broadcast_to(self.plant.demand, self.shape).copy()
        balance[:, 0, :] += stock
        required = np.r_[balance.ravel(), np.zeros(self.shared_count)]
        allowed = np.r_[np.tile(self.plant.link_capacity, self.members * self.days),
                        -np.tile(reserve, self.members), np.zeros(self.tail_count)]
        res = linprog(self.objective, A_eq=self.eq, b_eq=required, A_ub=self.ub, b_ub=allowed,
                      bounds=extremes, method="highs")
        if not res.success:
            raise RuntimeError(f"Hydrogen dispatch failed: {res.message}")
        return res.x

    def recommended_actions(self, availability, stock, import_limit):
        result = self.solve(availability, stock, import_limit, self.plant.reserve)
        orders = result[self.imported[0, 0]]
        reserve = result[self.stock[:, 0]].mean(axis=0)
        return orders, reserve

    def realize(self, available_today, inventory, orders, reserve):
        result = self.solve(available_today[None, None, :], inventory, orders, reserve, booked=orders)
        return {"production_kg": result[self.production[0, 0]], "import_kg": result[self.imported[0, 0]],
                "unmet_kg": result[self.unmet[0, 0]], "reserve_kg": result[self.stock[0, 0]],
                "transfers_kg": result[self.flow[0, 0]]}
