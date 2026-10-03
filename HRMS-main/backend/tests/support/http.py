import httpx


def error_code(response: httpx.Response) -> str | None:
    """Extract the error code from either envelope shape used by the app.

    Existing endpoints raise HTTPException(detail=error_response(...)) -> {"detail": {"error": {...}}};
    envelope-returning endpoints use {"error": {...}} at the top level.
    """
    body = response.json()
    if isinstance(body.get("detail"), dict):
        body = body["detail"]
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def data(response: httpx.Response):
    body = response.json()
    assert body["success"] is True, body
    return body["data"]
