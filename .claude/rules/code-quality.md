# Code Quality Rules

- Follow existing repository conventions: async SQLAlchemy 2 `select()`, `Mapped[...]` models, the `success_response`/`error_response` envelope, `HTTPException(detail=error_response(...))` in business code, and structlog.
- Prefer small, single-purpose services. Avoid giant AI service classes. One module per concern (see `rules/architecture.md`).
- Use typed Pydantic request/response models. Use enums from `models/enums.py` rather than string literals where they exist.
- Keep business logic out of route handlers for new AI code. Handlers parse the request, build the context, call a service and map the result/error.
- Don't duplicate authorization logic. All AI permission checks go through `services/ai/authorization/`.
- Keep provider-specific LLM/embedding code isolated in `services/ai/llm/` and `services/ai/embeddings.py`, behind protocols.
- Use FastAPI dependency injection (`Depends`) for context, DB session and provider instances, so tests can override them.
- Add structured logging (`structlog.get_logger()`), with event names like `ai.sql.blocked` and `ai.action.executed`, and always redacted.
- Handle errors explicitly by mapping to `AIError` subclasses with stable `AI_*` codes. Never use bare `except:`, and never swallow errors silently, except in the audit writer, which logs and continues.
- Don't rewrite working modules to make them look cleaner. Keep existing-file changes minimal and additive.
- Frontend: TypeScript, existing Tailwind styles, `lib/api.ts` function pattern returning `ApiResult<ApiEnvelope<T>>`.
