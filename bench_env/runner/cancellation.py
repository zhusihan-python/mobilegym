from __future__ import annotations

import threading
from typing import Any


class RunCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelled()


class MultiprocCancelToken(CancellationToken):
    """Cancellation token backed by an ``multiprocessing.Event``.

    VS-08: a child process cannot observe a ``threading.Event`` set in the
    parent, so multi-process runs share an ``mp.Event`` (created in a spawn
    context). This token exposes the same ``cancelled`` / ``cancel`` /
    ``raise_if_cancelled`` surface as :class:`CancellationToken`, but reads and
    sets the shared ``mp.Event`` instead. The ``.mp_event`` attribute lets the
    parent pass the same event to every child shard.

    When ``mp_event`` is None the token falls back to plain threading.Event
    behaviour (cooperative-only fallback for in-process / CLI paths that never
    spawn children).
    """

    def __init__(self, mp_event: Any = None) -> None:
        # Do NOT call super().__init__: we override _event below. We still keep
        # a private threading.Event so raise_if_cancelled / external code that
        # touches ``_event`` keeps working in the fallback path.
        self._event = mp_event if mp_event is not None else threading.Event()
        self.mp_event = mp_event

    @property
    def cancelled(self) -> bool:
        if self.mp_event is not None:
            return bool(self.mp_event.is_set())
        return self._event.is_set()

    def cancel(self) -> None:
        if self.mp_event is not None:
            self.mp_event.set()
        else:
            self._event.set()
