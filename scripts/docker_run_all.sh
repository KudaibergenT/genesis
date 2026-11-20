#!/usr/bin/env sh
# Build the evaluation image and run the full offline verification inside it with the official limits.
#   scripts/docker_run_all.sh [extra run_all.py args]
#   PLATFORM=linux/amd64 scripts/docker_run_all.sh     # another architecture (emulated if not native)
# The report is written to ./results/docker_run_all[_<arch>] on the host.
set -eu
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-genesis-auditenv:stage3}"
OUT="$(pwd)/results/docker_run_all"
PLATFORM_ARGS=""
if [ -n "${PLATFORM:-}" ]; then
  PLATFORM_ARGS="--platform $PLATFORM"
  IMAGE="$IMAGE-$(echo "$PLATFORM" | tr '/' '-')"
  OUT="$OUT-$(echo "$PLATFORM" | tr '/' '-')"
fi
mkdir -p "$OUT"
# The evaluator runs as root with all capabilities dropped (no CAP_DAC_OVERRIDE), so on Linux hosts it
# cannot write into a directory owned by the invoking user unless the directory itself allows it.
chmod 0777 "$OUT"

# shellcheck disable=SC2086
docker build $PLATFORM_ARGS -t "$IMAGE" .

# shellcheck disable=SC2086
docker run --rm $PLATFORM_ARGS \
  --network none \
  --cpus 2 --memory 4g --memory-swap 4g --pids-limit 512 \
  --read-only --tmpfs /tmp:rw,size=1g \
  --cap-drop ALL --cap-add SETUID --cap-add SETGID --cap-add KILL \
  --security-opt no-new-privileges \
  -v "$OUT:/out" \
  "$IMAGE" --out /out --require-offline --require-limits "$@"
