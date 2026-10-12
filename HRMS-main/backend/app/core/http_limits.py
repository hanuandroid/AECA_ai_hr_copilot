"""HTTP request hardening that is independent of business logic (production hardening Phase 2, B-26).

RangeHeaderLimitMiddleware: Starlette's FileResponse answers a multi-range request with one multipart part per range.
Even after the CVE-2025-62727 fix (Starlette 1.3.1), a header with tens of thousands of small ranges makes the server
build a response far larger than the file (measured ~15 s for 30 000 ranges on a 120 KB file). RFC 9110 allows a server
to ignore Range, so requests with too many ranges or an oversized Range header are served as a normal full response.
"""

MAX_RANGES = 8
MAX_RANGE_HEADER_BYTES = 1024


class RangeHeaderLimitMiddleware:
    def __init__(self, app, *, max_ranges: int = MAX_RANGES, max_header_bytes: int = MAX_RANGE_HEADER_BYTES):
        self.app = app
        self.max_ranges = max_ranges
        self.max_header_bytes = max_header_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = scope.get("headers") or []
            if any(name == b"range" and self._excessive(value) for name, value in headers):
                scope = {**scope, "headers": [(name, value) for name, value in headers if name != b"range"]}
        await self.app(scope, receive, send)

    def _excessive(self, value: bytes) -> bool:
        return len(value) > self.max_header_bytes or value.count(b",") + 1 > self.max_ranges
