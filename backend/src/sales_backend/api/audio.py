from __future__ import annotations

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity, get_settings
from sales_backend.api.models import AudioTranscriptionResponse
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.integrations.audio_codec import AudioTranscodeError, transcode_for_asr
from sales_backend.integrations.ffmpeg_config import FfmpegUnavailable
from sales_backend.integrations.senseaudio import MAX_ASR_FILE_BYTES
from sales_backend.services.agent_access import require_agent_action
from sales_backend.services.capabilities import require_capability
from sales_backend.services.asr import AsrService, operation_receipt, prepare_session
from sales_backend.services.runtime_config import load_runtime_configuration

router = APIRouter(prefix="/api/v1/audio", tags=["Audio"])
logger = logging.getLogger(__name__)

PURPOSES = {"chatbi", "visit_entry", "customer_create", "management_task", "demo_scene"}


async def require_audio_purpose(connection, actor, purpose):
    capability = {"demo_scene": "demo_scene.create", "visit_entry": "visit.structure",
                  "management_task": "task.create_daily", "customer_create": "customer.create",
                  "chatbi": "agent.chatbi"}[purpose]
    try:
        if purpose != "visit_entry":
            from sales_backend.services.authorization import require_permission

            await require_permission(connection, "visit.transcribe")
        await require_capability(connection, actor, "visit.transcribe")
        if purpose == "customer_create":
            await require_agent_action(connection, purpose)
        else:
            await require_capability(connection, actor, capability)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post(
    "/transcriptions", response_model=AudioTranscriptionResponse,
    responses={403: {"description": "当前转写、用途或客户端入口权限不足"}},
)
async def transcribe_audio(
    purpose: str = Form(...),
    language: str = Form("zh"),
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(None),
    operation_id: uuid.UUID | None = Form(None),
    stage: Literal["preview", "final"] = Form("final"),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> AudioTranscriptionResponse:
    """转写录音；需要 visit.transcribe 以及对应用途的当前有效权限。

    customer_create 用途允许 customer.create 或 customer.create_request 任一权限，
    仅保存转写文本。建档草稿、提交申请及正式客户创建分别鉴权，不由转写授予。
    """
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
    except FfmpegUnavailable as exc:
        logger.warning("audio_transcoder_unavailable")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AUDIO_TRANSCODER_UNAVAILABLE") from exc
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
    session = await prepare_session(database, identity.actor, settings, scene=purpose,
                                    session_id=session_id, language=language)
    client = AsrService(database, identity.actor, settings, session)
    operation_id = operation_id or uuid.uuid5(uuid.UUID(str(session['id'])), stage + ':full')
    result = await client.transcribe(filename=normalized_filename, content=normalized_content,
                                    mime_type=normalized_mime, language=language, stage=stage,
                                    operation_id=operation_id)
    artifact_id = client.last_artifact_id
    async with database.transaction(identity.actor, readonly=True) as connection:
        await require_audio_purpose(connection, identity.actor, purpose)
    return AudioTranscriptionResponse(
        asr=client.last_metadata,
        artifact_id=artifact_id,
        text=result.text,
        purpose=purpose,
        duration_seconds=result.duration_seconds,
        trace_id=result.trace_id,
    )


@router.get('/operations/{operation_id}')
async def get_audio_operation(operation_id: uuid.UUID, purpose: str,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    if purpose not in PURPOSES:
        raise HTTPException(422, 'INVALID_AUDIO_PURPOSE')
    async with database.transaction(identity.actor, readonly=True) as conn:
        await require_audio_purpose(conn, identity.actor, purpose)
    result = await operation_receipt(database, identity.actor, operation_id, scene=purpose)
    return result
