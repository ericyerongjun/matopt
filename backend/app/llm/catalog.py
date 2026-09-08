"""
Catalog of supported providers/models and the mapping from the UI's
reasoning-effort slider to provider-specific request parameters.

Slider levels (UI)          off | low | medium | high | max
  openai   (gpt-5.6-*)      reasoning_effort = none|low|medium|high|xhigh
  deepseek (deepseek-v4-*)  thinking.type = disabled | enabled + reasoning_effort=low|medium|high
  qwen     (qwen3.x)        enable_thinking=false | true (+ thinking_budget for low/medium)

The exact parameter names/values are kept in one place (``reasoning_params``)
so they can be adjusted when providers change their APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

LEVELS: tuple[str, ...] = ("off", "low", "medium", "high", "max")

LEVEL_LABELS = {
    "off": "Off",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "max": "Max",
}


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
    reasoning_style: str                     # openai | deepseek | qwen
    replay_reasoning: bool = False           # send reasoning_content back on tool-call turns
    allow_temperature: bool = True
    supports_parallel_tool_calls: bool = True
    alt_base_urls: dict = field(default_factory=dict)
    docs_url: str = ""

    def model(self, model_id: str) -> Optional[ModelSpec]:
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    @property
    def default_model(self) -> ModelSpec:
        return self.models[0]


PROVIDERS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        env_key="DEEPSEEK_API_KEY",
        default_base_url="https://api.deepseek.com",
        reasoning_style="deepseek",
        replay_reasoning=True,
        allow_temperature=False,
        docs_url="https://api-docs.deepseek.com/",
        models=(
            ModelSpec(
                id="deepseek-v4-pro", label="DeepSeek V4 Pro", family="DeepSeek V4",
                context_window=1_000_000, max_output=384_000,
                reasoning_levels=("off", "low", "medium", "high"), default_reasoning="high",
                description="Flagship reasoning model, strongest on math and code.",
                tags=("flagship",),
            ),
            ModelSpec(
                id="deepseek-v4-flash", label="DeepSeek V4 Flash", family="DeepSeek V4",
                context_window=1_000_000, max_output=384_000,
                reasoning_levels=("off", "low", "medium", "high"), default_reasoning="medium",
                description="Fast and inexpensive; good for iteration and data work.",
                tags=("fast",),
            ),
        ),
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        env_key="OPENAI_API_KEY",
        default_base_url=None,
        reasoning_style="openai",
        allow_temperature=False,
        docs_url="https://developers.openai.com/api/docs/models",
        models=(
            ModelSpec(
                id="gpt-5.6-terra", label="GPT-5.6 Terra", family="GPT-5.6",
                context_window=400_000, max_output=128_000,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="medium",
                description="Balanced GPT-5.6 tier: strong reasoning at moderate cost.",
                tags=("balanced",),
            ),
            ModelSpec(
                id="gpt-5.6-luna", label="GPT-5.6 Luna", family="GPT-5.6",
                context_window=400_000, max_output=128_000,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="medium",
                description="Fastest, most affordable GPT-5.6 tier.",
                tags=("fast",),
            ),
        ),
    ),
    "qwen": ProviderSpec(
        id="qwen",
        label="Qwen",
        env_key="QWEN_API_KEY",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        alt_base_urls={"china": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        reasoning_style="qwen",
        docs_url="https://www.alibabacloud.com/help/en/model-studio/deep-thinking",
        models=(
            ModelSpec(
                id="qwen3.8-max", label="Qwen3.8 Max", family="Qwen3.8",
                context_window=262_144, max_output=65_536,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="high",
                description="Latest flagship Qwen; hybrid thinking.",
                tags=("flagship",),
            ),
            ModelSpec(
                id="qwen3.8-flash", label="Qwen3.8 Flash", family="Qwen3.8",
                context_window=262_144, max_output=65_536,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="medium",
                description="Fast Qwen3.8 tier.",
                tags=("fast",),
            ),
            ModelSpec(
                id="qwen3.7-max", label="Qwen3.7 Max", family="Qwen3.7",
                context_window=262_144, max_output=65_536,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="high",
                description="Qwen3.7 flagship; hybrid thinking.",
            ),
            ModelSpec(
                id="qwen3.7-plus", label="Qwen3.7 Plus", family="Qwen3.7",
                context_window=262_144, max_output=65_536,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="medium",
                description="Qwen3.7 balanced tier.",
            ),
            ModelSpec(
                id="qwen3.5-plus", label="Qwen3.5 Plus", family="Qwen3.5",
                context_window=131_072, max_output=32_768,
                reasoning_levels=("off", "low", "medium", "high", "max"), default_reasoning="medium",
                description="Qwen3.5 balanced tier; hybrid thinking.",
            ),
        ),
    ),
}

# thinking_budget (tokens) used for Qwen when the slider is below "max"
QWEN_BUDGETS = {"low": 2_048, "medium": 8_192, "high": 32_768}


def reasoning_params(provider: ProviderSpec, model: Optional[ModelSpec], level: Optional[str]) -> tuple[dict, dict]:
    """Return (kwargs, extra_body) for chat.completions.create."""
    levels = model.reasoning_levels if model else LEVELS
    lvl = level if level in levels else (model.default_reasoning if model else "medium")
    kwargs: dict = {}
    extra: dict = {}
    style = provider.reasoning_style
    if style == "openai":
        mapping = {"off": "none", "low": "low", "medium": "medium", "high": "high", "max": "xhigh"}
        kwargs["reasoning_effort"] = mapping[lvl]
    elif style == "deepseek":
        if lvl == "off":
            extra["thinking"] = {"type": "disabled"}
        else:
            extra["thinking"] = {"type": "enabled"}
            extra["reasoning_effort"] = {"low": "low", "medium": "medium", "high": "high", "max": "high"}[lvl]
    elif style == "qwen":
        if lvl == "off":
            extra["enable_thinking"] = False
        else:
            extra["enable_thinking"] = True
            if lvl in QWEN_BUDGETS:
                extra["thinking_budget"] = QWEN_BUDGETS[lvl]
    return kwargs, extra


def find_model(model_id: str) -> Optional[tuple[ProviderSpec, ModelSpec]]:
    for p in PROVIDERS.values():
        m = p.model(model_id)
        if m:
            return p, m
    return None
