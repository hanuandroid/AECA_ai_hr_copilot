from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import settings, split_csv
from app.services.ai.actions.client import INTERNAL_BASE_URL
from app.core.http_limits import RangeHeaderLimitMiddleware
from app.core.logging import configure_logging
from app.core.production_checks import assert_production_ready
from app.db.revision_guard import assert_database_at_expected_revision
from app.db.session import engine
from app.services.ai.audit.middleware import AIAuditMiddleware
from app.services.ai.audit.recorder import record_error
from app.services.ai.errors import AIError, ai_error_response
from app.services.ai.limits import AIRequestBudgetMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    assert_production_ready(settings)  # Phase 10: fail fast on unsafe production config (no-op outside production)
    # S-07: refuse to serve unless the database is exactly at this code's migration head (read-only; never migrates).
    await assert_database_at_expected_revision(engine)
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
app.add_middleware(AIRequestBudgetMiddleware)  # AIS-10: per-request model-call budget + deadline (same AI paths)
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


# AIS-12 review (L1): FastAPI's default 422 body includes each error's `input`, which for POST /chat/actions/confirm can
# be the step-up password. On AI paths the inputs (and ctx) are dropped; every other path keeps the default handler.
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith(AIRequestBudgetMiddleware.PREFIXES):
        return await request_validation_exception_handler(request, exc)
    errors = [{key: value for key, value in error.items() if key not in ("input", "ctx")} for error in exc.errors()]
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errors}))


app.add_exception_handler(RequestValidationError, _validation_error_handler)
@app.get("/health")
async def root_health():
    return {"success": True, "data": {"status": "ok"}, "error": None}
