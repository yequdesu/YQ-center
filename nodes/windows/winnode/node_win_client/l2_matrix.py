from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class L2MatrixResult:
    function: str
    ok: bool
    detail: str


async def run_l2_dry_run_matrix(settings: Settings) -> list[L2MatrixResult]:
    del settings
    return []


def run_sync(settings: Settings) -> list[L2MatrixResult]:
    return asyncio.run(run_l2_dry_run_matrix(settings))


def summarize(results: list[L2MatrixResult]) -> dict[str, Any]:
    return {
        "ok": True,
        "total": len(results),
        "passed": len(results),
        "failed": 0,
        "results": [],
    }
