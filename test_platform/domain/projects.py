from dataclasses import dataclass
import re
import unicodedata


class ProjectError(Exception):
    pass


class DuplicateProjectName(ProjectError):
    pass


class ProjectNotFound(ProjectError):
    pass


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    slug: str
    archived_at: str | None
    created_at: str
    updated_at: str


def make_project_slug(name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return slug or "project"


def next_available_slug(name: str, existing_slugs: set[str]) -> str:
    base = make_project_slug(name)
    if base not in existing_slugs:
        return base

    suffix = 2
    while f"{base}-{suffix}" in existing_slugs:
        suffix += 1
    return f"{base}-{suffix}"


def normalize_project_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()


def clean_project_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip())
