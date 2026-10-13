# Stages (production hardening Phase 2):
#   base        dependencies only
#   production  non-root, no reload, single worker, healthcheck   -> docker-compose.prod.yml (target: production)
#   dev         default/last stage, unchanged dev behaviour (reload) -> docker-compose.yml
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM base AS production

# Unprivileged runtime user; only /app/storage (DB, uploads, model cache) is writable.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && mkdir -p /app/storage \
    && chown app:app /app/storage

COPY . .

# FORWARDED_ALLOW_IPS: set to the reverse proxy's address in deployment (uvicorn reads it for --proxy-headers).
# PYTHONPATH: operator scripts (scripts/*.py: ingest, backup, retention) import `app` (dev compose sets the same).
ENV ENVIRONMENT=production \
    FORWARDED_ALLOW_IPS=127.0.0.1 \
    PYTHONPATH=/app

USER app
EXPOSE 8000
STOPSIGNAL SIGTERM

# Readiness (DB reachable). Sends the first ALLOWED_HOSTS entry as Host so TrustedHostMiddleware accepts it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; host = (os.environ.get('ALLOWED_HOSTS') or 'localhost').split(',')[0].strip(); req = urllib.request.Request('http://127.0.0.1:8000/api/v1/health', headers={'Host': host}); sys.exit(0 if urllib.request.urlopen(req, timeout=4).status == 200 else 1)"]

# Single worker: SQLite and the in-process AI rate limits assume one process (see docs/production-hardening).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--timeout-graceful-shutdown", "20", "--no-server-header"]


FROM base AS dev

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
