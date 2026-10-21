"""Voice input (transcription) and spoken replies (synthesis)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.speech import LANGUAGES, MAX_AUDIO_BYTES, SpeechUnavailable, speech

router = APIRouter(prefix="/api/speech", tags=["speech"])


@router.get("/status")
async def speech_status():
    return speech.status()


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), language: Optional[str] = Form(None)):
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    try:
        return await speech.transcribe(data, language)
    except SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=60_000)
    language: Optional[str] = None
    voice: Optional[str] = Field(None, max_length=40)
    speed: float = Field(1.0, ge=0.5, le=2.0)


@router.post("/synthesize")
async def synthesize(body: SynthesizeRequest):
    if body.language and body.language not in LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language.")
    try:
        audio = await speech.synthesize(body.text, body.language, body.voice, body.speed)
    except SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
