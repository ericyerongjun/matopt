#!/usr/bin/env python3
"""Check the real demo server; optionally warm local STT/TTS with synthetic audio.

No LLM requests, saved chats, or API keys are printed or generated. Run before
presenting, not during the six-minute slot. Uses only the Python standard library.
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--language", choices=("en", "zh"), default="en")
    parser.add_argument("--warmup", action="store_true", help="Exercise microphone recognition and speech synthesis before checking readiness")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    headers = {}
    if os.environ.get("API_AUTH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["API_AUTH_TOKEN"]

    def request(path, body=None, content_type=None):
        request_headers = dict(headers)
        if content_type:
            request_headers["Content-Type"] = content_type
        request = urllib.request.Request(args.base_url.rstrip("/") + path, data=body, headers=request_headers)
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            data = response.read()
        print(f"OK {path} ({time.monotonic() - started:.2f}s)")
        return data

    try:
        request("/health")
        catalog = json.loads(request("/api/providers"))
        configured = any(provider.get("id") == "deepseek" and provider.get("configured") for provider in catalog.get("providers", []))
        status = json.loads(request("/api/speech/status"))
        if args.warmup:
            if not status.get("transcription") or not status.get("synthesis"):
                print("NOT READY: Install and enable local speech recognition and synthesis before warming up.")
                return 1
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\0\0" * 8000)
            boundary = "matopt-demo-audio"
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{args.language}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="warmup.wav"\r\n'
                    'Content-Type: audio/wav\r\n\r\n').encode() + buffer.getvalue() + f"\r\n--{boundary}--\r\n".encode()
            request("/api/speech/transcribe", body, f"multipart/form-data; boundary={boundary}")
            text = "Ready for the demo." if args.language == "en" else "演示已准备好。"
            request("/api/speech/synthesize", json.dumps({"text": text, "language": args.language}).encode(), "application/json")
            status = json.loads(request("/api/speech/status"))
        loaded = status.get("loaded") or {}
        synthesis_key = "synthesis_zh" if args.language == "zh" else "synthesis"
        microphone_ready = bool(status.get("transcription") and loaded.get("transcription"))
        voice_ready = bool(status.get("synthesis") and loaded.get(synthesis_key))
        print(f"DeepSeek credentials configured: {configured}")
        print(f"Microphone model loaded: {microphone_ready}")
        print(f"Tutor voice loaded ({args.language}): {voice_ready}")
        print("Browser check: allow microphone access; open the avatar; test one spoken question and one reply.")
        print("This checks server readiness, not API latency, browser permissions, or avatar rendering.")
        return 0 if configured and microphone_ready and voice_ready else 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Do not print response bodies, URLs or credentials from provider/server errors.
        print(f"NOT READY: request failed ({type(exc).__name__}). Check the server, authentication, and speech models.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
