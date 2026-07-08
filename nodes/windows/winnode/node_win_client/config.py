from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROUTES: dict[str, str] = {
    "node.hello": "/yqp",
    "node.register_capabilities": "/yqp",
    "node.heartbeat": "/yqp",
    "signal.report": "/yqp",
    "job.poll": "/yqp",
    "job.accepted": "/yqp",
    "job.event": "/yqp",
    "job.finished": "/yqp",
    "job.lease_renew": "/yqp",
    "node.reconcile_jobs": "/yqp",
    "artifact.upload": "/yqp",
    "agent.invoke": "/api/agent/invoke",
    "invocation.create": "/api/invocations",
}

DEFAULT_L2_ALLOWED_SERVICES = ["Spooler", "W32Time", "BITS"]
DEFAULT_L2_ALLOWED_TASK_PREFIXES = ["\\YeQu\\"]
DEFAULT_L2_ALLOWED_TEMP_PATHS = ["%TEMP%", "C:\\Windows\\Temp"]
DEFAULT_L2_ALLOWED_FILE_ROOTS = [
    "%TEMP%",
    "%USERPROFILE%\\Desktop",
    "%USERPROFILE%\\Downloads",
    "%USERPROFILE%\\Documents",
    "%PUBLIC%\\Desktop",
]
DEFAULT_L2_FORBIDDEN_FILE_PATHS = [
    "C:\\Windows\\System32",
    "%USERPROFILE%\\.ssh",
    "%USERPROFILE%\\.gnupg",
    "%LOCALAPPDATA%\\Google\\Chrome\\User Data",
    "%APPDATA%\\Mozilla\\Firefox\\Profiles",
]
DEFAULT_L2_ALLOWED_TEMPLATES = [
    "check_port_owner",
    "clear_app_cache",
    "collect_iis_logs",
    "rotate_app_logs",
]

ALPHA_CONFIG_TEMPLATE = """# YeQu Windows Client Alpha Configuration
center:
  base_url: "https://gtw.yequdesu.top"
  yqp_path: "/yqp/"
  timeout_sec: 30

node:
  node_id: "winClient"
  node_name: "Windows Client"
  token: "winc-token-4a7f3c9e1b2d8f6c"
  role:
    - "compute"
  locality: "lan"

daemon:
  heartbeat_interval_sec: 10
  signal_report_interval_sec: 5
  job_poll_interval_sec: 3
  max_concurrent_jobs: 4
  capability_refresh_interval_sec: 300
  reconnect_initial_delay_sec: 2
  reconnect_max_delay_sec: 60
  reconnect_ready_delay_sec: 5
  reconnect_probe_interval_sec: 2

runtime:
  mode: "hybrid"
  gui_start_at_login: false
  close_to_tray: true
  service_startup: "automatic"

paths:
  data_dir: "./data"
  log_dir: "./logs"

safety:
  allow_write_actions: true
  allowed_services:
    - "Spooler"
    - "wuauserv"
    - "WinDefend"
    - "EventLog"

cache:
  job_result_ttl_hours: 24

transfer:
  yq_croc:
    enabled: true
    binary_path: "./tools/yq-croc/current/yq-croc.exe"
    relay_url: null
    relay_password_env: null
    temp_dir: "./data/transfers"
    allow_send: true
    allow_receive: true
    max_concurrent_transfers: 1
"""


@dataclass(frozen=True)
class L2Policy:
    allow_write_actions: bool = True
    allowed_services: list[str] = field(default_factory=lambda: list(DEFAULT_L2_ALLOWED_SERVICES))
    allowed_task_prefixes: list[str] = field(
        default_factory=lambda: list(DEFAULT_L2_ALLOWED_TASK_PREFIXES)
    )
    allowed_temp_paths: list[str] = field(
        default_factory=lambda: list(DEFAULT_L2_ALLOWED_TEMP_PATHS)
    )
    allowed_file_roots: list[str] = field(
        default_factory=lambda: list(DEFAULT_L2_ALLOWED_FILE_ROOTS)
    )
    forbidden_file_paths: list[str] = field(
        default_factory=lambda: list(DEFAULT_L2_FORBIDDEN_FILE_PATHS)
    )
    allowed_templates: list[str] = field(default_factory=lambda: list(DEFAULT_L2_ALLOWED_TEMPLATES))


@dataclass(frozen=True)
class PathsConfig:
    data_dir: str = "./data"
    log_dir: str = "./logs"


@dataclass(frozen=True)
class CacheConfig:
    job_result_ttl_hours: float = 24


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "hybrid"
    gui_start_at_login: bool = False
    close_to_tray: bool = True
    service_startup: str = "automatic"


@dataclass(frozen=True)
class TransferYqCrocConfig:
    enabled: bool = True
    binary_path: str | None = "./tools/yq-croc/current/yq-croc.exe"
    relay_url: str | None = None
    relay_password_env: str | None = None
    temp_dir: str = "./data/transfers"
    allow_send: bool = True
    allow_receive: bool = True
    max_concurrent_transfers: int = 1


@dataclass(frozen=True)
class TransferConfig:
    yq_croc: TransferYqCrocConfig = field(default_factory=TransferYqCrocConfig)


@dataclass(frozen=True)
class Settings:
    center_base_url: str = "http://127.0.0.1:8000"
    node_id: str = "win-client"
    node_name: str = "Windows Client"
    node_token: str = "dev-node-token"
    api_token: str | None = None
    locality: str = "lan"
    role: list[str] = field(default_factory=lambda: ["client", "compute"])
    heartbeat_interval_sec: int = 10
    signal_report_interval_sec: int = 5
    job_poll_interval_sec: int = 3
    request_timeout_sec: float = 10.0
    send_job_events: bool = True
    routes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROUTES))
    l2_policy: L2Policy = field(default_factory=L2Policy)
    paths: PathsConfig = field(default_factory=PathsConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    transfer: TransferConfig = field(default_factory=TransferConfig)
    allow_write_actions: bool = True
    allowed_services: list[str] = field(default_factory=list)
    reconnect_initial_delay_sec: int = 2
    reconnect_max_delay_sec: int = 60
    reconnect_ready_delay_sec: int = 5
    reconnect_probe_interval_sec: int = 2
    max_concurrent_jobs: int = 4
    capability_refresh_interval_sec: int = 300


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("config YAML must be an object")
    return data


def init_config(output_path: str | Path = "config.local.yaml", overwrite: bool = False) -> dict:
    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Config already exists at {path}. Use --overwrite to replace.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ALPHA_CONFIG_TEMPLATE, encoding="utf-8")
    return {"path": str(path), "written": True}


def validate_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = _read_config(path)
    errors: list[str] = []

    center = data.get("center", {})
    if not center.get("base_url"):
        errors.append("center.base_url is required")

    node = data.get("node", {})
    if not node.get("node_id"):
        errors.append("node.node_id is required")
    if not node.get("token"):
        errors.append("node.token is required")

    safety = data.get("safety", {})
    if safety.get("allow_write_actions") is None:
        errors.append("safety.allow_write_actions is required")

    if errors:
        raise ValueError("Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    return {"valid": True, "path": str(path)}


def write_config(config_dict: dict[str, Any], output_path: str | Path) -> dict:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        config_dict,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "written": True}


def load_settings(config_path: str | None = None) -> Settings:
    path_value = config_path or os.getenv("NODE_WIN_CONFIG") or "config.local.yaml"
    data = _read_config(Path(path_value))

    center = data.get("center", {}) if isinstance(data.get("center"), dict) else {}
    node = data.get("node", {}) if isinstance(data.get("node"), dict) else {}
    daemon = data.get("daemon", {}) if isinstance(data.get("daemon"), dict) else {}
    paths_data = data.get("paths", {}) if isinstance(data.get("paths"), dict) else {}
    safety = data.get("safety", {}) if isinstance(data.get("safety"), dict) else {}
    cache_data = data.get("cache", {}) if isinstance(data.get("cache"), dict) else {}
    runtime_data = data.get("runtime", {}) if isinstance(data.get("runtime"), dict) else {}
    transfer_data = data.get("transfer", {}) if isinstance(data.get("transfer"), dict) else {}
    yq_croc_data = (
        transfer_data.get("yq_croc", {}) if isinstance(transfer_data.get("yq_croc"), dict) else {}
    )

    routes = dict(DEFAULT_ROUTES)
    yqp_path = center.get("yqp_path", "/yqp/")
    # Update all default YQP routes to use configured yqp_path
    for key in list(routes.keys()):
        if not key.startswith("agent.") and not key.startswith("invocation."):
            routes[key] = yqp_path
    route_overrides = center.get("routes", {})
    if isinstance(route_overrides, dict):
        routes.update({str(k): str(v) for k, v in route_overrides.items()})

    l2_data = data.get("l2", {})
    if not isinstance(l2_data, dict):
        l2_data = {}
    safety_allowed_services = _safe_str_list(safety.get("allowed_services", []))
    l2_allowed_services = _safe_str_list(
        l2_data.get("allowed_services", safety_allowed_services or DEFAULT_L2_ALLOWED_SERVICES)
    )
    allow_write_actions = bool(safety.get("allow_write_actions", True))

    l2_policy = L2Policy(
        allow_write_actions=allow_write_actions,
        allowed_services=l2_allowed_services,
        allowed_task_prefixes=_safe_str_list(
            l2_data.get("allowed_task_prefixes", DEFAULT_L2_ALLOWED_TASK_PREFIXES)
        ),
        allowed_temp_paths=_safe_str_list(
            l2_data.get("allowed_temp_paths", DEFAULT_L2_ALLOWED_TEMP_PATHS)
        ),
        allowed_file_roots=_safe_str_list(
            l2_data.get("allowed_file_roots", DEFAULT_L2_ALLOWED_FILE_ROOTS)
        ),
        forbidden_file_paths=_safe_str_list(
            l2_data.get("forbidden_file_paths", DEFAULT_L2_FORBIDDEN_FILE_PATHS)
        ),
        allowed_templates=_safe_str_list(
            l2_data.get("allowed_templates", DEFAULT_L2_ALLOWED_TEMPLATES)
        ),
    )

    paths_config = PathsConfig(
        data_dir=paths_data.get("data_dir", "./data"),
        log_dir=paths_data.get("log_dir", "./logs"),
    )

    cache_config = CacheConfig(
        job_result_ttl_hours=float(cache_data.get("job_result_ttl_hours", 24)),
    )
    runtime_config = RuntimeConfig(
        mode=_runtime_mode(runtime_data.get("mode", "hybrid")),
        gui_start_at_login=bool(runtime_data.get("gui_start_at_login", False)),
        close_to_tray=bool(runtime_data.get("close_to_tray", True)),
        service_startup=str(runtime_data.get("service_startup", "automatic")),
    )
    transfer_config = TransferConfig(
        yq_croc=TransferYqCrocConfig(
            enabled=bool(yq_croc_data.get("enabled", True)),
            binary_path=_optional_str(
                yq_croc_data.get("binary_path", "./tools/yq-croc/current/yq-croc.exe")
            ),
            relay_url=_optional_str(yq_croc_data.get("relay_url")),
            relay_password_env=_optional_str(yq_croc_data.get("relay_password_env")),
            temp_dir=str(yq_croc_data.get("temp_dir", "./data/transfers")),
            allow_send=bool(yq_croc_data.get("allow_send", True)),
            allow_receive=bool(yq_croc_data.get("allow_receive", True)),
            max_concurrent_transfers=int(yq_croc_data.get("max_concurrent_transfers", 1)),
        )
    )

    return Settings(
        center_base_url=os.getenv(
            "CENTER_BASE_URL",
            center.get("base_url", "http://127.0.0.1:8000"),
        ),
        node_id=os.getenv("NODE_ID", node.get("node_id", "win-client")),
        node_name=os.getenv("NODE_NAME", node.get("node_name", "Windows Client")),
        node_token=os.getenv("NODE_TOKEN", node.get("token", "dev-node-token")),
        api_token=os.getenv("API_TOKEN", node.get("api_token")),
        locality=os.getenv("NODE_LOCALITY", node.get("locality", "lan")),
        role=list(node.get("role", ["compute"])),
        heartbeat_interval_sec=int(daemon.get("heartbeat_interval_sec", 10)),
        signal_report_interval_sec=int(daemon.get("signal_report_interval_sec", 5)),
        job_poll_interval_sec=int(daemon.get("job_poll_interval_sec", 3)),
        max_concurrent_jobs=int(daemon.get("max_concurrent_jobs", 4)),
        capability_refresh_interval_sec=int(daemon.get("capability_refresh_interval_sec", 300)),
        request_timeout_sec=float(center.get("timeout_sec", 10.0)),
        send_job_events=bool(data.get("send_job_events", True)),
        routes=routes,
        l2_policy=l2_policy,
        paths=paths_config,
        cache=cache_config,
        runtime=runtime_config,
        transfer=transfer_config,
        allow_write_actions=allow_write_actions,
        allowed_services=safety_allowed_services,
        reconnect_initial_delay_sec=int(daemon.get("reconnect_initial_delay_sec", 2)),
        reconnect_max_delay_sec=int(daemon.get("reconnect_max_delay_sec", 60)),
        reconnect_ready_delay_sec=int(daemon.get("reconnect_ready_delay_sec", 5)),
        reconnect_probe_interval_sec=int(daemon.get("reconnect_probe_interval_sec", 2)),
    )


def _safe_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _runtime_mode(value: Any) -> str:
    mode = str(value or "hybrid").strip().lower()
    if mode in {"hybrid", "service", "desktop", "dev"}:
        return mode
    return "hybrid"
