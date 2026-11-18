# LaTeX manuscript

[Read the compiled paper](main.pdf) or [open the LaTeX source](main.tex). The manuscript uses a Palatino-style text and math font, two-column main text, and a single-column bibliography and appendix. The appendix contains the proofs, workflow diagram, generated figures, and reproduction commands.

Run `make` from this directory to compile using pdfLaTeX and BibTeX. The required TeX packages are listed in `main.tex`. The checked-in appendix plot PDFs are ready for compilation. After installing the Python dependencies in the repository root, run `make figures PYTHON=../.venv/bin/python` to regenerate them from the benchmark CSVs, then run `make` again.
