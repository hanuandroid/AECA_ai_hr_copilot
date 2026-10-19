# Testing Rules

- Every new service must have tests.
- Every security rule must have negative tests: the attack is attempted and blocked.
- Every AI endpoint must have API tests for EMPLOYEE, MANAGER, ADMIN, unauthenticated and INACTIVE users.
- Every SQL guardrail must have tests, one per blocked construct and one per exfiltration trick.
- Every permission rule (each cell of `ai_permissions_matrix.md`) must have a test. Prefer table-driven parametrized tests.
- Existing tests must keep passing. The repo started with **zero** tests, so Phase 1 adds characterization tests for every endpoint AI tools wrap. Treat those as the "existing tests" from then on.
- Never remove existing tests to make CI pass.
- Never weaken assertions without documenting the reason in the test and in `docs/phase-status.md`.
- If a test fails, find the root cause. Do not skip it, xfail it or loosen it.
- Tests never call real LLM or embedding APIs. Use `llm/fake.py` and the hash embedding (`embeddings.FakeEmbeddingProvider`). Real-provider runs belong to `python -m evals.run_evals --live`, only with approval (external calls that cost money).
- Tests use an isolated temp SQLite DB built by running Alembic migrations. Never use `storage/hrms.db`.
- Run from `HRMS-main/backend`: `pytest -q`. The frontend is checked with `npm run build` in `HRMS-main/frontend`.
