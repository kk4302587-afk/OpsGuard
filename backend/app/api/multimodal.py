"""Multimodal input API endpoints."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_knowledge_db_path
from app.multimodal.provider import (
    MultimodalError,
    UploadedBlob,
    normalize_transcript,
    provider,
)

router = APIRouter()

ATTACHMENT_DIR = Path("./data/attachments")


class NormalizeAudioRequest(BaseModel):
    text: str


@router.post("/images/analyze")
async def analyze_image(file: UploadFile = File(...)) -> dict:
    """Analyze an uploaded operations screenshot."""
    data = await file.read()
    blob = UploadedBlob(
        filename=file.filename or "image",
        content_type=file.content_type or "",
        data=data,
    )
    try:
        result = await provider.analyze_image(blob)
    except MultimodalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    attachment = await _store_attachment(blob, "image", result)
    result["attachment"] = attachment
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


@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: str):
    """Serve a stored multimodal attachment."""
    row = await _load_attachment(attachment_id)
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = Path(row["storage_path"])
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    return FileResponse(
        path,
        media_type=row["content_type"] or "application/octet-stream",
        filename=row["filename"],
    )


async def _store_attachment(blob: UploadedBlob, input_type: str, recognition: dict) -> dict:
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    attachment_id = str(uuid.uuid4())
    extension = _safe_extension(blob.filename, blob.content_type)
    storage_path = ATTACHMENT_DIR / f"{attachment_id}{extension}"
    storage_path.write_bytes(blob.data)

    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            """INSERT INTO message_attachments
            (id, input_type, filename, content_type, storage_path, size, sha256, recognition_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attachment_id,
                input_type,
                blob.filename,
                blob.content_type,
                str(storage_path),
                len(blob.data),
                blob.sha256,
                json.dumps(recognition, ensure_ascii=False, default=str),
            ),
        )
        await db.commit()

    return {
        "id": attachment_id,
        "type": input_type,
        "filename": blob.filename,
        "content_type": blob.content_type,
        "size": len(blob.data),
        "sha256": blob.sha256,
        "url": f"/api/multimodal/attachments/{attachment_id}",
    }


async def _load_attachment(attachment_id: str):
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM message_attachments WHERE id = ?",
            (attachment_id,),
        )
        return await cursor.fetchone()


def _safe_extension(filename: str, content_type: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".wav", ".mp3", ".m4a", ".webm"}:
        return suffix
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content_type == "audio/webm":
        return ".webm"
    return ".bin"
