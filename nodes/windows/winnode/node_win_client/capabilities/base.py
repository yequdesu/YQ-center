from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from node_win_client.config import L2Policy, TransferYqCrocConfig
from node_win_client.models import FunctionManifest, JobEventType

JobEventCallback = Callable[[JobEventType, dict[str, Any]], Any]
CapabilityHandler = Callable[[dict[str, Any], "CapabilityContext"], Any]


@dataclass(frozen=True)
class CapabilityContext:
    l2_policy: L2Policy
    transfer_yq_croc: TransferYqCrocConfig
    center_base_url: str
    node_token: str
    request_timeout_sec: float
    job_event_callback: JobEventCallback | None = None


@dataclass(frozen=True)
class NodeCapability:
    manifest: FunctionManifest
    handler: CapabilityHandler
    run_in_thread: bool = True
