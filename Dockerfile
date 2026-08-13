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

# Single worker on purpose — see the note at the top of this file. Raise only if
# a measurement says CPU is the bottleneck; the DB pool will bind first.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
