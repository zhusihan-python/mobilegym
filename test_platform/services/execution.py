from __future__ import annotations

import inspect
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from bench_env.runner.cancellation import CancellationToken, RunCancelled
from bench_env.runner.events import EventSink, NullEventSink
from bench_env.runner.base import Controller
from bench_env.metrics import result_is_error, result_is_success
from bench_env.config import RunnerConfig
from bench_env.env.recorder import RunRecorder
from bench_env.runner.serial import PreparedWorkItem, SerialRunner
from bench_env.task.registry import TaskRegistry

from test_platform.config import PlatformSettings
from test_platform.domain.canonical_json import canonical_json, canonical_sha256
from test_platform.domain.ids import new_id
from test_platform.domain.run_plans import EpisodeTemplate, PlannedLane, RunPlan
from test_platform.domain.runs import RunDetail, RunDomainError
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import RunRepository


@dataclass(frozen=True)
class PreparedTaskInstance:
    schema_version: int
    materialization_key: str
    task_base_id: str
    instance_id: int
    instance_seed: int
    template_index: int | None
    params: dict[str, Any]
    instruction: str
    source_target_revision_id: str
    initial_state_relative_path: str
    initial_state_hash: str
    projection_hash: str
    data_revision: str | None
    scenario_hash: str
    fingerprint: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class TaskFactory(Protocol):
    def instantiate(
        self,
        template: EpisodeTemplate,
        params: dict[str, Any] | None = None,
    ) -> Any:
        ...


class RegistryTaskFactory:
    def __init__(self, registry: TaskRegistry | None = None) -> None:
        self.registry = registry or TaskRegistry()

    def instantiate(
        self,
        template: EpisodeTemplate,
        params: dict[str, Any] | None = None,
    ) -> Any:
        task_cls = self.registry.get_by_id(template.task_base_id)
        task = task_cls(_seed=template.instance_seed, **(params or {}))
        task._instance_id = template.instance_id
        task._template_index = template.template_index
        return task


class SingleLaneMaterializer:
    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        task_factory: TaskFactory | None = None,
        env_factory: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.task_factory = task_factory or RegistryTaskFactory()
        self.env_factory = env_factory

    async def materialize_run(
        self,
        run_id: str,
        *,
        token: CancellationToken | None = None,
        events: EventSink | None = None,
    ) -> list[PreparedTaskInstance]:
        token = token or CancellationToken()
        # events is accepted for future lifecycle emission; VS-06 does not yet
        # emit materialize.* events, but the sink is plumbed so VS-07 can.
        _ = events or NullEventSink()

        plan = self._load_plan(run_id)
        lane = self._source_lane(plan)
        templates = self._unique_templates(plan.episodes)
        prepared: list[PreparedTaskInstance] = []

        for template in templates:
            token.raise_if_cancelled()
            existing = self._existing_prepared(run_id, template.materialization_key)
            if existing is not None:
                prepared.append(existing)
                continue
            prepared.append(await self._materialize_one(run_id, lane, template, token))

        return prepared

    async def _materialize_one(
        self,
        run_id: str,
        lane: PlannedLane,
        template: EpisodeTemplate,
        token: CancellationToken,
    ) -> PreparedTaskInstance:
        if self.env_factory is None:
            raise RunDomainError(
                "ENV_FACTORY_MISSING",
                "Serial materialization requires an environment factory.",
                status_code=500,
            )

        env = self.env_factory(lane)
        task = self.task_factory.instantiate(template)
        try:
            token.raise_if_cancelled()
            eval_mode = str(lane.runner_config.get("eval_mode", "grounded"))
            _initial_obs, params = await Controller.setup(env, task, eval_mode=eval_mode)
            instruction = str(task.description)
            state = await env.get_state(required_apps=None)
            relative_path = self._write_initial_state(run_id, template.materialization_key, state)
            initial_state_hash = canonical_sha256(state)
            projection_hash = initial_state_hash
            data_revision = self._data_revision(lane.target_revision_id)
            scenario_hash = canonical_sha256(
                {
                    "data_revision": data_revision,
                    "projection_hash": projection_hash,
                    "target_revision_id": lane.target_revision_id,
                }
            )
            payload_without_fingerprint = {
                "schema_version": 1,
                "materialization_key": template.materialization_key,
                "task_base_id": template.task_base_id,
                "instance_id": template.instance_id,
                "instance_seed": template.instance_seed,
                "template_index": template.template_index,
                "params": params,
                "instruction": instruction,
                "source_target_revision_id": lane.target_revision_id,
                "initial_state_relative_path": relative_path,
                "initial_state_hash": initial_state_hash,
                "projection_hash": projection_hash,
                "data_revision": data_revision,
                "scenario_hash": scenario_hash,
            }
            fingerprint = canonical_sha256(payload_without_fingerprint)
            prepared = PreparedTaskInstance(
                **payload_without_fingerprint,
                fingerprint=fingerprint,
            )
            self._persist_prepared(run_id, prepared)
            return prepared
        finally:
            await _maybe_await(task.teardown(env))
            close = getattr(env, "close", None)
            if callable(close):
                await _maybe_await(close())

    def _load_plan(self, run_id: str) -> RunPlan:
        row = self.database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunDomainError("RUN_NOT_FOUND", "Run was not found.", status_code=404)
        return RunPlan.model_validate(json.loads(row["run_plan_json"]))

    def _source_lane(self, plan: RunPlan) -> PlannedLane:
        if len(plan.lanes) != 1:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "VS-05 supports exactly one run lane.",
                status_code=409,
            )
        return plan.lanes[0]

    def _unique_templates(self, episodes: list[EpisodeTemplate]) -> list[EpisodeTemplate]:
        by_key: dict[str, EpisodeTemplate] = {}
        for episode in episodes:
            by_key.setdefault(episode.materialization_key, episode)
        return [by_key[key] for key in sorted(by_key)]

    def _existing_prepared(
        self,
        run_id: str,
        materialization_key: str,
    ) -> PreparedTaskInstance | None:
        row = self.database.connection.execute(
            """
            SELECT payload_json
            FROM prepared_tasks
            WHERE run_id = ? AND materialization_key = ?
            """,
            (run_id, materialization_key),
        ).fetchone()
        if row is None:
            return None
        return PreparedTaskInstance(**json.loads(row["payload_json"]))

    def _write_initial_state(
        self,
        run_id: str,
        materialization_key: str,
        state: dict[str, Any],
    ) -> str:
        relative_path = Path("platform") / "initial-states" / f"{_safe_name(materialization_key)}.json"
        absolute_path = self.settings.runs_dir / run_id / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = absolute_path.with_suffix(".json.tmp")
        temporary_path.write_text(f"{canonical_json(state)}\n", encoding="utf-8")
        temporary_path.replace(absolute_path)
        return relative_path.as_posix()

    def _data_revision(self, target_revision_id: str) -> str | None:
        row = self.database.connection.execute(
            "SELECT metadata_json FROM target_revisions WHERE id = ?",
            (target_revision_id,),
        ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"])
        data = metadata.get("data")
        if isinstance(data, dict):
            revision = data.get("revision")
            return str(revision) if revision is not None else None
        return None

    def _persist_prepared(self, run_id: str, prepared: PreparedTaskInstance) -> None:
        connection = self.database.connection
        prepared_id = new_id()
        now = _utc_timestamp()
        payload_json = canonical_json(prepared.to_payload())
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO prepared_tasks (
                      id, run_id, materialization_key, payload_json, payload_hash, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prepared_id,
                        run_id,
                        prepared.materialization_key,
                        payload_json,
                        prepared.fingerprint,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE episodes
                    SET prepared_task_id = ?
                    WHERE run_id = ? AND materialization_key = ?
                    """,
                    (prepared_id, run_id, prepared.materialization_key),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


class ResultIngestor:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ingest_episode_result(
        self,
        *,
        run_id: str,
        lane_attempt_id: str,
        episode_key: str,
        result: Any,
        artifact_root: str,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        connection = self.database.connection
        episode_row = connection.execute(
            """
            SELECT id
            FROM episodes
            WHERE run_id = ? AND episode_key = ?
            """,
            (run_id, episode_key),
        ).fetchone()
        if episode_row is None:
            raise RunDomainError(
                "EPISODE_RESULT_UNKNOWN",
                "The runner returned a result for an unknown episode.",
                status_code=500,
                details=[{"run_id": run_id, "episode_key": episode_key}],
            )

        lane_row = connection.execute(
            """
            SELECT run_attempt_id
            FROM lane_attempts
            WHERE id = ?
            """,
            (lane_attempt_id,),
        ).fetchone()
        if lane_row is None:
            raise RunDomainError(
                "LANE_ATTEMPT_NOT_FOUND",
                "Lane attempt was not found.",
                status_code=500,
                details=[{"lane_attempt_id": lane_attempt_id}],
            )

        normalized = _result_to_dict(result)
        outcome = _result_outcome(result, cancelled=cancelled)
        error_code = _error_code_for_outcome(outcome)
        terminal_state = "cancelled" if cancelled else "completed"
        now = _utc_timestamp()
        attempt_id = new_id()

        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            attempt_no = self._next_attempt_no(str(episode_row["id"]), lane_attempt_id)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO episode_attempts (
                      id, episode_id, lane_attempt_id, attempt_no, state,
                      outcome, error_code, result_json, artifact_root,
                      started_at, ended_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        episode_row["id"],
                        lane_attempt_id,
                        attempt_no,
                        terminal_state,
                        outcome,
                        error_code,
                        canonical_json(normalized),
                        artifact_root,
                        now,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE lane_attempts
                    SET state = ?,
                        started_at = COALESCE(started_at, ?),
                        ended_at = ?
                    WHERE id = ?
                    """,
                    (terminal_state, now, now, lane_attempt_id),
                )
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = ?,
                        started_at = COALESCE(started_at, ?),
                        ended_at = ?
                    WHERE id = ?
                    """,
                    (terminal_state, now, now, lane_row["run_attempt_id"]),
                )
                connection.execute(
                    """
                    UPDATE runs
                    SET state = ?,
                        started_at = COALESCE(started_at, ?),
                        ended_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (terminal_state, now, now, now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return {
            "id": attempt_id,
            "episode_id": episode_row["id"],
            "lane_attempt_id": lane_attempt_id,
            "attempt_no": attempt_no,
            "state": terminal_state,
            "outcome": outcome,
            "error_code": error_code,
            "artifact_root": artifact_root,
        }

    def _next_attempt_no(self, episode_id: str, lane_attempt_id: str) -> int:
        row = self.database.connection.execute(
            """
            SELECT COALESCE(MAX(attempt_no), 0) + 1
            FROM episode_attempts
            WHERE episode_id = ? AND lane_attempt_id = ?
            """,
            (episode_id, lane_attempt_id),
        ).fetchone()
        return int(row[0])

    def mark_run_cancelled(self, run_id: str) -> None:
        """Transition run/attempt/lane states to 'cancelled'.

        Called by the executor when the run was cancelled (token set) and the
        normal ingest path did not complete. Idempotent: rows already in a
        terminal state are left untouched.
        """
        now = _utc_timestamp()
        connection = self.database.connection
        cancellable = "('queued','preparing','running','evaluating','reporting')"
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    f"""
                    UPDATE runs
                    SET state = 'cancelled', ended_at = COALESCE(ended_at, ?), updated_at = ?
                    WHERE id = ? AND state IN {cancellable}
                    """,
                    (now, now, run_id),
                )
                connection.execute(
                    f"""
                    UPDATE run_attempts
                    SET state = 'cancelled', ended_at = COALESCE(ended_at, ?)
                    WHERE run_id = ? AND state IN {cancellable}
                    """,
                    (now, run_id),
                )
                connection.execute(
                    f"""
                    UPDATE lane_attempts
                    SET state = 'cancelled', ended_at = COALESCE(ended_at, ?)
                    WHERE lane_id IN (SELECT id FROM lanes WHERE run_id = ?)
                      AND state IN {cancellable}
                    """,
                    (now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


class SerialRunExecutor:
    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        task_factory: TaskFactory | None = None,
        env_factory: Any | None = None,
        agent_factory: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.task_factory = task_factory or RegistryTaskFactory()
        self.env_factory = env_factory
        self.agent_factory = agent_factory

    async def execute_run(
        self,
        run_id: str,
        *,
        token: CancellationToken | None = None,
        events: EventSink | None = None,
        run_event_writer: Any = None,
    ) -> RunDetail:
        token = token or CancellationToken()
        events = events or NullEventSink()
        writer = run_event_writer

        # Honor a cancel that landed while the run was queued (before submit()
        # registered its token). The supervisor sets cancel_requested_at
        # atomically; if it is already set, the run is cancelled before any
        # execution side effect — including before run.started is emitted, so a
        # pre-execution cancel produces a clean cancelled stream without a
        # misleading "started" event.
        if self._cancel_requested(run_id):
            token.cancel()
            raise RunCancelled()

        # Cancel check passed: mark running and emit run.started here (not in
        # the supervisor) so the event never precedes a queued cancellation.
        self._mark_run_running(run_id)
        if writer is not None:
            try:
                writer.emit(
                    run_id, "run.started", {"state": "running"},
                    entity_type="run", entity_id=run_id,
                )
            except Exception:  # noqa: BLE001
                pass
        prepared = await SingleLaneMaterializer(
            self.database,
            self.settings,
            task_factory=self.task_factory,
            env_factory=self.env_factory,
        ).materialize_run(run_id, token=token, events=events)
        if len(prepared) != 1:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "VS-05 supports exactly one prepared task.",
                status_code=409,
            )

        plan = self._load_plan(run_id)
        lane = _single_lane(plan)
        lane_attempt = self._lane_attempt(run_id)
        prepared_by_key = {item.materialization_key: item for item in prepared}
        work_items: list[PreparedWorkItem] = []
        for episode in plan.episodes:
            payload = prepared_by_key[episode.materialization_key]
            task = self.task_factory.instantiate(episode, params=payload.params)
            work_items.append(
                PreparedWorkItem(
                    episode_key=episode.episode_key,
                    task=task,
                    trial_id=episode.trial_id,
                    max_steps=episode.max_steps,
                )
            )
        if len(work_items) != 1:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "VS-05 supports exactly one serial episode.",
                status_code=409,
            )

        if self.env_factory is None or self.agent_factory is None:
            raise RunDomainError(
                "EXECUTOR_FACTORY_MISSING",
                "Serial execution requires environment and agent factories.",
                status_code=500,
            )
        env = self.env_factory(lane)
        agent = self.agent_factory(lane)
        lane_root = self.settings.runs_dir / run_id / lane_attempt["artifact_root"]
        config = _runner_config(lane)
        recorder = RunRecorder(
            self.settings.runs_dir,
            save_trajectory=not bool(config.no_save_trajectory),
            coord_space=config.coord_space,
            screenshot_scale=config.screenshot_scale,
            fixed_run_dir=lane_root,
        )
        recorder.start_run(
            agent=getattr(agent, "name", config.agent),
            model_name=config.model_name,
            extra_meta={"platform_run_id": run_id, "lane_key": lane.lane_key},
            repeat_n=config.repeat_n,
        )

        try:
            results = await SerialRunner(
                env,
                agent,
                [item.task for item in work_items],
                config,
                recorder=recorder,
                prepared_work_items=work_items,
                event_sink=events,
                cancellation_token=token,
            ).run()
        except RunCancelled:
            token.cancel()
            results = []

        ingestor = ResultIngestor(self.database)
        # P2.2: cancellation is driven solely by the token, NOT by an empty
        # results list — an exception that left results empty before the first
        # episode would otherwise be misclassified as a cancellation.
        # If a result itself is CANCELLED (the runner caught RunCancelled and
        # returned a CANCELLED ExecutionResult rather than re-raising), propagate
        # that as a run-level cancellation.
        any_result_cancelled = any(
            getattr(getattr(r, "execution", None), "stop_reason", None) == "CANCELLED"
            for r in results
        )
        if any_result_cancelled:
            token.cancel()
        cancelled = token.cancelled
        for work_item, result in zip(work_items, results, strict=True):
            artifact_root = _episode_artifact_root(
                lane_attempt["artifact_root"],
                result,
                repeat_n=config.repeat_n,
            )
            ingestor.ingest_episode_result(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                episode_key=work_item.episode_key,
                result=result,
                artifact_root=artifact_root,
                cancelled=token.cancelled,
            )

        if cancelled:
            ingestor.mark_run_cancelled(run_id)
            # Re-raise so the supervisor emits run.cancelled (not run.completed).
            # The run/lane state is already terminal here; the supervisor's
            # _mark_run_cancelled is idempotent.
            raise RunCancelled()
        # NOTE: run.* terminal events (run.completed / run.cancelled) are owned
        # by the supervisor's _execute wrapper to avoid duplicate emits. The
        # executor only raises RunCancelled; the supervisor finalizes + emits.
        return RunRepository(self.database).get(run_id)

    def _load_plan(self, run_id: str) -> RunPlan:
        row = self.database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunDomainError("RUN_NOT_FOUND", "Run was not found.", status_code=404)
        return RunPlan.model_validate(json.loads(row["run_plan_json"]))

    def _lane_attempt(self, run_id: str) -> dict[str, str]:
        row = self.database.connection.execute(
            """
            SELECT la.id, la.artifact_root
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE l.run_id = ?
            ORDER BY l.lane_key
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunDomainError("LANE_ATTEMPT_NOT_FOUND", "Lane attempt was not found.", status_code=500)
        return {"id": str(row["id"]), "artifact_root": str(row["artifact_root"])}

    def _cancel_requested(self, run_id: str) -> bool:
        row = self.database.connection.execute(
            "SELECT cancel_requested_at FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return row is not None and row["cancel_requested_at"] is not None

    def _mark_run_running(self, run_id: str) -> None:
        now = _utc_timestamp()
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'running',
                        started_at = COALESCE(started_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, run_id),
                )
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = 'running',
                        started_at = COALESCE(started_at, ?)
                    WHERE run_id = ?
                    """,
                    (now, run_id),
                )
                connection.execute(
                    """
                    UPDATE lane_attempts
                    SET state = 'running',
                        started_at = COALESCE(started_at, ?)
                    WHERE lane_id IN (SELECT id FROM lanes WHERE run_id = ?)
                    """,
                    (now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or canonical_sha256(value).split(":", 1)[1]


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _single_lane(plan: RunPlan) -> PlannedLane:
    if len(plan.lanes) != 1:
        raise RunDomainError(
            "UNSUPPORTED_RUN_PLAN",
            "VS-05 supports exactly one run lane.",
            status_code=409,
        )
    return plan.lanes[0]


def _runner_config(lane: PlannedLane) -> RunnerConfig:
    runner_config = dict(lane.runner_config)
    if isinstance(runner_config.get("physical_size"), list):
        runner_config["physical_size"] = tuple(runner_config["physical_size"])
    runner_config.setdefault("agent", "platform")
    runner_config.setdefault("model_name", "platform")
    runner_config.setdefault("quiet", True)
    runner_config.setdefault("runs_dir", Path("runs"))
    runner_config["repeat_n"] = 1
    return RunnerConfig(**{
        key: value
        for key, value in runner_config.items()
        if key in RunnerConfig.__dataclass_fields__
    })


def _episode_artifact_root(
    lane_artifact_root: str,
    result: Any,
    *,
    repeat_n: int,
) -> str:
    result_dict = _result_to_dict(result)
    task_id = str(result_dict.get("id") or getattr(result, "task_id", "episode"))
    task_id_safe = task_id.replace(".", "_").replace("/", "_").replace(" ", "_")
    trial_id = int(result_dict.get("trial_id") or getattr(result, "trial_id", 0) or 0)
    suffix = f"_t{trial_id}" if repeat_n > 1 else ""
    return f"{lane_artifact_root}/trajectory/{task_id_safe}{suffix}"


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted
    return {
        "id": getattr(result, "task_id", None),
        "trial_id": getattr(result, "trial_id", 0),
        "is_success": result_is_success(result),
        "is_error": result_is_error(result),
    }


def _result_outcome(result: Any, *, cancelled: bool = False) -> str:
    if cancelled:
        return "CANCELLED"
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
    if outcome == "CANCELLED":
        return "CANCELLED"
    return None
