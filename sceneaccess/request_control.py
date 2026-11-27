"""Cooperative request boundaries and interruption of active network/process I/O."""
from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable


class RequestCancelled(Exception):
    """The user cancelled this request; never treat it as a provider failure."""


class RequestControl:
    def __init__(self):
        self._condition = threading.Condition()
        self._cancelled = False
        self._pause = False
        self._interrupt: Callable[[], None] | None = None

    @property
    def pause_requested(self) -> bool:
        with self._condition:
            return self._pause

    @property
    def cancelled(self) -> bool:
        """Whether the request was cancelled, e.g. to say so when its answer is kept all the same."""
        with self._condition:
            return self._cancelled

    def check_cancelled(self) -> None:
        with self._condition:
            if self._cancelled:
                raise RequestCancelled()

    def register_interrupt(self, interrupt: Callable[[], None] | None) -> None:
        with self._condition:
            self._interrupt = interrupt
            cancelled = self._cancelled
        if cancelled and interrupt:
            interrupt()
            raise RequestCancelled()

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            interrupt = self._interrupt
            self._condition.notify_all()
        if interrupt:
            # Closing an already closed socket/process is harmless.
            with contextlib.suppress(OSError):
                interrupt()

    def pause(self) -> None:
        with self._condition:
            self._pause = True

    def resume(self) -> None:
        with self._condition:
            self._pause = False
            self._condition.notify_all()

    def checkpoint(self, on_paused: Callable[[], None] | None = None) -> None:
        with self._condition:
            self.check_cancelled()
            if self._pause and on_paused:
                on_paused()
            while self._pause and not self._cancelled:
                self._condition.wait()
            self.check_cancelled()
