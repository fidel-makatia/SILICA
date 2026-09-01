.PHONY: help install dev test lint demo clean

help:               ## list targets
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

install:            ## install the package + the `silica` CLI
	pip install -e .

dev:                ## install with the KLayout backend
	pip install -e ".[klayout]"

test:               ## run every suite
	@set -e; for t in tests/test_*.py; do echo "== $$t"; python3 $$t; done

lint:               ## style check (optional; needs flake8)
	flake8 --max-line-length 100 silica tests examples/add_pin_labels.py

demo:               ## run the parametric pad-row example
	python3 -m silica examples/padframe_gen.sil

clean:              ## remove caches and flow traces
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -f silica_flow_trace.jsonl
