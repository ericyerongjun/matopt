"""Voice endpoints and the text a spoken reply actually reads."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.speech import SpeechUnavailable, speakable, speech


def test_voice_models_prefetch_independently(tmp_path, monkeypatch):
    import threading
    from app.speech import SpeechService

    service = SpeechService(tmp_path)
    ready = [threading.Event() for _ in range(3)]
    release = threading.Event()
    finished = [threading.Event() for _ in range(3)]

    def load(index):
        ready[index].set()
        try:
            assert release.wait(5)
            if index == 1:
                raise SpeechUnavailable("One voice is unavailable")
        finally:
            finished[index].set()

    for index, name in enumerate(("_whisper_model", "_kokoro_model", "_kokoro_zh_model")):
        monkeypatch.setattr(service, name, lambda index=index: load(index))
    try:
        service.prefetch()
        assert all(event.wait(2) for event in ready), "A TTS loader must not block microphone prefetch"
    finally:
        release.set()
        assert all(event.wait(2) for event in finished)


def test_speakable_summarises_code_tables_and_display_math_but_keeps_inline_meaning():
    answer = (
        "## Result\nThe gradient is $\\frac{\\partial L}{\\partial w}$ and **important**.\n\n"
        "$$L = (y - \\hat y)^2$$\n\n```python\nprint(42)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "See [the docs](https://example.test) and ![plot](figures/a.png)."
    )
    spoken = speakable(answer)
    assert "Result" in spoken and "#" not in spoken and "**" not in spoken
    assert "partial L over partial w" in " ".join(spoken.split())
    assert "(equation shown on screen)" in spoken and "(code shown on screen)" in spoken
    assert "(table shown on screen)" in spoken and "(figure shown on screen)" in spoken
    assert "print(42)" not in spoken and "https://" not in spoken and "the docs" in spoken


@pytest.mark.asyncio
async def test_transcribe_and_synthesize_endpoints(monkeypatch):
    async def transcribe(audio, language=None):
        assert audio == b"RIFFfake" and language == "zh"
        return {"text": "你好", "language": "zh", "language_probability": 0.99, "duration": 1.2}

    async def synthesize(text, language=None, voice=None, speed=1.0):
        assert (text, language) == ("Hello", "en")
        return b"RIFF....WAVE"

    monkeypatch.setattr(speech, "transcribe", transcribe)
    monkeypatch.setattr(speech, "synthesize", synthesize)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        heard = await client.post("/api/speech/transcribe", files={"audio": ("v.webm", b"RIFFfake", "audio/webm")},
                                  data={"language": "zh"})
        assert heard.status_code == 200 and heard.json()["text"] == "你好"
        spoken = await client.post("/api/speech/synthesize", json={"text": "Hello", "language": "en"})
        assert spoken.status_code == 200 and spoken.headers["content-type"] == "audio/wav" and spoken.content.startswith(b"RIFF")
        bad = await client.post("/api/speech/synthesize", json={"text": "Hello", "language": "xx"})
        assert bad.status_code == 400
        status = (await client.get("/api/speech/status")).json()
        assert {lang["code"] for lang in status["languages"]} >= {"en", "zh", "ja"}


@pytest.mark.asyncio
async def test_unavailable_speech_is_a_clear_503(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise SpeechUnavailable("Speech recognition is not installed (faster-whisper).")

    monkeypatch.setattr(speech, "transcribe", unavailable)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/speech/transcribe", files={"audio": ("v.webm", b"x", "audio/webm")})
    assert response.status_code == 503 and "faster-whisper" in response.json()["detail"]
