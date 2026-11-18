PYTHON ?= python3
PDFLATEX ?= pdflatex
BIBTEX ?= bibtex
LATEX_FLAGS = -interaction=nonstopmode -halt-on-error -output-directory=build

.PHONY: all figures

all:
	mkdir -p build
	$(PDFLATEX) $(LATEX_FLAGS) main.tex
	cd build && BIBINPUTS=..: $(BIBTEX) main
	$(PDFLATEX) $(LATEX_FLAGS) main.tex
	$(PDFLATEX) $(LATEX_FLAGS) main.tex
	cp build/main.pdf main.pdf

figures:
	$(PYTHON) make_figures.py
