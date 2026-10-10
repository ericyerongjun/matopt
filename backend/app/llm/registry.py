"""Instantiates providers from settings and exposes the catalog to the API."""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.llm.catalog import (
    LEVELS, LEVEL_LABELS, MODEL_ID, PROVIDER_ID, PROVIDERS, ModelSpec, ProviderSpec,
    canonical_model, canonical_provider,
)
from app.llm.openai_compat import OpenAICompatProvider


class ProviderRegistry:
    def __init__(self):
        self._instances: dict[str, OpenAICompatProvider] = {}

    # ── configuration lookup ────────────────────────────────────────────

    @staticmethod
    def api_key(provider_id: str) -> Optional[str]:
        return settings.deepseek_api_key if provider_id == PROVIDER_ID else None

    @staticmethod
    def base_url(provider_id: str) -> Optional[str]:
        spec = PROVIDERS[provider_id]
        return settings.deepseek_base_url or spec.default_base_url

    def is_configured(self, provider_id: str) -> bool:
        return bool(self.api_key(provider_id))

    def configured_ids(self) -> list[str]:
        return [pid for pid in PROVIDERS if self.is_configured(pid)]

    # ── resolution ──────────────────────────────────────────────────────

    def resolve(self, provider_id: Optional[str], model_id: Optional[str]) -> tuple[ProviderSpec, ModelSpec]:
        pid = canonical_provider(provider_id)
        canonical_model(model_id)
        spec = PROVIDERS[pid]
        if not self.is_configured(pid):
            raise ValueError(f"{spec.label} is not configured. Set {spec.env_key} in backend/.env.")
        return spec, spec.default_model

    def get(self, provider_id: str) -> OpenAICompatProvider:
        if provider_id != PROVIDER_ID:
            raise ValueError("Only the DeepSeek provider can be dispatched.")
        inst = self._instances.get(provider_id)
        if inst is None:
            spec = PROVIDERS[provider_id]
            key = self.api_key(provider_id)
            if not key:
                raise ValueError(f"{spec.label} is not configured. Set {spec.env_key} in backend/.env.")
            inst = OpenAICompatProvider(spec, key, self.base_url(provider_id), timeout=settings.llm_timeout)
            self._instances[provider_id] = inst
        return inst

    # ── catalog for the frontend ────────────────────────────────────────

    def catalog(self) -> dict:
        default_pid = PROVIDER_ID
        default_model = MODEL_ID
        return {
            "default_provider": default_pid,
            "default_model": default_model,
            "levels": [{"id": lvl, "label": LEVEL_LABELS[lvl]} for lvl in LEVELS],
            "providers": [
                {
                    "id": p.id,
                    "label": p.label,
                    "configured": self.is_configured(p.id),
                    "env_key": p.env_key,
                    "docs_url": p.docs_url,
                    "default_model": p.default_model.id,
                    "models": [
                        {
                            "id": m.id,
                            "label": m.label,
                            "family": m.family,
                            "description": m.description,
                            "context_window": m.context_window,
                            "max_output": m.max_output,
                            "reasoning_levels": list(m.reasoning_levels),
                            "default_reasoning": m.default_reasoning,
                            "tags": list(m.tags),
                        }
                        for m in p.models
                    ],
                }
                for p in PROVIDERS.values()
            ],
        }


providers = ProviderRegistry()
