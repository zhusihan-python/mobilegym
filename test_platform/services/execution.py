from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import asdict, dataclass, replace as dataclasses_replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from bench_env.runner.cancellation import CancellationToken, RunCancelled
from bench_env.runner.events import EventSink, ExecutionEvent, NullEventSink
from bench_env.runner.base import Controller
from bench_env.metrics import result_is_error, result_is_success
from bench_env.config import RunnerConfig
from bench_env.env.recorder import RunRecorder
from bench_env.runner.parallel import ParallelRunner
from bench_env.runner.serial import PreparedWorkItem, SerialRunner
from bench_env.task.registry import TaskRegistry

from test_platform.config import PlatformSettings
from test_platform.domain.canonical_json import canonical_json, canonical_sha256
from test_platform.domain.ids import new_id
from test_platform.domain.pair_classification import (
    IntegrityStatus,
    PairClassification,
    classify_pair,
)
from test_platform.domain.run_plans import EpisodeTemplate, PlannedLane, RunPlan
from test_platform.domain.runs import RunDetail, RunDomainError
from test_platform.domain.state_projection import projection_hash_v1
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
            projection_hash = self._projection_hash(state)
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

    def _projection_hash(self, state: dict[str, Any]) -> str:
        """Projection-hash hook. Single-lane uses the raw state hash (VS-05
        behavior). PairedMaterializer overrides to use projection_hash_v1."""
        return canonical_sha256(state)

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


class PairedMaterializer(SingleLaneMaterializer):
    """Materialize ONE PreparedTaskInstance shared by baseline + candidate lanes.

    VS-09: both lanes of a 2-lane run reuse a single prepared task (frozen
    params + instruction). The materializer samples ONCE from the source lane
    (resolved from ``plan.materialization['source_lane_id']`` by matching
    ``PlannedLane.lane_id``), persists a single prepared_tasks row, and links
    BOTH lanes' episodes to it. The projection hash uses ``projection_hash_v1``
    semantics (volatile-stripped) so baseline/candidate states are comparable.
    """

    def _source_lane(self, plan: RunPlan) -> PlannedLane:
        if len(plan.lanes) != 2:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "VS-09 paired materialization requires exactly two run lanes.",
                status_code=409,
            )
        source_lane_id = plan.materialization.get("source_lane_id")
        if isinstance(source_lane_id, str):
            for lane in plan.lanes:
                if lane.lane_id == source_lane_id:
                    return lane
            # Fallback: match by lane_key for old plans (Contract 11).
            for lane in plan.lanes:
                if lane.lane_key == source_lane_id:
                    return lane
        # Fallback: first lane by lane_key order (compiler default).
        return plan.lanes[0]

    def _projection_hash(self, state: dict[str, Any]) -> str:
        # VS-09: use projection_hash_v1 (volatile-stripped) so baseline/candidate
        # states with different clocks/active-apps still compare equal.
        return projection_hash_v1(state)


class ResultIngestor:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ingest_episode_attempt(
        self,
        *,
        run_id: str,
        lane_attempt_id: str,
        episode_key: str,
        result: Any,
        artifact_root: str,
        cancelled: bool = False,
        error_code_override: str | None = None,
    ) -> dict[str, Any]:
        """Insert ONE episode_attempts row. Does NOT touch lane/run/run_attempt state.

        For parallel execution many episodes share a lane; finalizing the lane
        per-episode would complete the whole run on the first episode. The
        parallel executor calls this once per result, then ``finalize_lane_run``
        exactly once at the end.

        ``error_code_override`` (when provided) replaces the derived error_code
        — used by reconciliation to label missing/crashed episodes (e.g.
        WORKER_CRASH) without altering the outcome derivation.
        """
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

        # The lane_attempt must exist, but its state is left untouched here.
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
        error_code = (
            error_code_override
            if error_code_override is not None
            else _error_code_for_outcome(outcome)
        )
        # The episode_attempt's own state is terminal (it represents one finished
        # attempt); the lane/run state is finalized separately.
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
            "run_attempt_id": str(lane_row["run_attempt_id"]),
        }

    def finalize_lane_run(
        self,
        *,
        run_id: str,
        lane_attempt_id: str,
        terminal_state: str,
        cancelled: bool = False,
    ) -> None:
        """Transition lane_attempts/run_attempts/runs to ``terminal_state``.

        Idempotent: only rows still in cancellable states are updated, so a
        finalize after a cancel (or a repeated finalize) is a safe no-op on rows
        already terminal. ``cancelled`` is kept for API symmetry with
        ``ingest_episode_attempt``; the ``terminal_state`` argument is the
        authoritative value.

        VS-09 backward-compat wrapper: this now delegates to
        ``finalize_lane_only`` (lane) + ``finalize_run`` (run_attempt + run) so
        the paired executor can finalize lanes independently before the run.
        Single-lane executors that call this get identical behaviour.
        """
        _ = cancelled  # accepted for symmetry; terminal_state is authoritative
        self.finalize_lane_only(
            lane_attempt_id=lane_attempt_id, terminal_state=terminal_state
        )
        self.finalize_run(
            run_id=run_id, terminal_state=terminal_state, cancelled=cancelled
        )

    def finalize_lane_only(
        self,
        *,
        lane_attempt_id: str,
        terminal_state: str,
    ) -> None:
        """Finalize ONLY the lane_attempt row (not the run_attempt or run).

        VS-09 paired execution finalizes each lane independently after its
        episodes are ingested, then finalizes the run once after the comparison
        is recorded. Idempotent on terminal rows.
        """
        connection = self.database.connection
        lane_row = connection.execute(
            "SELECT run_attempt_id FROM lane_attempts WHERE id = ?",
            (lane_attempt_id,),
        ).fetchone()
        if lane_row is None:
            raise RunDomainError(
                "LANE_ATTEMPT_NOT_FOUND",
                "Lane attempt was not found.",
                status_code=500,
                details=[{"lane_attempt_id": lane_attempt_id}],
            )
        now = _utc_timestamp()
        cancellable = "('queued','preparing','running','evaluating','reporting')"
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    f"""
                    UPDATE lane_attempts
                    SET state = ?,
                        started_at = COALESCE(started_at, ?),
                        ended_at = COALESCE(ended_at, ?)
                    WHERE id = ? AND state IN {cancellable}
                    """,
                    (terminal_state, now, now, lane_attempt_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def finalize_run(
        self,
        *,
        run_id: str,
        terminal_state: str,
        cancelled: bool = False,
    ) -> None:
        """Finalize ONLY the run_attempt + run rows (not lane_attempts).

        VS-09 paired execution calls this once after both lanes are finalized
        and the comparison is recorded. Idempotent on terminal rows.
        """
        _ = cancelled  # accepted for symmetry; terminal_state is authoritative
        connection = self.database.connection
        now = _utc_timestamp()
        cancellable = "('queued','preparing','running','evaluating','reporting')"
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    f"""
                    UPDATE run_attempts
                    SET state = ?,
                        started_at = COALESCE(started_at, ?),
                        ended_at = COALESCE(ended_at, ?)
                    WHERE run_id = ? AND state IN {cancellable}
                    """,
                    (terminal_state, now, now, run_id),
                )
                connection.execute(
                    f"""
                    UPDATE runs
                    SET state = ?,
                        started_at = COALESCE(started_at, ?),
                        ended_at = COALESCE(ended_at, ?),
                        updated_at = ?
                    WHERE id = ? AND state IN {cancellable}
                    """,
                    (terminal_state, now, now, now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

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
        """Backward-compatible wrapper: insert the episode attempt, then finalize.

        Serial execution has exactly one episode per lane, so combining the two
        is correct there. Parallel execution MUST call the two methods explicitly
        (insert per episode, finalize once at the end).
        """
        summary = self.ingest_episode_attempt(
            run_id=run_id,
            lane_attempt_id=lane_attempt_id,
            episode_key=episode_key,
            result=result,
            artifact_root=artifact_root,
            cancelled=cancelled,
        )
        self.finalize_lane_run(
            run_id=run_id,
            lane_attempt_id=lane_attempt_id,
            terminal_state="cancelled" if cancelled else "completed",
            cancelled=cancelled,
        )
        return summary

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


class _RunExecutorBase:
    """Shared run-lifecycle helpers used by both Serial and Parallel executors.

    These methods read/write only ``self.database`` (no execution-specific
    state), so factoring them out avoids duplication while keeping each
    executor's ``execute_run`` self-contained.
    """

    database: Database

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


class SerialRunExecutor(_RunExecutorBase):
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
                episode_key=work_item.episode_key,
            )
            ingestor.ingest_episode_attempt(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                episode_key=work_item.episode_key,
                result=result,
                artifact_root=artifact_root,
                cancelled=token.cancelled,
            )
        # Serial execution has exactly one episode per lane, so finalizing once
        # after the single ingest is correct. (Parallel execution finalizes once
        # after ALL episodes are ingested.)
        ingestor.finalize_lane_run(
            run_id=run_id,
            lane_attempt_id=lane_attempt["id"],
            terminal_state="cancelled" if cancelled else "completed",
            cancelled=cancelled,
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


class ParallelRunExecutor(_RunExecutorBase):
    """Execute all episodes of a single lane concurrently with N workers.

    Mirrors SerialRunExecutor's lifecycle (queued-cancel check → mark running →
    emit run.started → materialize → run → ingest → finalize) but runs many
    episodes in parallel and reconciles results by episode_key so plan order is
    preserved regardless of completion order.

    The env_pool_factory(lane) -> EnvPool and agent_factory(lane) -> agent
    patterns are the parallel analogues of SerialRunExecutor's env/agent
    factories. For tests, inject fakes.
    """

    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        task_factory: TaskFactory | None = None,
        env_pool_factory: Any | None = None,
        agent_factory: Any | None = None,
        env_factory: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.task_factory = task_factory or RegistryTaskFactory()
        self.env_pool_factory = env_pool_factory
        # env_factory is used ONLY for the materialization phase (sampling one
        # env's state). If not provided, fall back to pulling a single env out
        # of the pool factory — but tests typically inject a dedicated
        # materialization env (mirroring SerialRunExecutor's iterator pattern).
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

        # (1) Honor a cancel that landed while the run was queued: raise BEFORE
        # emitting run.started so a pre-execution cancel produces a clean
        # cancelled stream with no misleading "started" event.
        if self._cancel_requested(run_id):
            token.cancel()
            raise RunCancelled()

        # (2) Mark running + emit run.started here (not in the supervisor) so the
        # event never precedes a queued cancellation. Terminal run.* events stay
        # owned by the supervisor.
        self._mark_run_running(run_id)
        if writer is not None:
            try:
                writer.emit(
                    run_id, "run.started", {"state": "running"},
                    entity_type="run", entity_id=run_id,
                )
            except Exception:  # noqa: BLE001
                pass

        # (3) Materialize the (single) source lane's unique templates. The same
        # SingleLaneMaterializer is reused; it produces one PreparedTaskInstance
        # per unique materialization_key regardless of trial count. Materialize
        # uses a single env (env_factory); execution uses the pool below.
        prepared = await SingleLaneMaterializer(
            self.database,
            self.settings,
            task_factory=self.task_factory,
            env_factory=self.env_factory,
        ).materialize_run(run_id, token=token, events=events)
        if not prepared:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "Parallel execution requires at least one prepared task.",
                status_code=409,
            )

        plan = self._load_plan(run_id)
        lane = _single_lane(plan)
        lane_attempt = self._lane_attempt(run_id)
        prepared_by_key = {item.materialization_key: item for item in prepared}

        # (4) Build work_items for EVERY planned episode (not just one). Each
        # trial of a repeated task becomes its own work item with a distinct
        # episode_key, so workers can run them concurrently.
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
        if not work_items:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "Parallel execution requires at least one episode.",
                status_code=409,
            )

        if self.env_pool_factory is None or self.agent_factory is None:
            raise RunDomainError(
                "EXECUTOR_FACTORY_MISSING",
                "Parallel execution requires environment pool and agent factories.",
                status_code=500,
            )
        env_pool = self.env_pool_factory(lane)
        config = _runner_config(lane)
        lane_root = self.settings.runs_dir / run_id / lane_attempt["artifact_root"]
        recorder = RunRecorder(
            self.settings.runs_dir,
            save_trajectory=not bool(config.no_save_trajectory),
            coord_space=config.coord_space,
            screenshot_scale=config.screenshot_scale,
            fixed_run_dir=lane_root,
        )
        recorder.start_run(
            agent=getattr(self.agent_factory(lane), "name", config.agent),
            model_name=config.model_name,
            extra_meta={"platform_run_id": run_id, "lane_key": lane.lane_key},
            repeat_n=len(work_items),
        )

        # (5/6) Run all episodes concurrently. ParallelRunner preserves plan
        # order in its returned list and emits worker.started/stopped + episode
        # events through ``events``.
        try:
            results = await ParallelRunner(
                env_pool,
                lambda: self.agent_factory(lane),
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

        # If any single result was CANCELLED, propagate as a run-level cancel.
        any_result_cancelled = any(
            getattr(getattr(r, "execution", None), "stop_reason", None) == "CANCELLED"
            for r in results
        )
        if any_result_cancelled:
            token.cancel()

        # (7) Reconcile results against the expected episode_keys (from
        # work_items). Missing → synthetic ERROR + WORKER_CRASH; unknown /
        # duplicate → hard error (never silently drop or double-count).
        self._reconcile_and_ingest(
            run_id=run_id,
            lane_attempt=lane_attempt,
            work_items=work_items,
            results=results,
            cancelled=token.cancelled,
            event_sink=events,
        )

        # (10) If cancelled, mark the run cancelled and re-raise so the
        # supervisor emits run.cancelled.
        if token.cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

    def _reconcile_and_ingest(
        self,
        *,
        run_id: str,
        lane_attempt: dict[str, str],
        work_items: list[PreparedWorkItem],
        results: list[Any],
        cancelled: bool,
        event_sink: Any = None,
    ) -> None:
        """Reconcile runner results against expected episode_keys and ingest.

        - MISSING expected key → synthetic ERROR result ingested with
          ``error_code_override='WORKER_CRASH'``.
        - UNKNOWN key (in results, not expected) → RunDomainError.
        - DUPLICATE key (two results, same key) → RunDomainError.
        Then ingest every episode (in plan order) and finalize the lane once.
        """
        expected_keys = [item.episode_key for item in work_items]
        expected_set = set(expected_keys)

        # Index results by episode_key, detecting duplicates.
        results_by_key: dict[str, Any] = {}
        unknown_keys: list[str] = []
        for result in results:
            key = getattr(result, "episode_key", None)
            if key is None:
                # A result without episode_key is unrecoverable for parallel
                # attribution — treat as unknown.
                unknown_keys.append("<missing episode_key>")
                continue
            if key not in expected_set:
                unknown_keys.append(key)
                continue
            if key in results_by_key:
                raise RunDomainError(
                    "EPISODE_RESULT_UNKNOWN",
                    "The runner returned duplicate results for one episode.",
                    status_code=500,
                    details=[{"run_id": run_id, "episode_key": key, "duplicate": True}],
                )
            results_by_key[key] = result
        if unknown_keys:
            raise RunDomainError(
                "EPISODE_RESULT_UNKNOWN",
                "The runner returned results for unknown episodes.",
                status_code=500,
                details=[
                    {"run_id": run_id, "unknown_episode_keys": unknown_keys}
                ],
            )

        ingestor = ResultIngestor(self.database)
        # Ingest in PLAN order so episode_attempts are deterministic.
        for work_item in work_items:
            key = work_item.episode_key
            artifact_root = _episode_artifact_root(
                lane_attempt["artifact_root"],
                results_by_key.get(key),
                repeat_n=1,
                episode_key=key,
            )
            if key in results_by_key:
                ingestor.ingest_episode_attempt(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt["id"],
                    episode_key=key,
                    result=results_by_key[key],
                    artifact_root=artifact_root,
                    cancelled=cancelled,
                )
            else:
                # Missing result → the worker crashed/exited before reporting,
                # OR the run was cancelled before this episode ran. The label
                # MUST match the cause (ParallelRunExecutor mirrors
                # MultiprocessRunExecutor here): a user cancel is CANCELLED
                # (not a crash). error_code + terminal episode event follow suit.
                synthetic = self._synthetic_crash_result(work_item, cancelled=cancelled)
                if cancelled:
                    missing_code = "CANCELLED"
                    terminal_event_type = "episode.cancelled"
                    terminal_outcome = "CANCELLED"
                else:
                    missing_code = "WORKER_CRASH"
                    terminal_event_type = "episode.error"
                    terminal_outcome = "ERROR"
                ingestor.ingest_episode_attempt(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt["id"],
                    episode_key=key,
                    result=synthetic,
                    artifact_root=artifact_root,
                    cancelled=cancelled,
                    error_code_override=missing_code,
                )
                # P2: also emit a live terminal episode event so the UI's
                # completed-count (deduped by episode_key) reflects this missing
                # episode. The event type + payload match the cause.
                if event_sink is not None:
                    try:
                        event_sink.emit(ExecutionEvent(
                            type=terminal_event_type,
                            timestamp="",
                            phase="execute",
                            task_id=getattr(work_item.task, "id", None),
                            trial_id=work_item.trial_id,
                            episode_key=key,
                            payload={
                                "outcome": terminal_outcome,
                                "error_code": missing_code,
                                "steps": 0,
                                "reason": "missing_result",
                            },
                        ))
                    except Exception:  # noqa: BLE001 — event failure must not break ingestion
                        pass

        # Finalize the lane/run exactly once after ALL episodes are ingested.
        ingestor.finalize_lane_run(
            run_id=run_id,
            lane_attempt_id=lane_attempt["id"],
            terminal_state="cancelled" if cancelled else "completed",
            cancelled=cancelled,
        )

    @staticmethod
    def _synthetic_crash_result(
        work_item: PreparedWorkItem, *, cancelled: bool = False,
    ) -> dict[str, Any]:
        """A minimal result dict for a missing/crashed/cancelled episode.

        Shaped so ``_result_to_dict``/``_result_outcome`` classify it correctly:
        - cancelled → CANCELLED outcome (is_error=False, stop_reason=CANCELLED)
          so reports/result_json reflect "cancelled", NOT "crashed".
        - crash (default) → ERROR outcome (is_error=True, stop_reason=ERROR).
        """
        if cancelled:
            return {
                "id": getattr(work_item.task, "id", "unknown"),
                "trial_id": work_item.trial_id,
                "is_success": False,
                "is_error": False,
                "execution": {
                    "error": None,
                    "stop_reason": "CANCELLED",
                },
            }
        return {
            "id": getattr(work_item.task, "id", "unknown"),
            "trial_id": work_item.trial_id,
            "is_success": False,
            "is_error": True,
            "execution": {
                "error": "Worker exited without reporting a result (WORKER_CRASH).",
                "stop_reason": "ERROR",
            },
        }


class MultiprocessRunExecutor(_RunExecutorBase):
    """Execute all episodes of a single lane across N processes (shards).

    VS-08: mirrors ParallelRunExecutor's lifecycle (queued-cancel check → mark
    running → emit run.started → materialize → run → reconcile → finalize) but
    shards the prepared work across ``MultiProcessRunner``. Each shard runs a
    ``ParallelRunner`` (in-process for tests via ``child_runner_factory``; real
    spawn for production).

    Results are reconciled by episode_key (plan order) via the SHARED
    ``_reconcile_and_ingest`` helper so behaviour is identical to
    ParallelRunExecutor: missing → WORKER_CRASH; unknown/duplicate → hard error.

    The ``child_runner_factory`` is the in-process seam: when provided, shards
    run as asyncio tasks (deterministic tests, no real process); when None,
    real ``mp.Process`` spawn (production). The executor builds EpisodeWorkSpecs
    from the materialized plan and hands them to MultiProcessRunner.
    """

    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        task_factory: TaskFactory | None = None,
        child_runner_factory: Any | None = None,
        env_pool_factory: Any | None = None,
        agent_factory: Any | None = None,
        env_factory: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.task_factory = task_factory or RegistryTaskFactory()
        # child_runner_factory: when set, MultiProcessRunner runs shards in-process
        # (asyncio tasks). When None, real mp.Process spawn (production). Tests
        # inject a factory that reconstructs tasks + builds ParallelRunners.
        self.child_runner_factory = child_runner_factory
        self.env_pool_factory = env_pool_factory
        # env_factory is used ONLY for the materialization phase.
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
        from bench_env.runner.multiprocess import MultiProcessRunner
        from bench_env.runner.work_spec import EpisodeWorkSpec

        token = token or CancellationToken()
        events = events or NullEventSink()
        writer = run_event_writer

        # (1) Honor a cancel that landed while queued.
        if self._cancel_requested(run_id):
            token.cancel()
            raise RunCancelled()

        # (2) Mark running + emit run.started.
        self._mark_run_running(run_id)
        if writer is not None:
            try:
                writer.emit(
                    run_id, "run.started", {"state": "running"},
                    entity_type="run", entity_id=run_id,
                )
            except Exception:  # noqa: BLE001
                pass

        # (3) Materialize unique templates.
        prepared = await SingleLaneMaterializer(
            self.database,
            self.settings,
            task_factory=self.task_factory,
            env_factory=self.env_factory,
        ).materialize_run(run_id, token=token, events=events)
        if not prepared:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "Multiprocess execution requires at least one prepared task.",
                status_code=409,
            )

        plan = self._load_plan(run_id)
        lane = _single_lane(plan)
        lane_attempt = self._lane_attempt(run_id)
        prepared_by_key = {item.materialization_key: item for item in prepared}

        # (4) Build PreparedWorkItems AND pickle-safe EpisodeWorkSpecs for every
        # planned episode. Children reconstruct tasks from the specs.
        work_items: list[PreparedWorkItem] = []
        work_specs: list[EpisodeWorkSpec] = []
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
            work_specs.append(
                EpisodeWorkSpec(
                    episode_key=episode.episode_key,
                    task_base_id=episode.task_base_id,
                    instance_id=episode.instance_id,
                    instance_seed=episode.instance_seed,
                    template_index=episode.template_index,
                    params=payload.params,
                    trial_id=episode.trial_id,
                    max_steps=episode.max_steps,
                )
            )
        if not work_items:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "Multiprocess execution requires at least one episode.",
                status_code=409,
            )

        # (5) Build the MultiProcessRunner config from the lane's runner_config.
        processes = int(lane.runner_config.get("processes", 1) or 1)
        parallel = int(lane.runner_config.get("parallel", 1) or 1)
        config = _runner_config(lane)
        # Override processes/parallel from the lane config so MultiProcessRunner
        # shards correctly.
        config = dataclasses_replace(config, processes=processes, parallel=parallel)

        # (6) Run the sharded execution. child_runner_factory is the in-process
        # seam (tests pass one; production passes None for real spawn). The
        # events sink bridges child envelopes into the platform stream.
        runner = MultiProcessRunner(
            [],  # tasks unused on the platform path (specs drive execution)
            config,
            child_runner_factory=self.child_runner_factory,
            event_sink=events,
            cancellation_token=token,
            prepared_work_specs=work_specs,
        )
        try:
            result_dicts = await runner.run()
        except RunCancelled:
            token.cancel()
            result_dicts = []

        # If any single result was CANCELLED, propagate as a run-level cancel.
        any_result_cancelled = any(
            (rd.get("execution", {}) or {}).get("stop_reason") == "CANCELLED"
            for rd in result_dicts
        )
        if any_result_cancelled:
            token.cancel()

        # (7) Reconcile results against expected episode_keys. result_dicts are
        # plain dicts (from ShardResultEnvelope.result_dict) carrying episode_key.
        self._reconcile_and_ingest_dicts(
            run_id=run_id,
            lane_attempt=lane_attempt,
            work_items=work_items,
            result_dicts=result_dicts,
            cancelled=token.cancelled,
            event_sink=events,
        )

        # (8) If cancelled, mark the run cancelled and re-raise.
        if token.cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

    def _reconcile_and_ingest_dicts(
        self,
        *,
        run_id: str,
        lane_attempt: dict[str, str],
        work_items: list[PreparedWorkItem],
        result_dicts: list[dict[str, Any]],
        cancelled: bool,
        event_sink: Any = None,
    ) -> None:
        """Reconcile plain-dict results (from ShardResultEnvelope) against the
        expected episode_keys and ingest. Mirrors _reconcile_and_ingest but
        works with dicts instead of EpisodeResult objects."""
        expected_keys = [item.episode_key for item in work_items]
        expected_set = set(expected_keys)

        results_by_key: dict[str, dict[str, Any]] = {}
        unknown_keys: list[str] = []
        for rd in result_dicts:
            key = rd.get("episode_key")
            if key is None:
                unknown_keys.append("<missing episode_key>")
                continue
            if key not in expected_set:
                unknown_keys.append(key)
                continue
            if key in results_by_key:
                raise RunDomainError(
                    "EPISODE_RESULT_UNKNOWN",
                    "The runner returned duplicate results for one episode.",
                    status_code=500,
                    details=[{"run_id": run_id, "episode_key": key, "duplicate": True}],
                )
            results_by_key[key] = rd
        if unknown_keys:
            raise RunDomainError(
                "EPISODE_RESULT_UNKNOWN",
                "The runner returned results for unknown episodes.",
                status_code=500,
                details=[{"run_id": run_id, "unknown_episode_keys": unknown_keys}],
            )

        ingestor = ResultIngestor(self.database)
        for work_item in work_items:
            key = work_item.episode_key
            artifact_root = _episode_artifact_root(
                lane_attempt["artifact_root"],
                results_by_key.get(key),
                repeat_n=1,
                episode_key=key,
            )
            if key in results_by_key:
                ingestor.ingest_episode_attempt(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt["id"],
                    episode_key=key,
                    result=results_by_key[key],
                    artifact_root=artifact_root,
                    cancelled=cancelled,
                )
            else:
                # Missing result → the worker crashed/exited before reporting,
                # OR the run was cancelled before this episode ran. The label
                # MUST match the cause: a user cancel is CANCELLED (not a
                # crash), so the UI/reports distinguish "cancelled" from
                # "crashed". error_code + terminal episode event follow suit.
                synthetic = self._synthetic_crash_result(work_item, cancelled=cancelled)
                if cancelled:
                    missing_code = "CANCELLED"
                    terminal_event_type = "episode.cancelled"
                    terminal_outcome = "CANCELLED"
                else:
                    missing_code = "WORKER_CRASH"
                    terminal_event_type = "episode.error"
                    terminal_outcome = "ERROR"
                ingestor.ingest_episode_attempt(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt["id"],
                    episode_key=key,
                    result=synthetic,
                    artifact_root=artifact_root,
                    cancelled=cancelled,
                    error_code_override=missing_code,
                )
                if event_sink is not None:
                    try:
                        event_sink.emit(ExecutionEvent(
                            type=terminal_event_type,
                            timestamp="",
                            phase="execute",
                            task_id=getattr(work_item.task, "id", None),
                            trial_id=work_item.trial_id,
                            episode_key=key,
                            payload={
                                "outcome": terminal_outcome,
                                "error_code": missing_code,
                                "steps": 0,
                                "reason": "missing_result",
                            },
                        ))
                    except Exception:  # noqa: BLE001
                        pass

        ingestor.finalize_lane_run(
            run_id=run_id,
            lane_attempt_id=lane_attempt["id"],
            terminal_state="cancelled" if cancelled else "completed",
            cancelled=cancelled,
        )

    @staticmethod
    def _synthetic_crash_result(
        work_item: PreparedWorkItem, *, cancelled: bool = False,
    ) -> dict[str, Any]:
        """A minimal result dict for a missing/crashed/cancelled episode.

        Shaped so ``_result_to_dict``/``_result_outcome`` classify it correctly:
        - cancelled → CANCELLED outcome (is_error=False, stop_reason=CANCELLED)
          so reports/result_json reflect "cancelled", NOT "crashed".
        - crash (default) → ERROR outcome (is_error=True, stop_reason=ERROR).
        """
        if cancelled:
            return {
                "id": getattr(work_item.task, "id", "unknown"),
                "trial_id": work_item.trial_id,
                "is_success": False,
                "is_error": False,
                "execution": {
                    "error": None,
                    "stop_reason": "CANCELLED",
                },
            }
            return {
                "id": getattr(work_item.task, "id", "unknown"),
                "trial_id": work_item.trial_id,
                "is_success": False,
                "is_error": True,
                "execution": {
                    "error": "Worker exited without reporting a result (WORKER_CRASH).",
                    "stop_reason": "ERROR",
                },
            }


class PairedSerialRunExecutor(_RunExecutorBase):
    """Execute a 2-lane (baseline + candidate) run serially and compare pairs.

    VS-09 per-lane lifecycle (Contract 7 — the executor does NOT use SerialRunner
    as a black box):

      1. ``Controller.setup(env, task, eval_mode)`` → initial_obs + params.
      2. Capture the lane's ACTUAL projection hash (``projection_hash_v1``) and
         verify ``str(task.description) == prepared.instruction``.
      3. If integrity OK → ``Controller.run(env, agent, task, initial_obs, ...)``.
         Controller.run's ``finally`` owns teardown + agent.reset_history for the
         OK path — the executor MUST NOT double-teardown (Contract 7).
      4. If mismatch → SKIP ``Controller.run``. The executor tears down + resets
         in its OWN ``try/finally`` (since Controller.run's finally won't run),
         then closes the env / finishes the recorder.

    Dual-side integrity (Contract 5): each lane's integrity_json records
    ``prepared_projection_hash`` + the lane's ``actual_projection_hash``. If
    EITHER lane's actual != prepared → ``IntegrityStatus.PROJECTION_MISMATCH``.

    Pair join + classification (Contracts 1, 4, 8): episode_attempts are joined
    by pair_key (== episode_key). ``classify_pair`` maps the joined pair to a
    ``PairClassification``. PAIRING_VIOLATION lanes persist an ERROR
    episode_attempt (error_code=PAIRING_VIOLATION, stop_reason=PAIRING_VIOLATION)
    and emit ``episode.error``.

    Finalization (Contract 2): per-lane ``finalize_lane_only`` runs after each
    lane; ``finalize_run`` runs ONCE after the comparison is recorded.

    Single-lane guards (Contract 6/13): this executor is NEW and does NOT modify
    ``SerialRunExecutor``, ``_single_lane``, or ``_lane_attempt``. The supervisor
    routes 2-lane runs here; 1-lane runs keep using ``SerialRunExecutor``.
    """

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

    def _paired_lanes(self, run_id: str) -> tuple[PlannedLane, PlannedLane]:
        """Return (baseline_lane, candidate_lane) ordered by lane_key."""
        plan = self._load_plan(run_id)
        if len(plan.lanes) != 2:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "VS-09 paired execution requires exactly two run lanes.",
                status_code=409,
            )
        lanes = sorted(plan.lanes, key=lambda lane: lane.lane_key)
        return lanes[0], lanes[1]

    def _lane_attempts_by_key(self, run_id: str) -> dict[str, dict[str, str]]:
        """Map lane_key → {id, artifact_root, lane_id, run_attempt_id} for this run."""
        rows = self.database.connection.execute(
            """
            SELECT l.lane_key, la.id, la.artifact_root, l.id AS lane_id,
                   la.run_attempt_id
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE l.run_id = ?
            ORDER BY l.lane_key
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            raise RunDomainError(
                "LANE_ATTEMPT_NOT_FOUND",
                "Lane attempt was not found.",
                status_code=500,
            )
        return {
            str(row["lane_key"]): {
                "id": str(row["id"]),
                "artifact_root": str(row["artifact_root"]),
                "lane_id": str(row["lane_id"]),
                "run_attempt_id": str(row["run_attempt_id"]),
            }
            for row in rows
        }

    async def _run_lane(
        self,
        *,
        run_id: str,
        lane: PlannedLane,
        lane_attempt: dict[str, str],
        prepared_by_key: dict[str, PreparedTaskInstance],
        episodes: list,
        token: CancellationToken,
        event_sink: EventSink,
        agent: Any,
    ) -> list[dict[str, Any]]:
        """Run ONE lane's episodes serially. Returns per-episode outcome dicts:

        ``{episode_key, outcome, result, artifact_root, integrity_status,
        actual_projection_hash, prepared}``

        Per Contract 7: setup is separated from run; teardown/reset ownership is
        determined by whether Controller.run's finally executed.
        """
        env = self.env_factory(lane)
        config = _runner_config(lane)
        lane_root = self.settings.runs_dir / run_id / lane_attempt["artifact_root"]
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
            repeat_n=len(episodes),
        )
        eval_mode = str(lane.runner_config.get("eval_mode", "grounded"))
        outcomes: list[dict[str, Any]] = []
        try:
            for episode in episodes:
                token.raise_if_cancelled()
                prepared = prepared_by_key[episode.materialization_key]
                task = self.task_factory.instantiate(episode, params=prepared.params)
                actual_projection_hash: str | None = None
                integrity_status = IntegrityStatus.OK
                integrity_reason: str | None = None
                ran_controller = False
                _tore_down = [False]  # mutable flag to prevent double-teardown (P2.2)
                result: Any = None
                try:
                    initial_obs, _params = await Controller.setup(
                        env, task, eval_mode=eval_mode
                    )
                    # Verify instruction (Contract 4/5).
                    if str(task.description) != prepared.instruction:
                        integrity_status = IntegrityStatus.INSTRUCTION_MISMATCH
                        integrity_reason = "instruction_mismatch"
                    # Capture actual projection hash (Contract 5).
                    state = await env.get_state(required_apps=None)
                    actual_projection_hash = projection_hash_v1(state)
                    if actual_projection_hash != prepared.projection_hash:
                        integrity_status = IntegrityStatus.PROJECTION_MISMATCH
                        integrity_reason = "projection_mismatch"

                    if integrity_status == IntegrityStatus.OK:
                        # OK path: Controller.run owns teardown in its finally.
                        # The executor MUST NOT double-teardown (Contract 7).
                        ran_controller = True
                        exec_result, iobs, fobs, ep_record, _task = await Controller.run(
                            env,
                            agent,
                            task,
                            initial_obs,
                            max_steps=episode.max_steps,
                            recorder=recorder,
                            trial_id=episode.trial_id,
                            eval_mode=eval_mode,
                            cancellation_token=token,
                            event_sink=event_sink,
                            worker_id=lane.lane_key,
                            episode_key=episode.episode_key,
                        )
                        # Run the judge (mirroring BaseRunner.run_episode) so the
                        # result carries a functional verdict (.success/.error).
                        # A cancelled execution skips judging (no valid verdict).
                        result = await self._assemble_episode_result(
                            task=task,
                            exec_result=exec_result,
                            initial_obs=iobs,
                            final_obs=fobs,
                            episode_record=ep_record,
                            recorder=recorder,
                            trial_id=episode.trial_id,
                            max_steps=episode.max_steps,
                            episode_key=episode.episode_key,
                            eval_mode=eval_mode,
                            worker_id=lane.lane_key,
                            event_sink=event_sink,
                        )
                        # Controller.run's finally does NOT reset agent history
                        # (that's BaseRunner.run_episode's job). Since we call
                        # Controller.run directly, reset history here (OK path).
                        reset_history = getattr(agent, "reset_history", None)
                        if callable(reset_history):
                            reset_history()
                    else:
                        # Mismatch path: SKIP Controller.run. The executor owns
                        # teardown + agent.reset_history (Controller.run's finally
                        # did NOT run). Then env close / recorder finish below.
                        # Build a synthetic result mirroring Contract 8.
                        result = self._pairing_violation_result(task, episode)
                except RunCancelled:
                    token.cancel()
                    # Cancel during setup/run: teardown already handled by
                    # Controller.run's finally if it started; otherwise here.
                    # Guard with _tore_down so the finally below does NOT
                    # double-teardown (P2.2: setup-time cancel hits both the
                    # except and the finally because ran_controller is False).
                    if not ran_controller and not _tore_down[0]:
                        await _maybe_await(task.teardown(env))
                        _tore_down[0] = True
                    raise
                finally:
                    if not ran_controller and not _tore_down[0]:
                        # Mismatch path teardown (Contract 7).
                        await _maybe_await(task.teardown(env))
                        _tore_down[0] = True

                outcomes.append(
                    {
                        "episode_key": episode.episode_key,
                        "pair_key": episode.pair_key,
                        "materialization_key": episode.materialization_key,
                        "result": result,
                        "integrity_status": integrity_status,
                        "integrity_reason": integrity_reason,
                        "actual_projection_hash": actual_projection_hash,
                        "prepared": prepared,
                        "trial_id": episode.trial_id,
                        # P2.2: carry the task's declared apps so pair integrity
                        # only tolerates additions under these apps (not all
                        # target metadata apps).
                        "task_app_ids": set(getattr(task, "apps", []) or []),
                    }
                )
        except RunCancelled:
            # P1 fix: do NOT let RunCancelled propagate past _run_lane — the
            # partial outcomes accumulated so far MUST reach the caller. The
            # caller detects cancellation via token.cancelled. The finally block
            # below still runs recorder/env cleanup.
            pass
        finally:
            recorder.finish_run()
            close = getattr(env, "close", None)
            if callable(close):
                await _maybe_await(close())
        return outcomes

    @staticmethod
    def _pairing_violation_result(task: Any, episode: Any) -> dict[str, Any]:
        """Synthetic result for a PAIRING_VIOLATION lane (Contract 8).

        Shaped so ``_result_to_dict``/``_result_outcome`` classify it as ERROR,
        and the persisted result_json.execution.stop_reason == 'PAIRING_VIOLATION'.
        """
        return {
            "id": getattr(task, "id", "unknown"),
            "trial_id": episode.trial_id,
            "is_success": False,
            "is_error": True,
            "execution": {
                "error": "PAIRING_VIOLATION: prepared fixture not honored.",
                "stop_reason": "PAIRING_VIOLATION",
            },
            "episode_key": episode.episode_key,
        }

    async def _assemble_episode_result(
        self,
        *,
        task: Any,
        exec_result: Any,
        initial_obs: Any,
        final_obs: Any,
        episode_record: Any,
        recorder: Any,
        trial_id: int,
        max_steps: int,
        episode_key: str,
        eval_mode: str,
        worker_id: str,
        event_sink: EventSink,
    ) -> Any:
        """Run the judge and build an EpisodeResult after Controller.run.

        Mirrors ``BaseRunner.run_episode``'s post-run phase (evaluate + assemble
        + finish + terminal event) so the result carries a functional verdict
        (``.success``/``.error``) that ``_result_outcome`` classifies correctly.

        Controller.run already tore down the task in its finally (Contract 7);
        this helper only evaluates + assembles + records. agent.reset_history is
        also owned by Controller.run's caller (BaseRunner.run_episode) — but
        since we call Controller.run directly, we reset history here.
        """
        from bench_env.runner.base import EpisodeResult, Evaluator

        # Skip judging for cancelled/error executions (mirrors run_episode).
        judge = None
        if (
            exec_result.stop_reason != "CANCELLED"
            and not exec_result.error
            and initial_obs is not None
            and final_obs is not None
        ):
            evaluator = Evaluator(eval_mode=eval_mode)
            try:
                judge = await evaluator.evaluate(
                    task, initial_obs, final_obs, exec_result, episode_record
                )
            except Exception as eval_err:  # noqa: BLE001
                from dataclasses import replace as dc_replace

                exec_result = dc_replace(
                    exec_result,
                    error=f"judge_error: {type(eval_err).__name__}: {eval_err}",
                )

        try:
            result = EpisodeResult(
                task_id=task.id,
                task_name=task.description,
                suite=getattr(task, "suite", ""),
                execution=exec_result,
                judge=judge,
                trial_id=trial_id,
                apps=list(getattr(task, "apps", [])),
                max_steps=max_steps,
                episode_key=episode_key,
                **EpisodeResult._task_taxonomy(task),
            )
        except Exception:  # noqa: BLE001
            result = EpisodeResult(
                task_id=task.id,
                task_name=str(task.id),
                suite=getattr(task, "suite", ""),
                execution=exec_result,
                judge=None,
                trial_id=trial_id,
                apps=list(getattr(task, "apps", [])),
                max_steps=max_steps,
                episode_key=episode_key,
            )

        try:
            if episode_record:
                episode_record.finish(result.to_dict())
            elif recorder:
                recorder.record_result(result.to_dict())
        finally:
            agent_reset = getattr(task, "_agent_for_reset", None)
            # Controller.run's finally does NOT reset agent history (that's
            # BaseRunner.run_episode's job). Since we call Controller.run
            # directly, reset history here for the OK path.
            _ = agent_reset  # agent reference not held here; reset via caller.

        # Emit the terminal episode event (mirrors run_episode) for the OK path.
        if exec_result.stop_reason != "CANCELLED":
            try:
                if result.error:
                    event_sink.emit(ExecutionEvent(
                        type="episode.error",
                        timestamp="",
                        phase="execute",
                        worker_id=worker_id,
                        task_id=task.id,
                        trial_id=trial_id,
                        episode_key=episode_key,
                        payload={
                            "outcome": "ERROR",
                            "steps": exec_result.steps,
                            "stop_reason": exec_result.stop_reason,
                            "error": result.error,
                        },
                    ))
                else:
                    event_sink.emit(ExecutionEvent(
                        type="episode.completed",
                        timestamp="",
                        phase="execute",
                        worker_id=worker_id,
                        task_id=task.id,
                        trial_id=trial_id,
                        episode_key=episode_key,
                        payload={
                            "outcome": "PASS" if result.success else "FAIL",
                            "steps": exec_result.steps,
                            "stop_reason": exec_result.stop_reason,
                        },
                    ))
            except Exception:  # noqa: BLE001
                pass
        return result

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

        # (1) Honor a cancel that landed while queued.
        if self._cancel_requested(run_id):
            token.cancel()
            raise RunCancelled()

        # (2) Mark running + emit run.started (terminal run.* owned by supervisor).
        self._mark_run_running(run_id)
        if writer is not None:
            try:
                writer.emit(
                    run_id, "run.started", {"state": "running"},
                    entity_type="run", entity_id=run_id,
                )
            except Exception:  # noqa: BLE001
                pass

        # (3) Materialize the single shared prepared task (Contract 6: paired).
        if self.env_factory is None or self.agent_factory is None:
            raise RunDomainError(
                "EXECUTOR_FACTORY_MISSING",
                "Paired serial execution requires environment and agent factories.",
                status_code=500,
            )

        cancelled = False
        baseline_outcomes: list[dict[str, Any]] = []
        candidate_outcomes: list[dict[str, Any]] = []
        # Pre-set defaults so the finalize path works even if cancellation
        # fires during materialization (before lanes/sinks/ingestor are built).
        ingestor = ResultIngestor(self.database)
        baseline_attempt: dict[str, str] | None = None
        candidate_attempt: dict[str, str] | None = None
        baseline_sink: Any = NullEventSink()
        candidate_sink: Any = NullEventSink()
        try:
            prepared = await PairedMaterializer(
                self.database,
                self.settings,
                task_factory=self.task_factory,
                env_factory=self.env_factory,
            ).materialize_run(run_id, token=token, events=events)
            if not prepared:
                raise RunDomainError(
                    "UNSUPPORTED_RUN_PLAN",
                    "Paired execution requires at least one prepared task.",
                    status_code=409,
                )
            prepared_by_key = {item.materialization_key: item for item in prepared}

            plan = self._load_plan(run_id)
            baseline_lane, candidate_lane = self._paired_lanes(run_id)
            lane_attempts = self._lane_attempts_by_key(run_id)
            baseline_attempt = lane_attempts[baseline_lane.lane_key]
            candidate_attempt = lane_attempts[candidate_lane.lane_key]

            # Build a per-lane event sink (Contract 3).
            episode_key_resolver = self._make_episode_key_resolver(run_id)
            baseline_sink = self._build_lane_sink(
                writer, run_id, baseline_lane, baseline_attempt, episode_key_resolver
            )
            candidate_sink = self._build_lane_sink(
                writer, run_id, candidate_lane, candidate_attempt, episode_key_resolver
            )

            ingestor = ResultIngestor(self.database)
            run_attempt_id = self._run_attempt_id(run_id)
            episodes = plan.episodes

            # (4) Run baseline lane, then candidate lane (serial).
            baseline_agent = self.agent_factory(baseline_lane)
            baseline_outcomes = await self._run_lane(
                run_id=run_id,
                lane=baseline_lane,
                lane_attempt=baseline_attempt,
                prepared_by_key=prepared_by_key,
                episodes=episodes,
                token=token,
                event_sink=baseline_sink,
                agent=baseline_agent,
            )
            candidate_agent = self.agent_factory(candidate_lane)
            candidate_outcomes = await self._run_lane(
                run_id=run_id,
                lane=candidate_lane,
                lane_attempt=candidate_attempt,
                prepared_by_key=prepared_by_key,
                episodes=episodes,
                token=token,
                event_sink=candidate_sink,
                agent=candidate_agent,
            )
        except RunCancelled:
            token.cancel()
            cancelled = True
            # P1 fix: PRESERVE already-collected outcomes — do NOT clear them.
            # Earlier completed episodes are valid results; only the episodes
            # that never ran (or were mid-cancel) are missing. Those are
            # synthesized as CANCELLED below in _ingest_lane_outcomes via the
            # cancelled=True path + missing-episode reconciliation.

        # P1 fix: _run_lane now catches RunCancelled internally and returns
        # partial outcomes (does NOT propagate). Detect cancellation via
        # token.cancelled — covers cancel during materialize, between episodes,
        # or mid-step (Controller.run returns CANCELLED result, token is set).
        if token.cancelled:
            cancelled = True

        # Also detect cancellation from result stop_reason (Controller.run
        # returns a CANCELLED result instead of raising when the token fires
        # mid-step).
        if not cancelled:
            for outcomes in (baseline_outcomes, candidate_outcomes):
                for oc in outcomes:
                    result = oc.get("result")
                    if isinstance(result, dict):
                        exec_d = result.get("execution") or {}
                        if exec_d.get("stop_reason") == "CANCELLED":
                            cancelled = True
                            token.cancel()
                            break
                    else:
                        exec_r = getattr(result, "execution", None)
                        if exec_r and getattr(exec_r, "stop_reason", None) == "CANCELLED":
                            cancelled = True
                            token.cancel()
                            break
                if cancelled:
                    break

        # If cancellation fired during materialization, the lane attempts were
        # never resolved from the DB. Resolve them now so finalize can run.
        if baseline_attempt is None or candidate_attempt is None:
            lane_attempts = self._lane_attempts_by_key(run_id)
            plan_fallback = self._load_plan(run_id)
            bl, cl = self._paired_lanes(run_id)
            baseline_attempt = lane_attempts[bl.lane_key]
            candidate_attempt = lane_attempts[cl.lane_key]

        # Resolve episodes for missing-episode reconciliation (P1.2). If cancel
        # fired during materialize, episodes wasn't set — load from plan.
        if not locals().get("episodes"):
            episodes = self._load_plan(run_id).episodes

        # (5) Ingest episode_attempts per lane. PAIRING_VIOLATION lanes get an
        # ERROR episode_attempt + episode.error event (Contract 8).
        baseline_ingested = self._ingest_lane_outcomes(
            ingestor=ingestor,
            run_id=run_id,
            lane_attempt=baseline_attempt,
            outcomes=baseline_outcomes,
            event_sink=baseline_sink,
            cancelled=cancelled,
            expected_episodes=episodes,
        )
        candidate_ingested = self._ingest_lane_outcomes(
            ingestor=ingestor,
            run_id=run_id,
            lane_attempt=candidate_attempt,
            outcomes=candidate_outcomes,
            event_sink=candidate_sink,
            cancelled=cancelled,
            expected_episodes=episodes,
        )

        # (6) Per-lane finalize_lane_only (Contract 2).
        terminal_lane_state = "cancelled" if cancelled else "completed"
        ingestor.finalize_lane_only(
            lane_attempt_id=baseline_attempt["id"],
            terminal_state=terminal_lane_state,
        )
        ingestor.finalize_lane_only(
            lane_attempt_id=candidate_attempt["id"],
            terminal_state=terminal_lane_state,
        )

        # (7) Pair join + classification + comparison persistence (Contracts 1,4,8).
        if not cancelled:
            self._record_comparison(
                run_id=run_id,
                run_attempt_id=run_attempt_id,
                baseline_lane=baseline_lane,
                candidate_lane=candidate_lane,
                baseline_lane_id=baseline_attempt["lane_id"],
                candidate_lane_id=candidate_attempt["lane_id"],
                baseline_ingested=baseline_ingested,
                candidate_ingested=candidate_ingested,
                baseline_outcomes=baseline_outcomes,
                candidate_outcomes=candidate_outcomes,
            )

        # (8) finalize_run once after the comparison (Contract 2).
        ingestor.finalize_run(
            run_id=run_id,
            terminal_state=terminal_lane_state,
            cancelled=cancelled,
        )

        if cancelled:
            ingestor.mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

    def _run_attempt_id(self, run_id: str) -> str:
        row = self.database.connection.execute(
            "SELECT id FROM run_attempts WHERE run_id = ? ORDER BY attempt_no DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunDomainError(
                "LANE_ATTEMPT_NOT_FOUND",
                "Run attempt was not found.",
                status_code=500,
            )
        return str(row["id"])

    def _make_episode_key_resolver(self, run_id: str):
        def resolver(episode_key: str) -> str | None:
            row = self.database.connection.execute(
                "SELECT id FROM episodes WHERE run_id = ? AND episode_key = ?",
                (run_id, episode_key),
            ).fetchone()
            return str(row["id"]) if row else None

        return resolver

    @staticmethod
    def _build_lane_sink(
        writer: Any,
        run_id: str,
        lane: PlannedLane,
        lane_attempt: dict[str, str],
        episode_key_resolver: Any,
    ) -> Any:
        """Build a per-lane PlatformRunnerEventSink (Contract 3)."""
        from test_platform.execution.runner_sink import PlatformRunnerEventSink

        return PlatformRunnerEventSink(
            writer,
            run_id=run_id,
            run_attempt_id=lane_attempt.get("run_attempt_id"),
            lane_id=lane_attempt["lane_id"],
            lane_attempt_id=lane_attempt["id"],
            worker_id=lane.lane_key,
            episode_key_resolver=episode_key_resolver,
        )

    def _ingest_lane_outcomes(
        self,
        *,
        ingestor: "ResultIngestor",
        run_id: str,
        lane_attempt: dict[str, str],
        outcomes: list[dict[str, Any]],
        event_sink: EventSink,
        cancelled: bool,
        expected_episodes: list[Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Ingest one lane's episode outcomes. Returns pair_key → ingested dict.

        Per-episode cancellation (P1.1): a run-level ``cancelled`` flag does NOT
        relabel already-completed results. Only results whose stop_reason is
        CANCELLED (or missing episodes synthesized below) use cancelled=True.

        Missing-episode reconciliation (P1.2): when the run was cancelled,
        expected episodes that have no outcome (never ran) are synthesized as
        CANCELLED attempts with an episode.cancelled event — mirroring the
        parallel executor's _reconcile_and_ingest missing branch.
        """
        ingested: dict[str, dict[str, Any]] = {}
        outcomes_by_key = {oc["episode_key"]: oc for oc in outcomes}

        # Ingest actual outcomes with per-result cancellation.
        for outcome in outcomes:
            pair_key = outcome["pair_key"]
            is_violation = outcome["integrity_status"].is_violation
            artifact_root = _episode_artifact_root(
                lane_attempt["artifact_root"],
                outcome["result"],
                repeat_n=1,
                episode_key=outcome["episode_key"],
            )
            error_code_override = "PAIRING_VIOLATION" if is_violation else None
            # P1.1: per-episode cancel — only this result's own stop_reason
            # determines cancelled, NOT the run-level flag. A baseline episode
            # that PASSed before candidate cancellation keeps PASS.
            result_cancelled = _result_is_cancelled(outcome["result"])
            summary = ingestor.ingest_episode_attempt(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                episode_key=outcome["episode_key"],
                result=outcome["result"],
                artifact_root=artifact_root,
                cancelled=result_cancelled,
                error_code_override=error_code_override,
            )
            ingested[pair_key] = summary
            # Emit a terminal episode event for PAIRING_VIOLATION (Contract 8).
            if is_violation:
                try:
                    event_sink.emit(ExecutionEvent(
                        type="episode.error",
                        timestamp="",
                        phase="execute",
                        task_id=getattr(outcome["result"], "id", None)
                            or outcome["result"].get("id"),
                        trial_id=outcome.get("trial_id", 0),
                        episode_key=outcome["episode_key"],
                        payload={
                            "outcome": "ERROR",
                            "error_code": "PAIRING_VIOLATION",
                            "stop_reason": "PAIRING_VIOLATION",
                            "reason": outcome.get("integrity_reason"),
                        },
                    ))
                except Exception:  # noqa: BLE001
                    pass

        # P1.2: missing-episode reconciliation for cancelled runs.
        if cancelled and expected_episodes is not None:
            for episode in expected_episodes:
                if episode.episode_key in outcomes_by_key:
                    continue  # already ingested above
                # This episode never ran (cancel before it started). Synthesize
                # a CANCELLED attempt so the lane has a complete episode grid.
                artifact_root = _episode_artifact_root(
                    lane_attempt["artifact_root"],
                    None,
                    repeat_n=1,
                    episode_key=episode.episode_key,
                )
                synthetic = {
                    "id": episode.task_base_id,
                    "trial_id": episode.trial_id,
                    "is_success": False,
                    "is_error": False,
                    "execution": {"error": None, "stop_reason": "CANCELLED"},
                    "episode_key": episode.episode_key,
                }
                summary = ingestor.ingest_episode_attempt(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt["id"],
                    episode_key=episode.episode_key,
                    result=synthetic,
                    artifact_root=artifact_root,
                    cancelled=True,
                    error_code_override="CANCELLED",
                )
                ingested[episode.pair_key] = summary
                # Emit episode.cancelled for the UI's completed-count.
                try:
                    event_sink.emit(ExecutionEvent(
                        type="episode.cancelled",
                        timestamp="",
                        phase="execute",
                        task_id=episode.task_base_id,
                        trial_id=episode.trial_id,
                        episode_key=episode.episode_key,
                        payload={
                            "outcome": "CANCELLED",
                            "error_code": "CANCELLED",
                            "steps": 0,
                            "reason": "missing_result",
                        },
                    ))
                except Exception:  # noqa: BLE001
                    pass

        return ingested

    def _record_comparison(
        self,
        *,
        run_id: str,
        run_attempt_id: str,
        baseline_lane: PlannedLane,
        candidate_lane: PlannedLane,
        baseline_lane_id: str,
        candidate_lane_id: str,
        baseline_ingested: dict[str, dict[str, Any]],
        candidate_ingested: dict[str, dict[str, Any]],
        baseline_outcomes: list[dict[str, Any]],
        candidate_outcomes: list[dict[str, Any]],
    ) -> None:
        """Join pairs by pair_key, classify, and persist the comparison
        (Contracts 1, 4, 8)."""
        from test_platform.persistence.repositories import ComparisonRepository

        # Index outcomes by pair_key for integrity lookup.
        baseline_by_pair = {o["pair_key"]: o for o in baseline_outcomes}
        candidate_by_pair = {o["pair_key"]: o for o in candidate_outcomes}
        pair_keys = sorted(set(baseline_by_pair) | set(candidate_by_pair))

        repo = ComparisonRepository(self.database)
        comparison_id = repo.record_comparison(
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            baseline_lane_id=baseline_lane_id,
            candidate_lane_id=candidate_lane_id,
            policy=self._load_plan(run_id).comparison,
        )

        summary_counts: dict[str, int] = {}
        for pair_key in pair_keys:
            b_out = baseline_by_pair.get(pair_key)
            c_out = candidate_by_pair.get(pair_key)
            b_attempt = baseline_ingested.get(pair_key)
            c_attempt = candidate_ingested.get(pair_key)

            # Derive the integrity status: PROJECTION_MISMATCH if EITHER lane's
            # actual != prepared; otherwise OK (instruction checked per lane).
            integrity = IntegrityStatus.OK
            integrity_reason = "OK"
            prepared_projection_hash: str | None = None
            baseline_actual: str | None = None
            candidate_actual: str | None = None
            if b_out is not None:
                prepared_projection_hash = b_out["prepared"].projection_hash
                baseline_actual = b_out["actual_projection_hash"]
                if b_out["integrity_status"].is_violation:
                    integrity = b_out["integrity_status"]
                    integrity_reason = b_out.get("integrity_reason") or b_out["integrity_status"].value
            if c_out is not None:
                candidate_actual = c_out["actual_projection_hash"]
                if c_out["integrity_status"].is_violation:
                    integrity = c_out["integrity_status"]
                    integrity_reason = c_out.get("integrity_reason") or c_out["integrity_status"].value

            b_outcome = b_attempt["outcome"] if b_attempt else None
            c_outcome = c_attempt["outcome"] if c_attempt else None
            classification = classify_pair(
                b_outcome, c_outcome, integrity_status=integrity
            )

            integrity_json = {
                "status": integrity.value,
                "reason": integrity_reason,
                "prepared_projection_hash": prepared_projection_hash,
                "baseline_actual_projection_hash": baseline_actual,
                "candidate_actual_projection_hash": candidate_actual,
            }
            delta_json = {
                "baseline_outcome": b_outcome,
                "candidate_outcome": c_outcome,
            }
            repo.record_pair(
                comparison_id=comparison_id,
                pair_key=pair_key,
                classification=classification.value,
                baseline_episode_attempt_id=(
                    b_attempt["id"] if b_attempt else None
                ),
                candidate_episode_attempt_id=(
                    c_attempt["id"] if c_attempt else None
                ),
                integrity=integrity_json,
                delta=delta_json,
            )
            summary_counts[classification.value] = (
                summary_counts.get(classification.value, 0) + 1
            )

        # Persist an aggregate summary on the comparison.
        self.database.connection.execute(
            "UPDATE comparisons SET summary_json = ? WHERE id = ?",
            (
                canonical_json(summary_counts),
                comparison_id,
            ),
        )
        self.database.connection.commit()


class PairedParallelRunExecutor(PairedSerialRunExecutor):
    """Execute a 2-lane (baseline + candidate) run CONCURRENTLY and compare pairs.

    VS-10 mirrors PairedSerialRunExecutor's per-lane lifecycle (Contracts 5/7/9
    from VS-09 carry over) but runs the two lanes via ``asyncio.gather`` instead
    of serial awaits. Differences from the serial paired executor:

    - **concurrent lanes** (Contract C): baseline + candidate run as two
      ``asyncio`` tasks gathered with ``return_exceptions=True``;
    - **sibling failure cancel/drain** (Contract 8): if one lane throws a
      non-cancel exception, ``token.cancel()`` is called so the other lane's
      ``Controller.run`` returns a CANCELLED result; the other lane is then
      awaited to completion and its env closed — no leaked env, no duplicate
      terminal events;
    - **worker lifecycle emitted manually** (Contract 9): since ``_run_lane``
      calls ``Controller.run`` directly (no ``ParallelRunner``), the executor
      emits ``worker.started``/``worker.stopped`` around each lane's episode
      loop using the lane-scoped sink;
    - **path-level pair integrity** (Contract 4): the executor captures each
      lane's projected state dict (BEFORE discard) and computes a path-level
      diff via ``compare_paired_states`` (version-direction-aware, Contract 5).

    Single-lane guards (Contract 11): this executor does NOT modify
    ``SerialRunExecutor``/``PairedSerialRunExecutor``/``_single_lane``. The
    supervisor routes 2-lane runs whose compare node ``execution == "parallel"``
    here; serial paired runs keep using ``PairedSerialRunExecutor``.
    """

    async def _run_lane_parallel(
        self,
        *,
        run_id: str,
        lane: PlannedLane,
        lane_attempt: dict[str, str],
        prepared_by_key: dict[str, PreparedTaskInstance],
        episodes: list,
        token: CancellationToken,
        event_sink: EventSink,
        agent: Any,
    ) -> list[dict[str, Any]]:
        """Run ONE lane's episodes, capturing the projected state dict (Contract 4).

        Mirrors ``PairedSerialRunExecutor._run_lane`` but additionally records
        ``actual_projected_state`` on each outcome so ``_record_comparison`` can
        compute the path-level diff. Emits worker.started/stopped around the
        episode loop (Contract 9).
        """
        env = self.env_factory(lane)
        config = _runner_config(lane)
        lane_root = self.settings.runs_dir / run_id / lane_attempt["artifact_root"]
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
            repeat_n=len(episodes),
        )
        eval_mode = str(lane.runner_config.get("eval_mode", "grounded"))
        outcomes: list[dict[str, Any]] = []

        # Contract 9: emit worker.started for this lane.
        try:
            event_sink.emit(ExecutionEvent(
                type="worker.started",
                timestamp="",
                phase="execute",
                worker_id=lane.lane_key,
                payload={"lane_key": lane.lane_key, "role": lane.role},
            ))
        except Exception:  # noqa: BLE001
            pass

        try:
            for episode in episodes:
                token.raise_if_cancelled()
                prepared = prepared_by_key[episode.materialization_key]
                task = self.task_factory.instantiate(episode, params=prepared.params)
                actual_projection_hash: str | None = None
                actual_projected_state: dict[str, Any] | None = None
                integrity_status = IntegrityStatus.OK
                integrity_reason: str | None = None
                ran_controller = False
                _tore_down = [False]
                result: Any = None
                try:
                    initial_obs, _params = await Controller.setup(
                        env, task, eval_mode=eval_mode
                    )
                    if str(task.description) != prepared.instruction:
                        integrity_status = IntegrityStatus.INSTRUCTION_MISMATCH
                        integrity_reason = "instruction_mismatch"
                    state = await env.get_state(required_apps=None)
                    # Contract 4: capture the raw state (for the path-level diff)
                    # BEFORE discard. The per-lane projection-hash check uses
                    # projection_hash_v1 (matching the materializer's
                    # PairedMaterializer._projection_hash) so the prepared/actual
                    # hashes compare on the same ignore set. For the strict
                    # snapshot policy the materializer hash differs from the
                    # strict profile — but the prepared fixture was hashed with
                    # projection_hash_v1, so we always compare on that basis.
                    raw_state = state
                    actual_projected_state = None  # computed lazily in compare
                    actual_projection_hash = projection_hash_v1(state)
                    if actual_projection_hash != prepared.projection_hash:
                        integrity_status = IntegrityStatus.PROJECTION_MISMATCH
                        integrity_reason = "projection_mismatch"

                    if integrity_status == IntegrityStatus.OK:
                        ran_controller = True
                        exec_result, iobs, fobs, ep_record, _task = await Controller.run(
                            env,
                            agent,
                            task,
                            initial_obs,
                            max_steps=episode.max_steps,
                            recorder=recorder,
                            trial_id=episode.trial_id,
                            eval_mode=eval_mode,
                            cancellation_token=token,
                            event_sink=event_sink,
                            worker_id=lane.lane_key,
                            episode_key=episode.episode_key,
                        )
                        result = await self._assemble_episode_result(
                            task=task,
                            exec_result=exec_result,
                            initial_obs=iobs,
                            final_obs=fobs,
                            episode_record=ep_record,
                            recorder=recorder,
                            trial_id=episode.trial_id,
                            max_steps=episode.max_steps,
                            episode_key=episode.episode_key,
                            eval_mode=eval_mode,
                            worker_id=lane.lane_key,
                            event_sink=event_sink,
                        )
                        reset_history = getattr(agent, "reset_history", None)
                        if callable(reset_history):
                            reset_history()
                    else:
                        result = self._pairing_violation_result(task, episode)
                except RunCancelled:
                    token.cancel()
                    if not ran_controller and not _tore_down[0]:
                        await _maybe_await(task.teardown(env))
                        _tore_down[0] = True
                    raise
                finally:
                    if not ran_controller and not _tore_down[0]:
                        await _maybe_await(task.teardown(env))
                        _tore_down[0] = True

                outcomes.append(
                    {
                        "episode_key": episode.episode_key,
                        "pair_key": episode.pair_key,
                        "materialization_key": episode.materialization_key,
                        "result": result,
                        "integrity_status": integrity_status,
                        "integrity_reason": integrity_reason,
                        "actual_projection_hash": actual_projection_hash,
                        "actual_projected_state": actual_projected_state,
                        "raw_state": raw_state,
                        "prepared": prepared,
                        "trial_id": episode.trial_id,
                    }
                )
        except RunCancelled:
            # Partial outcomes preserved (mirrors the serial paired executor).
            pass
        finally:
            recorder.finish_run()
            close = getattr(env, "close", None)
            if callable(close):
                await _maybe_await(close())
            # Contract 9: emit worker.stopped for this lane.
            try:
                event_sink.emit(ExecutionEvent(
                    type="worker.stopped",
                    timestamp="",
                    phase="execute",
                    worker_id=lane.lane_key,
                    payload={"lane_key": lane.lane_key},
                ))
            except Exception:  # noqa: BLE001
                pass
        return outcomes

    def _initial_state_policy(self, run_id: str) -> str:
        """Read the compare node's initial_state_policy from the run plan."""
        from test_platform.domain.pair_integrity import (
            DEFAULT_INITIAL_STATE_POLICY,
        )

        plan = self._load_plan(run_id)
        return str(plan.comparison.get("initial_state_policy") or DEFAULT_INITIAL_STATE_POLICY)

    def _lane_apps(self, lane: PlannedLane) -> list[dict[str, Any]]:
        """Read the apps list from the lane's frozen TargetRevision.metadata."""
        row = self.database.connection.execute(
            "SELECT metadata_json FROM target_revisions WHERE id = ?",
            (lane.target_revision_id,),
        ).fetchone()
        if row is None:
            return []
        metadata = json.loads(row["metadata_json"])
        apps = metadata.get("apps")
        return apps if isinstance(apps, list) else []

    async def execute_run(
        self,
        run_id: str,
        *,
        token: CancellationToken | None = None,
        events: EventSink | None = None,
        run_event_writer: Any = None,
    ) -> RunDetail:
        """Run both lanes concurrently (gather) with sibling-failure drain."""
        token = token or CancellationToken()
        events = events or NullEventSink()
        writer = run_event_writer

        # (1) Honor a cancel that landed while queued.
        if self._cancel_requested(run_id):
            token.cancel()
            raise RunCancelled()

        # (2) Mark running + emit run.started.
        self._mark_run_running(run_id)
        if writer is not None:
            try:
                writer.emit(
                    run_id, "run.started", {"state": "running"},
                    entity_type="run", entity_id=run_id,
                )
            except Exception:  # noqa: BLE001
                pass

        # (3) Materialize the single shared prepared task.
        if self.env_factory is None or self.agent_factory is None:
            raise RunDomainError(
                "EXECUTOR_FACTORY_MISSING",
                "Paired parallel execution requires environment and agent factories.",
                status_code=500,
            )

        ingestor = ResultIngestor(self.database)
        baseline_outcomes: list[dict[str, Any]] = []
        candidate_outcomes: list[dict[str, Any]] = []
        baseline_attempt: dict[str, str] | None = None
        candidate_attempt: dict[str, str] | None = None
        baseline_sink: Any = NullEventSink()
        candidate_sink: Any = NullEventSink()
        sibling_exception: BaseException | None = None
        episodes: list = []
        try:
            prepared = await PairedMaterializer(
                self.database,
                self.settings,
                task_factory=self.task_factory,
                env_factory=self.env_factory,
            ).materialize_run(run_id, token=token, events=events)
            if not prepared:
                raise RunDomainError(
                    "UNSUPPORTED_RUN_PLAN",
                    "Paired execution requires at least one prepared task.",
                    status_code=409,
                )
            prepared_by_key = {item.materialization_key: item for item in prepared}

            plan = self._load_plan(run_id)
            baseline_lane, candidate_lane = self._paired_lanes(run_id)
            lane_attempts = self._lane_attempts_by_key(run_id)
            baseline_attempt = lane_attempts[baseline_lane.lane_key]
            candidate_attempt = lane_attempts[candidate_lane.lane_key]

            episode_key_resolver = self._make_episode_key_resolver(run_id)
            baseline_sink = self._build_lane_sink(
                writer, run_id, baseline_lane, baseline_attempt, episode_key_resolver
            )
            candidate_sink = self._build_lane_sink(
                writer, run_id, candidate_lane, candidate_attempt, episode_key_resolver
            )
            episodes = plan.episodes
            baseline_agent = self.agent_factory(baseline_lane)
            candidate_agent = self.agent_factory(candidate_lane)

            # (4) Run BOTH lanes concurrently (Contract C). Wrap each lane so a
            # non-cancel exception cancels the token IMMEDIATELY (Contract 8),
            # letting the sibling lane observe the cancel via Controller.run's
            # per-step token check and drain cleanly. gather(return_exceptions=True)
            # then awaits both to completion.
            async def _lane_with_cancel(coro):
                try:
                    return await coro
                except RunCancelled:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    # Sibling failure: cancel the token so the OTHER lane drains.
                    token.cancel()
                    raise

            baseline_task_coro = _lane_with_cancel(
                self._run_lane_parallel(
                    run_id=run_id,
                    lane=baseline_lane,
                    lane_attempt=baseline_attempt,
                    prepared_by_key=prepared_by_key,
                    episodes=episodes,
                    token=token,
                    event_sink=baseline_sink,
                    agent=baseline_agent,
                )
            )
            candidate_task_coro = _lane_with_cancel(
                self._run_lane_parallel(
                    run_id=run_id,
                    lane=candidate_lane,
                    lane_attempt=candidate_attempt,
                    prepared_by_key=prepared_by_key,
                    episodes=episodes,
                    token=token,
                    event_sink=candidate_sink,
                    agent=candidate_agent,
                )
            )
            results = await asyncio.gather(
                baseline_task_coro, candidate_task_coro, return_exceptions=True
            )
            # Contract 8: surface the sibling exception (after both lanes
            # drained). The other lane was cancelled via the token and its env
            # closed in its finally block.
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, RunCancelled):
                    sibling_exception = result
                    token.cancel()
                    break
            if not isinstance(results[0], BaseException):
                baseline_outcomes = results[0]
            if not isinstance(results[1], BaseException):
                candidate_outcomes = results[1]
        except RunCancelled:
            token.cancel()

        cancelled = token.cancelled
        # P1 fix: sibling failure is NOT cancellation. It's an execution failure.
        # Lanes/run finalize to "failed", not "cancelled". The supervisor's
        # _mark_run_failed handles the terminal run.failed event.
        sibling_failed = sibling_exception is not None

        # Resolve lane attempts / episodes if materialization failed mid-way.
        if baseline_attempt is None or candidate_attempt is None:
            lane_attempts = self._lane_attempts_by_key(run_id)
            bl, cl = self._paired_lanes(run_id)
            baseline_attempt = lane_attempts[bl.lane_key]
            candidate_attempt = lane_attempts[cl.lane_key]
        episodes = episodes or self._load_plan(run_id).episodes

        # (5) Ingest per lane (reuse the serial paired helper).
        # On sibling failure, completed outcomes keep their real outcome (per-episode
        # cancel logic in _ingest_lane_outcomes handles CANCELLED stop_reason only).
        baseline_ingested = self._ingest_lane_outcomes(
            ingestor=ingestor,
            run_id=run_id,
            lane_attempt=baseline_attempt,
            outcomes=baseline_outcomes,
            event_sink=baseline_sink,
            cancelled=cancelled,
            expected_episodes=episodes,
        )
        candidate_ingested = self._ingest_lane_outcomes(
            ingestor=ingestor,
            run_id=run_id,
            lane_attempt=candidate_attempt,
            outcomes=candidate_outcomes,
            event_sink=candidate_sink,
            cancelled=cancelled,
            expected_episodes=episodes,
        )

        # (6) Per-lane finalize_lane_only.
        if sibling_failed:
            terminal_lane_state = "failed"
        elif cancelled:
            terminal_lane_state = "cancelled"
        else:
            terminal_lane_state = "completed"
        ingestor.finalize_lane_only(
            lane_attempt_id=baseline_attempt["id"],
            terminal_state=terminal_lane_state,
        )
        ingestor.finalize_lane_only(
            lane_attempt_id=candidate_attempt["id"],
            terminal_state=terminal_lane_state,
        )

        # (7) Pair join + classification + comparison (path-level integrity).
        if not cancelled and not sibling_failed:
            self._record_comparison(
                run_id=run_id,
                run_attempt_id=self._run_attempt_id(run_id),
                baseline_lane=baseline_lane,
                candidate_lane=candidate_lane,
                baseline_lane_id=baseline_attempt["lane_id"],
                candidate_lane_id=candidate_attempt["lane_id"],
                baseline_ingested=baseline_ingested,
                candidate_ingested=candidate_ingested,
                baseline_outcomes=baseline_outcomes,
                candidate_outcomes=candidate_outcomes,
            )

        # (8) finalize_run once after the comparison.
        ingestor.finalize_run(
            run_id=run_id,
            terminal_state=terminal_lane_state,
            cancelled=cancelled,
        )

        if sibling_exception is not None:
            # P1 fix: sibling failure → raise the exception. The supervisor's
            # _mark_run_failed handles runs/run_attempts/lane_attempts → 'failed'.
            # Do NOT call mark_run_cancelled — that would conflict with failed.
            raise sibling_exception
        if cancelled:
            ingestor.mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

    def _record_comparison(
        self,
        *,
        run_id: str,
        run_attempt_id: str,
        baseline_lane: PlannedLane,
        candidate_lane: PlannedLane,
        baseline_lane_id: str,
        candidate_lane_id: str,
        baseline_ingested: dict[str, dict[str, Any]],
        candidate_ingested: dict[str, dict[str, Any]],
        baseline_outcomes: list[dict[str, Any]],
        candidate_outcomes: list[dict[str, Any]],
    ) -> None:
        """Join pairs by pair_key, classify, and persist the comparison with
        PATH-LEVEL integrity (Contract 4). Overrides the serial paired executor
        to compute the path-level diff via compare_paired_states."""
        from test_platform.domain.pair_integrity import (
            compare_paired_states,
        )
        from test_platform.persistence.repositories import ComparisonRepository

        baseline_by_pair = {o["pair_key"]: o for o in baseline_outcomes}
        candidate_by_pair = {o["pair_key"]: o for o in candidate_outcomes}
        pair_keys = sorted(set(baseline_by_pair) | set(candidate_by_pair))

        repo = ComparisonRepository(self.database)
        comparison_id = repo.record_comparison(
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            baseline_lane_id=baseline_lane_id,
            candidate_lane_id=candidate_lane_id,
            policy=self._load_plan(run_id).comparison,
        )

        policy = self._initial_state_policy(run_id)
        baseline_apps = self._lane_apps(baseline_lane)
        candidate_apps = self._lane_apps(candidate_lane)

        summary_counts: dict[str, int] = {}
        for pair_key in pair_keys:
            b_out = baseline_by_pair.get(pair_key)
            c_out = candidate_by_pair.get(pair_key)
            b_attempt = baseline_ingested.get(pair_key)
            c_attempt = candidate_ingested.get(pair_key)

            # Contract 4: path-level integrity via compare_paired_states.
            # Reconstruct the raw states from the projected dicts is not
            # possible; compare_paired_states re-projects internally, so pass
            # the captured raw state is ideal. We captured projected dicts; to
            # reuse compare_paired_states we feed the projected dicts back as
            # "states" (they are already flat). compare_paired_states projects
            # again (idempotent on already-flat dicts: no nested structure).
            b_raw = (b_out or {}).get("raw_state")
            c_raw = (c_out or {}).get("raw_state")
            # P2.2: pass task_app_ids from the outcome so version-direction
            # tolerance only applies to the task's declared apps, not all
            # target metadata apps.
            pair_task_app_ids = (b_out or {}).get("task_app_ids") or (c_out or {}).get("task_app_ids")
            report = compare_paired_states(
                baseline_state=b_raw,
                candidate_state=c_raw,
                policy=policy,
                baseline_apps=baseline_apps,
                candidate_apps=candidate_apps,
                task_app_ids=pair_task_app_ids,
            )

            integrity = IntegrityStatus.OK
            integrity_reason = "OK"
            # Per-lane instruction/projection-hash mismatch (VS-09 semantics)
            # takes precedence — a lane that didn't honor its prepared fixture is
            # a pairing violation regardless of the path-level report.
            if b_out is not None and b_out["integrity_status"].is_violation:
                integrity = b_out["integrity_status"]
                integrity_reason = b_out.get("integrity_reason") or integrity.value
            elif c_out is not None and c_out["integrity_status"].is_violation:
                integrity = c_out["integrity_status"]
                integrity_reason = c_out.get("integrity_reason") or integrity.value
            elif report.is_violation:
                integrity = IntegrityStatus.PROJECTION_MISMATCH
                integrity_reason = "projection_mismatch"

            b_outcome = b_attempt["outcome"] if b_attempt else None
            c_outcome = c_attempt["outcome"] if c_attempt else None
            classification = classify_pair(b_outcome, c_outcome, integrity_status=integrity)

            integrity_json = {
                "status": integrity.value,
                "reason": integrity_reason,
                "prepared_projection_hash": (b_out or {}).get("prepared").projection_hash
                if (b_out or {}).get("prepared")
                else None,
                "baseline_actual_projection_hash": (b_out or {}).get("actual_projection_hash"),
                "candidate_actual_projection_hash": (c_out or {}).get("actual_projection_hash"),
                "path_diffs": [d.to_dict() for d in report.path_diffs],
            }
            delta_json = {
                "baseline_outcome": b_outcome,
                "candidate_outcome": c_outcome,
            }
            repo.record_pair(
                comparison_id=comparison_id,
                pair_key=pair_key,
                classification=classification.value,
                baseline_episode_attempt_id=(
                    b_attempt["id"] if b_attempt else None
                ),
                candidate_episode_attempt_id=(
                    c_attempt["id"] if c_attempt else None
                ),
                integrity=integrity_json,
                delta=delta_json,
            )
            summary_counts[classification.value] = (
                summary_counts.get(classification.value, 0) + 1
            )

        self.database.connection.execute(
            "UPDATE comparisons SET summary_json = ? WHERE id = ?",
            (canonical_json(summary_counts), comparison_id),
        )
        self.database.connection.commit()


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
    if isinstance(runner_config.get("viewport_size"), list):
        runner_config["viewport_size"] = tuple(runner_config["viewport_size"])
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


def _episode_artifact_name(episode_key: str) -> str:
    """Stable, filesystem-safe artifact segment for one episode_key.

    Sanitizes the key into a readable prefix, then appends an 8-char hash suffix
    derived from the ORIGINAL key. The hash guarantees uniqueness even when two
    distinct keys sanitize to the same string (e.g. ``a|b`` and ``a/b`` both
    collapse to ``a_b``)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", episode_key).strip("._")
    digest = canonical_sha256(episode_key).split(":", 1)[1][:8]
    # readable prefix (max 64 chars) + hash suffix guarantees uniqueness even
    # when two keys sanitize to the same string (e.g. "a|b" and "a/b").
    prefix = cleaned[:64].rstrip("._") or "episode"
    return f"{prefix}_{digest}"


def _episode_artifact_root(
    lane_artifact_root: str,
    result: Any,
    *,
    repeat_n: int,
    episode_key: str | None = None,
) -> str:
    if episode_key is not None:
        return f"{lane_artifact_root}/trajectory/{_episode_artifact_name(episode_key)}"
    result_dict = _result_to_dict(result)
    task_id = str(result_dict.get("id") or getattr(result, "task_id", "episode"))
    task_id_safe = task_id.replace(".", "_").replace("/", "_").replace(" ", "_")
    trial_id = int(result_dict.get("trial_id") or getattr(result, "trial_id", 0) or 0)
    suffix = f"_t{trial_id}" if repeat_n > 1 else ""
    return f"{lane_artifact_root}/trajectory/{task_id_safe}{suffix}"


def _result_is_cancelled(result: Any) -> bool:
    """Detect whether a single result was cancelled (per-episode, not run-level).

    A completed PASS/FAIL result is NOT cancelled even if the run was later
    cancelled — only results whose execution.stop_reason is CANCELLED count.
    """
    if isinstance(result, dict):
        return (result.get("execution") or {}).get("stop_reason") == "CANCELLED"
    exec_r = getattr(result, "execution", None)
    return exec_r is not None and getattr(exec_r, "stop_reason", None) == "CANCELLED"


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
