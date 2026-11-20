#!/usr/bin/env sh
# Build the evaluation image and run the full offline verification inside it with the official limits.
#   scripts/docker_run_all.sh [extra run_all.py args]
# The report is written to ./results/docker_run_all on the host.
set -eu
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-genesis-auditenv:stage3}"
OUT="$(pwd)/results/docker_run_all"
mkdir -p "$OUT"

docker build -t "$IMAGE" .

docker run --rm \
  --network none \
  --cpus 2 --memory 4g --memory-swap 4g --pids-limit 512 \
  --read-only --tmpfs /tmp:rw,size=1g \
  --cap-drop ALL --cap-add SETUID --cap-add SETGID --cap-add KILL \
  --security-opt no-new-privileges \
  -v "$OUT:/out" \
  "$IMAGE" --out /out --require-offline --require-limits "$@"
