from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Mock HRMS API"
    environment: str = "dev"
    app_timezone: str = "Asia/Kolkata"
    database_url: str = "sqlite+aiosqlite:///./storage/hrms.db"

    jwt_secret_key: str = "change_me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    # Comma-separated. Hosts the API answers to (TrustedHostMiddleware, production hardening B-25) and the browser
    # origins allowed by CORS. Production must set real values; wildcards are refused by production_checks.
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_allow_origins: str = "http://localhost:3000"
    # LangSmith/LangChain tracing would send prompts and HR data to an external service; production refuses it unless
    # this is explicitly set after a data-processing approval (B-16).
    allow_external_tracing: bool = False
    policy_upload_dir: str = "/app/storage/hr-policies"
    profile_photo_upload_dir: str = "/app/storage/profile-photos"
    employee_document_upload_dir: str = "/app/storage/employee-documents"

    # AI copilot settings. Secrets are read from the environment only; never hardcode keys.
    ai_enabled: bool = True
    ai_llm_provider: str = "anthropic"  # anthropic | fake
    ai_llm_model: str = "claude-sonnet-5-5"
    ai_llm_fast_model: str = "claude-haiku-4-5-20251001"
    anthropic_api_key: SecretStr | None = None
    # Current Claude 5.x models reject non-default sampling params; only sent when explicitly set.
    ai_temperature: float | None = None
    ai_max_tokens: int = 4096
    ai_llm_effort: str = "low"  # output_config.effort: low | medium | high | xhigh | max
    ai_llm_server_fallbacks: bool = True  # server-side refusal fallback ("default" mode, Claude API)
    ai_request_timeout_seconds: float = 30.0
    ai_embedding_provider: str = "fastembed"  # fastembed | fake
    ai_embedding_model: str = "BAAI/bge-small-en-v1.5"
    ai_embedding_cache_dir: str | None = None  # fastembed model cache (e.g. /app/storage/models)
    # How SQL-agent answers are worded (production hardening B-15). "deterministic" (default) never sends result rows
    # to the model provider. "llm" sends redacted rows to the provider for wording: enable only after the
    # data-processing policy, provider retention settings and contractual approvals are confirmed.
    ai_sql_result_formatting: Literal["deterministic", "llm"] = "deterministic"
    ai_sql_max_rows: int = 100
    ai_sql_max_joins: int = 4
    ai_sql_timeout_seconds: float = 3.0
    ai_action_confirmation_ttl_seconds: int = 300
    # Deadline for executing a confirmed action via the business API; on expiry the outcome is UNKNOWN (never retried).
    ai_action_execution_timeout_seconds: float = 10.0
    ai_conversation_max_messages: int = 200
    # Abuse and cost controls (production hardening B-09). Provisional defaults, not approved business quotas.
    # In-memory enforcement: valid only for a single-worker, single-instance deployment (see production_checks).
    ai_rate_limit_per_minute: int = 20  # per authenticated user, across all model-calling AI endpoints; 0 disables
    ai_daily_token_budget: int = 200_000  # per user per UTC day, from recorded provider usage; 0 disables
    ai_daily_cost_cap_usd: float | None = None  # per user per UTC day; disabled until pricing/budget policy is set
    ai_max_concurrent_llm_calls: int = 4  # process-wide
    ai_llm_queue_timeout_seconds: float = 15.0  # wait for a free model-call slot before AI_BUSY
    auth_login_rate_limit_per_minute: int = 10  # per client IP + normalized account identifier; 0 disables
    # Retention (production hardening B-14). PROVISIONAL planning defaults pending business/security/legal approval.
    ai_conversation_retention_days: int = 90  # by last activity (updated_at)
    ai_pending_action_retention_days: int = 30  # after terminal state or expiry; EXECUTING/UNKNOWN are never purged
    ai_audit_retention_days: int = 365  # reported only: audit events are append-only and are never deleted by tooling
    ai_audit_enabled: bool = True
    # HMAC key for audit text fingerprints (B-13): must differ from JWT_SECRET_KEY; required in production.
    # Rotating it breaks correlation between fingerprints made before and after the rotation (by design).
    ai_audit_fingerprint_key: SecretStr | None = None
    # Optional pricing (USD per million tokens) for ESTIMATED cost; unset -> cost is null. Not billing.
    ai_llm_input_cost_per_mtok: float | None = None
    ai_llm_output_cost_per_mtok: float | None = None
    ai_llm_fast_input_cost_per_mtok: float | None = None
    ai_llm_fast_output_cost_per_mtok: float | None = None
    ai_conversation_context_messages: int = 6
    ai_conversation_context_chars: int = 2000
    ai_conversation_stored_message_chars: int = 4000
    ai_rag_top_k: int = 4
    ai_rag_min_score: float = 0.35
    ai_policy_chunk_size: int = 800
    ai_policy_chunk_overlap: int = 120


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


settings = Settings()
