"""Architecture boundary checks for long-term maintainability."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "yequ"


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _python_files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def test_services_do_not_import_agent() -> None:
    offenders = []
    for path in _python_files(SRC / "services"):
        for imported in _imports_for(path):
            if imported == "yequ.agent" or imported.startswith("yequ.agent."):
                offenders.append((path.relative_to(ROOT).as_posix(), imported))

    assert offenders == []


def test_application_does_not_import_api_or_agent() -> None:
    app_dir = SRC / "application"
    if not app_dir.exists():
        return

    offenders = []
    for path in _python_files(app_dir):
        for imported in _imports_for(path):
            if (
                imported == "yequ.api"
                or imported.startswith("yequ.api.")
                or imported == "yequ.agent"
                or imported.startswith("yequ.agent.")
            ):
                offenders.append((path.relative_to(ROOT).as_posix(), imported))

    assert offenders == []


def test_agent_service_uses_application_for_tool_execution() -> None:
    forbidden = {
        "yequ.services.approval_service",
        "yequ.services.capability_resolver",
        "yequ.services.invocation_service",
        "yequ.services.job_service",
    }
    path = SRC / "agent" / "agent_service.py"

    offenders = sorted(imported for imported in _imports_for(path) if imported in forbidden)

    assert offenders == []


def test_agent_stream_preflight_uses_application_queries() -> None:
    forbidden = {
        "yequ.models.capability",
        "yequ.services.capability_resolver",
        "yequ.services.policy",
    }
    path = SRC / "agent" / "agent_stream.py"

    offenders = sorted(imported for imported in _imports_for(path) if imported in forbidden)

    assert offenders == []


def test_agent_runtime_files_do_not_import_center_services() -> None:
    offenders = []
    for filename in ("agent_service.py", "agent_stream.py"):
        path = SRC / "agent" / filename
        for imported in _imports_for(path):
            if imported == "yequ.services" or imported.startswith("yequ.services."):
                offenders.append((path.relative_to(ROOT).as_posix(), imported))

    assert offenders == []


def test_runtime_has_no_unregistered_capability_escape_hatch() -> None:
    forbidden_terms = {
        "allow_unregistered_function",
        "_resolve_unregistered_admin_function",
    }
    offenders = []
    for path in _python_files(SRC):
        text = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            if term in text:
                offenders.append((path.relative_to(ROOT).as_posix(), term))

    assert offenders == []
