"""Shared lightweight type aliases."""

from __future__ import annotations

from typing import Any, TypeAlias

JsonValue: TypeAlias = Any
JsonObject: TypeAlias = dict[str, JsonValue]
