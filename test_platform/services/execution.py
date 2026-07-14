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
from bench_env.runner.base import BaseRunner, Controller
from bench_env.metrics import result_is_error, result_is_success
from bench_env.config import RunnerConfig
from bench_env.env.recorder import RunRecorder, episode_key_artifact_name
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
from test_platform.domain.versioned_documents import read_run_plan
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
        templates = self._unique_templates(
            _selected_episodes_for_attempt(self.database, run_id, plan)
        )
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
        return read_run_plan(json.loads(row["run_plan_json"]))

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

        For batch execution many episodes share a lane; finalizing the lane
        per-episode would complete the whole run on the first episode.
        ``LaneOutcomeCommitter`` reuses the uncommitted implementation for each
        interpreted result inside its one batch transaction, then finalizes the
        lane and run in that same transaction.

        ``error_code_override`` (when provided) replaces the derived error_code
        — used by ``LaneOutcomeCommitter`` to label missing/crashed episodes
        (e.g. WORKER_CRASH) without altering the outcome derivation.
        """
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection = self.database.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                summary = self._ingest_episode_attempt_uncommitted(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt_id,
                    episode_key=episode_key,
                    result=result,
                    artifact_root=artifact_root,
                    cancelled=cancelled,
                    error_code_override=error_code_override,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return summary

    def _ingest_episode_attempt_uncommitted(
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

        normalized = _result_to_dict(result)
        outcome = _result_outcome(result, cancelled=cancelled)
        error_code = (
            error_code_override
            if error_code_override is not None
            else _error_code_for_outcome(outcome)
        )
        terminal_state = "cancelled" if cancelled else "completed"
        now = _utc_timestamp()
        attempt_id = new_id()
        attempt_no = self._next_attempt_no(str(episode_row["id"]), lane_attempt_id)
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
        with self.database._lock:  # noqa: SLF100 — one lane/run transaction
            connection = self.database.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._finalize_lane_only_uncommitted(
                    lane_attempt_id=lane_attempt_id,
                    terminal_state=terminal_state,
                )
                self._finalize_run_uncommitted(
                    run_id=run_id,
                    terminal_state=terminal_state,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

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
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection = self.database.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._finalize_lane_only_uncommitted(
                    lane_attempt_id=lane_attempt_id,
                    terminal_state=terminal_state,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _finalize_lane_only_uncommitted(
        self,
        *,
        lane_attempt_id: str,
        terminal_state: str,
    ) -> None:
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
        with self.database._lock:  # noqa: SLF100 — serialize against EventWriter/cancel
            connection = self.database.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._finalize_run_uncommitted(
                    run_id=run_id,
                    terminal_state=terminal_state,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _finalize_run_uncommitted(
        self,
        *,
        run_id: str,
        terminal_state: str,
    ) -> None:
        connection = self.database.connection
        now = _utc_timestamp()
        cancellable = "('queued','preparing','running','evaluating','reporting')"
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

        This remains for legacy one-result callers. Batch executors MUST use
        ``LaneOutcomeCommitter`` so every planned episode is interpreted before
        one finalization at the end.
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


@dataclass(frozen=True)
class LaneExpectedEpisode:
    episode_key: str
    task_id: str | None
    trial_id: int


@dataclass(frozen=True)
class LaneObservedResult:
    episode_key: str | None
    result: Any
    error_code_override: str | None = None
    terminal_event: ExecutionEvent | None = None


@dataclass(frozen=True)
class LaneOutcomeBatch:
    run_id: str
    lane_attempt_id: str
    lane_artifact_root: str
    expected: tuple[LaneExpectedEpisode, ...]
    observed: tuple[LaneObservedResult, ...]
    cancelled: bool
    repeat_n: int = 1
    terminal_state: str | None = None
    run_terminal_state: str | None = None
    finalize_run: bool = True


class LaneOutcomeCommitter:
    """Canonical reconciliation and durable commit for one single-lane batch."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._ingestor = ResultIngestor(database)

    def commit(
        self,
        batch: LaneOutcomeBatch,
        *,
        events: EventSink | None = None,
    ) -> tuple[dict[str, Any], ...]:
        observed_by_key = self._validate_and_index(batch)
        committed: list[dict[str, Any]] = []
        committed_events: list[ExecutionEvent] = []
        connection = self.database.connection

        with self.database._lock:  # noqa: SLF100 - one atomic lane outcome commit
            connection.execute("BEGIN IMMEDIATE")
            try:
                for expected in batch.expected:
                    observation = observed_by_key.get(expected.episode_key)
                    result = observation.result if observation is not None else None
                    artifact_root = _episode_artifact_root(
                        batch.lane_artifact_root,
                        result,
                        repeat_n=batch.repeat_n,
                        episode_key=expected.episode_key,
                    )
                    if observation is not None:
                        committed.append(
                            self._ingestor._ingest_episode_attempt_uncommitted(
                                run_id=batch.run_id,
                                lane_attempt_id=batch.lane_attempt_id,
                                episode_key=expected.episode_key,
                                result=result,
                                artifact_root=artifact_root,
                                cancelled=_result_is_cancelled(result),
                                error_code_override=observation.error_code_override,
                            )
                        )
                        if observation.terminal_event is not None:
                            committed_events.append(observation.terminal_event)
                        continue

                    synthetic, outcome, error_code, event_type = (
                        self._missing_interpretation(
                            expected,
                            cancelled=batch.cancelled,
                        )
                    )
                    committed.append(
                        self._ingestor._ingest_episode_attempt_uncommitted(
                            run_id=batch.run_id,
                            lane_attempt_id=batch.lane_attempt_id,
                            episode_key=expected.episode_key,
                            result=synthetic,
                            artifact_root=artifact_root,
                            cancelled=batch.cancelled,
                            error_code_override=error_code,
                        )
                    )
                    committed_events.append(
                        self._missing_event(
                            expected=expected,
                            outcome=outcome,
                            error_code=error_code,
                            event_type=event_type,
                        )
                    )

                terminal_state = batch.terminal_state or (
                    "cancelled" if batch.cancelled else "completed"
                )
                self._ingestor._finalize_lane_only_uncommitted(
                    lane_attempt_id=batch.lane_attempt_id,
                    terminal_state=terminal_state,
                )
                if batch.finalize_run:
                    self._ingestor._finalize_run_uncommitted(
                        run_id=batch.run_id,
                        terminal_state=batch.run_terminal_state or terminal_state,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        self._emit_committed(events, committed_events)
        return tuple(committed)

    @staticmethod
    def _validate_and_index(
        batch: LaneOutcomeBatch,
    ) -> dict[str, LaneObservedResult]:
        expected_keys = [item.episode_key for item in batch.expected]
        expected_set = set(expected_keys)
        if len(expected_keys) != len(expected_set):
            raise RunDomainError(
                "EPISODE_RESULT_UNKNOWN",
                "The run plan contains duplicate episode keys.",
                status_code=500,
                details=[{"run_id": batch.run_id, "duplicate_plan_keys": True}],
            )

        observed_by_key: dict[str, LaneObservedResult] = {}
        unknown_keys: list[str] = []
        for observation in batch.observed:
            key = observation.episode_key
            if key is None:
                unknown_keys.append("<missing episode_key>")
                continue
            if key not in expected_set:
                unknown_keys.append(key)
                continue
            if key in observed_by_key:
                raise RunDomainError(
                    "EPISODE_RESULT_UNKNOWN",
                    "The runner returned duplicate results for one episode.",
                    status_code=500,
                    details=[
                        {
                            "run_id": batch.run_id,
                            "episode_key": key,
                            "duplicate": True,
                        }
                    ],
                )
            observed_by_key[key] = observation
        if unknown_keys:
            raise RunDomainError(
                "EPISODE_RESULT_UNKNOWN",
                "The runner returned results for unknown episodes.",
                status_code=500,
                details=[
                    {"run_id": batch.run_id, "unknown_episode_keys": unknown_keys}
                ],
            )
        return observed_by_key

    @staticmethod
    def _missing_interpretation(
        expected: LaneExpectedEpisode,
        *,
        cancelled: bool,
    ) -> tuple[dict[str, Any], str, str, str]:
        if cancelled:
            return (
                {
                    "id": expected.task_id or "unknown",
                    "trial_id": expected.trial_id,
                    "is_success": False,
                    "is_error": False,
                    "execution": {"error": None, "stop_reason": "CANCELLED"},
                },
                "CANCELLED",
                "CANCELLED",
                "episode.cancelled",
            )
        return (
            {
                "id": expected.task_id or "unknown",
                "trial_id": expected.trial_id,
                "is_success": False,
                "is_error": True,
                "execution": {
                    "error": "Worker exited without reporting a result (WORKER_CRASH).",
                    "stop_reason": "ERROR",
                },
            },
            "ERROR",
            "WORKER_CRASH",
            "episode.error",
        )

    @staticmethod
    def _missing_event(
        *,
        expected: LaneExpectedEpisode,
        outcome: str,
        error_code: str,
        event_type: str,
    ) -> ExecutionEvent:
        return ExecutionEvent(
            type=event_type,
            timestamp="",
            phase="execute",
            task_id=expected.task_id,
            trial_id=expected.trial_id,
            episode_key=expected.episode_key,
            payload={
                "outcome": outcome,
                "error_code": error_code,
                "steps": 0,
                "reason": "missing_result",
            },
        )

    @staticmethod
    def _emit_committed(
        events: EventSink | None,
        committed_events: list[ExecutionEvent],
    ) -> None:
        if events is None:
            return
        for event in committed_events:
            try:
                events.emit(event)
            except Exception:  # noqa: BLE001 - event failure must not break ingestion
                pass


def _expected_lane_episodes(
    work_items: list[PreparedWorkItem],
) -> tuple[LaneExpectedEpisode, ...]:
    return tuple(
        LaneExpectedEpisode(
            episode_key=item.episode_key,
            task_id=getattr(item.task, "id", None),
            trial_id=item.trial_id,
        )
        for item in work_items
    )


def _serial_observations(
    expected: tuple[LaneExpectedEpisode, ...],
    results: list[Any],
) -> tuple[LaneObservedResult, ...]:
    return tuple(
        LaneObservedResult(
            episode_key=(
                expected[index].episode_key
                if index < len(expected)
                else getattr(result, "episode_key", None)
            ),
            result=result,
        )
        for index, result in enumerate(results)
    )


def _object_observations(results: list[Any]) -> tuple[LaneObservedResult, ...]:
    return tuple(
        LaneObservedResult(
            episode_key=getattr(result, "episode_key", None),
            result=result,
        )
        for result in results
    )


def _dict_observations(
    results: list[dict[str, Any]],
) -> tuple[LaneObservedResult, ...]:
    return tuple(
        LaneObservedResult(episode_key=result.get("episode_key"), result=result)
        for result in results
    )


def _current_run_attempt_id(database: Database, run_id: str) -> str:
    row = database.connection.execute(
        """
        SELECT id
        FROM run_attempts
        WHERE run_id = ?
        ORDER BY attempt_no DESC, id DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise RunDomainError(
            "RUN_ATTEMPT_NOT_FOUND",
            "Run attempt was not found.",
            status_code=500,
            details=[{"run_id": run_id}],
        )
    return str(row["id"])


def _selected_episodes_for_attempt(
    database: Database,
    run_id: str,
    plan: RunPlan,
    *,
    lane_key: str | None = None,
) -> list[EpisodeTemplate]:
    run_attempt_id = _current_run_attempt_id(database, run_id)
    lane_filter = ""
    params: tuple[Any, ...]
    if lane_key is None:
        params = (run_attempt_id,)
    else:
        lane_filter = " AND l.lane_key = ?"
        params = (run_attempt_id, lane_key)
    rows = database.connection.execute(
        f"""
        SELECT e.episode_key
        FROM run_attempt_episode_selection AS s
        JOIN episodes AS e ON e.id = s.episode_id
        JOIN lanes AS l ON l.id = s.lane_id
        WHERE s.run_attempt_id = ?{lane_filter}
        ORDER BY e.episode_key
        """,
        params,
    ).fetchall()
    if not rows:
        return list(plan.episodes)
    selected_keys = {str(row["episode_key"]) for row in rows}
    return [episode for episode in plan.episodes if episode.episode_key in selected_keys]


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
        return read_run_plan(json.loads(row["run_plan_json"]))

    def _lane_attempt(self, run_id: str) -> dict[str, str]:
        run_attempt_id = _current_run_attempt_id(self.database, run_id)
        row = self.database.connection.execute(
            """
            SELECT la.id, la.artifact_root
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE l.run_id = ? AND la.run_attempt_id = ?
            ORDER BY l.lane_key
            LIMIT 1
            """,
            (run_id, run_attempt_id),
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
        run_attempt_id = _current_run_attempt_id(self.database, run_id)
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
                    WHERE id = ?
                    """,
                    (now, run_attempt_id),
                )
                connection.execute(
                    """
                    UPDATE lane_attempts
                    SET state = 'running',
                        started_at = COALESCE(started_at, ?)
                    WHERE run_attempt_id = ?
                    """,
                    (now, run_attempt_id),
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
        if not prepared:
            raise RunDomainError(
                "UNSUPPORTED_RUN_PLAN",
                "Serial execution requires at least one prepared task.",
                status_code=409,
            )

        plan = self._load_plan(run_id)
        lane = _single_lane(plan)
        lane_attempt = self._lane_attempt(run_id)
        prepared_by_key = {item.materialization_key: item for item in prepared}
        work_items: list[PreparedWorkItem] = []
        for episode in _selected_episodes_for_attempt(self.database, run_id, plan):
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
                "Serial execution requires at least one episode.",
                status_code=409,
            )

        if self.env_factory is None or self.agent_factory is None:
            raise RunDomainError(
                "EXECUTOR_FACTORY_MISSING",
                "Serial execution requires environment and agent factories.",
                status_code=500,
            )
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
            if _is_manual_sequence_plan(plan):
                results = await self._run_isolated_work_items(
                    lane=lane,
                    agent=agent,
                    work_items=work_items,
                    config=config,
                    recorder=recorder,
                    token=token,
                    events=events,
                )
            else:
                env = self.env_factory(lane)
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
        expected = _expected_lane_episodes(work_items)
        LaneOutcomeCommitter(self.database).commit(
            LaneOutcomeBatch(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                lane_artifact_root=lane_attempt["artifact_root"],
                expected=expected,
                observed=_serial_observations(expected, results),
                cancelled=cancelled,
                repeat_n=config.repeat_n,
                run_terminal_state="cancelled" if cancelled else "evaluating",
            ),
            events=events,
        )

        if cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
            # Re-raise so the supervisor emits run.cancelled (not run.completed).
            # The run/lane state is already terminal here; the supervisor's
            # _mark_run_cancelled is idempotent.
            raise RunCancelled()
        # NOTE: run.* terminal events (run.completed / run.cancelled) are owned
        # by the supervisor's _execute wrapper to avoid duplicate emits. The
        # executor only raises RunCancelled; the supervisor finalizes + emits.
        return RunRepository(self.database).get(run_id)

    async def _run_isolated_work_items(
        self,
        *,
        lane: PlannedLane,
        agent: Any,
        work_items: list[PreparedWorkItem],
        config: RunnerConfig,
        recorder: RunRecorder,
        token: CancellationToken,
        events: EventSink,
    ) -> list[Any]:
        results: list[Any] = []
        try:
            for work_item in work_items:
                token.raise_if_cancelled()
                env = self.env_factory(lane)
                try:
                    result = await BaseRunner.run_episode(
                        env,
                        agent,
                        work_item.task,
                        work_item.max_steps,
                        recorder,
                        trial_id=work_item.trial_id,
                        loop_threshold=config.loop_detect,
                        cancellation_token=token,
                        event_sink=events,
                        worker_id="serial",
                        episode_key=work_item.episode_key,
                    )
                    results.append(result)
                finally:
                    close = getattr(env, "close", None)
                    if callable(close):
                        await _maybe_await(close())
        except RunCancelled:
            token.cancel()
        finally:
            recorder.finish_run(
                repeat_n=config.repeat_n,
                pass_k=config.pass_k,
            )
        return results


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
        for episode in _selected_episodes_for_attempt(self.database, run_id, plan):
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
        expected = _expected_lane_episodes(work_items)
        LaneOutcomeCommitter(self.database).commit(
            LaneOutcomeBatch(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                lane_artifact_root=lane_attempt["artifact_root"],
                expected=expected,
                observed=_object_observations(results),
                cancelled=token.cancelled,
                run_terminal_state=(
                    "cancelled" if token.cancelled else "evaluating"
                ),
            ),
            events=events,
        )

        # (10) If cancelled, mark the run cancelled and re-raise so the
        # supervisor emits run.cancelled.
        if token.cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

class MultiprocessRunExecutor(_RunExecutorBase):
    """Execute all episodes of a single lane across N processes (shards).

    VS-08: mirrors ParallelRunExecutor's lifecycle (queued-cancel check → mark
    running → emit run.started → materialize → run → reconcile → finalize) but
    shards the prepared work across ``MultiProcessRunner``. Each shard runs a
    ``ParallelRunner`` (in-process for tests via ``child_runner_factory``; real
    spawn for production).

    Dictionary results are adapted into the shared ``LaneOutcomeCommitter`` so
    behavior is identical to the serial and parallel single-lane executors:
    missing → WORKER_CRASH; unknown/duplicate → hard error.

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
        for episode in _selected_episodes_for_attempt(self.database, run_id, plan):
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
        # shards correctly, and keep generated shard artifacts under the
        # platform run root instead of bench_env's default runs/ directory.
        config = dataclasses_replace(
            config,
            processes=processes,
            parallel=parallel,
            runs_dir=self.settings.runs_dir / run_id / lane_attempt["artifact_root"],
        )

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
        LaneOutcomeCommitter(self.database).commit(
            LaneOutcomeBatch(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                lane_artifact_root=lane_attempt["artifact_root"],
                expected=_expected_lane_episodes(work_items),
                observed=_dict_observations(result_dicts),
                cancelled=token.cancelled,
                run_terminal_state=(
                    "cancelled" if token.cancelled else "evaluating"
                ),
            ),
            events=events,
        )

        # (8) If cancelled, mark the run cancelled and re-raise.
        if token.cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

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
        run_attempt_id = _current_run_attempt_id(self.database, run_id)
        rows = self.database.connection.execute(
            """
            SELECT l.lane_key, la.id, la.artifact_root, l.id AS lane_id,
                   la.run_attempt_id
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE l.run_id = ? AND la.run_attempt_id = ?
            ORDER BY l.lane_key
            """,
            (run_id, run_attempt_id),
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
        baseline_attempt: dict[str, str] | None = None
        candidate_attempt: dict[str, str] | None = None
        baseline_sink: Any = NullEventSink()
        candidate_sink: Any = NullEventSink()
        lane_exception: BaseException | None = None
        failed_lane_key: str | None = None
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

            episodes = _selected_episodes_for_attempt(self.database, run_id, plan)

            # (4) Run baseline lane, then candidate lane (serial).
            try:
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
            except BaseException as exc:  # noqa: BLE001 - persist lane facts first
                lane_exception = exc
                failed_lane_key = baseline_lane.lane_key
                token.cancel()

            if lane_exception is None:
                try:
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
                except BaseException as exc:  # noqa: BLE001 - persist lane facts first
                    lane_exception = exc
                    failed_lane_key = candidate_lane.lane_key
                    token.cancel()
        except RunCancelled:
            token.cancel()
            cancelled = True
            # P1 fix: PRESERVE already-collected outcomes — do NOT clear them.
            # Earlier completed episodes are valid results; only the episodes
            # that never ran (or were mid-cancel) are missing. Those are
            # synthesized as CANCELLED below by LaneOutcomeCommitter via the
            # cancelled=True path + missing-episode reconciliation.

        # P1 fix: _run_lane now catches RunCancelled internally and returns
        # partial outcomes (does NOT propagate). Detect cancellation via
        # token.cancelled — covers cancel during materialize, between episodes,
        # or mid-step (Controller.run returns CANCELLED result, token is set).
        if token.cancelled and lane_exception is None:
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

        self._commit_paired_terminal_facts(
            run_id=run_id,
            writer=writer,
            baseline_outcomes=baseline_outcomes,
            candidate_outcomes=candidate_outcomes,
            episodes=episodes if "episodes" in locals() else [],
            cancelled=cancelled,
            failed_lane_key=failed_lane_key,
        )

        if lane_exception is not None:
            raise lane_exception
        if cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
            raise RunCancelled()
        return RunRepository(self.database).get(run_id)

    def _run_attempt_id(self, run_id: str) -> str:
        return _current_run_attempt_id(self.database, run_id)

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

    def _commit_paired_terminal_facts(
        self,
        *,
        run_id: str,
        writer: Any,
        baseline_outcomes: list[dict[str, Any]],
        candidate_outcomes: list[dict[str, Any]],
        episodes: list[Any],
        cancelled: bool,
        failed_lane_key: str | None,
    ) -> None:
        """Commit both lane grids, comparison, then the run in canonical order."""
        baseline_lane, candidate_lane = self._paired_lanes(run_id)
        lane_attempts = self._lane_attempts_by_key(run_id)
        baseline_attempt = lane_attempts[baseline_lane.lane_key]
        candidate_attempt = lane_attempts[candidate_lane.lane_key]
        if not episodes:
            plan = self._load_plan(run_id)
            episodes = _selected_episodes_for_attempt(self.database, run_id, plan)

        episode_key_resolver = self._make_episode_key_resolver(run_id)
        baseline_sink = self._build_lane_sink(
            writer,
            run_id,
            baseline_lane,
            baseline_attempt,
            episode_key_resolver,
        )
        candidate_sink = self._build_lane_sink(
            writer,
            run_id,
            candidate_lane,
            candidate_attempt,
            episode_key_resolver,
        )

        execution_failed = failed_lane_key is not None
        baseline_missing_cancelled = cancelled or (
            execution_failed and failed_lane_key != baseline_lane.lane_key
        )
        candidate_missing_cancelled = cancelled or (
            execution_failed and failed_lane_key != candidate_lane.lane_key
        )
        baseline_observed_complete = (
            len({outcome["episode_key"] for outcome in baseline_outcomes})
            == len(episodes)
            and all(
                not _result_is_cancelled(outcome["result"])
                for outcome in baseline_outcomes
            )
        )
        candidate_observed_complete = (
            len({outcome["episode_key"] for outcome in candidate_outcomes})
            == len(episodes)
            and all(
                not _result_is_cancelled(outcome["result"])
                for outcome in candidate_outcomes
            )
        )
        baseline_terminal_state = (
            "failed"
            if failed_lane_key == baseline_lane.lane_key
            else "cancelled"
            if baseline_missing_cancelled and not baseline_observed_complete
            else "completed"
        )
        candidate_terminal_state = (
            "failed"
            if failed_lane_key == candidate_lane.lane_key
            else "cancelled"
            if candidate_missing_cancelled and not candidate_observed_complete
            else "completed"
        )
        run_terminal_state = (
            "failed" if execution_failed else "cancelled" if cancelled else "evaluating"
        )

        baseline_ingested = self._commit_lane_outcomes(
            run_id=run_id,
            lane_attempt=baseline_attempt,
            outcomes=baseline_outcomes,
            event_sink=baseline_sink,
            missing_cancelled=baseline_missing_cancelled,
            terminal_state=baseline_terminal_state,
            expected_episodes=episodes,
        )
        candidate_ingested = self._commit_lane_outcomes(
            run_id=run_id,
            lane_attempt=candidate_attempt,
            outcomes=candidate_outcomes,
            event_sink=candidate_sink,
            missing_cancelled=candidate_missing_cancelled,
            terminal_state=candidate_terminal_state,
            expected_episodes=episodes,
        )

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

        ResultIngestor(self.database).finalize_run(
            run_id=run_id,
            terminal_state=run_terminal_state,
            cancelled=cancelled,
        )

    def _commit_lane_outcomes(
        self,
        *,
        run_id: str,
        lane_attempt: dict[str, str],
        outcomes: list[dict[str, Any]],
        event_sink: EventSink,
        missing_cancelled: bool,
        terminal_state: str,
        expected_episodes: list[Any],
    ) -> dict[str, dict[str, Any]]:
        """Adapt one paired lane into the canonical outcome commit interface."""
        observed: list[LaneObservedResult] = []
        for outcome in outcomes:
            is_violation = outcome["integrity_status"].is_violation
            terminal_event = None
            if is_violation:
                result_dict = _result_to_dict(outcome["result"])
                terminal_event = ExecutionEvent(
                    type="episode.error",
                    timestamp="",
                    phase="execute",
                    task_id=str(result_dict.get("id") or "unknown"),
                    trial_id=outcome.get("trial_id", 0),
                    episode_key=outcome["episode_key"],
                    payload={
                        "outcome": "ERROR",
                        "error_code": "PAIRING_VIOLATION",
                        "stop_reason": "PAIRING_VIOLATION",
                        "reason": outcome.get("integrity_reason"),
                    },
                )
            observed.append(
                LaneObservedResult(
                    episode_key=outcome["episode_key"],
                    result=outcome["result"],
                    error_code_override=(
                        "PAIRING_VIOLATION" if is_violation else None
                    ),
                    terminal_event=terminal_event,
                )
            )

        committed = LaneOutcomeCommitter(self.database).commit(
            LaneOutcomeBatch(
                run_id=run_id,
                lane_attempt_id=lane_attempt["id"],
                lane_artifact_root=lane_attempt["artifact_root"],
                expected=tuple(
                    LaneExpectedEpisode(
                        episode_key=episode.episode_key,
                        task_id=episode.task_base_id,
                        trial_id=episode.trial_id,
                    )
                    for episode in expected_episodes
                ),
                observed=tuple(observed),
                cancelled=missing_cancelled,
                terminal_state=terminal_state,
                finalize_run=False,
            ),
            events=event_sink,
        )
        return {
            episode.pair_key: summary
            for episode, summary in zip(expected_episodes, committed, strict=True)
        }

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
        pair_keys = sorted(
            set(baseline_ingested)
            | set(candidate_ingested)
            | set(baseline_by_pair)
            | set(candidate_by_pair)
        )

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
                        "task_app_ids": set(getattr(task, "apps", []) or []),
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

        baseline_outcomes: list[dict[str, Any]] = []
        candidate_outcomes: list[dict[str, Any]] = []
        baseline_attempt: dict[str, str] | None = None
        candidate_attempt: dict[str, str] | None = None
        baseline_sink: Any = NullEventSink()
        candidate_sink: Any = NullEventSink()
        sibling_exception: BaseException | None = None
        failed_lane_key: str | None = None
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
            episodes = _selected_episodes_for_attempt(self.database, run_id, plan)
            try:
                baseline_agent = self.agent_factory(baseline_lane)
            except RunCancelled:
                raise
            except BaseException as exc:  # noqa: BLE001 - persist lane facts first
                sibling_exception = exc
                failed_lane_key = baseline_lane.lane_key
                token.cancel()

            if sibling_exception is None:
                try:
                    candidate_agent = self.agent_factory(candidate_lane)
                except RunCancelled:
                    raise
                except BaseException as exc:  # noqa: BLE001 - persist lane facts first
                    sibling_exception = exc
                    failed_lane_key = candidate_lane.lane_key
                    token.cancel()

            if sibling_exception is None:
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
                for index, result in enumerate(results):
                    if isinstance(result, BaseException) and not isinstance(result, RunCancelled):
                        sibling_exception = result
                        failed_lane_key = (
                            baseline_lane.lane_key
                            if index == 0
                            else candidate_lane.lane_key
                        )
                        token.cancel()
                        break
                if not isinstance(results[0], BaseException):
                    baseline_outcomes = results[0]
                if not isinstance(results[1], BaseException):
                    candidate_outcomes = results[1]
        except RunCancelled:
            token.cancel()

        # P1 fix: sibling failure is NOT cancellation. It's an execution failure.
        # Lanes/run finalize to "failed", not "cancelled". The supervisor's
        # _mark_run_failed handles the terminal run.failed event.
        sibling_failed = sibling_exception is not None
        cancelled = token.cancelled and not sibling_failed

        self._commit_paired_terminal_facts(
            run_id=run_id,
            writer=writer,
            baseline_outcomes=baseline_outcomes,
            candidate_outcomes=candidate_outcomes,
            episodes=episodes,
            cancelled=cancelled,
            failed_lane_key=failed_lane_key,
        )

        if sibling_exception is not None:
            # P1 fix: sibling failure → raise the exception. The supervisor's
            # _mark_run_failed handles runs/run_attempts/lane_attempts → 'failed'.
            # Do NOT call mark_run_cancelled — that would conflict with failed.
            raise sibling_exception
        if cancelled:
            ResultIngestor(self.database).mark_run_cancelled(run_id)
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
        pair_keys = sorted(
            set(baseline_ingested)
            | set(candidate_ingested)
            | set(baseline_by_pair)
            | set(candidate_by_pair)
        )

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
            report = (
                compare_paired_states(
                    baseline_state=b_raw,
                    candidate_state=c_raw,
                    policy=policy,
                    baseline_apps=baseline_apps,
                    candidate_apps=candidate_apps,
                    task_app_ids=pair_task_app_ids,
                )
                if b_out is not None and c_out is not None
                else None
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
            elif report is not None and report.is_violation:
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
                "path_diffs": (
                    [d.to_dict() for d in report.path_diffs]
                    if report is not None
                    else []
                ),
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


def _is_manual_sequence_plan(plan: RunPlan) -> bool:
    return any(
        episode.sequence_group_id == "manual_sequence"
        for episode in plan.episodes
    )


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
    return episode_key_artifact_name(episode_key)


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
