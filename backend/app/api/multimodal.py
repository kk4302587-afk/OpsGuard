"""Multimodal input API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.multimodal.provider import (
    MultimodalError,
    UploadedBlob,
    normalize_transcript,
    provider,
)

router = APIRouter()


class NormalizeAudioRequest(BaseModel):
    text: str


@router.post("/images/analyze")
async def analyze_image(file: UploadFile = File(...)) -> dict:
    """Analyze an uploaded operations screenshot."""
    blob = UploadedBlob(
        filename=file.filename or "image",
        content_type=file.content_type or "",
        data=await file.read(),
    )
    try:
        result = await provider.analyze_image(blob)
    except MultimodalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": result}


@router.post("/audio/transcribe")
async def transcribe_audio(file: UploadFile = File(...)) -> dict:
    """Transcribe uploaded voice input."""
    blob = UploadedBlob(
        filename=file.filename or "audio",
        content_type=file.content_type or "",
        data=await file.read(),
    )
    try:
        result = await provider.transcribe_audio(blob)
    except MultimodalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": result}


@router.post("/audio/normalize")
async def normalize_audio_text(payload: NormalizeAudioRequest) -> dict:
    """Normalize an already-transcribed operations phrase."""
    return {"result": normalize_transcript(payload.text)}
