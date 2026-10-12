from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import settings, split_csv
from app.services.ai.actions.client import INTERNAL_BASE_URL
from app.core.http_limits import RangeHeaderLimitMiddleware
from app.core.logging import configure_logging
from app.core.production_checks import assert_production_ready
from app.services.ai.audit.middleware import AIAuditMiddleware
from app.services.ai.audit.recorder import record_error
from app.services.ai.errors import AIError, ai_error_response


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    assert_production_ready(settings)  # Phase 10: fail fast on unsafe production config (no-op outside production)
    yield


app = FastAPI(title="Mock HRMS API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=split_csv(settings.cors_allow_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Phase 9: let the browser read the AI correlation headers (no credentials or data in them).
    expose_headers=["X-Request-Id", "X-Conversation-Id"],
)
app.add_middleware(AIAuditMiddleware)  # Phase 8: request_id + audit scope for /api/v1/chat* and /api/v1/ai* only
app.add_middleware(RangeHeaderLimitMiddleware)  # Production hardening B-26: ignore abusive multi-range requests
# Production hardening B-25: reject unknown Host headers (400) before routing, so a spoofed Host can never produce a
# redirect or URL pointing elsewhere. The in-process action client's internal host is always allowed.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[*split_csv(settings.allowed_hosts), urlparse(INTERNAL_BASE_URL).hostname],
)
app.include_router(api_router, prefix="/api/v1")
# AI errors only (raised by /api/v1/chat/* AI code); existing endpoints are unaffected.
def _ai_error_handler(_request, exc: AIError):
    record_error(exc)  # passive: records only the safe public code/status
    return ai_error_response(exc)


app.add_exception_handler(AIError, _ai_error_handler)
@app.get("/health")
async def root_health():
    return {"success": True, "data": {"status": "ok"}, "error": None}
