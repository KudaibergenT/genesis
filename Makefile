PY ?= .venv/bin/python
export PYTHONPATH := src
export PYTHONHASHSEED := 0

.PHONY: venv test generate baselines verify

venv:
	python3.11 -m venv .venv && .venv/bin/pip install -q "pytest==8.3.4" "hypothesis==6.122.3"

test:
	$(PY) -m pytest -q

generate:
	$(PY) -m auditenv generate

baselines:
	$(PY) -m auditenv baselines --out trajectories

verify: test baselines
