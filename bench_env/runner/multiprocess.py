"""MultiProcessRunner - shard benchmark runs across Python processes.

VS-08: extended with an in-process ``child_runner_factory`` mode (Contract 1)
for deterministic testing without spawn overhead, plus the four shard envelope
types (Contract 3) flowing child→parent over the shared queue, worker_id
normalization (Contract 6), bounded child sink with coalescing, and the
grace→terminate→kill cancellation sequence (Contract 5).

Two entry modes:
- CLI: ``MultiProcessRunner(tasks, config)`` — spawns real mp.Process children
  (production). Results are tailed from each shard's results.jsonl.
- Platform / in-process: ``MultiProcessRunner(tasks, config,
  child_runner_factory=..., prepared_work_specs=..., event_sink=...,
  cancellation_token=...)``. When ``child_runner_factory`` is set, children run
  as asyncio tasks (no real process); the orchestration (shard assignment,
  envelope bridging, cancellation, cleanup, reconciliation) is IDENTICAL to the
  spawn path — only the "run the child" step differs.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import math
import multiprocessing as mp
import os
import queue as queue_mod
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from bench_env.config import RunnerConfig
from bench_env.env.recorder import allocate_run_dir
from bench_env.logger import add_log_file, configure_logging, get_logger
from bench_env.metrics import load_jsonl, result_key, task_trial_key
from bench_env.runner.base import BaseRunner, EpisodeResult
from bench_env.runner.cancellation import CancellationToken
from bench_env.runner.events import (
    EventSink,
    ExecutionEvent,
    NullEventSink,
    ShardEventEnvelope,
    ShardFatalEnvelope,
    ShardLifecycleEnvelope,
    ShardResultEnvelope,
)

logger = get_logger(__name__)


# Event types considered CRITICAL: their QueueEventSink must NEVER drop them —
# on a full queue it falls back to a blocking put() (backpressure). Step/metric
# events are coalesced (dropped + counted) instead.
_CRITICAL_EVENT_TYPES = frozenset(
    {
        "episode.completed",
        "episode.error",
        "episode.cancelled",
        "worker.started",
        "worker.stopped",
        "fatal",
        "result",
        "lifecycle",
    }
)


@dataclass
class ProgressEvent:
    rank: int
    task_id: str
    trial_id: int
    success: bool
    error: str | None = None
    kind: str = "episode"


@dataclass
class ShardSpec:
    rank: int
    task_ids: list[str]
    run_dir: Path
    parallel: int = 1
    num_browsers: int = 0
    # VS-08: the EpisodeWorkSpecs assigned to this shard (platform path).
    work_specs: list[Any] | None = None


@dataclass
class ShardHandle:
    spec: ShardSpec
    process: mp.Process | None = None
    # In-process mode: the asyncio.Task running the child factory's coroutine.
    task: asyncio.Task | None = None
    reported_exit: bool = False
    crashed: bool = False
    exitcode: int | None = None
    error: str | None = None


def _split_evenly(total: int, buckets: int) -> list[int]:
    """Distribute ``total`` across ``buckets`` so the sum equals ``total``.

    Front buckets receive the +1 from ``divmod`` remainder.
    """
    if buckets <= 0:
        return []
    base, rem = divmod(total, buckets)
    return [base + (1 if i < rem else 0) for i in range(buckets)]


def _configure_default_executor() -> None:
    import concurrent.futures

    max_workers = int(os.environ.get("MOBILE_GYM_TO_THREAD_WORKERS", "1024"))
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="bench-to-thread",
        )
    )


def _silence_child_stdio() -> None:
    devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = devnull
    sys.stderr = devnull


def _normalize_worker_id(rank: int, worker_id: str | None) -> str | None:
    """Prefix a child's bare worker_id (``W0``) with the shard rank
    (``p{rank:02d}-W0``) so two shards' ``W0`` never collide in the parent's
    activeWorkers set. Returns None unchanged."""
    if not worker_id:
        return worker_id
    # Only normalize the bare ``W\d+`` form emitted by ParallelRunner; leave
    # already-prefixed or custom ids untouched.
    if worker_id.startswith("W") and worker_id[1:].isdigit():
        return f"p{rank:02d}-{worker_id}"
    return worker_id


class QueueEventSink:
    """Bounded child→parent event sink wrapping the shared mp.Queue.

    Wraps each ExecutionEvent in a ShardEventEnvelope(rank, event) and
    ``put_nowait``s it. On ``queue.Full``:
    - CRITICAL events (terminal episode outcomes, worker lifecycle, fatal,
      result, lifecycle) fall back to a blocking ``put()`` (backpressure) so a
      child stuck in a critical emit can still make progress.
    - Step/metric events (episode.step_recorded, metric.sample) are DROPPED and
      a coalesce counter is incremented; after the run the parent emits one
      ``stream.events_coalesced`` event with the counts.
    """

    def __init__(self, progress_queue: Any, rank: int) -> None:
        self._queue = progress_queue
        self._rank = rank
        self.coalesced = 0

    def emit(self, event: ExecutionEvent) -> None:
        envelope = ShardEventEnvelope(rank=self._rank, event=event)
        try:
            self._queue.put_nowait(envelope)
        except (queue_mod.Full, asyncio.QueueFull):
            if event.type in _CRITICAL_EVENT_TYPES:
                # Backpressure: block until the parent drains. asyncio.Queue.put
                # is a coroutine; callers in async contexts must use the async
                # variant. The sync blocking put works for mp.Queue and the
                # standard queue; for asyncio.Queue on a full bounded queue the
                # critical path would need scheduling — but our in-process
                # queue is unbounded by default, so this branch is rare.
                put = getattr(self._queue, "put", None)
                if put is not None:
                    result = put(envelope)
                    if asyncio.iscoroutine(result):
                        # Close the coroutine to avoid un-awaited warnings; the
                        # bounded critical-emit case for asyncio.Queue is not
                        # supported synchronously (callers should use the async
                        # sink). Coalesce instead of blocking the loop.
                        result.close()
                        self.coalesced += 1
                else:
                    self.coalesced += 1
            else:
                self.coalesced += 1
        except Exception:  # noqa: BLE001 — queue closed / shutting down
            self.coalesced += 1


async def _run_shard_async(
    config: RunnerConfig,
    task_ids: list[str],
    rank: int,
    progress_queue,
    shard_run_dir: Path,
) -> None:
    _configure_default_executor()

    # shard_run_dir is runs/<run>/shards/pNN; shard_run_dir.parent.parent is runs/<run>.
    parent_run_dir = shard_run_dir.parent.parent
    child_config = dataclasses.replace(
        config,
        task_id=None,
        task_ids=task_ids,
        run_dir=shard_run_dir,
        runs_dir=shard_run_dir.parent,
        trajectory_dir=parent_run_dir / "trajectory",
        browser_log_dir=parent_run_dir / "browser_logs",
        browser_log_prefix=f"p{rank:02d}_",
    )

    def emit(result: EpisodeResult) -> None:
        progress_queue.put(
            ProgressEvent(
                rank=rank,
                task_id=result.task_id,
                trial_id=result.trial_id,
                success=result.success,
                error=result.error,
            )
        )

    runner = await ParallelRunner.from_config(child_config, progress_callback=emit)
    await runner.run()


def _shard_main(
    config: RunnerConfig,
    task_ids: list[str],
    rank: int,
    progress_queue,
    shard_run_dir: Path,
) -> None:
    _silence_child_stdio()
    shard_run_dir = Path(shard_run_dir)
    shard_run_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(quiet=False)
    add_log_file(shard_run_dir / "console.log")

    try:
        asyncio.run(_run_shard_async(config, task_ids, rank, progress_queue, shard_run_dir))
    except BaseException as err:
        logger.exception(f"[p{rank:02d}] shard crashed: {type(err).__name__}: {err}")
        try:
            progress_queue.put(
                ProgressEvent(
                    rank=rank,
                    task_id="__shard__",
                    trial_id=0,
                    success=False,
                    error=f"{type(err).__name__}: {err}",
                    kind="fatal",
                )
            )
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# VS-08 Contract 8: spawn smoke entrypoint (module-level, pickleable).
# ---------------------------------------------------------------------------


def _spawn_smoke_shard_main(
    rank: int,
    work_specs: list[Any],
    progress_queue,
    mp_event: Any,
) -> None:
    """Minimal pickleable shard entrypoint for the spawn smoke test.

    Does NOT build a Playwright env. Emits a ShardLifecycleEnvelope(started),
    a ShardResultEnvelope per spec, a ShardEventEnvelope, acks the shared
    mp.Event cancellation, then emits ShardLifecycleEnvelope(stopped). Used by
    the real-spawn smoke test to verify EpisodeWorkSpec/ShardEventEnvelope
    pickle, mp.Event propagation, and _shard_main importability under spawn.
    """
    # Pickle round-trip of each EpisodeWorkSpec happens implicitly during
    # spawn transport; reconstruct is not exercised here (no registry).
    try:
        progress_queue.put_nowait(ShardLifecycleEnvelope(rank=rank, kind="started"))
        for spec in work_specs:
            episode_key = getattr(spec, "episode_key", f"smoke-{rank}")
            progress_queue.put_nowait(
                ShardResultEnvelope(
                    rank=rank,
                    episode_key=episode_key,
                    result_dict={
                        "id": getattr(spec, "task_base_id", "smoke"),
                        "trial_id": getattr(spec, "trial_id", 0),
                        "is_success": True,
                        "episode_key": episode_key,
                        "execution": {"stop_reason": "complete", "steps": 0},
                    },
                )
            )
        progress_queue.put_nowait(
            ShardEventEnvelope(
                rank=rank,
                event=ExecutionEvent(
                    type="episode.completed", timestamp="", worker_id="W0",
                    episode_key=getattr(work_specs[0], "episode_key", f"smoke-{rank}")
                    if work_specs else None,
                    payload={"outcome": "PASS"},
                ),
            )
        )
        # Ack cancellation: if the parent set the mp.Event, observe it.
        if mp_event is not None and mp_event.is_set():
            progress_queue.put_nowait(
                ShardLifecycleEnvelope(rank=rank, kind="stopped", exitcode=0)
            )
        else:
            # Wait briefly for a cancel signal, then stop cleanly.
            for _ in range(50):
                if mp_event is not None and mp_event.is_set():
                    break
                time.sleep(0.01)
            progress_queue.put_nowait(
                ShardLifecycleEnvelope(rank=rank, kind="stopped", exitcode=0)
            )
    except Exception as err:  # noqa: BLE001
        try:
            progress_queue.put_nowait(
                ShardFatalEnvelope(rank=rank, exitcode=1, error=f"{type(err).__name__}: {err}")
            )
        except Exception:
            pass
        raise


class MultiProcessRunner(BaseRunner):
    """父进程编排多个 ParallelRunner shard。"""

    def __init__(
        self,
        tasks: list[Any],
        config: RunnerConfig,
        *,
        child_runner_factory: Callable[..., Awaitable[list[EpisodeResult]]] | None = None,
        event_sink: EventSink | None = None,
        cancellation_token: CancellationToken | None = None,
        prepared_work_specs: list[Any] | None = None,
        cancel_grace_seconds: float | None = None,
    ):
        self.tasks = tasks
        self.config = config
        self.verbose = not config.quiet
        self.run_dir: Path | None = None
        self._start_time: datetime | None = None
        self._shards: list[ShardSpec] = []
        self._handles: list[ShardHandle] = []
        self._progress_queue = None
        self._completed_keys: set[str] = set()
        self._success_count = 0
        self._fail_count = 0
        # Top-level live aggregation: parent tails each shard's results.jsonl
        # by tracked byte offset and appends new whole lines to run_dir/results.jsonl
        # (and errors.jsonl) so external tooling can watch progress in real time.
        self._top_results_file = None
        self._top_errors_file = None
        self._shard_offsets: dict[int, int] = {}

        # VS-08 platform path wiring.
        self._child_runner_factory = child_runner_factory
        self._external_event_sink = event_sink or NullEventSink()
        self._cancellation_token = cancellation_token or CancellationToken()
        self._prepared_work_specs = prepared_work_specs
        # Result envelopes collected from the queue (authoritative for platform).
        self._collected_results: dict[str, dict[str, Any]] = {}
        # Per-rank coalesce counters (from child QueueEventSink). After the run,
        # if any > 0, emit one stream.events_coalesced event.
        self._coalesce_counts: dict[int, int] = {}
        if cancel_grace_seconds is not None:
            self._cancel_grace_seconds = float(cancel_grace_seconds)
        else:
            self._cancel_grace_seconds = float(
                os.environ.get("MOBILE_GYM_CANCEL_GRACE_SECONDS", "10")
            )

    # The in-process mode is active when a child factory is provided.
    @property
    def _in_process_mode(self) -> bool:
        return self._child_runner_factory is not None

    @property
    def _is_platform_path(self) -> bool:
        """True when prepared_work_specs drive execution (vs CLI task_ids)."""
        return self._prepared_work_specs is not None

    @classmethod
    async def from_args(cls, args: argparse.Namespace) -> "MultiProcessRunner":
        config = RunnerConfig.from_args(args)
        return await cls.from_config(config)

    @classmethod
    async def from_config(
        cls,
        config: RunnerConfig,
        progress_callback: Callable[[EpisodeResult], None] | None = None,
    ) -> "MultiProcessRunner":
        from bench_env import factory

        if config.agent == "human":
            raise ValueError("Multiprocess mode does not support human agent")
        if config.processes < 1:
            raise ValueError("--processes must be >= 1")
        if config.parallel < 1:
            raise ValueError("--parallel must be >= 1")
        if progress_callback is not None:
            raise ValueError("MultiProcessRunner does not support progress_callback")

        tasks = factory.load_tasks(config)
        return cls(tasks, config)

    async def run(self) -> list[dict[str, Any]]:
        from tqdm import tqdm
        from bench_env.logger import tqdm_logging_redirect

        self._prepare_run()
        assert self.run_dir is not None

        monitor_task = self._start_monitor(self.run_dir, self.config) if self.config.monitor else None
        total_episodes = len(self.tasks) * self.config.repeat_n if self.tasks else 0
        if self._is_platform_path:
            total_episodes = len(self._prepared_work_specs or [])
        shard_parallels = [s.parallel for s in self._shards]
        if shard_parallels and min(shard_parallels) == max(shard_parallels):
            parallel_desc = str(shard_parallels[0])
        else:
            parallel_desc = f"min={min(shard_parallels)} max={max(shard_parallels)}" if shard_parallels else "0"
        logger.info(
            f"Tasks: {len(self.tasks)}, Repeat: {self.config.repeat_n}, "
            f"Processes: {len(self._shards)}, Parallel(total): {sum(shard_parallels)}, "
            f"Parallel/shard: {parallel_desc}, Output: {self.run_dir}"
        )

        interrupted: BaseException | None = None
        try:
            self._start_children()
            with tqdm_logging_redirect():
                pbar = tqdm(
                    total=total_episodes,
                    desc="Evaluating",
                    unit="ep",
                    dynamic_ncols=True,
                    disable=not self.verbose,
                )
                try:
                    await self._monitor_children(pbar)
                finally:
                    pbar.close()
        except (KeyboardInterrupt, asyncio.CancelledError) as err:
            logger.warning("Multiprocess run interrupted, terminating child shards")
            await self._cancel_and_terminate()
            interrupted = err
        finally:
            self._stop_monitor(monitor_task)

        # If the run was cancelled (token set), walk the grace→terminate→kill
        # sequence so a child stuck in a critical put() can drain and exit.
        if self._cancellation_token.cancelled and interrupted is None:
            await self._cancel_and_terminate()

        results, summary = self._finalize()
        self._log_summary(summary)

        # VS-08: emit one coalesced event if any child dropped step/metric events.
        self._emit_coalesced_event()

        if interrupted is not None:
            raise interrupted
        return results

    def _effective_processes(self) -> int:
        # Platform path: shard count is derived from prepared_work_specs length.
        if self._is_platform_path:
            specs = self._prepared_work_specs or []
            if not specs:
                return 0
            return max(1, min(self.config.processes, len(specs)))

        if not self.tasks:
            return 0

        effective = max(1, min(self.config.processes, self.config.parallel, len(self.tasks)))
        isolation = str(self.config.isolation)
        browser_budget = int(self.config.num_browsers or 0)

        if (
            browser_budget > 0
            and isolation in {"pages", "contexts"}
            and effective > browser_budget
        ):
            limited = max(1, min(browser_budget, self.config.parallel, len(self.tasks)))
            logger.warning(
                f"--processes {self.config.processes} would create {effective} shards, "
                f"but --browsers {browser_budget} is a total browser budget in "
                f"{isolation} isolation. Using {limited} processes so no shard falls "
                "back to browser auto-allocation."
            )
            return limited

        return effective

    def _prepare_run(self) -> None:
        from bench_env import factory

        self._start_time = datetime.now()
        runs_root = Path(self.config.runs_dir).expanduser().resolve()
        timestamp = self._start_time.strftime("%Y%m%d_%H%M%S")
        self.run_dir = allocate_run_dir(runs_root, timestamp)
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "shards").mkdir(exist_ok=True)
        # Pre-create shared dirs so child shards don't race on mkdir.
        if not self.config.no_save_trajectory:
            (self.run_dir / "trajectory").mkdir(exist_ok=True)
        (self.run_dir / "browser_logs").mkdir(exist_ok=True)

        add_log_file(self.run_dir / "console.log")
        self._open_live_files()

        self._shards = self._build_shards()

        # VS-08 platform path: the agent name is not validated here (the child
        # factory owns agent construction); use the raw config value instead.
        try:
            agent_name = factory.get_agent_name(self.config)
        except Exception:  # noqa: BLE001 — probe/test agents aren't registered
            agent_name = self.config.agent
        meta = {
            "start_time": self._start_time.isoformat(),
            "agent": agent_name,
            "model_name": self.config.model_name,
            "repeat_n": self.config.repeat_n,
            "save_trajectory": not self.config.no_save_trajectory,
            "coord_space": self.config.coord_space,
            "has_pil": True,
            **self.build_run_meta(self.config, self.tasks),
            "effective_processes": len(self._shards),
            "parallel_per_shard": [s.parallel for s in self._shards],
            "browsers_per_shard": [s.num_browsers for s in self._shards],
        }
        (self.run_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _open_live_files(self) -> None:
        assert self.run_dir is not None
        self._top_results_file = (self.run_dir / "results.jsonl").open("w", encoding="utf-8")
        self._top_errors_file = (self.run_dir / "errors.jsonl").open("w", encoding="utf-8")

    def _close_live_files(self) -> None:
        for attr in ("_top_results_file", "_top_errors_file"):
            f = getattr(self, attr, None)
            if f is None:
                continue
            try:
                f.close()
            finally:
                setattr(self, attr, None)

    def _tail_shard_results(self) -> None:
        # Pull newly-flushed whole lines from each shard's results.jsonl into
        # the top-level results.jsonl/errors.jsonl so `tail -f` works during the run.
        if self._top_results_file is None:
            return
        from bench_env.metrics import build_error_entry, result_is_error

        wrote = False
        for spec in self._shards:
            src = spec.run_dir / "results.jsonl"
            if not src.exists():
                continue
            offset = self._shard_offsets.get(spec.rank, 0)
            try:
                size = src.stat().st_size
            except OSError:
                continue
            if size <= offset:
                continue
            with src.open("rb") as f:
                f.seek(offset)
                chunk = f.read(size - offset)
            # Only consume up to the last newline so a partially-written final
            # line is left for the next tick.
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                continue
            consumed = chunk[: last_nl + 1]
            for raw_line in consumed.splitlines():
                if not raw_line.strip():
                    continue
                try:
                    line_str = raw_line.decode("utf-8")
                    row = json.loads(line_str)
                except (UnicodeDecodeError, json.JSONDecodeError) as err:
                    logger.warning(f"[p{spec.rank:02d}] tail skipped malformed line: {err}")
                    continue
                self._top_results_file.write(line_str + "\n")
                if result_is_error(row):
                    self._top_errors_file.write(
                        json.dumps(build_error_entry(row), ensure_ascii=False, default=str) + "\n"
                    )
                wrote = True
            self._shard_offsets[spec.rank] = offset + len(consumed)
        if wrote:
            self._top_results_file.flush()
            self._top_errors_file.flush()

    def _append_results_to_top_level(self, results: list[dict[str, Any]]) -> None:
        if self._top_results_file is None:
            return
        from bench_env.metrics import build_error_entry, result_is_error

        for r in results:
            self._top_results_file.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            if result_is_error(r):
                self._top_errors_file.write(
                    json.dumps(build_error_entry(r), ensure_ascii=False, default=str) + "\n"
                )
        self._top_results_file.flush()
        self._top_errors_file.flush()

    def _build_shards(self) -> list[ShardSpec]:
        assert self.run_dir is not None

        # Platform path: shard the prepared_work_specs across processes.
        if self._is_platform_path:
            return self._build_shards_from_specs()

        k = self._effective_processes()
        chunk_size = max(1, math.ceil(len(self.tasks) / k))
        chunks: list[tuple[int, list[Any]]] = []
        for rank in range(k):
            chunk = self.tasks[rank * chunk_size:(rank + 1) * chunk_size]
            if not chunk:
                continue
            chunks.append((rank, chunk))

        actual_k = len(chunks)
        # Distribute total parallel/browsers across actual shards so the sums
        # equal the user-requested totals (no over-allocation, no extra envs).
        parallel_split = _split_evenly(self.config.parallel, actual_k)
        if self.config.num_browsers > 0:
            browsers_split = _split_evenly(self.config.num_browsers, actual_k)
        else:
            browsers_split = [0] * actual_k

        shards: list[ShardSpec] = []
        for i, (rank, chunk) in enumerate(chunks):
            shards.append(
                ShardSpec(
                    rank=rank,
                    task_ids=[task.id for task in chunk],
                    run_dir=self.run_dir / "shards" / f"p{rank:02d}",
                    parallel=max(1, parallel_split[i]),
                    num_browsers=browsers_split[i],
                )
            )
        return shards

    def _build_shards_from_specs(self) -> list[ShardSpec]:
        """VS-08: distribute prepared_work_specs evenly across shards."""
        assert self.run_dir is not None
        specs = self._prepared_work_specs or []
        k = self._effective_processes()
        if k <= 0 or not specs:
            return []
        chunk_size = max(1, math.ceil(len(specs) / k))
        parallel_split = _split_evenly(self.config.parallel, k)
        if self.config.num_browsers > 0:
            browsers_split = _split_evenly(self.config.num_browsers, k)
        else:
            browsers_split = [0] * k
        shards: list[ShardSpec] = []
        for rank in range(k):
            chunk = specs[rank * chunk_size:(rank + 1) * chunk_size]
            if not chunk:
                continue
            shards.append(
                ShardSpec(
                    rank=rank,
                    task_ids=[getattr(s, "task_base_id", "") for s in chunk],
                    run_dir=self.run_dir / "shards" / f"p{rank:02d}",
                    parallel=max(1, parallel_split[rank]),
                    num_browsers=browsers_split[rank],
                    work_specs=list(chunk),
                )
            )
        return shards

    def _shard_config(self, spec: ShardSpec) -> RunnerConfig:
        return dataclasses.replace(
            self.config,
            parallel=spec.parallel,
            num_browsers=spec.num_browsers,
            processes=1,
            monitor=False,
            quiet=True,
            run_dir=None,
        )

    def _start_children(self) -> None:
        if self._in_process_mode:
            self._start_children_in_process()
        else:
            self._start_children_spawn()

    def _start_children_spawn(self) -> None:
        ctx = mp.get_context("spawn")
        self._progress_queue = ctx.Queue()
        stagger_s = float(os.environ.get("MOBILE_GYM_PROCESS_STAGGER_SEC", "0"))

        for i, spec in enumerate(self._shards):
            child_config = self._shard_config(spec)
            p = ctx.Process(
                target=_shard_main,
                args=(child_config, spec.task_ids, spec.rank, self._progress_queue, spec.run_dir),
                name=f"bench-shard-p{spec.rank:02d}",
            )
            p.start()
            self._handles.append(ShardHandle(spec=spec, process=p))
            logger.info(
                f"[p{spec.rank:02d}] started pid={p.pid}, tasks={len(spec.task_ids)}, "
                f"parallel={spec.parallel}, browsers={spec.num_browsers or 'auto'}"
            )
            if stagger_s > 0 and i + 1 < len(self._shards):
                time.sleep(stagger_s)

    def _start_children_in_process(self) -> None:
        """In-process mode: run each child factory as an asyncio task.

        Uses an asyncio.Queue as the shared queue (QueueEventSink's put_nowait /
        Full handling works on it). Each shard gets a QueueEventSink wrapping
        that queue with its rank; the factory is invoked with
        (shard_spec, work_specs, event_sink, cancellation_token).
        """
        self._progress_queue = asyncio.Queue()
        for spec in self._shards:
            sink = QueueEventSink(self._progress_queue, rank=spec.rank)
            coro = self._child_runner_factory(
                spec, spec.work_specs or [], sink, self._cancellation_token,
            )
            task = asyncio.create_task(
                self._run_in_process_child(spec, coro, sink),
                name=f"bench-shard-p{spec.rank:02d}",
            )
            self._handles.append(ShardHandle(spec=spec, task=task))

    async def _run_in_process_child(
        self, spec: ShardSpec, coro: Awaitable[list[EpisodeResult]], sink: QueueEventSink,
    ) -> None:
        """Await a child factory coroutine, capture results into envelopes, and
        record the coalesce count. Exceptions mark the shard crashed."""
        rank = spec.rank
        try:
            results = await coro
            for r in results:
                episode_key = getattr(r, "episode_key", None)
                if episode_key is None:
                    # Fall back to to_dict's episode_key if present.
                    rd = r.to_dict() if hasattr(r, "to_dict") else {}
                    episode_key = rd.get("episode_key")
                if episode_key is None:
                    continue
                rd = r.to_dict() if hasattr(r, "to_dict") else {}
                self._progress_queue.put_nowait(
                    ShardResultEnvelope(rank=rank, episode_key=episode_key, result_dict=rd)
                )
        except asyncio.CancelledError:
            # Parent tore us down (non-responsive child path). Record coalesce
            # count and re-raise so the task is marked cancelled.
            self._coalesce_counts[rank] = sink.coalesced
            raise
        except Exception as err:  # noqa: BLE001
            logger.exception(f"[p{rank:02d}] in-process shard crashed: {type(err).__name__}: {err}")
            try:
                self._progress_queue.put_nowait(
                    ShardFatalEnvelope(rank=rank, exitcode=1, error=f"{type(err).__name__}: {err}")
                )
            except Exception:
                pass
        finally:
            self._coalesce_counts[rank] = sink.coalesced

    async def _monitor_children(self, pbar) -> None:
        while True:
            await self._drain_progress(pbar)
            self._tail_shard_results()

            all_done = await self._check_children_done()
            if all_done:
                break
            # VS-08: if the run was cancelled, stop waiting for cooperative
            # children and break out so the grace→terminate→kill sequence runs.
            if self._cancellation_token.cancelled:
                break
            await asyncio.sleep(0.2)

        await self._drain_progress(pbar)
        self._tail_shard_results()

    async def _check_children_done(self) -> bool:
        """Return True when every shard handle has finished (process exited or
        asyncio task done). Records exit codes / crash flags."""
        all_done = True
        for handle in self._handles:
            if handle.process is not None:
                p = handle.process
                if p.is_alive():
                    all_done = False
                    continue
                if not handle.reported_exit:
                    p.join(timeout=0)
                    handle.reported_exit = True
                    handle.exitcode = p.exitcode
                    if p.exitcode:
                        handle.crashed = True
                        logger.error(f"[p{handle.spec.rank:02d}] exited with code {p.exitcode}")
                    else:
                        logger.info(f"[p{handle.spec.rank:02d}] completed")
            elif handle.task is not None:
                t = handle.task
                if not t.done():
                    all_done = False
                    continue
                if not handle.reported_exit:
                    handle.reported_exit = True
                    exc = t.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        handle.crashed = True
                        handle.exitcode = 1
                        handle.error = f"{type(exc).__name__}: {exc}"
                        logger.error(f"[p{handle.spec.rank:02d}] task crashed: {handle.error}")
                    elif t.cancelled():
                        handle.exitcode = 0
                        logger.info(f"[p{handle.spec.rank:02d}] task cancelled")
                    else:
                        handle.exitcode = 0
                        logger.info(f"[p{handle.spec.rank:02d}] completed")
        return all_done

    async def _drain_progress(self, pbar) -> None:
        # Bound the inner loop so a flood of events can't block the asyncio loop.
        if self._progress_queue is None:
            return

        BATCH = 64
        processed = 0
        while True:
            try:
                event = self._progress_queue.get_nowait()
            except (queue_mod.Empty, asyncio.QueueEmpty):
                return

            # Legacy CLI ProgressEvent (spawn path): tally + tail file is source.
            if isinstance(event, ProgressEvent):
                if event.kind == "fatal":
                    logger.error(f"[p{event.rank:02d}] fatal: {event.error}")
                    continue
                key = task_trial_key(event.task_id, event.trial_id)
                if key in self._completed_keys:
                    continue
                self._completed_keys.add(key)
                if event.success:
                    self._success_count += 1
                else:
                    self._fail_count += 1
                if pbar:
                    pbar.set_postfix_str(f"✓{self._success_count} ✗{self._fail_count}")
                    pbar.update(1)
            elif isinstance(event, ShardEventEnvelope):
                self._forward_shard_event(event)
                if pbar and event.event.type in {
                    "episode.completed", "episode.error", "episode.cancelled",
                }:
                    self._success_count += 1 if event.event.type == "episode.completed" else 0
                    self._fail_count += 0 if event.event.type == "episode.completed" else 1
                    pbar.set_postfix_str(f"✓{self._success_count} ✗{self._fail_count}")
                    pbar.update(1)
            elif isinstance(event, ShardResultEnvelope):
                # Authoritative result source (platform). Dedup by episode_key
                # at finalize; here just collect.
                if event.episode_key not in self._collected_results:
                    self._collected_results[event.episode_key] = event.result_dict
                else:
                    logger.warning(
                        f"[p{event.rank:02d}] duplicate result envelope for "
                        f"{event.episode_key}; keeping first"
                    )
            elif isinstance(event, ShardFatalEnvelope):
                logger.error(
                    f"[p{event.rank:02d}] fatal exitcode={event.exitcode}: {event.error}"
                )
                self._mark_shard_crashed(event.rank, event.exitcode, event.error)
                self._forward_shard_fatal(event)
            elif isinstance(event, ShardLifecycleEnvelope):
                self._forward_shard_lifecycle(event)
            else:
                continue

            processed += 1
            if processed >= BATCH:
                await asyncio.sleep(0)
                processed = 0

    def _forward_shard_event(self, envelope: ShardEventEnvelope) -> None:
        """Normalize worker_id and forward the wrapped ExecutionEvent to the
        external sink, stamping shard_rank into the payload."""
        rank = envelope.rank
        ev = envelope.event
        normalized_worker = _normalize_worker_id(rank, ev.worker_id)
        # Build a new payload with shard_rank merged in (don't mutate the frozen
        # event / its payload dict in place).
        payload = dict(ev.payload) if ev.payload else {}
        payload.setdefault("shard_rank", rank)
        forwarded = ExecutionEvent(
            type=ev.type,
            timestamp=ev.timestamp,
            phase=ev.phase,
            worker_id=normalized_worker,
            task_id=ev.task_id,
            trial_id=ev.trial_id,
            episode_key=ev.episode_key,
            payload=payload,
        )
        try:
            self._external_event_sink.emit(forwarded)
        except Exception:  # noqa: BLE001 — external sink must not break draining
            logger.debug("external event sink emit failed", exc_info=True)

    def _forward_shard_fatal(self, envelope: ShardFatalEnvelope) -> None:
        rank = envelope.rank
        try:
            self._external_event_sink.emit(ExecutionEvent(
                type="shard.fatal", timestamp="", phase="execute",
                payload={
                    "shard_rank": rank,
                    "exitcode": envelope.exitcode,
                    "error": envelope.error,
                },
            ))
        except Exception:  # noqa: BLE001
            logger.debug("external event sink emit failed (fatal)", exc_info=True)

    def _forward_shard_lifecycle(self, envelope: ShardLifecycleEnvelope) -> None:
        rank = envelope.rank
        event_type = "shard.started" if envelope.kind == "started" else "shard.stopped"
        payload: dict[str, Any] = {"shard_rank": rank}
        if envelope.exitcode is not None:
            payload["exitcode"] = envelope.exitcode
        if envelope.error is not None:
            payload["error"] = envelope.error
        try:
            self._external_event_sink.emit(ExecutionEvent(
                type=event_type, timestamp="", phase="execute", payload=payload,
            ))
        except Exception:  # noqa: BLE001
            logger.debug("external event sink emit failed (lifecycle)", exc_info=True)

    def _mark_shard_crashed(self, rank: int, exitcode: int | None, error: str | None) -> None:
        for handle in self._handles:
            if handle.spec.rank == rank:
                handle.crashed = True
                handle.exitcode = exitcode
                handle.error = error
                break

    def _emit_coalesced_event(self) -> None:
        """After the run, if any child dropped step/metric events, emit one
        ``stream.events_coalesced`` event with the per-rank counts."""
        total = sum(self._coalesce_counts.values())
        if total <= 0:
            return
        try:
            self._external_event_sink.emit(ExecutionEvent(
                type="stream.events_coalesced", timestamp="", phase="execute",
                payload={
                    "total": total,
                    "by_shard": {
                        f"p{rank:02d}": count
                        for rank, count in sorted(self._coalesce_counts.items())
                        if count > 0
                    },
                },
            ))
        except Exception:  # noqa: BLE001
            logger.debug("coalesced event emit failed", exc_info=True)

    async def _cancel_and_terminate(self) -> None:
        """VS-08 Contract 5 cancellation sequence:
        (1) cooperative token signal;
        (2) grace loop for cancel_grace_seconds that CONTINUOUSLY drains the
            queue + tails results (NOT a pure sleep — a child stuck in a
            critical put() can drain and exit);
        (3) terminate();
        (4) join(timeout);
        (5) kill() if still alive.
        For in-process mode, (3)-(5) cancel the asyncio tasks.
        """
        # (1) Cooperative signal.
        try:
            self._cancellation_token.cancel()
        except Exception:  # noqa: BLE001
            pass

        # (2) Grace loop: continuously drain + tail so children can flush.
        deadline = time.monotonic() + self._cancel_grace_seconds
        while time.monotonic() < deadline:
            await self._drain_progress(None)
            self._tail_shard_results()
            if self._all_children_gone():
                return
            await asyncio.sleep(0.05)

        # (3)-(5) terminate / kill.
        self._terminate_children()

    def _all_children_gone(self) -> bool:
        for handle in self._handles:
            if handle.process is not None and handle.process.is_alive():
                return False
            if handle.task is not None and not handle.task.done():
                return False
        return True

    def _terminate_children(self) -> None:
        for handle in self._handles:
            if handle.process is not None and handle.process.is_alive():
                handle.process.terminate()
            elif handle.task is not None and not handle.task.done():
                handle.task.cancel()
        # join / await cancellation
        for handle in self._handles:
            if handle.process is not None and handle.process.is_alive():
                handle.process.join(timeout=5)
                if handle.process.is_alive() and hasattr(handle.process, "kill"):
                    handle.process.kill()
                    handle.process.join(timeout=2)
            # In-process tasks are cancelled synchronously above; nothing to join.

    def _finalize(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert self.run_dir is not None

        # VS-08 platform path: authoritative results come from collected
        # ShardResultEnvelopes, deduped by episode_key in PLAN order.
        if self._is_platform_path:
            return self._finalize_platform()

        from bench_env.metrics import write_summary_json

        # Final tail catches anything children flushed between the last poll
        # and their exit.
        self._tail_shard_results()

        streamed = load_jsonl(self.run_dir / "results.jsonl")
        existing_keys = {result_key(r) for r in streamed}
        task_by_id = {task.id: task for task in self.tasks}
        missing: list[dict[str, Any]] = []
        for spec in self._shards:
            exitcode = self._exitcode_for_rank(spec.rank)
            for task_id in spec.task_ids:
                for trial_id in range(self.config.repeat_n):
                    key = task_trial_key(task_id, trial_id)
                    if key in existing_keys:
                        continue
                    reason = (
                        f"Shard p{spec.rank:02d} exited with code {exitcode}"
                        if exitcode
                        else f"Shard p{spec.rank:02d} produced no result"
                    )
                    missing.append(self._make_missing_result(task_by_id[task_id], trial_id, reason))

        if missing:
            logger.warning(f"Adding {len(missing)} missing-result ERROR entries")
            self._append_results_to_top_level(missing)

        self._close_live_files()

        results = streamed + missing
        summary = write_summary_json(
            self.run_dir,
            results,
            repeat_n=self.config.repeat_n,
            pass_k=self.config.pass_k,
            start_time=self._start_time.isoformat() if self._start_time else None,
        )
        return results, summary

    def _finalize_platform(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Build the deduped, plan-ordered result list from collected envelopes.

        Contract 3: dedup by episode_key BEFORE returning (keep first, warn on
        dup; never raise). Plan order is the order of prepared_work_specs.
        """
        from bench_env.metrics import write_summary_json

        plan_keys = [s.episode_key for s in (self._prepared_work_specs or [])]
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        # First, any collected envelopes (deduped already in _drain_progress).
        # Emit in PLAN order.
        for key in plan_keys:
            if key in self._collected_results and key not in seen:
                seen.add(key)
                results.append(self._collected_results[key])
        # Any envelopes whose key is NOT in the plan (shouldn't happen, but keep
        # them rather than silently dropping) — appended after plan keys.
        for key, rd in self._collected_results.items():
            if key not in seen:
                seen.add(key)
                results.append(rd)

        # Persist for CLI-style tools (best-effort).
        if self._top_results_file is not None:
            self._append_results_to_top_level(results)
        self._close_live_files()

        summary = write_summary_json(
            self.run_dir,
            results,
            repeat_n=max(1, len(plan_keys)),
            pass_k=self.config.pass_k,
            start_time=self._start_time.isoformat() if self._start_time else None,
        )
        return results, summary

    def _exitcode_for_rank(self, rank: int) -> int | None:
        for handle in self._handles:
            if handle.spec.rank == rank:
                return handle.exitcode if handle.exitcode is not None else (
                    handle.process.exitcode if handle.process is not None else None
                )
        return None

    def _make_missing_result(self, task: Any, trial_id: int, reason: str) -> dict[str, Any]:
        now = datetime.now().isoformat()
        execution = {
            "steps": 0,
            "finished": False,
            "truncated": False,
            "stop_reason": "ERROR",
            "agent_message": None,
            "agent_answer": None,
            "runtime_s": 0.0,
            "error": reason,
            "stopwatch_total_s": 0.0,
            "stopwatch_flat": {},
            "stopwatch_tree": [],
        }
        result: dict[str, Any] = {
            "id": task.id,
            "task_name": getattr(task, "description", task.id),
            "suite": getattr(task, "suite", "unknown"),
            "apps": list(getattr(task, "apps", []) or []),
            "trial_id": trial_id,
            "execution": execution,
            "judge": None,
            "is_success": False,
            "is_error": True,
            "progress": 0.0,
            "false_complete": False,
            "overdue_termination": False,
            "max_steps": self.config.get_max_steps(task),
            "start_time": now,
            "end_time": now,
        }
        for field in ("difficulty", "scope", "objective", "composition"):
            value = getattr(task, field, "")
            if value:
                result[field] = value
        caps = list(getattr(task, "capabilities", []) or [])
        if caps:
            result["capabilities"] = caps
        return result

    def _log_summary(self, summary: dict[str, Any]) -> None:
        total = summary.get("total_episodes", 0)
        errors = summary.get("error", 0)
        logger.info(f"\n{'=' * 60}")
        logger.info(f"  RESULTS SUMMARY ({total} episodes, {errors} errors)")
        logger.info(f"{'=' * 60}")
        logger.info(
            f"  Success Rate (SR):              "
            f"{summary.get('success', 0)}/{max(1, total - errors)} = "
            f"{summary.get('success_rate', 0):.1%}"
        )
        logger.info(f"  Failed:                         {summary.get('failed', 0)}")
        logger.info(f"  Errors:                         {errors}")
        logger.info(f"  Output:                         {self.run_dir}")
        logger.info(f"{'=' * 60}")
