import pytest

from test_platform.config import PlatformSettings
from test_platform.domain.projects import DuplicateProjectName
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ProjectRepository


@pytest.fixture
def repository(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        yield ProjectRepository(database)
    finally:
        database.close()


def test_repository_creates_collision_safe_slugs(repository):
    first = repository.create("Mobile App Regression")
    second = repository.create("Mobile App Regression!")

    assert first.slug == "mobile-app-regression"
    assert second.slug == "mobile-app-regression-2"


def test_repository_rejects_duplicate_active_project_names(repository):
    repository.create("Mobile App Regression")

    with pytest.raises(DuplicateProjectName):
        repository.create("  mobile app regression  ")


def test_repository_hides_archived_projects_by_default(repository):
    active = repository.create("Active Project")
    archived = repository.create("Archived Project")

    repository.archive(archived.id)

    assert [project.id for project in repository.list()] == [active.id]
    assert {project.id for project in repository.list(include_archived=True)} == {
        active.id,
        archived.id,
    }
