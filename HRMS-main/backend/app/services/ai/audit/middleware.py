"""AIAuditMiddleware: one audit scope (one request_id) per AI HTTP request.

Pure ASGI (no response buffering). For /api/v1/chat* and /api/v1/ai* it:
- creates the scope and exposes its id to all AI code (contextvar) and to the client (X-Request-Id);
- records AI_HTTP_REQUEST (endpoint template, method, HTTP status, latency) when the response is done;
- flushes the request's events (fail-open to the structured log).
It never inspects bodies or headers beyond the path/method, and never changes the response status.
"""

import re

from app.core.config import settings
from app.services.ai.audit import recorder, store
from app.services.ai.audit.events import AuditEventType, AuditStatus

AUDITED_PREFIXES = ("/api/v1/chat", "/api/v1/ai")
_SAFE_SEGMENT = re.compile(r"^[a-z_-]{1,40}$")


def endpoint_template(scope: dict) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    # Unmatched path: keep only known-safe segments (ids, uuids and junk become {id}).
    segments = [segment if _SAFE_SEGMENT.match(segment) else "{id}" for segment in scope.get("path", "").split("/")]
    return "/".join(segments)[:120]


def _status_for(http_status: int) -> AuditStatus:
    if http_status < 400:
        return AuditStatus.SUCCESS
    if http_status in (401, 403):
        return AuditStatus.DENIED
    return AuditStatus.FAILED


class AIAuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") == "OPTIONS"  # CORS preflight: no user, no AI work
                or not scope.get("path", "").startswith(AUDITED_PREFIXES)):
            await self.app(scope, receive, send)
            return
        audit_scope = recorder.new_scope()
        token = recorder.activate(audit_scope)
        response_status = {"code": 500}
        timer = recorder.Timer()

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                response_status["code"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", audit_scope.request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            http_status = response_status["code"]
            recorder.record(AuditEventType.AI_HTTP_REQUEST, _status_for(http_status), duration_ms=timer.ms,
                            endpoint=endpoint_template(scope), method=scope.get("method"), http_status=http_status,
                            actor_role=audit_scope.actor_role)
            recorder.deactivate(token)
            if settings.ai_audit_enabled:
                await store.flush(audit_scope)
