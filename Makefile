PY ?= .venv/bin/python
export PYTHONPATH := src
export PYTHONHASHSEED := 0

.PHONY: venv test generate baselines audit reward-analysis verify

venv:
	python3.11 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

test:
	$(PY) -m pytest -q

generate:
	$(PY) -m auditenv generate

baselines:
	$(PY) -m auditenv baselines --out trajectories
	$(PY) -m auditenv baselines --split stage2_eval --out trajectories

audit:
	$(PY) scripts/audit_episodes.py

reward-analysis:
	$(PY) scripts/reward_analysis.py

verify: test audit baselines
