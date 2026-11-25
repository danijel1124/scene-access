"""Per-user single-instance lock with a minimal loopback activation channel.

The channel only accepts a fixed activation message: no pickle, file paths,
commands or user data. wx owns the cross-platform process lock; a second start
never creates another application if activation fails.
"""
from __future__ import annotations

import hashlib
import socket
import threading
import time
from pathlib import Path
from typing import Callable

import wx

_REQUEST = b'SCENEACCESS ACTIVATE\n'
_REPLY = b'OK\n'


class SingleInstance:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.port_path = self.root / 'instance.port'
        name = 'sceneaccess-' + hashlib.sha256(str(self.root.resolve()).encode('utf-8')).hexdigest()[:24]
        self._checker = wx.SingleInstanceChecker()
        if not self._checker.Create(name, str(self.root)):
            raise RuntimeError('The SceneAccess single-instance lock could not be created.')
        self.is_primary = not self._checker.IsAnotherRunning()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._port: int | None = None

    def start(self, on_activate: Callable[[], None]) -> None:
        if not self.is_primary or self._socket:
            raise RuntimeError('Only the primary instance may open the activation listener.')
        listener = self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(4)
        listener.settimeout(0.2)
        self._port = listener.getsockname()[1]
        temporary = self.port_path.with_suffix('.tmp')
        temporary.write_text(str(self._port), encoding='ascii')
        temporary.replace(self.port_path)

        def serve() -> None:
            while not self._stop.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    with connection:
                        connection.settimeout(0.3)
                        with connection.makefile('rb') as stream:
                            if stream.readline(32) == _REQUEST:
                                on_activate()
                                connection.sendall(_REPLY)
                except OSError:
                    continue

        self._thread = threading.Thread(target=serve, daemon=True, name='SceneAccess activation')
        self._thread.start()

    def activate_existing(self, timeout: float = 3) -> bool:
        if self.is_primary:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                port = int(self.port_path.read_text(encoding='ascii'))
                if not 1024 <= port <= 65535:
                    return False
                with socket.create_connection(('127.0.0.1', port), timeout=0.3) as connection:
                    connection.sendall(_REQUEST)
                    with connection.makefile('rb') as stream:
                        if stream.readline(16) == _REPLY:
                            return True
            except (OSError, ValueError):
                pass
            self._stop.wait(0.05)
        return False

    def close(self) -> None:
        self._stop.set()
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._thread:
            self._thread.join(1)
            self._thread = None
        if self.is_primary and self._port is not None:
            try:
                if self.port_path.read_text(encoding='ascii') == str(self._port):
                    self.port_path.unlink()
            except OSError:
                pass
        self._checker = None  # Destroying the wx object releases its OS-managed lock.
