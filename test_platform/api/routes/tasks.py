from fastapi import APIRouter, Query

from test_platform.api.errors import ApiError
from test_platform.domain.task_catalog import TaskCatalogFilter, build_task_catalog_snapshot

router = APIRouter(prefix="/api/platform/v1")


@router.get("/tasks")
def list_tasks(
    suite: list[str] | None = Query(default=None),
    app: list[str] | None = Query(default=None),
    difficulty: list[str] | None = Query(default=None),
    query: str | None = None,
) -> dict[str, object]:
    snapshot = build_task_catalog_snapshot(
        filters=TaskCatalogFilter(
            suites=suite,
            apps=app,
            difficulties=difficulty,
            query=query,
        )
    )
    return {
        "items": [item.model_dump(mode="json") for item in snapshot.items],
        "next_cursor": None,
        "digest": snapshot.digest,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    snapshot = build_task_catalog_snapshot()
    for item in snapshot.items:
        if item.task_base_id == task_id:
            return item.model_dump(mode="json")
    raise ApiError(
        "TASK_NOT_FOUND",
        "Task was not found.",
        status_code=404,
        details=[{"task_id": task_id}],
    )
