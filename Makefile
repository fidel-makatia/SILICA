.PHONY: test demo install dev

install:            ## install the package + CLI
	pip install -e .

dev:                ## install with the KLayout backend
	pip install -e ".[klayout]"

test:               ## run every test suite
	python3 tests/test_core.py
	python3 tests/test_lang.py
	python3 tests/test_flow.py
	python3 tests/test_backend_klayout.py

demo:               ## run the parametric padframe example
	python3 -m silica examples/padframe_gen.sil
