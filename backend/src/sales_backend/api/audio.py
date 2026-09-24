from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity, get_settings
from sales_backend.api.models import AudioTranscriptionResponse
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.integrations.audio_codec import AudioTranscodeError, transcode_for_asr
from sales_backend.integrations.senseaudio import MAX_ASR_FILE_BYTES, SenseAudioClient, SenseAudioError
from sales_backend.repositories.audio_artifacts import save_transcript
from sales_backend.services.capabilities import require_capability
from sales_backend.services.model_calls import DatabaseModelObserver
from sales_backend.services.runtime_config import load_runtime_configuration

router = APIRouter(prefix="/api/v1/audio", tags=["Audio"])
logger = logging.getLogger(__name__)

PURPOSES = {"chatbi", "visit_entry", "customer_create", "management_task", "demo_scene"}


async def require_audio_purpose(connection, actor, purpose):
    capability = {"demo_scene": "demo_scene.create", "visit_entry": "visit.structure",
                  "management_task": "task.create_daily", "customer_create": "customer.create",
                  "chatbi": "agent.chatbi"}[purpose]
    try:
        await require_capability(connection, actor, "visit.transcribe")
        await require_capability(connection, actor, capability)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/transcriptions", response_model=AudioTranscriptionResponse)
async def transcribe_audio(
    purpose: str = Form(...),
    language: str = Form("zh"),
    file: UploadFile = File(...),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> AudioTranscriptionResponse:
    if purpose not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "INVALID_AUDIO_PURPOSE")
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_audio_purpose(connection, identity.actor, purpose)
    content = await file.read(MAX_ASR_FILE_BYTES + 1)
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "AUDIO_EMPTY")
    if len(content) > MAX_ASR_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "AUDIO_TOO_LARGE")
    try:
        normalized_filename, normalized_content, normalized_mime = await transcode_for_asr(
            filename=file.filename or "recording.mp3",
            content=content,
        )
    except AudioTranscodeError as exc:
        logger.warning(
            "ASR audio conversion failure purpose=%s filename=%s size=%s detail=%s",
            purpose,
            file.filename,
            len(content),
            str(exc),
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ASR_AUDIO_INVALID") from exc
    runtime = await load_runtime_configuration(database, identity.actor, settings, purpose="asr")
    settings = runtime.settings
    client = SenseAudioClient(settings, observer=DatabaseModelObserver(database, identity.actor, "audio." + purpose))
    try:
        result = await client.transcribe(
            filename=normalized_filename,
            content=normalized_content,
            mime_type=normalized_mime,
            language=language,
        )
    except SenseAudioError as exc:
        logger.warning(
            "ASR upstream failure purpose=%s filename=%s size=%s status=%s detail=%s",
            purpose,
            file.filename,
            len(content),
            exc.status_code,
            str(exc),
        )
        if exc.status_code == 429:
            detail = "ASR_RATE_LIMITED"
        elif exc.status_code in {500, 502, 503, 504}:
            detail = "ASR_SERVICE_BUSY"
        else:
            detail = "ASR_UPSTREAM_FAILED"
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail) from exc
    finally:
        await client.close()
    artifact_id = str(uuid.uuid4())
    async with database.transaction(identity.actor) as connection:
        await require_audio_purpose(connection, identity.actor, purpose)
        await save_transcript(
            connection, identity.actor, artifact_id, result, purpose, file.filename, settings.asr_model
        )
    return AudioTranscriptionResponse(
        artifact_id=artifact_id,
        text=result.text,
        purpose=purpose,
        duration_seconds=result.duration_seconds,
        trace_id=result.trace_id,
    )
