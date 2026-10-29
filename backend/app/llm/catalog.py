"""The single dispatch route: DeepSeek V4.1 Flash, API ``deepseek-flash``.

Verified against DeepSeek's Models & Pricing, Lists Models and Thinking Mode
on 2026-09-30. Historical route IDs are migration inputs only; they are never
exposed in the live catalog or sent to a provider.

https://api-docs.deepseek.com/quick_start/pricing
https://api-docs.deepseek.com/api/list-models/
https://api-docs.deepseek.com/api/create-chat-completion/
https://api-docs.deepseek.com/guides/thinking_mode/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PROVIDER_ID = "deepseek"
MODEL_ID = "deepseek-flash"
CONTEXT_WINDOW = 1_048_576
MAX_OUTPUT_TOKENS = 393_216
LEVELS = ("off", "low", "high", "max")
LEVEL_LABELS = {"off": "Off", "low": "Low", "high": "High", "max": "Max"}

# IDs previously stored by this application or its embedded data-agent UI.
# This finite migration list does not admit custom/new arbitrary model names.
LEGACY_PROVIDERS = frozenset({"openai", "qwen"})
LEGACY_MODELS = frozenset({
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    "deepseek-chat", "deepseek-reasoner", "deepseek/deepseek-v4-pro",
    "gpt-5.6-terra", "gpt-5.6-luna", "qwen3.8-max", "qwen3.8-flash",
    "qwen3.7-max", "qwen3.7-plus", "qwen3.5-plus", "mimo-v2.5-pro", "mimo-v2.5",
    "claude-sonnet-4-6", "openai/gpt-5.3-codex", "minimax-m2.5",
})


def canonical_provider(provider_id: str | None) -> str:
    if provider_id is not None and not isinstance(provider_id, str):
        raise ValueError("Provider must be a string; only DeepSeek is available.")
    pid = (provider_id or "").strip().lower()
    if pid and pid != PROVIDER_ID and pid not in LEGACY_PROVIDERS:
        raise ValueError("Only the DeepSeek provider is available.")
    return PROVIDER_ID


def canonical_model(model_id: str | None) -> str:
    if model_id is not None and not isinstance(model_id, str):
        raise ValueError("Model must be a string; only deepseek-flash is available.")
    mid = (model_id or "").strip()
    if mid and mid != MODEL_ID and mid not in LEGACY_MODELS:
        raise ValueError("Only DeepSeek V4.1 Flash (deepseek-flash) is available.")
    return MODEL_ID


def canonical_reasoning(level: str | None) -> str:
    # Official compatibility mappings; expose only the model's actual levels.
    if level is not None and not isinstance(level, str):
        raise ValueError("Reasoning level must be off, low, high or max.")
    return {"none": "off", "minimal": "low", "medium": "high", "xhigh": "high", "ultra": "max"}.get(level, level) if level else "high"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    family: str
    context_window: int
    max_output: int
    reasoning_levels: tuple[str, ...]
    default_reasoning: str
    description: str = ""
    supports_tools: bool = True
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    env_key: str
    default_base_url: Optional[str]
    models: tuple[ModelSpec, ...]
    reasoning_style: str
    replay_reasoning: bool = False
    allow_temperature: bool = True
    supports_parallel_tool_calls: bool = False
    alt_base_urls: dict = field(default_factory=dict)
    docs_url: str = ""

    def model(self, model_id: str) -> Optional[ModelSpec]:
        return next((m for m in self.models if m.id == model_id), None)

    @property
    def default_model(self) -> ModelSpec:
        return self.models[0]


PROVIDERS = {
    PROVIDER_ID: ProviderSpec(
        id=PROVIDER_ID, label="DeepSeek", env_key="DEEPSEEK_API_KEY",
        default_base_url="https://api.deepseek.com", reasoning_style="deepseek",
        replay_reasoning=True, docs_url="https://api-docs.deepseek.com/quick_start/pricing",
        models=(ModelSpec(
            id=MODEL_ID, label="DeepSeek V4.1 Flash", family="DeepSeek V4.1",
            context_window=CONTEXT_WINDOW, max_output=MAX_OUTPUT_TOKENS,
            reasoning_levels=LEVELS, default_reasoning="high",
            description="DeepSeek V4.1 Flash with thinking, tool calls and image input.", tags=("fast",),
        ),),
    ),
}


def reasoning_params(provider: ProviderSpec, model: Optional[ModelSpec], level: Optional[str]) -> tuple[dict, dict]:
    """Return the official Chat Completions kwargs and SDK extra body."""
    if provider.id != PROVIDER_ID or model is None or model.id != MODEL_ID:
        raise ValueError("Only DeepSeek V4.1 Flash (deepseek-flash) can be dispatched.")
    lvl = canonical_reasoning(level)
    if lvl not in LEVELS:
        raise ValueError(f"Unsupported reasoning level: {level!r}. Use off, low, high or max.")
    return (
        {"reasoning_effort": "none" if lvl == "off" else lvl},
        {"thinking": {"type": "disabled" if lvl == "off" else "enabled"}},
    )


def find_model(model_id: str) -> Optional[tuple[ProviderSpec, ModelSpec]]:
    spec = PROVIDERS[PROVIDER_ID]
    model = spec.model(model_id)
    return (spec, model) if model is not None else None
