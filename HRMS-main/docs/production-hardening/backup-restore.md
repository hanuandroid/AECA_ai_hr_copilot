# Retention, backup and recovery (Phase 2: B-11, B-14 and approval item 6)

Status: **tooling implemented and drilled on a copy of the dev database. Not yet operated in production.** Every period below is a **provisional planning default**. Business, security and legal owners have not approved it.

## 1. Retention (`services/ai/retention.py`, `scripts/retention_purge.py`)

| Data | Provisional rule | Setting | What the tool does |
|---|---|---|---|
| AI conversations and their messages | 90 days after last activity (`updated_at`) | `AI_CONVERSATION_RETENTION_DAYS` | Deletes on `--apply`. Messages are deleted explicitly, because SQLite does not enforce the cascade. |
| Pending actions in a terminal state (COMPLETED, FAILED, DENIED, EXPIRED, INVALID) | 30 days after `used_at`, or after `expires_at` if never used | `AI_PENDING_ACTION_RETENTION_DAYS` | Deletes on `--apply`. |
| Pending actions never confirmed (PENDING) | 30 days after `expires_at` | same | Deletes on `--apply`. |
| Pending actions in EXECUTING or UNKNOWN | Never purged | none | Counted only. They go through the reconciliation report (§2). |
| AI audit events | At least 365 days | `AI_AUDIT_RETENTION_DAYS` | **Counted only. Never deleted.** The table is append-only and protected by triggers. Any audit deletion needs a separately approved legal and security procedure. |

The cleanup has three safeguards:
- **Dry run by default.** Without `--apply`, it only prints counts.
- **Authorization.** `--apply --operator-employee-id N` requires `N` to be an **ACTIVE ADMIN** in the database. Anyone else is refused (exit 2) and nothing is deleted.
- **Audit.** Each applied run writes one `RETENTION_PURGE` audit event per table. Each event is attributed to the operator and contains only `operation=retention:<table>` and `row_count`. A dry run writes no audit event.

**Do not run `--apply` against production** until the retention periods are approved.

## 2. Stale and unknown actions (`services/ai/actions/reconciliation.py`, `scripts/stale_actions_report.py`)

This report is read-only. It lists two kinds of pending action:
- `UNKNOWN`: the execution deadline was exceeded (B-10).
- `EXECUTING`: claimed more than `--minutes` ago (default 15).

For each one it shows only identifiers, status, the claim timestamp and the result code. It never shows payloads, summaries or token hashes. The exit code is 3 when items need review, so it can be used in monitoring.

The report never changes or retries anything. An operator checks each item in the HR system (for example, whether the leave request exists) and resolves it there. Who performs that check is an open decision.

## 3. Backup (`app/ops/backup.py`, `scripts/backup.py`)

Each backup is a directory named `hrms-backup-<UTC timestamp>/` containing:
- `hrms.db`: made with SQLite's online backup API, so it is consistent while the API is running, then checked with `PRAGMA integrity_check`.
- `uploads/hr-policies`, `uploads/employee-documents`, `uploads/profile-photos`: the persistent upload directories. An absent directory is recorded as empty.
- `config.json`: non-secret settings only. Any setting whose name contains secret, key, password, token, credential or `database_url`, and any SecretStr value, is **excluded entirely, not masked**. The application configuration itself comes from the environment and secret store, which are not backed up here.
- `manifest.json`: the sha256 and size of every file, the Alembic revision and the integrity result.

The backup is built in a `.partial` directory and renamed only when complete, so a failed run leaves no half-written backup. Output lists only counts, sizes and paths.

Backup retention: `--keep-days 30` lists backups older than 30 days, and `--keep-days 30 --prune` deletes them. Only directories whose names match `hrms-backup-<timestamp>` are considered.

Provisional targets: daily backups, 30-day retention, **RPO 24 h, RTO 4 h**. These are not approved and not yet scheduled; no scheduler is configured.

## 4. Restore (`scripts/restore.py`)

- `--verify-only` checks every checksum, rejects missing or unexpected files, and runs the integrity check.
- `--destination DIR` verifies first, then requires `DIR` to be **empty or absent**. It copies the database and uploads, verifies the restored checksums and integrity again, and never writes to the configured live paths.

Cutover is a separate, deliberate operator step:
1. Stop the API.
2. Move the restored `hrms.db` and upload directories into the configured paths.
3. Start the API.
4. Check `/api/v1/health` and `alembic current`.
5. Run the stale-actions report.

The database stores **absolute file paths** for documents and photos, so the upload directories must be restored to the same configured paths (for example `/app/storage/...`).

## 5. Restore drill (2026-10-13, local, copy of the dev database)

The drill ran in a throwaway container from the `hrms-main-api` image, against a copy of `storage/hrms.db` and the policy files placed in a scratch directory. The real dev DB checksum was unchanged afterwards.

| Step | Result |
|---|---|
| Backup | 26 files, 3,192,873 bytes, Alembic `0020_ai_audit_events`, checksums and integrity verified |
| Secret names in `config.json` | 0 matches |
| `--verify-only` | OK |
| Restore to an empty directory | OK; 26 files re-verified; integrity ok |
| Restore into a non-empty directory | Refused (exit 1) |
| Row counts, source vs restored | 25 tables, 0 mismatches |
| App session on the restored DB | 1003 employees read through the application's own engine |
| Tampered backup (`config.json` modified) | Refused: `checksum mismatch: config.json` |
| Retention dry run on the copy | All counts 0; nothing deleted |
| Retention `--apply` by an unknown operator | Refused (exit 2) |
| Stale-actions report | Empty (exit 0) |

The end-to-end drill took well under a minute. This says nothing about the production RTO: it does not measure off-host transfer, decryption or cutover.

Not verified: a restore on a different host, a restore from off-host storage, cutover of a running production stack, and a backup taken under write load.

## 6. Open decisions (owners required)

1. **Retention periods** (90 / 30 / 365 days): business, HR, legal and privacy approval.
2. **Audit-event lifecycle** beyond 365 days, including any approved deletion or archival procedure. No tool deletes audit events today.
3. **Backup encryption at rest**: tool and key management. Backups contain personal and payroll data.
4. **Backup destination and off-host copies**: location, provider and region.
5. **Access control** for the backup location and for who may run restore or retention `--apply`.
6. **Scheduling and monitoring** for daily backups, the stale-actions report and backup-age alerts.
7. **Final RPO and RTO**, confirmed by a timed drill in the target environment.
8. **Reconciliation owner** for UNKNOWN and EXECUTING actions.
