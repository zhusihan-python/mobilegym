from uuid import uuid4
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def install_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


def install_mutation_origin_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def mutation_origin_middleware(request: Request, call_next):
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and not _origin_allowed(origin, request):
                request_id = getattr(request.state, "request_id", uuid4().hex)
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "MUTATION_ORIGIN_REJECTED",
                            "message": "Mutating requests must originate from the local console origin.",
                            "details": [{"origin": origin}],
                            "request_id": request_id,
                        }
                    },
                    headers={"x-request-id": request_id},
                )
        return await call_next(request)


def _origin_allowed(origin: str, request: Request) -> bool:
    parsed = urlparse(origin)
    origin_host = (parsed.hostname or "").lower()
    request_host = (request.url.hostname or "").lower()
    if not origin_host or not request_host:
        return False
    if origin_host == request_host:
        return True
    return _is_loopback_host(origin_host) and _is_loopback_host(request_host)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    if normalized == "::1":
        return True
    if normalized.startswith("127."):
        return True
    return False
