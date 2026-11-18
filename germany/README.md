# Germany-wide AI forecasting for hydrogen reserve operations

This pilot asks whether a learned, seven-day wind and solar forecast improves daily reserve and import decisions during renewable shortages. It uses Germany's four electricity control areas as regional zones. The historical generation and archived weather predictors are public data. Electrolyzer capacity, contracted renewable shares, delivery obligations, storage, links, and the availability of backup hydrogen are experimental inputs recorded in [study.toml](study.toml).

Germany's hydrogen strategy identifies import diversification and security of supply as objectives. The operating scenario here examines performance during an interruption of backup hydrogen imports. That interruption is a modeled stress test. [Federal hydrogen strategy](https://www.bundeswirtschaftsministerium.de/Redaktion/EN/Dossier/hydrogen.html) · [Federal Network Agency core-network information](https://www.bundesnetzagentur.de/EN/Areas/Energy/HydrogenCoreNetwork/start.html)

## What the pipeline does

1. **Observe renewable supply.** Download daily wind and solar generation for 50Hertz, Amprion, TenneT, and TransnetBW from Bundesnetzagentur | SMARD.de. Use onshore wind in all four zones and add available offshore wind feeds. [SMARD data and license](https://www.smard.de/en/datennutzung) · [SMARD control areas](https://www.smard.de/page/en/wiki-article/6138/205642/control-area)
2. **Forecast a week.** Use a multilayer neural network with 64 and 32 hidden units to forecast all seven days, four zones, and two technologies jointly. Inputs include recent generation, seasonal indicators, and archived ECMWF forecast wind speed and radiation at two sample points per zone. One model minimizes ordinary squared error. A second gives three times the training weight to windows with the weakest three-day modeled hydrogen production potential. Compare with a ridge regression using the same inputs and a simple persistence baseline. [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api)
3. **Represent uncertainty.** Add complete seven-day forecast-error trajectories from the calibration period to each prediction. Select comparable calibration windows using season and predicted shortfall severity. Applying one error trajectory at a time retains cross-region and cross-day errors within that sample. The pilot uses eight paths per decision.
4. **Make a daily decision.** Solve a linear program to recommend per-zone backup import orders and end-of-day hydrogen reserve targets. It models production limits, tank capacity, mass balance, delivery demand, and two-way link limits. The objective includes operating costs, missed deliveries, and a tail-risk penalty for large weekly shortfalls. Each day's import order is shared across all forecast scenarios. Day-ahead plans are updated as new forecasts arrive.
5. **Replay past decisions.** Use realized renewable generation in a one-day dispatch calculation after the import decision is taken. Apply identical plant and network constraints to all methods. Compare normal backup access and an import interruption. Include a fixed reserve rule alongside the predictive policies.

## Evaluation design

Training uses April–December 2024, calibration uses January–September 2025, and the test uses October 2025–September 2026. A forecast window must fit entirely inside its split. The latest generation feature is issue day minus two to account for reporting delay. Each target day uses the archive field at its corresponding previous-day offset and aggregates valid local hours through 18:00, leaving a buffer before the recommendation is made. These are lead-time-specific archived predictors. The archive pools individual valid times and does not identify the exact release timestamp of each input.

The forecast metrics are national GWh/day error, CRPS, and a Brier score for a low-supply window. The dispatch metrics are unmet hydrogen demand, shortage days, worst seven-day shortfall, imports, transfers, and modeled operating costs. The network is an illustrative national system represented by four zones. It uses daily averaged renewable availability, assumed links, and fixed costs. The rollout treats within-day generation as known for dispatch after the import order. The scenario planner allows flexible later actions in each trajectory. A deployment study would need actual contracts, hourly plant constraints, and issue-time-stamped weather runs.

This design builds on probabilistic scheduling and research on German periods of low wind and solar output. Its research question is whether forecasting and training for low hydrogen supply helps regional reserve decisions in held-out replay. [Probabilistic forecasting and stochastic hydrogen control](https://www.sciencedirect.com/science/article/pii/S2213138826005321) · [Weather regimes during German Dunkelflauten](https://doi.org/10.1002/met.2141)

## Results and technical note

The held-out replay covers 359 days from 1 October 2025 to 24 September 2026. Ridge gave the best forecast scores. During the simulated interruption, the reserve-weighted neural policy lowered unmet demand from 0.800% to 0.704% relative to the ordinary neural policy. Ridge reached 0.300%, and the fixed-reserve rule reached 0.143%. With backup imports available, the fixed rule met all demand. Ridge had lower modeled cash costs and missed 1.27 tonnes of delivery. These results apply to the configured portfolio and assumed costs. [Forecast metrics](report/forecast_scores.csv) · [Operational metrics](report/operations_summary.csv) · [Run manifest](report/run_manifest.json)

The [technical note](paper/main.pdf) includes the algorithm, evaluation, comparisons, and a mass-balance proof. Its [LaTeX sources](paper/) compile with `make` from `germany/paper`.

## Run it

From the repository root with Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r germany/requirements.txt
.venv/bin/python -m germany.data all
.venv/bin/python -m germany.run
.venv/bin/python -m unittest discover -s tests -v
```

Source extracts and checksums are stored in `data/raw/germany`. The backtest writes `forecast_scores.csv`, `operations_summary.csv`, `regional_decisions.csv`, `regional_transfers.csv`, a plot, and a run manifest to `results/germany`. In the regional decisions file, `import_order_kg` and `reserve_target_kg` are recommendations made at issue time. Production, transfers, and closing stocks are replayed operations after the modeled availability is revealed.
