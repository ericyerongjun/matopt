"""Instantiates providers from settings and exposes the catalog to the API."""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.llm.catalog import LEVEL_LABELS, PROVIDERS, ModelSpec, ProviderSpec
from app.llm.openai_compat import OpenAICompatProvider


class ProviderRegistry:
    def __init__(self):
        self._instances: dict[str, OpenAICompatProvider] = {}

    # ── configuration lookup ────────────────────────────────────────────

    @staticmethod
    def api_key(provider_id: str) -> Optional[str]:
        return {
            "openai": settings.openai_api_key,
            "deepseek": settings.deepseek_api_key,
            "qwen": settings.qwen_api_key,
        }.get(provider_id)

    @staticmethod
    def base_url(provider_id: str) -> Optional[str]:
        spec = PROVIDERS[provider_id]
        override = {
            "openai": settings.openai_base_url,
            "deepseek": settings.deepseek_base_url,
            "qwen": settings.qwen_base_url,
        }.get(provider_id)
        if provider_id == "qwen" and not override and settings.qwen_region.lower() in ("cn", "china"):
            return spec.alt_base_urls["china"]
        return override or spec.default_base_url

    def is_configured(self, provider_id: str) -> bool:
        return bool(self.api_key(provider_id))

    def configured_ids(self) -> list[str]:
        return [pid for pid in PROVIDERS if self.is_configured(pid)]

    # ── resolution ──────────────────────────────────────────────────────

    def resolve(self, provider_id: Optional[str], model_id: Optional[str]) -> tuple[ProviderSpec, ModelSpec]:
        pid = (provider_id or "").strip().lower()
        mid = (model_id or "").strip()
        if not pid and mid:
            for p in PROVIDERS.values():
                if p.model(mid):
                    pid = p.id
                    break
        if not pid:
            pid = settings.default_provider if self.is_configured(settings.default_provider) else (self.configured_ids() or [None])[0]
        if not pid or pid not in PROVIDERS:
            raise ValueError("No LLM provider is configured. Set DEEPSEEK_API_KEY, OPENAI_API_KEY or QWEN_API_KEY in backend/.env.")
        spec = PROVIDERS[pid]
        if not self.is_configured(pid):
            raise ValueError(f"{spec.label} is not configured. Set {spec.env_key} in backend/.env.")
        model = spec.model(mid) if mid else None
        if mid and model is None:
            # allow custom model ids on the same provider (fine-tunes / newer snapshots)
            model = ModelSpec(id=mid, label=mid, family="custom", context_window=128_000, max_output=32_768,
                              reasoning_levels=spec.default_model.reasoning_levels,
                              default_reasoning=spec.default_model.default_reasoning)
        return spec, model or spec.default_model

    def get(self, provider_id: str) -> OpenAICompatProvider:
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
        default_pid = settings.default_provider if self.is_configured(settings.default_provider) else (self.configured_ids() or [None])[0]
        default_model = None
        if default_pid:
            spec = PROVIDERS[default_pid]
            default_model = spec.model(settings.default_model).id if spec.model(settings.default_model) else spec.default_model.id
        return {
            "default_provider": default_pid,
            "default_model": default_model,
            "levels": [{"id": lvl, "label": LEVEL_LABELS[lvl]} for lvl in ("off", "low", "medium", "high", "max")],
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
