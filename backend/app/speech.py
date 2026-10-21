"""Voice input and spoken replies, run locally on the backend.

Transcription uses faster-whisper (CTranslate2, int8 on CPU); speech uses
Kokoro-82M through kokoro-onnx. Both models download on first use into
``<DATA_DIR>/models`` and stay loaded. Audio never leaves the server.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_RELEASES = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
# Model files: name -> (URL, SHA-256). v1.0 speaks most languages through
# espeak-ng; Chinese uses the v1.1-zh model with misaki's Mandarin front-end,
# which pronounces Chinese far better than espeak's generic rules.
KOKORO_FILES = {
    "kokoro-v1.0.int8.onnx": (_RELEASES + "model-files-v1.0/kokoro-v1.0.int8.onnx",
                              "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    "voices-v1.0.bin": (_RELEASES + "model-files-v1.0/voices-v1.0.bin",
                        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
KOKORO_ZH_FILES = {
    "kokoro-v1.1-zh.int8.onnx": (_RELEASES + "model-files-v1.1/kokoro-v1.1-zh.int8.onnx",
                                 "11751c087b4bbeed031e2b687b11dda698bd27ba0509472f960b57f835a999f7"),
    "voices-v1.1-zh.bin": (_RELEASES + "model-files-v1.1/voices-v1.1-zh.bin",
                           "14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd"),
    "config-v1.1-zh.json": ("https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/raw/01e7505bd6a7a2ac4975463114c3a7650a9f7218/config.json",
                            "bc333efa5ce4ceff433c8c8e5d027a1eca0166001e4e4a62bea2d26ff7a46890"),
}
# Whisper's first-window prompt steers vocabulary and script (Simplified Chinese).
TRANSCRIBE_PROMPTS = {
    "zh": "以下是普通话的句子，使用简体中文。主题：数学、统计、机器学习和计算机科学。",
}
DEFAULT_TRANSCRIBE_PROMPT = "Mathematics, statistics, machine learning and computer science."


@dataclass(frozen=True)
class Language:
    code: str        # app / Whisper language code
    label: str       # shown in the language picker, in that language
    kokoro: str      # espeak-ng language used by Kokoro
    voice: str       # default Kokoro voice


LANGUAGES = {lang.code: lang for lang in (
    Language("en", "English", "en-us", "af_heart"),
    Language("zh", "中文", "cmn", "zf_001"),
    Language("ja", "日本語", "ja", "jf_alpha"),
    Language("es", "Español", "es", "ef_dora"),
    Language("fr", "Français", "fr-fr", "ff_siwis"),
    Language("it", "Italiano", "it", "if_sara"),
    Language("pt", "Português", "pt-br", "pf_dora"),
    Language("hi", "हिन्दी", "hi", "hf_alpha"),
)}

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_SPEECH_CHARS = 6_000


class SpeechUnavailable(RuntimeError):
    pass


def speakable(markdown: str) -> str:
    """Turn a Markdown answer into text worth hearing.

    Code, tables and display equations are summarised rather than read symbol
    by symbol; inline maths keeps its readable characters.
    """
    text = re.sub(r"```.*?```", " (code shown on screen) ", markdown, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$|\\\[.*?\\\]", " (equation shown on screen) ", text, flags=re.S)
    text = re.sub(r"^\s*\|.*\|\s*$(\n^\s*\|.*\|\s*$)*", " (table shown on screen) ", text, flags=re.M)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " (figure shown on screen) ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)

    def inline_math(match: re.Match) -> str:
        body = match.group(1) or match.group(2) or ""
        body = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1 over \2", body)
        body = re.sub(r"\\(alpha|beta|gamma|delta|theta|lambda|mu|sigma|pi|eta|epsilon|nabla|partial)", r" \1 ", body)
        body = body.replace("\\cdot", " times ").replace("\\times", " times ").replace("^2", " squared")
        body = re.sub(r"\\[a-zA-Z]+", " ", body)
        return re.sub(r"[{}_^\\]", " ", body)

    text = re.sub(r"\$([^$\n]+)\$|\\\((.+?)\\\)", inline_math, text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`>#]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:MAX_SPEECH_CHARS]


def _wav(samples, sample_rate: int) -> bytes:
    import numpy as np

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


class SpeechService:
    def __init__(self, models_dir: Optional[Path] = None):
        self.models_dir = Path(models_dir or settings.data_dir / "models")
        self._whisper = None
        self._kokoro = None
        self._kokoro_zh = None
        self._zh_g2p = None
        self._ja_g2p = None
        self._lock = threading.Lock()
        self._whisper_lock = threading.Lock()
        self._kokoro_lock = threading.Lock()

    # ── status ──────────────────────────────────────────────────────────
    def status(self) -> dict:
        def installed(module: str) -> bool:
            import importlib.util
            return importlib.util.find_spec(module) is not None

        return {
            "enabled": settings.speech_enabled,
            "transcription": settings.speech_enabled and installed("faster_whisper"),
            "synthesis": settings.speech_enabled and installed("kokoro_onnx"),
            "whisper_model": settings.whisper_model,
            "languages": [{"code": lang.code, "label": lang.label, "voice": lang.voice} for lang in LANGUAGES.values()],
            "loaded": {"transcription": self._whisper is not None, "synthesis": self._kokoro is not None},
        }

    # ── models ──────────────────────────────────────────────────────────
    def _whisper_model(self):
        with self._lock:
            if self._whisper is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise SpeechUnavailable("Speech recognition is not installed (faster-whisper).") from exc
                self._whisper = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8",
                                             download_root=str(self.models_dir / "whisper"))
            return self._whisper

    def _kokoro_model(self):
        with self._lock:
            if self._kokoro is None:
                try:
                    from kokoro_onnx import Kokoro
                except ImportError as exc:
                    raise SpeechUnavailable("Speech synthesis is not installed (kokoro-onnx).") from exc
                folder = self._fetch(KOKORO_FILES)
                self._kokoro = Kokoro(str(folder / "kokoro-v1.0.int8.onnx"), str(folder / "voices-v1.0.bin"))
            return self._kokoro

    def _kokoro_zh_model(self):
        """The Mandarin model and its G2P; English words inside Chinese use espeak."""
        with self._lock:
            if self._kokoro_zh is None:
                try:
                    from kokoro_onnx import Kokoro
                    from misaki import zh
                except ImportError as exc:
                    raise SpeechUnavailable("Chinese speech needs misaki-fork[zh].") from exc
                folder = self._fetch(KOKORO_ZH_FILES)
                model = Kokoro(str(folder / "kokoro-v1.1-zh.int8.onnx"), str(folder / "voices-v1.1-zh.bin"),
                               vocab_config=str(folder / "config-v1.1-zh.json"))
                self._zh_g2p = zh.ZHG2P(version="1.1", en_callable=lambda text: model.tokenizer.phonemize(text, "en-us"))
                self._kokoro_zh = model
            return self._kokoro_zh

    def _japanese_g2p(self):
        """Japanese readings from OpenJTalk; espeak cannot read kanji."""
        with self._lock:
            if self._ja_g2p is None:
                try:
                    from misaki import ja
                except ImportError as exc:
                    raise SpeechUnavailable("Japanese speech needs pyopenjtalk-plus.") from exc
                self._ja_g2p = ja.JAG2P(version="pyopenjtalk")
            return self._ja_g2p

    def _fetch(self, files: dict[str, tuple[str, str]]) -> Path:
        folder = self.models_dir / "kokoro"
        folder.mkdir(parents=True, exist_ok=True)
        for name, (url, digest) in files.items():
            if not (folder / name).exists():
                self._download(url, folder / name, digest)
        return folder

    @staticmethod
    def _download(url: str, path: Path, digest: str) -> None:
        import hashlib

        partial = path.with_suffix(path.suffix + ".part")
        sha = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                sha.update(chunk)
                out.write(chunk)
        if sha.hexdigest() != digest:
            partial.unlink(missing_ok=True)
            raise SpeechUnavailable(f"Downloaded {path.name} did not match its published checksum.")
        partial.rename(path)

    # ── operations ──────────────────────────────────────────────────────
    def _transcribe(self, audio: bytes, language: Optional[str]) -> dict:
        model = self._whisper_model()
        with self._whisper_lock:
            hint = language if language in LANGUAGES else None
            segments, info = model.transcribe(io.BytesIO(audio), language=hint, vad_filter=True, beam_size=1,
                                              initial_prompt=TRANSCRIBE_PROMPTS.get(hint or "", DEFAULT_TRANSCRIBE_PROMPT))
            text = " ".join(segment.text.strip() for segment in segments).strip()
        return {"text": text, "language": info.language, "language_probability": round(info.language_probability, 3),
                "duration": round(info.duration, 2)}

    def _synthesize(self, text: str, language: Optional[str], voice: Optional[str], speed: float) -> bytes:
        lang = LANGUAGES.get(language or "en", LANGUAGES["en"])
        spoken = speakable(text)
        if not spoken:
            raise ValueError("Nothing to speak.")
        if lang.code == "zh":
            kokoro = self._kokoro_zh_model()
            with self._kokoro_lock:
                phonemes, _ = self._zh_g2p(spoken)
                chosen = voice if voice and voice in kokoro.get_voices() else lang.voice
                samples, sample_rate = kokoro.create(phonemes, voice=chosen, speed=speed, is_phonemes=True)
            return _wav(samples, sample_rate)
        kokoro = self._kokoro_model()
        g2p = self._japanese_g2p() if lang.code == "ja" else None
        with self._kokoro_lock:
            chosen = voice if voice and voice in kokoro.get_voices() else lang.voice
            if g2p is not None:
                phonemes, _ = g2p(spoken)
                samples, sample_rate = kokoro.create(phonemes, voice=chosen, speed=speed, is_phonemes=True)
            else:
                samples, sample_rate = kokoro.create(spoken, voice=chosen, speed=speed, lang=lang.kokoro)
        return _wav(samples, sample_rate)

    def prefetch(self) -> None:
        """Download and load the models in the background so the first voice request is quick."""
        def run() -> None:
            for load in (self._kokoro_model, self._whisper_model, self._kokoro_zh_model):
                try:
                    load()
                except Exception:  # noqa: BLE001 - voice stays optional; requests report their own errors
                    logger.warning("Speech model prefetch failed", exc_info=True)
        threading.Thread(target=run, name="speech-prefetch", daemon=True).start()

    async def transcribe(self, audio: bytes, language: Optional[str] = None) -> dict:
        if not settings.speech_enabled:
            raise SpeechUnavailable("Voice features are disabled (SPEECH_ENABLED=false).")
        if not audio:
            raise ValueError("No audio was recorded.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise ValueError("The recording is too long; keep voice messages under a few minutes.")
        return await asyncio.to_thread(self._transcribe, audio, language)

    async def synthesize(self, text: str, language: Optional[str] = None, voice: Optional[str] = None,
                         speed: float = 1.0) -> bytes:
        if not settings.speech_enabled:
            raise SpeechUnavailable("Voice features are disabled (SPEECH_ENABLED=false).")
        return await asyncio.to_thread(self._synthesize, text, language, voice, min(max(speed, 0.5), 2.0))


speech = SpeechService()
