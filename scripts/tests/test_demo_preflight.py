import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("demo_preflight", Path(__file__).parents[1] / "demo_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_warmup_checks_real_speech_endpoints_without_starting_an_agent(monkeypatch, capsys, language):
    calls = []

    def open_request(request, timeout):
        from urllib.parse import urlsplit
        path = urlsplit(request.full_url).path
        calls.append((path, request.data))
        if path == "/health":
            payload = {"status": "ok"}
        elif path == "/api/providers":
            payload = {"providers": [{"id": "deepseek", "configured": True}]}
        elif path == "/api/speech/status":
            warmed = any(path == "/api/speech/synthesize" for path, _ in calls)
            payload = {"transcription": True, "synthesis": True,
                       "loaded": {"transcription": warmed, "synthesis": warmed, "synthesis_zh": warmed}}
        elif path == "/api/speech/transcribe":
            assert b"RIFF" in request.data and b"warmup.wav" in request.data
            payload = {"text": ""}
        elif path == "/api/speech/synthesize":
            assert json.loads(request.data)["language"] == language
            return io.BytesIO(b"RIFF....WAVE")
        else:
            pytest.fail(f"Unexpected request: {path}")
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(preflight.urllib.request, "urlopen", open_request)
    assert preflight.main(["--warmup", "--language", language]) == 0
    assert len(calls) == 6
    assert "not API latency" in capsys.readouterr().out


def test_unloaded_speech_models_are_not_reported_ready(monkeypatch):
    def open_request(request, timeout):
        payload = {"providers": [{"id": "deepseek", "configured": True}],
                   "transcription": True, "synthesis": True, "loaded": {}}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(preflight.urllib.request, "urlopen", open_request)
    assert preflight.main([]) == 1
