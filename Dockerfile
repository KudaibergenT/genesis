# Offline, CPU-only evaluation image for genesis-auditenv.
#   docker build -t genesis-auditenv:stage3 .
#   scripts/docker_run_all.sh          # runs scripts/run_all.py with the official limits
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONUTF8=1 \
    TZ=UTC \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    AUDITENV_ROOT=/app \
    HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis

WORKDIR /app

# Test dependencies only (the environment uses the standard library); hashes are enforced.
COPY requirements-dev.lock ./
RUN pip install --no-deps --require-hashes -r requirements-dev.lock

COPY pyproject.toml README.md LICENSE judge_adapter.py ./
COPY src ./src
COPY schemas ./schemas
COPY configs ./configs
COPY episodes ./episodes
COPY scripts ./scripts
COPY jury_sim ./jury_sim
COPY tests ./tests
COPY docs ./docs

# The agent process runs as this unprivileged user; closed truth files are readable by root only.
RUN groupadd --system --gid 10001 agent \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin agent \
 && chmod -R a+rX /app \
 && find /app/episodes -type d -name truth -exec chmod -R go-rwx {} + \
 && find /app -name '__pycache__' -prune -exec rm -rf {} +

ENTRYPOINT ["python", "scripts/run_all.py"]
CMD ["--out", "/tmp/report"]
