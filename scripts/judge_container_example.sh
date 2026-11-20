#!/usr/bin/env sh
# How the jury calls judge_adapter.py inside the container (documented in README, executed by tests).
#   IMAGE=genesis-auditenv:stage3 scripts/judge_container_example.sh
# Working directory in the container: /app. Episode directories go to a host directory mounted at /jury.
set -eu
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-genesis-auditenv:stage3}"
OUT="${OUT:-$(pwd)/results/judge_example}"
mkdir -p "$OUT"
# The container's root runs without CAP_DAC_OVERRIDE (--cap-drop ALL), so the mounted directory must be
# writable for it. Alternative: run without --cap-drop, or mount a directory owned by root.
chmod 0777 "$OUT"

docker run --rm \
  --network none --cpus 2 --memory 4g --memory-swap 4g --pids-limit 512 \
  --read-only --tmpfs /tmp:rw,size=1g \
  --cap-drop ALL --cap-add SETUID --cap-add SETGID --cap-add KILL --security-opt no-new-privileges \
  -v "$OUT:/jury" --workdir /app --entrypoint python "$IMAGE" -c '
import json, judge_adapter as ja

ja.generate_episode(216, json.load(open("configs/scenarios/interaction_fx_partial_at_cutoff.json")), "/jury/D16")
env = ja.make_env()
obs, info = env.reset(0, {"mode": "closed", "episode_dir": "/jury/D16", "team_config": {}})
page = env.step({"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})
truth = json.load(open("/jury/D16/truth.json"))          # jury side only (oracle)
findings = {"episode_id": truth["episode_id"], "findings": [
    {"type": i["type"], "affected_ids": i["affected_ids"], "claimed_amount_minor": i["expected_amount_minor"],
     "currency": i["currency"], "evidence_ids": i["evidence_sets"][0], "explanation": "oracle"}
    for i in truth["issues"]]}
done = env.step({"name": "submit", "args": {"findings": findings}})
score = ja.score_episode("/jury/D16", env.submitted_findings(), env.inspection_steps())
print(json.dumps({"first_observation_keys": sorted(obs), "page_rows": len(page[0]["result"]["records"]),
                  "submit": done[0], "closed_reward_seen_by_policy": done[1], "score": score}))
'
