# DSEC API — production image for the OVH VPS.
#
# Two stages so the ~165 MB of build-time wheel machinery never reaches the
# runtime image. Everything the app imports (Pillow, pillow-heif, psycopg,
# cryptography, anthropic) ships prebuilt wheels for linux/amd64, so no compiler
# is needed at runtime.
#
# Measured footprint of the running app: ~136 MB RSS steady, ~227 MB peak during
# a 12 MP image resize. One worker is the right default — 188 of the ~202
# endpoints are sync `def`, which Starlette runs on a thread pool inside a single
# process, so concurrency comes from threads and DB connections, not processes.

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is used by the container healthcheck below and by the cron sidecar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. The app writes nothing to disk in production (media
# goes to Supabase, all state to Neon), so the filesystem can stay read-only.
RUN useradd --create-home --uid 10001 dsec

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=dsec:dsec alembic.ini ./
COPY --chown=dsec:dsec alembic ./alembic
COPY --chown=dsec:dsec app ./app
COPY --chown=dsec:dsec scripts ./scripts

USER dsec
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Two workers, one per vCPU. This was 1, on the reasoning that the DB pool would
# bind before CPU did. Load-tested on the real VPS-1 (2 vCPU), and that reasoning
# was wrong — the pool never saturated and the app was CPU-bound instead:
#
#   /website/events, external load, warm      1 worker    2 workers
#     concurrency 10                            58 req/s    68 req/s
#     concurrency 30                            61 req/s    82 req/s   (+34%)
#     p50 at concurrency 30                     465 ms      334 ms
#
# The database is not the constraint: a Neon round trip from this box is 1.6 ms
# and the events query 6.3 ms, against ~70 ms of total request time. The rest is
# ORM + Pydantic work, which is CPU, so a second process buys real throughput.
# /health (no DB, trivial serialisation) reaches ~530 req/s either way.
#
# Do not raise this past the core count: each worker holds its OWN DB pool, so
# workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW) = 2 x 20 = 40 connections to Neon.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
