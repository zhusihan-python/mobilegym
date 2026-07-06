from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Protocol

from test_platform.config import PlatformSettings
from test_platform.domain.canonical_json import canonical_json, canonical_sha256
from test_platform.domain.ids import new_id
from test_platform.domain.run_plans import RunPlan, RunPlanCompiler
from test_platform.domain.runs import RunDomainError, RunIdempotencyConflict, RunDetail
from test_platform.domain.retry_resume import (
    select_resume_lane_episodes,
    select_retry_lane_episodes,
)
from test_platform.domain.task_catalog import TaskCatalogSnapshot, build_task_catalog_snapshot
from test_platform.domain.workflows import WorkflowDefinition, WorkflowDomainError
from test_platform.execution.event_writer import EventWriter
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    RunRepository,
    TargetRepository,
    WorkflowRepository,
)

logger = logging.getLogger(__name__)

# Run lifecycle states in which a cancel request is meaningful. Terminal states
# (completed/failed/cancelled) ignore cancel and are reported as a no-op.
_CANCELLABLE_RUN_STATES = frozenset(
    {"queued", "preparing", "running", "evaluating", "reporting"}
)


class FakeRunSupervisor:
    def __init__(self) -> None:
        self._queued_run_ids: list[str] = []

    def submit(self, run_id: str) -> None:
        if run_id not in self._queued_run_ids:
            self._queued_run_ids.append(run_id)

    def request_cancel(self, run_id: str) -> bool:  # pragma: no cover - trivial
        return False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def snapshot(self) -> dict[str, list[str]]:
        return {"queued_run_ids": list(self._queued_run_ids)}


class _Executor(Protocol):
    async def execute_run(self, run_id: str, *, token: Any, events: Any) -> RunDetail: ...


class RunSupervisor:
    """Owns run execution tasks and per-run cancellation tokens.

    Runs.py routes are synchronous (FastAPI runs them in a threadpool), so
    `submit()` and `request_cancel()` are synchronous and thread-safe:
      * submit() registers the token in the current thread BEFORE scheduling the
        task, guaranteeing an immediate cancel right after create_run returns can
        find the token.
      * request_cancel() performs its DB writes synchronously before returning,
        so the cancel flag is durable by the time the HTTP response is sent.
    The actual asyncio task is scheduled onto the loop captured in start().
    """

    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        executor: _Executor | None = None,
        executor_resolver: Callable[[Any], _Executor] | None = None,
        broker: SSEBroker | None = None,
        clock: Callable[[], str] | None = None,
        token_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        # VS-07: the supervisor resolves an executor PER LANE so a parallel lane
        # gets a ParallelRunExecutor and a serial lane gets a SerialRunExecutor.
        # ``executor`` remains as a deprecated convenience: if passed, it is
        # wrapped in a constant resolver (used by every lane). Production wiring
        # passes ``executor_resolver``; tests inject either.
        if executor_resolver is not None:
            self._executor_resolver = executor_resolver
        elif executor is not None:
            self._executor_resolver = lambda lane: executor  # noqa: E731
        else:
            # No executor supplied: default to the production lane-aware resolver
            # (serial unless the lane opts into parallelism).
            self._executor_resolver = _default_executor_resolver(database, settings)
        self._executor = executor  # retained for backward-compat introspection
        self._broker = broker or SSEBroker()
        self._event_writer = EventWriter(database, self._broker)
        self._tokens: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._now = clock or _utc_timestamp
        # VS-08: token_factory creates a FRESH cancellation token per run.
        # Default is a plain CancellationToken (cooperative-only). Multiprocess
        # runs inject a factory that builds MultiprocCancelToken (wrapping an
        # mp.Event) so cancel propagates to spawned children.
        self._token_factory = token_factory

    def bind_broker(self, broker: SSEBroker) -> None:
        self._broker = broker
        self._event_writer = EventWriter(self._database, broker)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._broker.bind_loop(self._loop)

    async def stop(self) -> None:
        # Cancel any in-flight tasks and wait for them to wind down.
        for run_id, task in list(self._tasks.items()):
            if not task.done():
                token = self._tokens.get(run_id)
                if token is not None:
                    token.cancel()
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    def submit(self, run_id: str) -> None:
        from bench_env.runner.cancellation import CancellationToken

        # VS-08: use the injected token_factory when present (multiprocess runs
        # inject a factory that creates MultiprocCancelToken sharing an mp.Event).
        # Default: a plain CancellationToken (cooperative-only fallback).
        if self._token_factory is not None:
            token = self._token_factory()
        else:
            token = CancellationToken()
        # Register synchronously in THIS thread so a cancel immediately after
        # create_run returns can find the token before the loop task starts.
        self._tokens[run_id] = token
        if self._loop is None:
            # No running loop (e.g. unit test without start()); defer until
            # start() is called by enqueuing a sentinel via the executor later.
            return
        self._loop.call_soon_threadsafe(self._schedule, run_id, token)

    def request_cancel(
        self,
        run_id: str,
        *,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> dict[str, object] | bool:
        """Atomically mark the run cancel-requested and signal its token.

        Thread-safe: serialized via the shared database RLock (same lock the
        EventWriter uses) so concurrent cancel/event writes on the single
        connection cannot interleave.

        Without an idempotency key: returns True if this call set the cancel
        flag, False if the run was already cancelled or terminal. Never raises.

        With an idempotency key: returns a dict mirroring the HTTP response
        `{run_id, cancel_requested, state}`. A repeated identical key replays
        the first response verbatim; a conflicting key reusing a different
        request body raises RunIdempotencyConflict.
        """
        now = self._now()
        connection = self._database.connection
        # The idempotency route is scoped per-run (matching the table PK (key,
        # route)) so a key legitimately reused for a different run/route does
        # not collide or read an ambiguous row.
        idem_route = f"POST /api/platform/v1/runs/{run_id}/cancel" if idempotency_key else None

        # Short-circuit: idempotency replay before any write.
        if idempotency_key is not None:
            replayed = self._read_cancel_idempotency(idempotency_key, idem_route, request_hash)
            if replayed is not None:
                return replayed

        with self._database._lock:  # noqa: SLF100 — serialize against EventWriter
            # Re-check idempotency inside the lock to close the double-submit race.
            if idempotency_key is not None:
                replayed = self._read_cancel_idempotency(idempotency_key, idem_route, request_hash)
                if replayed is not None:
                    return replayed
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE runs
                    SET cancel_requested_at = ?, updated_at = ?
                    WHERE id = ? AND cancel_requested_at IS NULL
                      AND state IN ('queued','preparing','running','evaluating','reporting')
                    """,
                    (now, now, run_id),
                )
                rowcount = cursor.rowcount
                state_row = connection.execute(
                    "SELECT state FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                current_state = str(state_row["state"]) if state_row else "unknown"
                if idempotency_key is not None:
                    response = {
                        "run_id": run_id,
                        "cancel_requested": rowcount == 1,
                        "state": current_state,
                    }
                    connection.execute(
                        """
                        INSERT INTO idempotency_keys (
                          key, route, request_hash, response_status, response_json,
                          run_id, created_at
                        )
                        VALUES (?, ?, ?, 200, ?, ?, ?)
                        """,
                        (
                            idempotency_key,
                            idem_route,
                            request_hash or "",
                            canonical_json(response),
                            run_id,
                            now,
                        ),
                    )
                connection.commit()
            except sqlite3.Error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                logger.exception("Failed to set cancel_requested_at for run %s", run_id)
                if idempotency_key is not None:
                    return {"run_id": run_id, "cancel_requested": False, "state": current_state if "current_state" in dir() else "unknown"}
                return False

        cancel_set = rowcount == 1
        if cancel_set:
            self._event_writer.emit(
                run_id,
                "run.cancel_requested",
                {"occurred_at": now},
                entity_type="run",
                entity_id=run_id,
            )
            token = self._tokens.get(run_id)
            if token is not None:
                token.cancel()

        if idempotency_key is not None:
            return {"run_id": run_id, "cancel_requested": cancel_set, "state": current_state}
        return cancel_set

    def _read_cancel_idempotency(
        self,
        idempotency_key: str,
        route: str,
        request_hash: str | None,
    ) -> dict[str, object] | None:
        """Return the replayed response for a cancel idempotency key, or None.

        Query is scoped by (key, route) to match the table PK and create-run's
        behaviour — a key reused for a different route/run is NOT a conflict
        here; it simply has no replay row for this route. A key seen on the SAME
        route with a different request body raises RunIdempotencyConflict.

        Raises RunIdempotencyConflict if the (key, route) was seen with a
        different body.
        """
        import json as _json

        row = self._database.connection.execute(
            "SELECT request_hash, response_json FROM idempotency_keys "
            "WHERE key = ? AND route = ?",
            (idempotency_key, route),
        ).fetchone()
        if row is None:
            return None
        if request_hash is not None and row["request_hash"] != request_hash:
            raise RunIdempotencyConflict(idempotency_key)
        try:
            return _json.loads(row["response_json"]) if row["response_json"] else None
        except (ValueError, TypeError):
            return None

    def snapshot(self) -> dict[str, list[str]]:
        return {"active_run_ids": [rid for rid, t in self._tasks.items() if not t.done()]}

    # -- internals ----------------------------------------------------------

    def _schedule(self, run_id: str, token: Any) -> None:
        assert self._loop is not None
        task = self._loop.create_task(self._execute(run_id, token))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))

    async def _execute(self, run_id: str, token: Any) -> None:
        from bench_env.runner.cancellation import RunCancelled

        from test_platform.execution.runner_sink import PlatformRunnerEventSink

        # VS-09 Contract 8: load the RunPlan FIRST to decide routing. A 2-lane
        # plan uses the paired path (the paired executor builds its OWN per-lane
        # event sinks — Contract 3 — so the supervisor does NOT build the
        # single-lane sink for it). A 1-lane plan keeps the VS-07 lane-scoped
        # path unchanged.
        plan_lane_count = self._plan_lane_count(run_id)

        if plan_lane_count == 2:
            # Paired path: the paired executor owns per-lane sinks + routing.
            terminal_event = await self._execute_paired(run_id, token)
        else:
            terminal_event = await self._execute_single_lane(run_id, token)

        # Emit the terminal run.* event exactly once (the executor does NOT emit
        # run.* terminal events — only the supervisor does, to avoid duplicates).
        if terminal_event == "run.completed":
            self._emit_terminal(run_id, "run.completed")
        # run.cancelled / run.failed are emitted inside _mark_run_cancelled /
        # _mark_run_failed respectively.

    async def _execute_single_lane(self, run_id: str, token: Any) -> str:
        """VS-05/06/07/08 single-lane execution path (unchanged)."""
        from bench_env.runner.cancellation import RunCancelled

        from test_platform.execution.runner_sink import PlatformRunnerEventSink

        # Resolve the run/lane attempt ids so episode/step events carry full
        # identity into the events table.
        run_attempt_id, lane_attempt_id, lane_id = self._resolve_attempt_ids(run_id)

        # VS-07: resolve the executor PER LANE so a parallel lane gets a
        # ParallelRunExecutor. The resolver inspects the lane's runner_config.
        lane = self._resolve_lane(run_id)
        executor = self._executor_resolver(lane)

        # Build a non-throwing adapter that bridges bench_env ExecutionEvents
        # (episode.started / step_recorded / episode.completed / ...) into the
        # platform EventWriter, so the SSE stream carries live episode progress.
        # VS-07: the episode_key_resolver maps a runner episode_key to the
        # persisted episodes.id so events carry a stable episode_id column.
        runner_sink = PlatformRunnerEventSink(
            self._event_writer,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            lane_id=lane_id,
            lane_attempt_id=lane_attempt_id,
            worker_id="serial",
            episode_key_resolver=self._make_episode_key_resolver(run_id),
        )

        terminal_event: str | None = None
        try:
            await executor.execute_run(
                run_id, token=token, events=runner_sink,
                run_event_writer=self._event_writer,
            )
            terminal_event = "run.completed"
        except RunCancelled:
            await self._mark_run_cancelled(run_id)
            terminal_event = "run.cancelled"
        except Exception:  # noqa: BLE001 — supervisor must not crash the loop
            logger.exception("Run %s failed during execution", run_id)
            await self._mark_run_failed(run_id)
            terminal_event = "run.failed"
        return terminal_event or "run.failed"

    async def _execute_paired(self, run_id: str, token: Any) -> str:
        """VS-09/VS-10 paired (2-lane) execution path.

        Resolves the appropriate paired executor. VS-10 Contract 1: the compare
        node's ``execution`` axis selects between ``serial`` (default,
        PairedSerialRunExecutor) and ``parallel`` (PairedParallelRunExecutor).
        The executor builds its own per-lane event sinks from the writer
        (Contract 3), so the supervisor passes the writer through.
        """
        from bench_env.runner.cancellation import RunCancelled
        from bench_env.runner.events import NullEventSink

        from test_platform.services.execution import (
            PairedParallelRunExecutor,
            PairedSerialRunExecutor,
        )

        lane = self._resolve_lane(run_id)
        resolved_executor = self._executor_resolver(lane)
        parallel_execution = self._paired_execution_axis(run_id) == "parallel"

        # If the resolver already returned the correct paired executor type, use
        # it. Otherwise construct one (carrying over any injected factories).
        desired_type = (
            PairedParallelRunExecutor if parallel_execution else PairedSerialRunExecutor
        )
        if isinstance(resolved_executor, desired_type):
            executor = resolved_executor
        else:
            executor = desired_type(
                self._database,
                self._settings,
                task_factory=getattr(resolved_executor, "task_factory", None),
                env_factory=getattr(resolved_executor, "env_factory", None),
                agent_factory=getattr(resolved_executor, "agent_factory", None),
            )

        terminal_event: str | None = None
        try:
            await executor.execute_run(
                run_id, token=token,
                events=NullEventSink(),
                run_event_writer=self._event_writer,
            )
            terminal_event = "run.completed"
        except RunCancelled:
            await self._mark_run_cancelled(run_id)
            terminal_event = "run.cancelled"
        except Exception:  # noqa: BLE001 — supervisor must not crash the loop
            logger.exception("Run %s failed during execution", run_id)
            await self._mark_run_failed(run_id)
            terminal_event = "run.failed"
        return terminal_event or "run.failed"

    def _paired_execution_axis(self, run_id: str) -> str:
        """Read the compare node's ``execution`` axis from the run plan.

        VS-10 Contract 1: ``serial`` (default) or ``parallel``. Cheap read: the
        compare node config is stored in ``run_plan_json.comparison``.
        """
        row = self._database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return "serial"
        try:
            plan = json.loads(row["run_plan_json"])
            comparison = plan.get("comparison") or {}
            execution = comparison.get("execution")
            return str(execution) if execution in ("serial", "parallel") else "serial"
        except (ValueError, TypeError):
            return "serial"

    def _plan_lane_count(self, run_id: str) -> int:
        """Count lanes for the run's persisted plan (cheap DB read).

        Used to route 2-lane runs to the paired path WITHOUT parsing the full
        RunPlan on the hot path (mirrors _resolve_lane's lightweight approach).
        """
        row = self._database.connection.execute(
            "SELECT COUNT(*) AS n FROM lanes WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def _resolve_attempt_ids(self, run_id: str) -> tuple[str | None, str | None, str | None]:
        row = self._database.connection.execute(
            """
            SELECT ra.id AS run_attempt_id, la.id AS lane_attempt_id, l.id AS lane_id
            FROM run_attempts ra
            JOIN lane_attempts la ON la.run_attempt_id = ra.id
            JOIN lanes l ON l.id = la.lane_id
            WHERE ra.run_id = ?
            ORDER BY l.lane_key
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None, None, None
        return str(row["run_attempt_id"]), str(row["lane_attempt_id"]), str(row["lane_id"])

    def _resolve_lane(self, run_id: str) -> Any:
        """Return a lane-like object with ``runner_config`` for the executor resolver.

        VS-07 supports a single source lane, so we read the first lane's
        ``runner_config_json`` from the lanes table. The resolver only inspects
        ``runner_config.get('parallel', 1)``, so a lightweight namespace suffices
        (avoiding a full RunPlan parse on the hot path).
        """
        import types as _types

        row = self._database.connection.execute(
            "SELECT lane_key, runner_config_json FROM lanes WHERE run_id = ? "
            "ORDER BY lane_key LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return _types.SimpleNamespace(runner_config={})
        runner_config = json.loads(row["runner_config_json"]) if row["runner_config_json"] else {}
        return _types.SimpleNamespace(
            lane_key=str(row["lane_key"]),
            runner_config=runner_config,
        )

    def _make_episode_key_resolver(self, run_id: str) -> Callable[[str], str | None]:
        """Build a episode_key -> episodes.id resolver for the runner event sink.

        Looks up the persisted episodes table for this run so runner-emitted
        episode events carry a stable episode_id column. Returns None when the
        key is unknown (the sink leaves episode_id null rather than raising).
        """

        def episode_key_resolver(episode_key: str) -> str | None:
            row = self._database.connection.execute(
                "SELECT id FROM episodes WHERE run_id = ? AND episode_key = ?",
                (run_id, episode_key),
            ).fetchone()
            return str(row["id"]) if row else None

        return episode_key_resolver

    def _emit_terminal(self, run_id: str, event_type: str) -> None:
        self._event_writer.emit(
            run_id,
            event_type,
            {},
            entity_type="run",
            entity_id=run_id,
        )

    async def _mark_run_cancelled(self, run_id: str) -> None:
        now = self._now()
        connection = self._database.connection
        with self._database._lock:  # noqa: SLF100 — serialize against EventWriter
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'cancelled', ended_at = COALESCE(ended_at, ?), updated_at = ?
                    WHERE id = ? AND state IN ('queued','preparing','running','evaluating','reporting')
                    """,
                    (now, now, run_id),
                )
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = 'cancelled', ended_at = COALESCE(ended_at, ?)
                    WHERE run_id = ? AND state IN ('queued','preparing','running','evaluating','reporting')
                    """,
                    (now, run_id),
                )
                connection.execute(
                    """
                    UPDATE lane_attempts
                    SET state = 'cancelled', ended_at = COALESCE(ended_at, ?)
                    WHERE lane_id IN (SELECT id FROM lanes WHERE run_id = ?)
                      AND state IN ('queued','preparing','running','evaluating','reporting')
                    """,
                    (now, run_id),
                )
                connection.commit()
            except sqlite3.Error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
        self._event_writer.emit(
            run_id,
            "run.cancelled",
            {"occurred_at": now},
            entity_type="run",
            entity_id=run_id,
        )

    async def _mark_run_failed(self, run_id: str) -> None:
        now = self._now()
        connection = self._database.connection
        with self._database._lock:  # noqa: SLF100 — serialize against EventWriter
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'failed', ended_at = COALESCE(ended_at, ?), updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, run_id),
                )
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = 'failed', ended_at = COALESCE(ended_at, ?), error_code = 'EXECUTION_ERROR'
                    WHERE run_id = ? AND state IN ('queued','preparing','running','evaluating','reporting')
                    """,
                    (now, run_id),
                )
                # P2: lane_attempts must also reach 'failed', mirroring the
                # cancel path — otherwise a failed run leaves lanes stuck in
                # 'running', an inconsistent terminal state.
                connection.execute(
                    """
                    UPDATE lane_attempts
                    SET state = 'failed', ended_at = COALESCE(ended_at, ?)
                    WHERE lane_id IN (SELECT id FROM lanes WHERE run_id = ?)
                      AND state IN ('queued','preparing','running','evaluating','reporting')
                    """,
                    (now, run_id),
                )
                connection.commit()
            except sqlite3.Error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
        self._event_writer.emit(
            run_id,
            "run.failed",
            {"occurred_at": now},
            entity_type="run",
            entity_id=run_id,
        )


class RunService:
    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        supervisor: Any,
        catalog_builder: Callable[[], TaskCatalogSnapshot] = build_task_catalog_snapshot,
    ) -> None:
        self.database = database
        self.settings = settings
        self.supervisor = supervisor
        self.catalog_builder = catalog_builder

    def create_run(
        self,
        *,
        workflow_version_id: str,
        name: str | None,
        seed: int,
        idempotency_key: str,
    ) -> RunDetail:
        request_hash = canonical_sha256(
            {
                "workflow_version_id": workflow_version_id,
                "name": name,
                "seed": seed,
            }
        )
        existing = self._find_idempotent_run(idempotency_key, request_hash)
        if existing is not None:
            return existing

        workflow_repository = WorkflowRepository(self.database)
        try:
            version = workflow_repository.get_version(workflow_version_id)
            workflow = workflow_repository.get(version.workflow_id)
        except WorkflowDomainError as exc:
            raise RunDomainError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
                details=exc.details,
            ) from exc

        definition = WorkflowDefinition.model_validate(version.definition)
        targets = self._resolve_targets(definition)
        # VS-10 Contract 3: create-run is the AUTHORITATIVE constraint gate.
        # Resolve frozen target revisions (already done above) and evaluate the
        # compare node's target_constraints. Any violation raises RunDomainError
        # (409) with the violation details — the run is NOT created.
        self._enforce_target_constraints(definition, targets)
        run_id = new_id()
        created_at = _utc_timestamp()
        try:
            plan = RunPlanCompiler().compile(
                run_id=run_id,
                workflow_version_id=workflow_version_id,
                definition=definition,
                catalog=self.catalog_builder(),
                targets=targets,
                seed=seed,
                created_at=created_at,
            )
        except ValueError as exc:
            raise RunDomainError(
                "RUN_PLAN_INVALID",
                str(exc),
                status_code=409,
            ) from exc
        temporary_root = self.settings.runs_dir / f".{run_id}.tmp"
        final_root = self.settings.runs_dir / run_id
        self._write_plan_artifact(temporary_root, plan)

        connection = self.database.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_graph(
                connection,
                project_id=workflow.project_id,
                name=name,
                definition=definition,
                plan=plan,
                artifact_root=final_root,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

        try:
            temporary_root.replace(final_root)
        except OSError as exc:
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ?, ended_at = ? WHERE id = ?",
                ("failed", _utc_timestamp(), _utc_timestamp(), run_id),
            )
            connection.commit()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise RunDomainError(
                "ARTIFACT_IO_ERROR",
                "The run plan artifact could not be finalized.",
                status_code=500,
                details=[{"run_id": run_id}],
            ) from exc

        self.supervisor.submit(run_id)
        return RunRepository(self.database).get(run_id)

    def retry_run(self, run_id: str) -> dict[str, Any]:
        run = RunRepository(self.database).get(run_id)
        if run.state not in {"completed", "failed", "cancelled"}:
            raise RunDomainError(
                "RUN_RETRY_NOT_TERMINAL",
                "Only terminal runs can be retried.",
                status_code=409,
                details=[{"run_id": run_id, "state": run.state}],
            )

        source_attempt = self._latest_terminal_run_attempt(run_id)
        planned = self._planned_lane_episodes(run_id)
        latest_attempts = self._episode_attempts_for_run_attempt(source_attempt["id"])
        selected = select_retry_lane_episodes(planned, latest_attempts)
        if not selected:
            raise RunDomainError(
                "RUN_RETRY_SELECTION_EMPTY",
                "No failed or errored lane episodes are available to retry.",
                status_code=409,
                details=[{"run_id": run_id}],
            )

        result = self._create_followup_attempt(
            run_id=run_id,
            reason="retry",
            selected=selected,
        )
        self.supervisor.submit(run_id)
        return result

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = RunRepository(self.database).get(run_id)
        if run.state not in {"completed", "failed", "cancelled"}:
            raise RunDomainError(
                "RUN_RESUME_NOT_TERMINAL",
                "Only terminal runs can be resumed.",
                status_code=409,
                details=[{"run_id": run_id, "state": run.state}],
            )

        self._assert_resume_compatible(run_id)
        source_attempt = self._latest_terminal_run_attempt(run_id)
        planned = self._planned_lane_episodes(run_id)
        latest_attempts = self._episode_attempts_for_run_attempt(source_attempt["id"])
        selected = select_resume_lane_episodes(planned, latest_attempts)
        if not selected:
            raise RunDomainError(
                "RUN_RESUME_SELECTION_EMPTY",
                "No missing or service-restarted lane episodes are available to resume.",
                status_code=409,
                details=[{"run_id": run_id}],
            )

        result = self._create_followup_attempt(
            run_id=run_id,
            reason="resume",
            selected=selected,
        )
        self.supervisor.submit(run_id)
        return result

    def reconcile_startup(self) -> dict[str, Any]:
        from test_platform.services.execution import (
            ResultIngestor,
            _episode_artifact_root,
        )

        connection = self.database.connection
        active_lane_attempts = connection.execute(
            """
            SELECT DISTINCT r.id AS run_id, la.id AS lane_attempt_id, la.artifact_root
            FROM runs AS r
            JOIN run_attempts AS ra ON ra.run_id = r.id
            JOIN lane_attempts AS la ON la.run_attempt_id = ra.id
            WHERE r.state IN ('queued','preparing','running','evaluating','reporting')
               OR ra.state IN ('queued','preparing','running','evaluating','reporting')
               OR la.state IN ('queued','preparing','running','evaluating','reporting')
            ORDER BY r.id, la.id
            """
        ).fetchall()
        if not active_lane_attempts:
            return {"recovered_runs": 0, "service_restarted_episodes": 0}

        ingestor = ResultIngestor(self.database)
        recovered_run_ids: set[str] = set()
        restarted_episodes = 0
        now = _utc_timestamp()

        for lane_attempt in active_lane_attempts:
            run_id = str(lane_attempt["run_id"])
            lane_attempt_id = str(lane_attempt["lane_attempt_id"])
            recovered_run_ids.add(run_id)

            active_episode_attempts = connection.execute(
                """
                SELECT ea.id, e.task_id, e.trial_id
                FROM episode_attempts AS ea
                JOIN episodes AS e ON e.id = ea.episode_id
                WHERE ea.lane_attempt_id = ?
                  AND ea.state IN ('queued','preparing','running','evaluating','reporting')
                ORDER BY e.trial_id, e.episode_key
                """,
                (lane_attempt_id,),
            ).fetchall()
            for attempt in active_episode_attempts:
                result = _service_restarted_result(
                    task_id=str(attempt["task_id"]),
                    trial_id=int(attempt["trial_id"] or 0),
                )
                with self.database._lock:  # noqa: SLF100
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                            """
                            UPDATE episode_attempts
                            SET state = 'completed',
                                outcome = 'ERROR',
                                error_code = 'SERVICE_RESTARTED',
                                result_json = ?,
                                ended_at = COALESCE(ended_at, ?)
                            WHERE id = ?
                            """,
                            (canonical_json(result), now, attempt["id"]),
                        )
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                restarted_episodes += 1

            missing_episodes = connection.execute(
                """
                SELECT e.episode_key, e.task_id, e.trial_id
                FROM episodes AS e
                WHERE e.run_id = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM episode_attempts AS ea
                    WHERE ea.episode_id = e.id
                      AND ea.lane_attempt_id = ?
                      AND ea.state IN ('completed','cancelled')
                  )
                ORDER BY e.trial_id, e.episode_key
                """,
                (run_id, lane_attempt_id),
            ).fetchall()
            for episode in missing_episodes:
                result = _service_restarted_result(
                    task_id=str(episode["task_id"]),
                    trial_id=int(episode["trial_id"] or 0),
                )
                ingestor.ingest_episode_attempt(
                    run_id=run_id,
                    lane_attempt_id=lane_attempt_id,
                    episode_key=str(episode["episode_key"]),
                    result=result,
                    artifact_root=_episode_artifact_root(
                        str(lane_attempt["artifact_root"]),
                        result,
                        repeat_n=1,
                        episode_key=str(episode["episode_key"]),
                    ),
                    error_code_override="SERVICE_RESTARTED",
                )
                restarted_episodes += 1

            ingestor.finalize_lane_only(
                lane_attempt_id=lane_attempt_id,
                terminal_state="failed",
            )

        for run_id in sorted(recovered_run_ids):
            ingestor.finalize_run(
                run_id=run_id,
                terminal_state="failed",
                cancelled=False,
            )

        return {
            "recovered_runs": len(recovered_run_ids),
            "service_restarted_episodes": restarted_episodes,
        }

    def _assert_resume_compatible(self, run_id: str) -> None:
        target_violations = []
        target_repository = TargetRepository(self.database)
        rows = self.database.connection.execute(
            """
            SELECT lane_key, target_id, target_revision_id
            FROM lanes
            WHERE run_id = ?
            ORDER BY lane_key
            """,
            (run_id,),
        ).fetchall()
        for row in rows:
            current = target_repository.latest_revision(str(row["target_id"]))
            current_revision_id = current.id if current is not None else None
            if current_revision_id != row["target_revision_id"]:
                target_violations.append(
                    {
                        "kind": "target_revision",
                        "lane_key": str(row["lane_key"]),
                        "target_id": str(row["target_id"]),
                        "expected_revision_id": str(row["target_revision_id"]),
                        "current_revision_id": current_revision_id,
                    }
                )
        if target_violations:
            raise RunDomainError(
                "RUN_RESUME_INCOMPATIBLE_REVISION",
                "The run cannot be resumed because its frozen target revisions are no longer current.",
                status_code=409,
                details=target_violations,
            )

        row = self.database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunDomainError(
                "RUN_NOT_FOUND",
                "Run was not found.",
                status_code=404,
                details=[{"run_id": run_id}],
            )
        run_plan = json.loads(row["run_plan_json"])
        task_source = run_plan.get("task_source")
        if not isinstance(task_source, dict):
            return
        current_catalog = self.catalog_builder()
        expected_repository_revision = task_source.get("repository_revision")
        expected_registry_digest = task_source.get("registry_digest")
        if (
            current_catalog.repository_revision != expected_repository_revision
            or current_catalog.digest != expected_registry_digest
        ):
            raise RunDomainError(
                "RUN_RESUME_INCOMPATIBLE_REVISION",
                "The run cannot be resumed because its frozen task source is no longer current.",
                status_code=409,
                details=[
                    {
                        "kind": "task_source",
                        "expected_repository_revision": expected_repository_revision,
                        "current_repository_revision": current_catalog.repository_revision,
                        "expected_registry_digest": expected_registry_digest,
                        "current_registry_digest": current_catalog.digest,
                    }
                ],
            )

    def _find_idempotent_run(
        self,
        idempotency_key: str,
        request_hash: str,
    ) -> RunDetail | None:
        row = self.database.connection.execute(
            """
            SELECT request_hash, run_id
            FROM idempotency_keys
            WHERE key = ? AND route = ?
            """,
            (idempotency_key, "POST /api/platform/v1/runs"),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise RunIdempotencyConflict(idempotency_key)
        return RunRepository(self.database).get(row["run_id"])

    def _latest_terminal_run_attempt(self, run_id: str) -> dict[str, Any]:
        row = self.database.connection.execute(
            """
            SELECT id, attempt_no, state
            FROM run_attempts
            WHERE run_id = ?
              AND state IN ('completed', 'failed', 'cancelled')
            ORDER BY attempt_no DESC, id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunDomainError(
                "RUN_ATTEMPT_NOT_FOUND",
                "No terminal run attempt is available.",
                status_code=409,
                details=[{"run_id": run_id}],
            )
        return {
            "id": str(row["id"]),
            "attempt_no": int(row["attempt_no"]),
            "state": str(row["state"]),
        }

    def _planned_lane_episodes(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT e.id AS episode_id, e.episode_key, l.id AS lane_id, l.lane_key
            FROM episodes AS e
            CROSS JOIN lanes AS l
            WHERE e.run_id = ? AND l.run_id = ?
            ORDER BY e.episode_key, l.lane_key
            """,
            (run_id, run_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _episode_attempts_for_run_attempt(self, run_attempt_id: str) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT e.episode_key, l.lane_key, ea.state, ea.outcome, ea.error_code,
                   ea.attempt_no
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE la.run_attempt_id = ?
            ORDER BY e.episode_key, l.lane_key, ea.attempt_no
            """,
            (run_attempt_id,),
        ).fetchall()
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            latest[(str(row["episode_key"]), str(row["lane_key"]))] = dict(row)
        return list(latest.values())

    def _create_followup_attempt(
        self,
        *,
        run_id: str,
        reason: str,
        selected: list[dict[str, str]],
    ) -> dict[str, Any]:
        now = _utc_timestamp()
        connection = self.database.connection
        lanes = connection.execute(
            """
            SELECT id, lane_key
            FROM lanes
            WHERE run_id = ?
            ORDER BY lane_key
            """,
            (run_id,),
        ).fetchall()
        node_rows = connection.execute(
            """
            SELECT node_id, node_type
            FROM workflow_node_runs
            WHERE run_attempt_id = (
              SELECT id FROM run_attempts
              WHERE run_id = ?
              ORDER BY attempt_no DESC, id DESC
              LIMIT 1
            )
            ORDER BY node_id
            """,
            (run_id,),
        ).fetchall()
        planned_by_key = {
            (item["episode_key"], item["lane_key"]): item
            for item in self._planned_lane_episodes(run_id)
        }
        run_attempt_id = new_id()
        with self.database._lock:  # noqa: SLF100
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(attempt_no), 0) + 1
                    FROM run_attempts
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                attempt_no = int(row[0])
                connection.execute(
                    """
                    INSERT INTO run_attempts (
                      id, run_id, attempt_no, reason, state, created_at
                    )
                    VALUES (?, ?, ?, ?, 'queued', ?)
                    """,
                    (run_attempt_id, run_id, attempt_no, reason, now),
                )
                for node in node_rows:
                    connection.execute(
                        """
                        INSERT INTO workflow_node_runs (
                          id, run_attempt_id, node_id, node_type, state, created_at
                        )
                        VALUES (?, ?, ?, ?, 'queued', ?)
                        """,
                        (new_id(), run_attempt_id, node["node_id"], node["node_type"], now),
                    )
                for lane in lanes:
                    connection.execute(
                        """
                        INSERT INTO lane_attempts (
                          id, lane_id, run_attempt_id, state, artifact_root, created_at
                        )
                        VALUES (?, ?, ?, 'queued', ?, ?)
                        """,
                        (
                            new_id(),
                            lane["id"],
                            run_attempt_id,
                            f"lanes/{lane['lane_key']}/attempts/{attempt_no:04d}",
                            now,
                        ),
                    )
                for item in selected:
                    planned = planned_by_key[(item["episode_key"], item["lane_key"])]
                    connection.execute(
                        """
                        INSERT INTO run_attempt_episode_selection (
                          id, run_attempt_id, lane_id, episode_id, reason, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id(),
                            run_attempt_id,
                            planned["lane_id"],
                            planned["episode_id"],
                            item["reason"],
                            now,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'queued',
                        cancel_requested_at = NULL,
                        ended_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return {
            "run_id": run_id,
            "run_attempt_id": run_attempt_id,
            "attempt_no": attempt_no,
            "reason": reason,
            "selected_lane_episodes": selected,
        }

    def _resolve_targets(self, definition: WorkflowDefinition) -> dict[str, Any]:
        matrix = next((node for node in definition.nodes if node.type == "matrix"), None)
        lanes = matrix.config.get("lanes", {}) if matrix else {}
        if not isinstance(lanes, dict):
            raise RunDomainError("RUN_PLAN_INVALID", "Workflow matrix lanes must be an object.")

        repository = TargetRepository(self.database)
        targets: dict[str, Any] = {}
        for lane in lanes.values():
            target_id = lane.get("target_id") if isinstance(lane, dict) else None
            if not isinstance(target_id, str):
                raise RunDomainError("RUN_PLAN_INVALID", "Every lane requires a target.")
            target = repository.get(target_id)
            revision = repository.latest_revision(target_id)
            if revision is None or revision.health_status != "healthy":
                raise RunDomainError(
                    "TARGET_REVISION_MISSING",
                    "Every run lane requires a healthy target revision.",
                    details=[{"target_id": target_id}],
                )
            targets[target_id] = {"target": target, "revision": revision}
        return targets

    def _enforce_target_constraints(
        self,
        definition: WorkflowDefinition,
        targets: dict[str, Any],
    ) -> None:
        """VS-10 Contract 3: the AUTHORITATIVE constraint gate.

        For a paired (2-lane) workflow whose compare node declares
        ``target_constraints``, evaluate them against the FROZEN
        TargetRevision.metadata (Contract 6) and raise RunDomainError (409) with
        the violation details if any constraint is violated. Single-lane
        workflows and workflows without a compare node are unaffected.
        """
        from test_platform.domain.comparison_constraints import (
            evaluate_target_constraints,
        )

        matrix = next((node for node in definition.nodes if node.type == "matrix"), None)
        compare = next((node for node in definition.nodes if node.type == "compare"), None)
        if matrix is None or compare is None:
            return
        lanes = matrix.config.get("lanes", {}) if matrix else {}
        if not isinstance(lanes, dict) or len(lanes) < 2:
            return
        constraints = compare.config.get("target_constraints")
        if not isinstance(constraints, list):
            constraints = None  # default to all axes

        # Order lanes by lane_key so baseline/candidate are deterministic.
        ordered_keys = sorted(lanes)
        ordered_lanes = [lanes[k] for k in ordered_keys]
        baseline_lane = ordered_lanes[0]
        candidate_lane = ordered_lanes[1]
        baseline_target_id = baseline_lane.get("target_id") if isinstance(baseline_lane, dict) else None
        candidate_target_id = candidate_lane.get("target_id") if isinstance(candidate_lane, dict) else None
        baseline_resolved = targets.get(baseline_target_id) if isinstance(baseline_target_id, str) else None
        candidate_resolved = targets.get(candidate_target_id) if isinstance(candidate_target_id, str) else None
        if baseline_resolved is None or candidate_resolved is None:
            return  # _resolve_targets already raised for missing targets
        baseline_revision = baseline_resolved.get("revision")
        candidate_revision = candidate_resolved.get("revision")
        baseline_metadata = getattr(baseline_revision, "metadata", None)
        candidate_metadata = getattr(candidate_revision, "metadata", None)

        violations = evaluate_target_constraints(
            baseline_metadata=baseline_metadata,
            candidate_metadata=candidate_metadata,
            constraints=constraints,
        )
        if violations:
            raise RunDomainError(
                "COMPARISON_CONSTRAINT_VIOLATED",
                "The paired comparison constraints are not satisfied.",
                status_code=409,
                details=[violation.to_dict() for violation in violations],
            )

    def _write_plan_artifact(self, temporary_root: Path, plan: RunPlan) -> None:
        platform_dir = temporary_root / "platform"
        platform_dir.mkdir(parents=True, exist_ok=False)
        (platform_dir / "run-plan.json").write_text(
            f"{canonical_json(plan.model_dump(mode='json'))}\n",
            encoding="utf-8",
        )

    def _insert_graph(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        name: str | None,
        definition: WorkflowDefinition,
        plan: RunPlan,
        artifact_root: Path,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        plan_json = canonical_json(plan.model_dump(mode="json"))
        connection.execute(
            """
            INSERT INTO runs (
              id, project_id, workflow_version_id, name, state,
              run_plan_json, run_plan_hash, artifact_root,
              next_event_sequence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 2, ?, ?)
            """,
            (
                plan.run_id,
                project_id,
                plan.workflow_version_id,
                name,
                plan_json,
                plan.fingerprint,
                str(artifact_root),
                plan.created_at,
                plan.created_at,
            ),
        )
        run_attempt_id = new_id()
        connection.execute(
            """
            INSERT INTO run_attempts (
              id, run_id, attempt_no, reason, state, created_at
            )
            VALUES (?, ?, 1, 'initial', 'queued', ?)
            """,
            (run_attempt_id, plan.run_id, plan.created_at),
        )
        for node in definition.nodes:
            connection.execute(
                """
                INSERT INTO workflow_node_runs (
                  id, run_attempt_id, node_id, node_type, state, created_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (new_id(), run_attempt_id, node.id, node.type, plan.created_at),
            )
        for lane in plan.lanes:
            connection.execute(
                """
                INSERT INTO lanes (
                  id, run_id, lane_key, role, target_id, target_revision_id,
                  runner_config_json, reproducibility_fingerprint, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lane.lane_id,
                    plan.run_id,
                    lane.lane_key,
                    lane.role,
                    lane.target_id,
                    lane.target_revision_id,
                    canonical_json(lane.runner_config),
                    canonical_sha256(
                        lane.model_dump(mode="json", exclude={"lane_id"})
                    ),
                    plan.created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO lane_attempts (
                  id, lane_id, run_attempt_id, state, artifact_root, created_at
                )
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (
                    new_id(),
                    lane.lane_id,
                    run_attempt_id,
                    f"lanes/{lane.lane_key}",
                    plan.created_at,
                ),
            )
        for episode in plan.episodes:
            connection.execute(
                """
                INSERT INTO episodes (
                  id, run_id, episode_key, materialization_key, pair_key,
                  task_base_id, task_id, instance_id, instance_seed,
                  template_index, trial_id, max_steps, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    plan.run_id,
                    episode.episode_key,
                    episode.materialization_key,
                    episode.pair_key,
                    episode.task_base_id,
                    episode.task_id,
                    episode.instance_id,
                    episode.instance_seed,
                    episode.template_index,
                    episode.trial_id,
                    episode.max_steps,
                    plan.created_at,
                ),
            )
        connection.execute(
            """
            INSERT INTO events (
              id, run_id, sequence, type, entity_type, entity_id,
              occurred_at, payload_json
            )
            VALUES (?, ?, 1, 'run.created', 'run', ?, ?, ?)
            """,
            (
                new_id(),
                plan.run_id,
                plan.run_id,
                plan.created_at,
                canonical_json(
                    {
                        "state": "queued",
                        "fingerprint": plan.fingerprint,
                    }
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO idempotency_keys (
              key, route, request_hash, response_status, run_id, created_at
            )
            VALUES (?, ?, ?, 201, ?, ?)
            """,
            (
                idempotency_key,
                "POST /api/platform/v1/runs",
                request_hash,
                plan.run_id,
                plan.created_at,
            ),
        )


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _service_restarted_result(*, task_id: str, trial_id: int) -> dict[str, Any]:
    return {
        "id": task_id,
        "trial_id": trial_id,
        "is_success": False,
        "is_error": True,
        "execution": {
            "error": "Execution was interrupted by a service restart.",
            "stop_reason": "SERVICE_RESTARTED",
        },
    }


def _default_executor_resolver(
    database: Database, settings: PlatformSettings
) -> Callable[[Any], Any]:
    """Production lane-aware executor resolver (deferred — VS-07 wires fakes).

    A lane whose ``runner_config['processes']`` is > 1 gets a
    MultiprocessRunExecutor; otherwise one with ``parallel`` > 1 gets a
    ParallelRunExecutor; otherwise a SerialRunExecutor. NOTE: real env/agent
    factories are not yet wired here (production execution is deferred), so the
    returned executors carry NO factories and would raise
    EXECUTOR_FACTORY_MISSING if actually run. VS-07/VS-08 tests inject their own
    resolver/fakes; ``create_app`` keeps the FakeRunSupervisor as the default.
    """
    # Imported lazily to avoid importing execution.py at module import time
    # (execution.py imports bench_env, which is heavy).
    from test_platform.services.execution import (
        MultiprocessRunExecutor,
        ParallelRunExecutor,
        SerialRunExecutor,
    )

    def resolve(lane: Any) -> Any:
        runner_config = getattr(lane, "runner_config", {}) or {}
        processes = int(runner_config.get("processes", 1) or 1)
        parallel = int(runner_config.get("parallel", 1) or 1)
        if processes > 1:
            return MultiprocessRunExecutor(database, settings)
        if parallel > 1:
            return ParallelRunExecutor(database, settings)
        return SerialRunExecutor(database, settings)

    return resolve
