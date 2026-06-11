"""Local IPC transport — Unix socket server for local collector.

Phase 1 stub: the local collector writes directly to the database
through the CollectorRunner, bypassing the network stack.
The Unix socket endpoint is defined here for future use.
"""

from __future__ import annotations

import logging
import os
import socketserver
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalIPCHandler(socketserver.BaseRequestHandler):
    """Handler for Unix socket connections.

    Phase 1: this is a stub. Local collector uses direct DB access.
    Phase 2+: local collector can send YQP messages over this socket.
    """

    def handle(self) -> None:
        data = self.request.recv(65536)
        if data:
            logger.debug("Local IPC received %d bytes", len(data))
            # Placeholder: parse and process YQP message
            self.request.sendall(b'{"status":"ok"}')


class LocalIPCTransport:
    """Unix socket server for receiving YQP messages from local processes."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._server: socketserver.UnixStreamServer | None = None

    def start(self) -> None:
        """Start the Unix socket server in a background thread."""
        socket_dir = os.path.dirname(self.socket_path)
        os.makedirs(socket_dir, exist_ok=True)

        # Remove stale socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = socketserver.ThreadingUnixStreamServer(
            self.socket_path, LocalIPCHandler
        )
        import threading
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Local IPC server listening on %s", self.socket_path)

    def stop(self) -> None:
        """Stop the Unix socket server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
