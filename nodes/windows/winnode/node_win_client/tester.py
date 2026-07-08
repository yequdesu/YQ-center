from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .daemon import WinNodeDaemon
from .transport import CenterTransport


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    response: dict[str, Any] = field(default_factory=dict)


class IntegrationTester:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.daemon = WinNodeDaemon(settings)
        self.transport = CenterTransport(settings)

    async def close(self) -> None:
        await self.daemon.close()
        await self.transport.close()

    async def run_all(self, include_agent: bool = True) -> list[CheckResult]:
        checks: list[CheckResult] = []
        checks.append(await self._check("node.hello", self.daemon.hello))
        checks.append(
            await self._check("node.register_capabilities", self.daemon.register_capabilities)
        )
        checks.append(await self._check("node.heartbeat", self.daemon.heartbeat))
        checks.append(await self._check("signal.report", self.daemon.report_signals))
        checks.append(await self._check("node.reconcile_jobs", self.daemon.reconcile_jobs))
        checks.append(await self._check("job.poll", self.daemon.poll_once))
        if include_agent:
            checks.append(await self._check("invocation.create", self.create_invocation))
            checks.append(await self._check("agent.invoke", self.invoke_agent))
        return checks

    async def create_invocation(self) -> dict[str, Any]:
        return await self.transport.post_route(
            "invocation.create",
            {
                "actor_id": "win-client-test",
                "session_id": "sess_win_client_test",
                "mode": "auto",
                "function_name": "windows.transfer.croc.status",
                "input": {},
                "target_node_id": self.settings.node_id,
            },
        )

    async def invoke_agent(self) -> dict[str, Any]:
        return await self.transport.post_route(
            "agent.invoke",
            {
                "actor_id": "win-client-test",
                "session_id": "sess_win_client_agent_test",
                "message": "Call windows.transfer.croc.status through Center and summarize the result.",
                "mode": "readonly",
                "limits": {
                    "max_depth": 2,
                    "max_steps": 3,
                    "max_total_duration": 30,
                },
            },
        )

    async def _check(self, name: str, action: Any) -> CheckResult:
        try:
            response = await action()
            return CheckResult(name=name, ok=True, response=response)
        except Exception as exc:
            return CheckResult(name=name, ok=False, detail=str(exc))


async def run_for_duration(settings: Settings, seconds: int) -> None:
    daemon = WinNodeDaemon(settings)
    task = asyncio.create_task(daemon.run())
    try:
        await asyncio.sleep(seconds)
    finally:
        daemon.stop()
        await task
        await daemon.close()

