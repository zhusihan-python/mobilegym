"""Serializable prepared-work spec for multi-process execution.

``PreparedWorkItem`` carries a live ``task`` object that cannot survive spawn
transport. ``EpisodeWorkSpec`` is its pickle-safe analogue: it carries the task
reconstruction payload (task_base_id + seed + params + identity) so a child
process can rebuild the task from the ``TaskRegistry``.

Children reconstruct via :func:`reconstruct_task`, which mirrors
``RegistryTaskFactory.instantiate`` but works from a spec instead of an
``EpisodeTemplate``.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EpisodeWorkSpec:
    """Pickle-safe analogue of ``PreparedWorkItem`` for cross-process transport.

    Fields are all primitives / plain containers so the spec survives ``spawn``
    pickling. ``task_base_id`` + ``params`` + ``instance_seed`` are sufficient
    for ``TaskRegistry`` reconstruction (see :func:`reconstruct_task`).
    """

    episode_key: str
    task_base_id: str
    instance_id: int
    instance_seed: int
    template_index: int | None
    params: dict[str, Any]
    trial_id: int
    max_steps: int


def reconstruct_task(spec: EpisodeWorkSpec, registry: Any) -> Any:
    """Rebuild a ``BaseTask`` from an ``EpisodeWorkSpec`` + ``TaskRegistry``.

    Mirrors ``RegistryTaskFactory.instantiate``: look up the class by
    ``task_base_id``, construct with the prepared seed + explicit params, then
    restore ``_instance_id`` / ``_template_index``. Passing ``**spec.params``
    marks every value as a user parameter so the sampler cannot overwrite it
    (the VS-07 prepared-injection contract).
    """
    task_cls = registry.get_by_id(spec.task_base_id)
    task = task_cls(_seed=spec.instance_seed, **spec.params)
    task._instance_id = spec.instance_id
    task._template_index = spec.template_index
    return task


def spec_is_picklable(spec: EpisodeWorkSpec) -> bool:
    """Return True iff the spec round-trips through pickle (spawn transport)."""
    try:
        restored = pickle.loads(pickle.dumps(spec))
        return restored == spec
    except Exception:
        return False
