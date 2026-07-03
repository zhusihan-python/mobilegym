from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.projects import DuplicateProjectName, Project, ProjectNotFound
from test_platform.persistence.repositories import ProjectRepository

router = APIRouter(prefix="/api/platform/v1")


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class UpdateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/projects")
def list_projects(request: Request, include_archived: bool = False) -> dict[str, object]:
    repository = ProjectRepository(get_database(request))
    return {
        "items": [
            _project_response(project)
            for project in repository.list(include_archived=include_archived)
        ],
        "next_cursor": None,
    }


@router.post("/projects", status_code=201)
def create_project(request: Request, body: CreateProjectRequest) -> dict[str, object]:
    repository = ProjectRepository(get_database(request))
    try:
        return _project_response(repository.create(body.name))
    except DuplicateProjectName as exc:
        raise _duplicate_project_name_error() from exc


@router.get("/projects/{project_id}")
def get_project(request: Request, project_id: str) -> dict[str, object]:
    repository = ProjectRepository(get_database(request))
    try:
        return _project_response(repository.get(project_id))
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc


@router.patch("/projects/{project_id}")
def update_project(
    request: Request,
    project_id: str,
    body: UpdateProjectRequest,
) -> dict[str, object]:
    repository = ProjectRepository(get_database(request))
    try:
        return _project_response(repository.rename(project_id, body.name))
    except DuplicateProjectName as exc:
        raise _duplicate_project_name_error() from exc
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc


@router.post("/projects/{project_id}/archive")
def archive_project(request: Request, project_id: str) -> dict[str, object]:
    repository = ProjectRepository(get_database(request))
    try:
        return _project_response(repository.archive(project_id))
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc


def _project_response(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "archived_at": project.archived_at,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _duplicate_project_name_error() -> ApiError:
    return ApiError(
        "PROJECT_NAME_EXISTS",
        "An active project with this name already exists.",
        status_code=409,
    )


def _project_not_found_error(project_id: str) -> ApiError:
    return ApiError(
        "PROJECT_NOT_FOUND",
        "Project was not found.",
        status_code=404,
        details=[{"project_id": project_id}],
    )
