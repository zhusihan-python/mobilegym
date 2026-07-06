from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from bench_env.metrics import result_is_error, result_is_success


class LegacyImportError(ValueError):
    """Raised when a directory is not a supported legacy run root."""


@dataclass(frozen=True)
class LegacyEpisode:
    episode_key: str
    materialization_key: str
    pair_key: str
    task_base_id: str
    task_id: str
    task_name: str | None
    suite: str | None
    apps: list[str]
    instance_id: int
    instance_seed: int
    template_index: int | None
    trial_id: int
    max_steps: int
    result: dict[str, Any]
    outcome: str
    error_code: str | None
    artifact_root: str


@dataclass(frozen=True)
class LegacyRunImport:
    source_root: Path
    source_name: str
    meta: dict[str, Any]
    summary: dict[str, Any]
    errors: list[dict[str, Any]]
    episodes: list[LegacyEpisode]
    provenance_missing: list[str]
    started_at: str | None
    ended_at: str | None


def load_legacy_run(source_path: str | Path) -> LegacyRunImport:
    source_root = Path(source_path).expanduser().resolve()
    if not source_root.exists() or not source_root.is_dir():
        raise LegacyImportError(f"Legacy run root does not exist: {source_root}")

    results_path = source_root / "results.jsonl"
    if not results_path.is_file():
        raise LegacyImportError(f"Legacy run root must contain results.jsonl: {source_root}")

    meta = _read_json_object(source_root / "meta.json")
    summary = _read_json_object(source_root / "summary.json")
    errors = _read_jsonl(source_root / "errors.jsonl")
    results = _read_jsonl(results_path)
    if not results:
        raise LegacyImportError(f"Legacy results.jsonl is empty: {results_path}")

    repeat_n = _positive_int(meta.get("repeat_n"), default=1)
    sample_seed = _positive_int(meta.get("sample_seed"), default=0)
    episodes = [
        _episode_from_result(
            result,
            index=index,
            repeat_n=repeat_n,
            sample_seed=sample_seed,
            source_root=source_root,
        )
        for index, result in enumerate(results)
    ]
    return LegacyRunImport(
        source_root=source_root,
        source_name=source_root.name,
        meta=meta,
        summary=summary,
        errors=errors,
        episodes=episodes,
        provenance_missing=["workflow", "target_revision", "task_source"],
        started_at=_string_or_none(meta.get("start_time") or summary.get("start_time")),
        ended_at=_string_or_none(summary.get("end_time") or meta.get("end_time")),
    )


def is_legacy_run_root(path: str | Path) -> bool:
    root = Path(path)
    return (
        (root / "results.jsonl").is_file()
        or (root / "meta.json").is_file()
        or (root / "trajectory").is_dir()
    )


def _episode_from_result(
    result: dict[str, Any],
    *,
    index: int,
    repeat_n: int,
    sample_seed: int,
    source_root: Path,
) -> LegacyEpisode:
    task_id = _task_id(result)
    trial_id = _positive_int(result.get("trial_id"), default=0)
    episode_key = _string_or_none(result.get("episode_key")) or f"{task_id}|t{trial_id}"
    task_name = _string_or_none(result.get("task_name"))
    suite = _string_or_none(result.get("suite"))
    apps = [str(app) for app in result.get("apps", []) if isinstance(app, str)]
    max_steps = _positive_int(result.get("max_steps"), default=0)
    outcome = _outcome(result)
    return LegacyEpisode(
        episode_key=episode_key,
        materialization_key=task_id,
        pair_key=episode_key,
        task_base_id=task_id,
        task_id=task_id,
        task_name=task_name,
        suite=suite,
        apps=apps,
        instance_id=index + 1,
        instance_seed=sample_seed + index if sample_seed else index + 1,
        template_index=None,
        trial_id=trial_id,
        max_steps=max_steps,
        result=result,
        outcome=outcome,
        error_code=_error_code_for_outcome(outcome),
        artifact_root=_artifact_root(source_root, task_id, trial_id, repeat_n),
    )


def _artifact_root(source_root: Path, task_id: str, trial_id: int, repeat_n: int) -> str:
    task_id_safe = task_id.replace(".", "_").replace("/", "_").replace(" ", "_")
    candidates = []
    if repeat_n > 1:
        candidates.append(f"trajectory/{task_id_safe}_t{trial_id}")
    candidates.append(f"trajectory/{task_id_safe}")
    if repeat_n <= 1:
        candidates.append(f"trajectory/{task_id_safe}_t{trial_id}")
    for candidate in candidates:
        if (source_root / candidate).is_dir():
            return candidate
    return candidates[0]


def _outcome(result: dict[str, Any]) -> str:
    if result_is_error(result):
        return "ERROR"
    if result_is_success(result):
        return "PASS"
    return "FAIL"


def _error_code_for_outcome(outcome: str) -> str | None:
    if outcome == "ERROR":
        return "EXECUTION_ERROR"
    if outcome == "FAIL":
        return "ASSERTION_FAILURE"
    return None


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LegacyImportError(f"{path.name} must contain a JSON object.")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        value = json.loads(stripped)
        if not isinstance(value, dict):
            raise LegacyImportError(f"{path.name}:{line_number} must contain a JSON object.")
        rows.append(value)
    return rows


def _task_id(result: dict[str, Any]) -> str:
    task_id = result.get("id") or result.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        return task_id
    raise LegacyImportError("Every legacy result row requires an id or task_id.")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default
