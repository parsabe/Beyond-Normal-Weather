# Published flash-drought comparison

Lin et al. project gridded changes in wind and solar flash-drought frequency. The external comparison reads the published Figure 2 workbook, selects the valid grid cell nearest Syracuse, and computes the same kind of frequency change from the study's three CMIP6 models. Both comparisons use SSP2-4.5, 1985–2014 as the reference, and 2045–2074 as the projection window. This is a comparison of the weather hazard. The hydrogen delivery and storage comparison uses ERA5 in the main experiment. [Paper, source data, and methods](https://doi.org/10.1038/s41558-026-02753-3)

For each local model and chronology method, find complete spells lasting at least three days below the historical 10th percentile of the daily capacity factor. Onset is the decline from the preceding day to the first day in the spell. Classify a spell as flash when its onset exceeds the 90th percentile of historical spell onsets. Fix the historical thresholds for the future window. Compute the relative change using the mean number of events per 30-year sequence. The source workbook values use the authors' model ensemble and capacity factor methods. The pilot uses its own local ERA5 calibration and three models, so numerical differences can reflect those choices as well as chronology.

## Reproduce the comparison

Run the main experiment in this checkout, then execute these commands from its root. Set the `--data`, `--future-data`, `--references`, and `--fit` paths if reusing downloads from another checkout.

```bash
.venv/bin/python -m pip install -r benchmarks/requirements.txt
.venv/bin/python -m beyond_normal.benchmark --download
.venv/bin/python -m beyond_normal.data cmip6 \
  --future-years 2045 2074 --experiments ssp245 --output data/raw/lin-2026
.venv/bin/python -m beyond_normal.benchmark
.venv/bin/python -m unittest discover -s tests -v
```

The downloader retrieves the publisher source-data archive, supplementary PDF, and public Zenodo record. The comparison reads the Figure 2 workbook inside the source-data archive. The final CSVs and checksum manifest are in `results/`.
