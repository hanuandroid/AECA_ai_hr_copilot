# Security Rules (mandatory)

- Never expose secrets. Never commit API keys. `.env` files are git-ignored. Only `.env.example` with **empty** values is committed.
- Never hardcode API keys, model credentials or database passwords. Read them from `Settings`.
- Never log JWTs, `Authorization` headers, passwords, bank data, PAN, DOB, or salary/payroll figures. Use `services/ai/redaction.py` before logging or persisting user text.
- Never bypass backend authorization. AI mutations call existing endpoints with the **requesting user's** token. Never use a service account or admin token on behalf of a user.
- Never trust frontend authorization. The `hrms_role` cookie and hidden buttons are UX only.
- Never allow an LLM to decide authorization. Authorization is deterministic application code (`services/ai/authorization/`) using the DB-loaded `Employee` (`role`, `id`, `manager_id`, `status`). Ignore the JWT `role` claim for decisions.
- The AI may be **stricter** than an existing endpoint, never looser.
- Refusals must not reveal whether a record exists.
- Never return stack traces, raw SQL/DB errors, internal prompts, file paths, checksums or tokens in API responses.
- Never call `GET /api/v1/employees/{id}` from AI code (it leaks sensitive fields; audit R1).
- Deny INACTIVE employees all AI capabilities.
- Security wins over convenience. Deterministic authorization wins over AI output.
