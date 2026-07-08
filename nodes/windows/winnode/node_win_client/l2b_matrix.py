from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .plugins import FakeSystemPlugin


@dataclass(frozen=True)
class L2BMatrixCase:
    function: str
    input_data: dict[str, Any]
    expected_key: str


@dataclass(frozen=True)
class L2BMatrixResult:
    function: str
    ok: bool
    detail: str


async def run_l2b_matrix(settings: Settings) -> list[L2BMatrixResult]:
    plugin = FakeSystemPlugin(settings.l2_policy)
    with tempfile.TemporaryDirectory(prefix="yequ-l2b-") as temp_dir:
        root = Path(temp_dir)
        sample = root / "sample.log"
        sample.write_text("hello from yequ\n", encoding="utf-8")
        cases = _build_cases(root, sample)
        results: list[L2BMatrixResult] = []
        for case in cases:
            results.append(await _run_case(plugin, case))
        return results


def _build_cases(root: Path, sample: Path) -> list[L2BMatrixCase]:
    del sample
    return [
        L2BMatrixCase("windows.everything.find", {"root": str(root), "query": "sample.log"}, "matches"),
    ]


async def _run_case(plugin: FakeSystemPlugin, case: L2BMatrixCase) -> L2BMatrixResult:
    try:
        output = await plugin.execute(case.function, case.input_data)
        if case.expected_key not in output:
            return L2BMatrixResult(case.function, False, f"missing key: {case.expected_key}")
        if "dry_run" in output and output["dry_run"] is not True:
            return L2BMatrixResult(
                case.function,
                False,
                "write function did not default to dry_run",
            )
        return L2BMatrixResult(case.function, True, "ok")
    except Exception as exc:
        return L2BMatrixResult(case.function, False, str(exc))


def run_sync(settings: Settings) -> list[L2BMatrixResult]:
    return asyncio.run(run_l2b_matrix(settings))


def summarize(results: list[L2BMatrixResult]) -> dict[str, Any]:
    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": [
            {"function": result.function, "ok": result.ok, "detail": result.detail}
            for result in results
        ],
    }

