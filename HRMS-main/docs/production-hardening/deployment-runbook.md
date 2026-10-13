# Deployment and operations runbook (production-shaped stack)

**Status:** for a **controlled pilot or staging** deployment. **NOT YET PRODUCTION READY.** See [phase-2-report.md](phase-2-report.md) §8 for the blockers.
**Scope:** `docker-compose.prod.yml` (API and web) on one host behind a TLS reverse proxy. The development workflow (`docker-compose.yml`) is unchanged.

## 1. Topology and fixed assumptions

| Item | Value | Why |
|---|---|---|
| API | 1 container, **1 uvicorn worker**, uid 10001, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges` | SQLite and the in-memory AI rate limits and budgets are only correct in one process. Startup refuses `WEB_CONCURRENCY` or `--workers` greater than 1. |
| Web | `next start` on Node 24.21.0, user `node`, read-only root filesystem | Node 20 is end-of-life. |
| Ports | Bound to `127.0.0.1` by default (`API_BIND` and `WEB_BIND` override this) | Nothing is exposed without the proxy. |
| Persistent data | Volume `api_storage` → `/app/storage`: the DB, uploads and the embedding model cache | Back it up (§5). |
| TLS, HSTS, request-size limits | **The reverse proxy's job**; not provided here | Open decision: which proxy, and who operates it. |

## 2. Configuration

1. Copy `backend/production.env.example` to a file outside git, for example `backend/.env.production`, and set `API_ENV_FILE` to its path.
2. Fill in:
   - `JWT_SECRET_KEY` and `AI_AUDIT_FINGERPRINT_KEY`: each at least 32 random characters, and different from each other.
   - `ALLOWED_HOSTS`: the exact API host names. Docker's healthcheck uses the first entry.
   - `CORS_ALLOW_ORIGINS`: the exact web origin(s).
   - `FORWARDED_ALLOW_IPS`: the proxy's address.
3. Keep `AI_ENABLED=false` until the data-processing decisions are approved ([data-processing.md](data-processing.md) §5). With AI disabled, the Copilot answers `AI_UNAVAILABLE`.
4. Leave `AI_SQL_RESULT_FORMATTING` unset (default `deterministic`) and `ALLOW_EXTERNAL_TRACING=false`.
5. Set `PUBLIC_API_BASE_URL` at **build** time. It is compiled into the web bundle.

The API **refuses to start** in production on any of these: a default or short JWT secret, a missing or reused fingerprint key, wildcard hosts or origins, more than one worker, tracing variables without approval, or AI enabled without a key. The error names the settings and never their values.

## 3. First deployment

```bash
cd HRMS-main
export API_ENV_FILE=./backend/.env.production PUBLIC_API_BASE_URL=https://api.example.internal
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm api alembic -c alembic.ini upgrade head
docker compose -f docker-compose.prod.yml run --rm api python scripts/seed.py   # demo data only, never real HR data
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps                                    # api must be "healthy"
```

Checks:
- `curl -H "Host: <allowed host>" http://127.0.0.1:8000/api/v1/health` returns 200.
- A request with any other Host header returns 400.

After the first deployment every user must log in again: access tokens now carry `type: access`.

## 4. Upgrade and rollback

1. Take a backup (§5) and run `restore.py --verify-only` on it.
2. Build the new images and run `alembic upgrade head` with `run --rm`.
3. Run `up -d`. Graceful shutdown waits up to 20 s for in-flight requests (`stop_grace_period` is 30 s).
4. Run the stale-actions report (§6).
5. **Rollback:** go back to the previous image tags. If a migration ran, restore the pre-upgrade backup (§5.2): migrations are not written to be reversible in production.

## 5. Backups and restore

Details and drill evidence are in [backup-restore.md](backup-restore.md). The root filesystem is read-only, so mount a destination writable by uid 10001:

```bash
docker compose -f docker-compose.prod.yml run --rm -v /srv/hrms-backups:/backups api \
  python scripts/backup.py --destination /backups --keep-days 30          # add --prune only after approval
```

### 5.1 Verify

```bash
docker compose -f docker-compose.prod.yml run --rm -v /srv/hrms-backups:/backups:ro api \
  python scripts/restore.py --backup /backups/hrms-backup-<ts> --verify-only
```

### 5.2 Restore

1. Restore into an empty scratch directory with `--destination`.
2. Stop the stack.
3. Copy the restored `hrms.db` and the upload directories into the volume, **at the same paths**: the database stores absolute file paths.
4. Start the stack.
5. Check health and `alembic current`.

Scheduling, encryption, off-host copies and access control are **open decisions** (backup-restore.md §6).

## 6. Routine operations

| Task | Command (inside the API container) | Notes |
|---|---|---|
| Stale or unknown AI actions | `python scripts/stale_actions_report.py` | Read-only. Exit code 3 means items need human review in the HR system. |
| Retention dry run | `python scripts/retention_purge.py` | Prints counts only. |
| Retention apply | `python scripts/retention_purge.py --apply --operator-employee-id <active admin id>` | **Only after the retention periods are approved.** Audited. Never deletes audit events. |
| Policy index | `python scripts/ingest_policies.py` | Needs AI enabled. With AI disabled it exits with an error (traceback); this is known and cosmetic. |

## 7. Monitoring signals

These are available today but **not wired to any alerting** (open decision):
- the Docker health state;
- `GET /api/v1/ai/audit/metrics` (admin);
- structured logs, for example `ai.audit.write_failed`, `ai.audit.fallback`, `ai.retention.run` and rate-limit denials;
- the stale-actions report's exit code.
