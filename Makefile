PY ?= .venv/bin/python
export PYTHONPATH := src
export PYTHONHASHSEED := 0

.PHONY: venv test generate baselines audit reward-analysis run-all docker-build docker-run-all verify

venv:
	python3.11 -m venv .venv && .venv/bin/pip install -q --no-deps --require-hashes -r requirements-dev.lock

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

# Single offline verification command (host): report in results/run_all/
run-all:
	$(PY) scripts/run_all.py

docker-build:
	docker build -t genesis-auditenv:stage3 .

# Official offline verification inside the container (no network, 2 CPU, 4 GB): results/docker_run_all/
docker-run-all:
	scripts/docker_run_all.sh

verify: run-all
