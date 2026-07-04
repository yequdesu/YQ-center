"""Model-aware YCR budget profiles."""

from __future__ import annotations

from dataclasses import dataclass

from yequ.config import Settings


@dataclass(frozen=True, slots=True)
class BudgetProfile:
    provider: str
    model: str
    max_input_tokens: int
    reserved_response_tokens: int
    max_tool_observation_tokens: int
    max_context_block_tokens: int
    max_string_chars: int
    max_list_items: int
    max_dict_keys: int

    @property
    def max_prompt_block_chars(self) -> int:
        return max(1000, self.max_context_block_tokens * 4)


def budget_profile_from_settings(settings: Settings) -> BudgetProfile:
    max_input = max(4096, settings.ycr_default_input_token_budget)
    return BudgetProfile(
        provider="deepseek",
        model=settings.deepseek_model,
        max_input_tokens=max_input,
        reserved_response_tokens=max(1024, settings.ycr_reserved_response_tokens),
        max_tool_observation_tokens=max(512, max_input // 20),
        max_context_block_tokens=max(2048, max_input // 3),
        max_string_chars=max(800, max_input // 20 * 4),
        max_list_items=8,
        max_dict_keys=32,
    )


def estimate_tokens(value: object) -> int:
    return max(1, len(str(value)) // 4)
