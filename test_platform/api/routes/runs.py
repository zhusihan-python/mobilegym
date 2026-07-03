from fastapi import APIRouter, Request

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError

router = APIRouter(prefix="/api/platform/v1")


@router.get("/runs")
def list_runs(request: Request) -> dict[str, object]:
    database = get_database(request)
    readiness = database.readiness()
    if not readiness["ready"]:
        raise ApiError(
            "SERVICE_NOT_READY",
            "The Test Platform service is not ready.",
            status_code=503,
            details=[
                {"name": name, **check}
                for name, check in readiness["checks"].items()
                if not check["ready"]
            ],
        )

    return {
        "items": database.list_runs(),
        "next_cursor": None,
    }
