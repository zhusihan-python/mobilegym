from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from test_platform.api.dependencies import get_database

router = APIRouter()


@router.get("/health/live")
def live() -> dict[str, bool]:
    return {"live": True}


@router.get("/health/ready")
def ready(request: Request) -> JSONResponse:
    readiness = get_database(request).readiness()
    status_code = 200 if readiness["ready"] else 503
    return JSONResponse(status_code=status_code, content=readiness)
