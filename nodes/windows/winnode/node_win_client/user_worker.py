from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import load_settings
from .errors import normalize_execution_error
from .plugins import FakeSystemPlugin
from .runtime_context import user_context_required


class UserWorkerHandler(BaseHTTPRequestHandler):
    server_version = "YeQuUserWorker/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        self._send_json(
            200,
            {
                "ok": True,
                "user": getpass.getuser(),
                "pid": os.getpid(),
                "execution_context": "user",
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/execute":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            function = str(data.get("function", ""))
            input_data = data.get("input") if isinstance(data.get("input"), dict) else {}
            if not user_context_required(function):
                self._send_json(
                    403,
                    {
                        "ok": False,
                        "error": {
                            "code": "user_worker_function_denied",
                            "message": f"Function is not allowed in user worker: {function}",
                        },
                    },
                )
                return
            result = asyncio.run(self.server.plugin.execute(function, input_data))  # type: ignore[attr-defined]
            self._send_json(200, {"ok": True, "result": result})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": normalize_execution_error(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UserWorkerServer(ThreadingHTTPServer):
    plugin: FakeSystemPlugin


def serve(config: str = "config.local.yaml", host: str = "127.0.0.1", port: int = 9817) -> None:
    os.environ["YEQU_USER_WORKER"] = "1"
    settings = load_settings(config)
    server = UserWorkerServer((host, port), UserWorkerHandler)
    server.plugin = FakeSystemPlugin(
        settings.l2_policy,
        settings.transfer.yq_croc,
        center_base_url=settings.center_base_url,
        node_token=settings.node_token,
        request_timeout_sec=settings.request_timeout_sec,
    )
    print(f"YeQu user worker listening on http://{host}:{port} as {getpass.getuser()}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="YeQu Windows user-session worker")
    parser.add_argument("--config", default="config.local.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("YEQU_USER_WORKER_PORT", "9817")))
    args = parser.parse_args()
    serve(config=args.config, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
