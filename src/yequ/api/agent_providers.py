"""Agent provider registry and API-facing provider resolution."""

from __future__ import annotations

from fastapi import HTTPException, status

from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentProvider

_provider_registry: dict[str, AgentProvider] = {}


def register_provider(provider: AgentProvider) -> None:
    """Register an Agent Provider for runtime setup or tests."""
    _provider_registry[provider.provider_name()] = provider


def get_provider(name: str) -> AgentProvider | None:
    """Get a registered provider by name."""
    return _provider_registry.get(name)


async def resolve_provider(provider_name: str) -> AgentProvider:
    provider = get_provider(provider_name)
    if provider is not None:
        return provider
    if provider_name == "fake":
        provider = FakeAgentProvider()
        register_provider(provider)
        return provider
    if provider_name == "deepseek":
        from yequ.agent.deepseek_provider import DeepSeekProvider

        provider = DeepSeekProvider()
        register_provider(provider)
        return provider
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Provider {provider_name!r} not found",
    )
