"""Regenerate the manuscript exhibits from the Germany backtest CSVs."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import dates as mdates
from matplotlib import pyplot as plt
import pandas as pd


PAPER = Path(__file__).resolve().parent
ROOT = PAPER.parents[1]
FOCAL = "reserve_neural"
STYLES = {
    "reserve_neural": ("Reserve-weighted AI", "#0d7562"),
    "ridge": ("Ridge", "#366dab"),
    "persistence": ("Persistence", "#727e8d"),
    "fixed_reserve": ("Fixed reserve", "#a56b32"),
}


def style_axes(axis):
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", alpha=0.17)
    axis.set_axisbelow(True)


def plot_comparison(results, output):
    forecasts = pd.read_csv(results / "forecast_scores.csv")
    operations = pd.read_csv(results / "operations_summary.csv")
    stress = operations.loc[(operations.condition == "no_imports") & operations.method.isin(STYLES)].copy()
    stress = stress.sort_values("unmet_percent", ascending=False)
    figure, axes = plt.subplots(1, 2, figsize=(10.6, 3.5), layout="constrained")
    for method, (label, color) in STYLES.items():
        rows = forecasts.loc[forecasts.method == method]
        if not rows.empty:
            axes[0].plot(rows.lead_day, rows.country_crps_gwh, marker="o", color=color, label=label)
    axes[0].set(title="National forecast accuracy", xlabel="Lead day", ylabel="CRPS (GWh/day)", xticks=range(1, 8))
    axes[0].legend(frameon=False, fontsize=9)
    labels = [STYLES[method][0] for method in stress.method]
    colors = [STYLES[method][1] for method in stress.method]
    bars = axes[1].barh(labels, stress.unmet_percent, color=colors, height=0.58)
    axes[1].bar_label(bars, fmt="%.3f", padding=4, fontsize=9)
    axes[1].set(title="Backup interruption", xlabel="Unmet hydrogen demand (%)",
                xlim=(0, stress.unmet_percent.max() * 1.18))
    for axis in axes:
        style_axes(axis)
    axes[1].grid(False, axis="y")
    axes[1].grid(True, axis="x", alpha=0.17)
    figure.savefig(output, metadata={"CreationDate": None})
    plt.close(figure)


def plot_worst_week(results, output):
    daily = pd.read_csv(results / "daily_operations.csv", parse_dates=["date"])
    stress = daily.loc[daily.condition == "no_imports"]
    focal = stress.loc[stress.method == FOCAL].sort_values("date").set_index("date")
    if not focal.index.equals(pd.date_range(focal.index[0], focal.index[-1])):
        raise ValueError("The episode calculation needs consecutive dates")
    last = focal.unmet_kg.rolling(7).sum().idxmax()
    first = last - pd.Timedelta(days=6)
    demand_tonnes = focal.demand_kg.iloc[0] / 1000
    episode = stress.loc[stress.date.between(first, last)]
    figure, axes = plt.subplots(2, 1, sharex=True, figsize=(10.6, 4.55), layout="constrained")
    for method in ("fixed_reserve", "ridge", FOCAL):
        rows = episode.loc[episode.method == method].sort_values("date")
        label, color = STYLES[method]
        axes[0].plot(rows.date, rows.reserve_kg / 1000, marker="o", color=color, label=label)
        axes[1].plot(rows.date, rows.unmet_kg.cumsum() / 1000, marker="o", color=color)
    axes[0].axhline(demand_tonnes, color="#8e959b", linestyle="--", linewidth=1, label="One day of demand")
    axes[0].set(ylabel="Closing stock (t)")
    axes[0].legend(frameon=False, ncols=2, fontsize=9)
    axes[1].set(ylabel="Cumulative unmet (t)", xlabel=str(last.year))
    axes[1].xaxis.set_major_locator(mdates.DayLocator())
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    for axis in axes:
        style_axes(axis)
        axis.set_ylim(bottom=0)
    figure.savefig(output, metadata={"CreationDate": None})
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results" / "germany")
    args = parser.parse_args()
    output = PAPER / "figures"
    output.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "STIXGeneral", "font.size": 12, "pdf.fonttype": 42})
    plot_comparison(args.results, output / "decision-comparison.pdf")
    plot_worst_week(args.results, output / "reserve-episode.pdf")


if __name__ == "__main__":
    main()
