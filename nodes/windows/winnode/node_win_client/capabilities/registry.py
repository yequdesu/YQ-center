from __future__ import annotations

import asyncio
import inspect
import pkgutil
from importlib import import_module
from typing import Any

from .base import CapabilityContext, NodeCapability


def load_capabilities() -> dict[str, NodeCapability]:
    package_name = __package__
    if package_name is None:
        raise RuntimeError("capabilities package name is unavailable")
    package = import_module(package_name)
    capabilities: dict[str, NodeCapability] = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in {"base", "registry"} or module_info.ispkg:
            continue
        module = import_module(f"{package_name}.{module_info.name}")
        capability = getattr(module, "CAPABILITY", None)
        if not isinstance(capability, NodeCapability):
            continue
        name = capability.manifest.name
        if name in capabilities:
            raise RuntimeError(f"duplicate capability name: {name}")
        capabilities[name] = capability
    return capabilities


async def execute_capability(
    capabilities: dict[str, NodeCapability],
    name: str,
    input_data: dict[str, Any],
    context: CapabilityContext,
) -> dict[str, Any]:
    capability = capabilities.get(name)
    if capability is None:
        raise KeyError(name)
    if capability.run_in_thread:
        result = await asyncio.to_thread(capability.handler, input_data, context)
    else:
        result = capability.handler(input_data, context)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise TypeError(f"capability {name} returned {type(result).__name__}, expected dict")
    return result
