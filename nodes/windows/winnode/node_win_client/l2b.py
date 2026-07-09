from __future__ import annotations

import csv
import io
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import L2Policy

SEARCH_MAX_RESULTS = 1000


def execute_l2b(function: str, input_data: dict[str, Any], l2_policy: L2Policy) -> dict[str, Any]:
    if function == "windows.everything.find":
        return file_search(input_data, l2_policy)
    raise ValueError(f"function not supported by l2b: {function}")


def file_search(input_data: dict[str, Any], l2_policy: L2Policy) -> dict[str, Any]:
    query = str(input_data.get("query") or "").strip()
    root_value = input_data.get("root")
    root = _validate_file_path(root_value, l2_policy) if root_value else None
    mode = _normalize_search_mode(input_data.get("mode"), query)
    recursive = bool(input_data.get("recursive", True))
    include_files = bool(input_data.get("include_files", True))
    include_dirs = bool(input_data.get("include_dirs", True))
    case_sensitive = bool(input_data.get("case_sensitive", False))
    limit = max(1, min(int(input_data.get("limit", 100)), SEARCH_MAX_RESULTS))
    warnings: list[str] = []
    everything_path = _find_everything_cli()

    if everything_path is None:
        raise ValueError(
            "everything_unavailable: es.exe was not found. Configure YEQU_EVERYTHING_CLI."
        )
    if not query and root is None:
        raise ValueError("windows.everything.find requires query or root")
    if not include_files and not include_dirs:
        raise ValueError("windows.everything.find requires include_files or include_dirs")

    matcher = _compile_file_search_matcher(query, mode, case_sensitive)
    candidates, candidate_warning = _everything_candidates(
        everything_path,
        query,
        root=root,
        mode=mode,
        recursive=recursive,
        include_files=include_files,
        include_dirs=include_dirs,
        case_sensitive=case_sensitive,
        limit=limit * 10,
    )
    if candidate_warning:
        raise ValueError(candidate_warning)

    matches: list[dict[str, Any]] = []
    for path in candidates:
        if len(matches) >= limit:
            break
        try:
            if root and not _is_relative_to(path, root):
                continue
            _validate_file_path(str(path), l2_policy)
            is_file = path.is_file()
            is_dir = path.is_dir()
            if is_file and not include_files:
                continue
            if is_dir and not include_dirs:
                continue
            score, match_type = matcher(path)
            if score <= 0:
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        matches.append(
            {
                "path": str(path),
                "name": path.name,
                "is_file": is_file,
                "is_dir": is_dir,
                "size": stat.st_size if is_file else 0,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "score": round(score, 4),
                "match_type": match_type,
            }
        )

    matches.sort(key=lambda item: (-float(item["score"]), str(item["path"]).lower()))
    return {
        "backend": "everything",
        "query": query,
        "mode": mode,
        "root": str(root) if root else None,
        "recursive": recursive,
        "limit": limit,
        "truncated": len(matches) >= limit,
        "candidate_count": len(candidates),
        "everything_available": True,
        "everything_path": everything_path,
        "matches": matches[:limit],
        "warnings": warnings,
    }


def _normalize_search_mode(value: Any, query: str) -> str:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "exact", "glob", "substring", "fuzzy", "regex"}:
        raise ValueError(f"unsupported file search mode: {mode}")
    if mode != "auto":
        return mode
    if not query:
        return "substring"
    if any(char in query for char in "*?[]"):
        return "glob"
    return "substring"


def _compile_file_search_matcher(
    query: str, mode: str, case_sensitive: bool
) -> Any:
    needle = query if case_sensitive else query.lower()
    if mode == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query, flags)

        def regex_match(path: Path) -> tuple[float, str]:
            haystack = str(path)
            if pattern.search(path.name):
                return (1.0, "regex_name")
            if pattern.search(haystack):
                return (0.95, "regex_path")
            return (0.0, "none")

        return regex_match

    if mode == "glob":
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(fnmatch_translate(query), flags)

        def glob_match(path: Path) -> tuple[float, str]:
            if pattern.fullmatch(path.name):
                return (1.0, "glob_name")
            if pattern.fullmatch(str(path)):
                return (0.95, "glob_path")
            return (0.0, "none")

        return glob_match

    if mode == "exact":

        def exact_match(path: Path) -> tuple[float, str]:
            name = path.name if case_sensitive else path.name.lower()
            full = str(path) if case_sensitive else str(path).lower()
            if name == needle:
                return (1.0, "exact_name")
            if full == needle:
                return (0.95, "exact_path")
            return (0.0, "none")

        return exact_match

    if mode == "fuzzy":
        fuzzy_needle = "".join(char for char in needle if not char.isspace())

        def fuzzy_match(path: Path) -> tuple[float, str]:
            name = path.name if case_sensitive else path.name.lower()
            full = str(path) if case_sensitive else str(path).lower()
            if needle in name:
                return (0.92, "substring_name")
            if needle in full:
                return (0.82, "substring_path")
            score = _subsequence_score(fuzzy_needle, name)
            if score >= 0.6:
                return (score, "fuzzy_name")
            score = _subsequence_score(fuzzy_needle, full)
            if score >= 0.68:
                return (score * 0.9, "fuzzy_path")
            return (0.0, "none")

        return fuzzy_match

    def substring_match(path: Path) -> tuple[float, str]:
        if not needle:
            return (0.5, "root_listing")
        name = path.name if case_sensitive else path.name.lower()
        full = str(path) if case_sensitive else str(path).lower()
        if needle in name:
            return (0.9, "substring_name")
        if needle in full:
            return (0.8, "substring_path")
        return (0.0, "none")

    return substring_match


def _subsequence_score(needle: str, haystack: str) -> float:
    if not needle:
        return 0.0
    index = 0
    first = -1
    last = -1
    for pos, char in enumerate(haystack):
        if char != needle[index]:
            continue
        if first < 0:
            first = pos
        last = pos
        index += 1
        if index == len(needle):
            span = max(1, last - first + 1)
            density = len(needle) / span
            coverage = len(needle) / max(1, len(haystack))
            return max(0.35, min(0.78, 0.45 + density * 0.25 + coverage * 0.1))
    return 0.0


def _find_everything_cli() -> str | None:
    configured = os.getenv("YEQU_EVERYTHING_CLI") or os.getenv("EVERYTHING_CLI")
    candidates = [
        configured,
        shutil.which("es.exe"),
        shutil.which("es"),
        r"C:\Program Files\Everything\es.exe",
        r"C:\Program Files (x86)\Everything\es.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def fnmatch_translate(pattern: str) -> str:
    # Keep glob matching deterministic without importing fnmatch for one call.
    escaped = ""
    for char in pattern:
        if char == "*":
            escaped += ".*"
        elif char == "?":
            escaped += "."
        else:
            escaped += re.escape(char)
    return escaped


def _everything_candidates(
    es_path: str,
    query: str,
    *,
    root: Path | None,
    mode: str,
    recursive: bool,
    include_files: bool,
    include_dirs: bool,
    case_sensitive: bool,
    limit: int,
) -> tuple[list[Path], str | None]:
    search_text = _everything_search_text(query, mode)
    command = [
        es_path,
        "-timeout",
        "5000",
        "-n",
        str(max(1, min(limit, SEARCH_MAX_RESULTS * 10))),
        "-full-path-and-name",
        "-size",
        "-date-modified",
        "-size-format",
        "1",
        "-date-format",
        "3",
        "-tsv",
        "-no-header",
    ]
    if case_sensitive:
        command.append("-case")
    if mode == "regex":
        command.extend(["-regex", search_text])
    else:
        command.append(search_text)
    if root is not None:
        command.extend(["-path" if recursive else "-parent", str(root)])
    if include_files and not include_dirs:
        command.append("/a-d")
    elif include_dirs and not include_files:
        command.append("/ad")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"everything_query_failed: {exc}"
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return [], f"everything_query_failed: {message or completed.returncode}"
    paths: list[Path] = []
    reader = csv.reader(io.StringIO(completed.stdout), delimiter="\t")
    for row in reader:
        if not row:
            continue
        paths.append(Path(row[0]))
    return paths, None


def _everything_search_text(query: str, mode: str) -> str:
    if not query:
        return "*"
    if mode == "fuzzy":
        chars = [re.escape(char) for char in query if not char.isspace()]
        return "*" + "*".join(chars) + "*" if chars else "*"
    return query


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _object_schema(name: str) -> dict[str, Any]:
    return {"type": "object", "properties": {name: {"type": "object"}}}


def _l2b_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "action": {"type": "string"},
            "target_type": {"type": "string"},
            "target": {"type": "object"},
            "before": {"type": ["object", "null"]},
            "after": {"type": ["object", "null"]},
            "audit": {"type": "object"},
        },
    }


def _validate_file_path(
    value: Any,
    l2_policy: L2Policy,
    *,
    enforce_roots: bool = False,
) -> Path:
    if value is None:
        raise ValueError("path is required")
    path = Path(os.path.expandvars(_expand_user_path_alias(str(value)))).expanduser().resolve()
    if not enforce_roots:
        return path
    allowed_roots = [_resolve_configured_path(item) for item in l2_policy.allowed_file_roots]
    forbidden_roots = [_resolve_configured_path(item) for item in l2_policy.forbidden_file_paths]
    if not any(_path_is_relative_to(path, root) for root in allowed_roots if str(root)):
        raise ValueError(f"path is not in L2 file root allowlist: {path}")
    if any(_path_is_relative_to(path, root) for root in forbidden_roots if str(root)):
        raise ValueError(f"path is forbidden: {path}")
    return path


def _expand_user_path_alias(value: str) -> str:
    text = value.strip().strip('"')
    normalized = text.replace("/", "\\").strip("\\").lower()
    aliases = {
        "desktop": "Desktop",
        "妗岄潰": "Desktop",
        "downloads": "Downloads",
        "download": "Downloads",
        "涓嬭浇": "Downloads",
        "documents": "Documents",
        "document": "Documents",
        "鏂囨。": "Documents",
    }
    if normalized not in aliases:
        return value
    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        return value
    folder_name = aliases[normalized]
    one_drive = os.environ.get("ONEDRIVE") or os.environ.get("ONEDRIVECONSUMER")
    candidates: list[Path] = []
    if one_drive:
        candidates.append(Path(one_drive) / folder_name)
    candidates.append(Path(user_profile) / folder_name)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def _resolve_configured_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    if expanded == "%TEMP%":
        expanded = os.environ.get("TEMP", "")
    if expanded == "%TMP%":
        expanded = os.environ.get("TMP", "")
    return Path(expanded).expanduser().resolve()


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _run_powershell_json(command: str, env_vars: dict[str, str], timeout_sec: int) -> Any:
    env = os.environ.copy()
    env.update(env_vars)
    prefix = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", prefix + command],
        capture_output=True,
        check=False,
        creationflags=_subprocess_creation_flags(),
        encoding="utf-8",
        env=env,
        errors="replace",
        text=True,
        timeout=timeout_sec,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return json.loads(completed.stdout)


def _run_powershell_action(command: str, env_vars: dict[str, str], timeout_sec: int) -> None:
    env = os.environ.copy()
    env.update(env_vars)
    prefix = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", prefix + command],
        capture_output=True,
        check=False,
        creationflags=_subprocess_creation_flags(),
        encoding="utf-8",
        env=env,
        errors="replace",
        text=True,
        timeout=timeout_sec,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"powershell command failed: {completed.returncode}")


def _subprocess_creation_flags() -> int:
    if platform.system().lower() != "windows":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _redact_username(value: Any) -> str | None:
    if value is None:
        return None
    username = str(value)
    if "\\" in username:
        return username.split("\\", 1)[-1]
    return username
