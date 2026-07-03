from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from bench_env.task.registry import TaskRegistry


class TaskCatalogFilter(BaseModel):
    suites: list[str] | None = None
    apps: list[str] | None = None
    difficulties: list[str] | None = None
    query: str | None = None


class TaskCatalogItem(BaseModel):
    task_base_id: str
    suite: str
    class_name: str
    apps: list[str] = Field(default_factory=list)
    templates: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    difficulty: str
    scope: str
    objective: str
    composition: str
    capabilities: list[str] = Field(default_factory=list)
    max_steps: int | None = None
    answer_fields: bool
    optimal_path_lengths: list[int] = Field(default_factory=list)


class TaskCatalogSnapshot(BaseModel):
    schema_version: int = 1
    repository_revision: str
    digest: str
    items: list[TaskCatalogItem] = Field(default_factory=list)


def build_task_catalog_snapshot(
    *,
    filters: TaskCatalogFilter | None = None,
    repository_revision: str = "unversioned",
    registry: TaskRegistry | None = None,
) -> TaskCatalogSnapshot:
    active_filters = filters or TaskCatalogFilter()
    task_registry = registry or TaskRegistry()
    suite_names = active_filters.suites or task_registry.list_suites(include_generated=False)

    items: list[TaskCatalogItem] = []
    for suite in sorted(suite_names):
        for task_name in task_registry.list_tasks(suite):
            task_cls = task_registry.get(suite, task_name)
            item = _task_item_from_class(suite, task_name, task_cls)
            if _matches_filters(item, active_filters):
                items.append(item)

    items.sort(key=lambda item: item.task_base_id)
    digest = _catalog_digest(
        schema_version=1,
        repository_revision=repository_revision,
        items=items,
    )
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision=repository_revision,
        digest=digest,
        items=items,
    )


def _task_item_from_class(suite: str, task_name: str, task_cls: type) -> TaskCatalogItem:
    optimal_paths = list(getattr(task_cls, "optimal_paths", []) or [])
    return TaskCatalogItem(
        task_base_id=f"{suite}.{task_name}",
        suite=suite,
        class_name=task_name,
        apps=list(getattr(task_cls, "apps", []) or []),
        templates=list(getattr(task_cls, "templates", []) or []),
        parameters=_json_safe(getattr(task_cls, "parameters", {}) or {}),
        difficulty=str(getattr(task_cls, "difficulty", "L1") or "L1"),
        scope=str(getattr(task_cls, "scope", "S1") or "S1"),
        objective=str(getattr(task_cls, "objective", "operate") or "operate"),
        composition=str(getattr(task_cls, "composition", "atomic") or "atomic"),
        capabilities=list(getattr(task_cls, "capabilities", []) or []),
        max_steps=getattr(task_cls, "max_steps", None),
        answer_fields=bool(getattr(task_cls, "answer_fields", None)),
        optimal_path_lengths=[len(path) for path in optimal_paths],
    )


def _matches_filters(item: TaskCatalogItem, filters: TaskCatalogFilter) -> bool:
    if filters.apps and not set(filters.apps).intersection(item.apps):
        return False
    if filters.difficulties and item.difficulty not in set(filters.difficulties):
        return False
    if filters.query:
        query = filters.query.casefold()
        haystack = " ".join(
            [
                item.task_base_id,
                item.class_name,
                item.suite,
                " ".join(item.apps),
                " ".join(item.templates),
                " ".join(item.capabilities),
            ]
        ).casefold()
        if query not in haystack:
            return False
    return True


def _catalog_digest(
    *,
    schema_version: int,
    repository_revision: str,
    items: list[TaskCatalogItem],
) -> str:
    payload = {
        "schema_version": schema_version,
        "repository_revision": repository_revision,
        "items": [item.model_dump(mode="json") for item in items],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, tuple):
        return [_json_safe(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if callable(value):
        module = getattr(value, "__module__", "")
        name = getattr(value, "__qualname__", getattr(value, "__name__", repr(value)))
        return f"{module}.{name}" if module else name
    return repr(value)
