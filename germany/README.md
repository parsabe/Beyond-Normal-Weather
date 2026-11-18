# AI for Renewable Hydrogen Supply in Germany

[Read the paper](paper/main.pdf) · [LaTeX source](paper/main.tex) · [Backtest outputs](report/)

The paper documents one Germany-wide AI workflow for forecasting renewable generation and recommending regional hydrogen reserves and backup orders. It explains the energy-security context, method, algorithm, backtest, benchmark results, and limitations.

## Reproduce the paper

Run from the repository root with Python 3.12, pdfLaTeX, and BibTeX. The committed figures allow `make -C germany/paper` to compile the paper directly.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r germany/requirements.txt
.venv/bin/python -m germany.data all
.venv/bin/python -m germany.run
.venv/bin/python -m unittest discover -s tests -v
make -C germany/paper figures PYTHON="$PWD/.venv/bin/python"
make -C germany/paper
```

[Study configuration](study.toml) · [Run manifest](report/run_manifest.json) · [SMARD data attribution](https://www.smard.de/en/datennutzung) · [Archived forecast source](https://open-meteo.com/en/docs/previous-runs-api)
