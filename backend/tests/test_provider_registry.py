"""Contract tests for the only live model and finite saved-route migrations."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.llm.base import ChatOptions
from app.llm.catalog import (
    CONTEXT_WINDOW, LEGACY_MODELS, MAX_OUTPUT_TOKENS, MODEL_ID, PROVIDERS,
    canonical_model, reasoning_params,
)
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.registry import ProviderRegistry


def test_only_flash_is_exposed_even_without_credentials():
    catalog = ProviderRegistry().catalog()
    assert catalog["default_provider"] == "deepseek"
    assert catalog["default_model"] == MODEL_ID
    assert [p["id"] for p in catalog["providers"]] == ["deepseek"]
    model, = catalog["providers"][0]["models"]
    assert model["id"] == MODEL_ID
    assert model["label"] == "DeepSeek V4.1 Flash"
    assert model["context_window"] == CONTEXT_WINDOW == 1_048_576
    assert model["max_output"] == MAX_OUTPUT_TOKENS == 393_216
    assert model["reasoning_levels"] == ["off", "low", "high", "max"]


@pytest.mark.parametrize("provider,model", [
    (None, None), ("deepseek", MODEL_ID), ("openai", "gpt-5.6-terra"),
    ("qwen", "qwen3.8-max"), ("deepseek", "deepseek-v4-pro"),
])
def test_saved_provider_selections_resolve_to_flash_without_rewriting_input(monkeypatch, provider, model):
    monkeypatch.setattr(settings, "deepseek_api_key", "deepseek-test")
    saved = {"provider": provider, "model": model}
    original = dict(saved)
    spec, resolved = ProviderRegistry().resolve(saved["provider"], saved["model"])
    assert (spec.id, resolved.id) == ("deepseek", MODEL_ID)
    assert saved == original


@pytest.mark.parametrize("value", ["made-up-model", "gpt-999", "DEEPSEEK-FLASH", [], 12])
def test_unknown_and_malformed_models_are_rejected(monkeypatch, value):
    monkeypatch.setattr(settings, "deepseek_api_key", "deepseek-test")
    with pytest.raises(ValueError):
        ProviderRegistry().resolve("deepseek", value)


def test_known_legacy_models_are_migration_inputs_only():
    assert all(canonical_model(model) == MODEL_ID for model in LEGACY_MODELS)
    assert all(PROVIDERS["deepseek"].model(model) is None for model in LEGACY_MODELS)


def test_no_other_provider_or_key_fallback(monkeypatch):
    registry = ProviderRegistry()
    monkeypatch.setattr(settings, "deepseek_api_key", None)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        registry.resolve("openai", "gpt-5.6-terra")
    with pytest.raises(ValueError):
        registry.resolve("custom", MODEL_ID)
    with pytest.raises(ValueError):
        registry.get("openai")


def test_settings_normalize_only_known_saved_defaults():
    config = Settings(_env_file=None, default_provider="qwen", default_model="qwen3.8-max", default_reasoning="medium")
    assert (config.default_provider, config.default_model, config.default_reasoning) == ("deepseek", MODEL_ID, "high")
    for overrides in ({"default_model": "custom"}, {"default_provider": "custom"},
                      {"default_reasoning": "custom"}, {"max_output_tokens": MAX_OUTPUT_TOKENS + 1}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("level,effort,thinking", [
    (None, "high", "enabled"), ("off", "none", "disabled"),
    ("low", "low", "enabled"), ("high", "high", "enabled"), ("max", "max", "enabled"),
    ("medium", "high", "enabled"), ("xhigh", "high", "enabled"), ("ultra", "max", "enabled"),
])
def test_official_reasoning_options(level, effort, thinking):
    spec = PROVIDERS["deepseek"]
    assert reasoning_params(spec, spec.default_model, level) == (
        {"reasoning_effort": effort}, {"thinking": {"type": thinking}},
    )


def make_provider():
    # No HTTP client is needed to exercise final dispatch validation.
    provider = object.__new__(OpenAICompatProvider)
    provider.spec = PROVIDERS["deepseek"]
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    ChatOptions(model="deepseek-v4-pro"), ChatOptions(model="custom"),
    ChatOptions(model=MODEL_ID, extra={"model": "other"}),
    ChatOptions(model=MODEL_ID, extra={"thinking": {"type": "disabled"}}),
    ChatOptions(model=MODEL_ID, max_tokens=0), ChatOptions(model=MODEL_ID, max_tokens=True),
    ChatOptions(model=MODEL_ID, max_tokens=MAX_OUTPUT_TOKENS + 1),
])
async def test_final_boundary_rejects_before_any_stream_or_auxiliary_dispatch(options):
    provider = make_provider()

    async def unexpected(**kwargs):
        pytest.fail("Invalid model/options reached the SDK")

    provider.client = NS(chat=NS(completions=NS(create=unexpected)))
    with pytest.raises(ValueError):
        await provider.complete([], options)
    with pytest.raises(ValueError):
        _ = [delta async for delta in provider.stream([], None, options)]


@pytest.mark.asyncio
async def test_direct_adapter_defaults_to_deepseek_url_and_rejects_other_spec():
    provider = OpenAICompatProvider(PROVIDERS["deepseek"], "test-key", None)
    assert str(provider.client.base_url) == "https://api.deepseek.com"
    assert provider.client.max_retries == 0
    await provider.client.close()
    with pytest.raises(ValueError):
        OpenAICompatProvider(replace(PROVIDERS["deepseek"], id="openai"), "test-key", None)


def test_temperature_only_passes_in_non_thinking_mode():
    provider = make_provider()
    assert "temperature" not in provider._request_params(ChatOptions(model=MODEL_ID, temperature=0.3))[0]
    assert provider._request_params(ChatOptions(model=MODEL_ID, reasoning="off", temperature=0.3))[0]["temperature"] == 0.3


def test_data_agent_ignores_old_provider_credentials_and_model_lists():
    from data_agent_app.core.config import Settings as DataAgentSettings
    from data_agent_app.services.llm_service import LLMService

    config = DataAgentSettings(_env_file=None, DEEPSEEK_API_KEY="deepseek-test", OPENAI_API_KEY="other-test",
                            OPENAI_BASE_URL="https://other.invalid", DEFAULT_MODEL="mimo-v2.5-pro",
                            MODEL_LIST=["mimo-v2.5-pro", "deepseek-v4-pro"], MULTIMODAL_MODELS=["mimo-v2.5"])
    assert config.DEEPSEEK_API_KEY == "deepseek-test"
    assert config.DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    assert config.DEFAULT_MODEL == MODEL_ID
    assert config.MODEL_LIST == config.MULTIMODAL_MODELS == [MODEL_ID]
    assert not hasattr(config, "OPENAI_API_KEY")
    service = object.__new__(LLMService)
    assert service._normalize_model("mimo-v2.5-pro") == MODEL_ID
    with pytest.raises(ValueError):
        service._normalize_model("custom")


@pytest.mark.asyncio
async def test_data_agent_stream_and_auxiliary_dispatch_only_flash():
    from data_agent_app.services.llm_service import LLMService

    requests = []

    class Stream:
        async def __aiter__(self):
            yield NS(usage=None, choices=[NS(finish_reason="stop", delta=NS(content="answer", tool_calls=[]))])

    async def create(**kwargs):
        requests.append(kwargs)
        if kwargs.get("stream"):
            return Stream()
        return NS(choices=[NS(message=NS(content="summary"))])

    service = object.__new__(LLMService)
    service.default_model = MODEL_ID
    service.client = NS(chat=NS(completions=NS(create=create)))
    events = [event async for event in service.stream_chat([], model="mimo-v2.5-pro")]
    assert events[-1]["type"] == "done"
    assert await service.chat([], model="deepseek-v4-pro") == "summary"
    assert [request["model"] for request in requests] == [MODEL_ID, MODEL_ID]
    assert requests[0]["reasoning_effort"] == "high"
    assert requests[1]["reasoning_effort"] == "none"
    errors = [event async for event in service.stream_chat([], model="custom")]
    assert errors == [{"type": "error", "content": "Only DeepSeek V4.1 Flash (deepseek-flash) is available."}]
    assert await service.chat([], model="custom") == "Error: Only DeepSeek V4.1 Flash (deepseek-flash) is available."
    assert len(requests) == 2


def test_data_agent_title_uses_same_deepseek_credentials_and_flash(monkeypatch):
    from data_agent_app.services import llm_service

    captured = {}

    def create(**kwargs):
        captured["request"] = kwargs
        return NS(choices=[NS(message=NS(content="A title"))])

    def client(**kwargs):
        captured["client"] = kwargs
        return NS(chat=NS(completions=NS(create=create)))

    monkeypatch.setattr(llm_service.openai, "OpenAI", client)
    service = object.__new__(llm_service.LLMService)
    service.default_model = "mimo-v2.5-pro"  # stale cached/default selections cannot override title dispatch
    assert service.generate_title("message") == "A title"
    assert captured["client"]["api_key"] == (llm_service.settings.DEEPSEEK_API_KEY or "not-configured")
    assert captured["client"]["base_url"] == llm_service.settings.DEEPSEEK_BASE_URL
    assert captured["request"]["model"] == MODEL_ID
    assert captured["request"]["reasoning_effort"] == "none"


def test_data_agent_retains_all_new_thoughts_without_rewriting_legacy_usage():
    import json
    from data_agent_app.api.v1.chat import _persisted_reasoning, _usage_with_reasoning

    original = {"model": MODEL_ID, "metadata": {"prompt_tokens": 7}}
    first = _usage_with_reasoning(original, "first thought")
    second = _usage_with_reasoning(original, " then second thought")
    assert original == {"model": MODEL_ID, "metadata": {"prompt_tokens": 7}}
    assert first["metadata"]["prompt_tokens"] == 7
    records = [
        NS(message_id=1, metadata_json='{"prompt_tokens": 2}'),
        NS(message_id=2, metadata_json=json.dumps(first["metadata"])),
        NS(message_id=2, metadata_json=json.dumps(second["metadata"])),
        NS(message_id=3, metadata_json="legacy malformed metadata"),
    ]
    assert _persisted_reasoning(records) == {2: "first thought then second thought"}
