# Beyond Normal Weather

The `germany-ai-operations` branch adds a Germany-wide forecast and hydrogen reserve scheduling study. [Germany implementation, data, and results](germany/README.md).

Can a weather model calibrated against hydrogen delivery shortfalls improve estimates of storage reliability? This repository tests that question with ERA5, three CMIP6 models, and an illustrative wind–solar hydrogen plant near Syracuse, New York. The [original proposal](Project/new%20-%20Topic.pdf) motivates the study. The earlier New York heat experiment is on `master`.

## Run the experiment

Use Python 3.12. The download command uses public endpoints and needs network access. Run subsequent commands offline with the cached inputs.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m beyond_normal.data all
.venv/bin/python -m beyond_normal.run
.venv/bin/python -m unittest discover -s tests -v
```

`requirements.txt` gives the supported dependency ranges. `requirements-lock.txt` records the environment used for the pilot. On systems where `venv` cannot install pip, use `uv venv --python 3.12` and `uv pip sync requirements-lock.txt`.

`study.toml` defines the site, periods, plant, and ensemble. `data/source/cmip6_stores.csv` pins model, member, grid, dataset version, and store URL for every input. The fetcher stores the downloaded ERA5 responses, clipped daily series, source grid coordinates, calendar, and SHA-256 checksums in `data/raw/`. Use a separate `--output` when fetching a different site or period. See [data and references](references/README.md) for details.

## Experiment

1. Use hourly ERA5 to build local daily wind and solar capacity factors. A cubic turbine curve uses 100 m wind, and a simple photovoltaic proxy uses downward shortwave radiation. Aggregate after converting each hour to power.
2. Apply monthly additive quantile delta mapping to each model's daily 10 m wind speed and solar radiation, then map the corrected local resources to capacity factors using ERA5 quantiles. The mapping clamps beyond the observed extrema. This is a simple point-scale statistical transfer with bounded power outputs.
3. Use the corrected climate model sequence as the baseline. For the proposed method, resample paired, consecutive ERA5 rank blocks with similar monthly wind and solar ranks and block means. Reorder the same corrected values within each calendar month across the period, preserving each monthly wind and solar distribution exactly. A fixed seven-day resampler is an additional baseline. Paired-day shuffling and circular shifts of solar alone provide separate timing diagnostics.
4. Route each daily sequence through the same plant. The electrolyzer has a daily energy cap and fixed efficiency. Serve the delivery contract from production and the tank, then store surplus up to capacity. Repeat the first year once from an empty tank before scoring the continuous trajectory. The case assumes a fixed off-grid contract and idealized storage. Real plant operation also depends on subdaily timing, losses, ramping, and plant-specific power curves.
5. Use 1981–1998 for weather calibration and block sampling, 1999–2006 to choose block length and replacement fraction by CRPS of annual unmet demand, and 2007–2014 to evaluate the frozen settings. The seven-day tank is withheld from tuning. Evaluation compares predictive distributions of annual outcomes with ERA5-derived annual outcomes because the climate simulations have their own weather chronology.
6. Freeze the method and estimate 2041–2060 outcomes for SSP2-4.5 and SSP5-8.5. Report each model separately. The figure shows the range of model means for the climate-model-order baseline. Annual projections and synthetic weather draws share model assumptions and forcing.

## Outputs

| File under `results/` | Contents |
| --- | --- |
| `calibration_grid.csv` | Candidate scores on the calibration period |
| `historical_scores.csv` | Calibration and test CRPS by method, model, and storage size. Lower is better. |
| `test_crps_differences.csv` | Test difference against climate-model order and descriptive moving-block bootstrap bounds across eight ERA5 years |
| `annual_outcomes.csv` | Unmet delivery, share of shortfall days, and longest within-year shortfall per trajectory, year, and tank size |
| `future_summary.csv` | Model- and scenario-specific future summaries |
| `storage_targets.csv` | Smallest tested tank meeting the configured mean unmet delivery target. A blank means none met it. |
| `comparison.png` | Holdout CRPS and future storage sensitivity |
| `run_manifest.json` | Design, versions, chosen parameters, and checksums |

For the paper-data comparison and pilot interpretation, see `hydrogen-benchmarks`.
