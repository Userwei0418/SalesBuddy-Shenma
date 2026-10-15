"""Durable document/audio ingestion. OCR is deliberately not part of this contract."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import wave
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncpg
from defusedxml import ElementTree

from sales_backend.async_resources import file_io
from sales_backend.db import DatabaseBusy
from sales_backend.integrations.ffmpeg import run_ffmpeg
from sales_backend.integrations.senseaudio import (
    MAX_ASR_FILE_BYTES,
    EmptyTranscriptionError,
    InvalidTranscriptionError,
    SenseAudioClient,
    SenseAudioError,
)
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.capabilities import require_capability
from sales_backend.services.model_calls import DatabaseModelObserver
from sales_backend.services.runtime_config import load_runtime_configuration

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".amr", ".webm"}
MAX_UPLOAD = 100 * 1024 * 1024
MAX_DOCUMENT = 20 * 1024 * 1024
MAX_TEXT = 50000
IMPORT_ROOT = Path(os.environ.get("VISIT_IMPORT_ROOT", "/var/lib/sales-backend/visit-imports"))
MIN_TAIL_SECONDS = 2
ASR_CHECKPOINT_VERSION = 1


class AudioImportError(SenseAudioError):
    """Safe, actionable failure for one segment; earlier checkpoints stay intact."""

    def __init__(self, cause, segment, total, completed):
        if isinstance(cause, EmptyTranscriptionError):
            self.code = "ASR_NO_TEXT"
            detail = "未识别到有效文字，请确认录音清晰；可重试或拆分后上传"
        elif isinstance(cause, InvalidTranscriptionError):
            self.code = "ASR_INVALID_RESPONSE"
            detail = "转写服务返回格式异常，请重试；持续失败请联系管理员"
        else:
            self.code = "ASR_SERVICE_UNAVAILABLE"
            detail = "转写服务请求失败，请稍后重试"
        progress = f"；已保留前{completed}段文字，重试将继续处理" if completed else ""
        self.user_message = f"录音第{segment}/{total}段{detail}{progress}"
        super().__init__(self.user_message, status_code=cause.status_code, retryable=cause.retryable)


def import_failure_message(exc):
    if isinstance(exc, AudioImportError):
        return exc.user_message
    if isinstance(exc, SenseAudioError):
        return "语音服务暂不可用，请稍后重试"
    if isinstance(exc, (wave.Error, EOFError)):
        return "录音文件无法解码，请确认文件完整后重新上传"
    if isinstance(exc, (DatabaseBusy, TimeoutError, ConnectionError, asyncpg.PostgresConnectionError)):
        return "材料处理或保存暂未完成，请稍后重试；已保存的分段会继续复用"
    if isinstance(exc, OSError):
        return "材料文件暂无法读取或处理，请稍后重试；持续失败请联系管理员"
    return str(exc)[:200]


def prepare_audio_parts(parts):
    """Keep every decoded PCM frame; a tiny tail must not be sent by itself."""
    info = []
    for part in parts:
        with wave.open(str(part)) as stream:
            info.append((stream.getparams(), stream.getnframes() / stream.getframerate()))
    if sum(duration for _, duration in info) > 3600:
        raise ValueError("单次录音最长60分钟，请拆分上传")
    if not any(params.nframes for params, _ in info):
        raise ValueError("录音中没有有效音频，请重新录制")
    for part, (params, _) in zip(parts, info, strict=True):
        if part.stat().st_size > MAX_ASR_FILE_BYTES:
            raise ValueError("录音分段超过识别大小限制，请拆分录音后上传")
        with wave.open(str(part)) as stream:
            if len(stream.readframes(params.nframes)) != params.nframes * params.nchannels * params.sampwidth:
                raise ValueError("录音分段不完整，请重新上传")
    if len(parts) > 1 and info[-1][1] < MIN_TAIL_SECONDS:
        previous, tail = info[-2][0], info[-1][0]
        if ((previous.nchannels, previous.sampwidth, previous.framerate, previous.comptype)
                != (tail.nchannels, tail.sampwidth, tail.framerate, tail.comptype)):
            raise ValueError("录音分段格式不一致，请重新上传")
        frame_size = previous.nchannels * previous.sampwidth
        # The converter emits mono 16 kHz PCM: 242 seconds is under 8 MB. Keep
        # an explicit provider-limit guard if the codec or segment size changes.
        if 44 + (previous.nframes + tail.nframes) * frame_size > MAX_ASR_FILE_BYTES:
            raise ValueError("录音尾段合并后超过识别大小限制，请拆分录音后上传")
        merged = parts[-1].with_name("merged-tail.wav")
        with wave.open(str(merged), "wb") as output:
            output.setparams(previous._replace(nframes=0))
            for part in parts[-2:]:
                with wave.open(str(part)) as stream:
                    frames = stream.readframes(stream.getnframes())
                    if len(frames) != stream.getnframes() * frame_size:
                        raise ValueError("录音分段不完整，请重新上传")
                    output.writeframes(frames)
        parts = [*parts[:-2], merged]
    if any(part.stat().st_size > MAX_ASR_FILE_BYTES for part in parts):
        raise ValueError("录音分段超过识别大小限制，请拆分录音后上传")
    return parts


def asr_configuration_fingerprint(settings):
    # Scope reuse to provider/model/configuration, never to credentials. Only
    # the digest is persisted, not URLs, keys or provider response bodies.
    value = {"model": settings.asr_model, "base_url": settings.senseaudio_base_url,
             "endpoint": settings.model_api_endpoint, "metadata": settings.model_api_metadata}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def document_text(filename, content):
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".txt"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
    elif suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("请先解除PDF密码后再上传")
        if len(reader.pages) > 200:
            raise ValueError("PDF最多200页，请拆分上传")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix in {".docx", ".pptx"}:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 3000 or sum(i.file_size for i in archive.infolist()) > 80 * 1024 * 1024:
                raise ValueError("文档解压体积过大，请精简后上传")
            if suffix == ".docx":
                names = ["word/document.xml"]
            else:
                import re

                names = sorted(
                    (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml", n).group(1)),
                )
            sections = []
            for name in names:
                xml = archive.read(name)
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise ValueError("文档XML格式不支持")
                root = ElementTree.fromstring(xml)
                paragraphs = []
                for p in root.iter():
                    if p.tag.endswith("}p"):
                        paragraphs.append("".join(t.text or "" for t in p.iter() if t.tag.endswith("}t")))
                sections.append("\n".join(paragraphs))
            text = "\n\n".join(sections)
    else:
        raise ValueError("暂不支持此文档格式")
    text = text.replace("\x00", "").strip()
    if not text:
        raise ValueError("未提取到可编辑文字；扫描件暂不支持，请上传文本型文档")
    if len(text) > MAX_TEXT:
        raise ValueError("文字超过5万字，请拆分文件后上传")
    return text


async def audio_text(filename, content, client, *, resume_evidence=None, checkpoint=None, configuration=""):
    with TemporaryDirectory(prefix="sales-visit-audio-") as folder:
        source = Path(folder) / ("source" + Path(filename).suffix.lower())
        await file_io(source.write_bytes, content)
        output = Path(folder) / "part-%03d.wav"
        try:
            result = await run_ffmpeg(
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-t",
                "3601",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "segment",
                "-segment_time",
                "240",
                str(output),
                timeout=120,
            )
        except TimeoutError:
            raise ValueError("录音转码超时，请压缩或拆分重试") from None
        parts = sorted(Path(folder).glob("part-*.wav"))
        if result.returncode or not parts:
            raise ValueError("录音文件无法解码，请确认文件完整")
        parts = await file_io(prepare_audio_parts, parts)
        hashes = [hashlib.sha256(await file_io(part.read_bytes)).hexdigest() for part in parts]
        identity = {"source": "asr_checkpoint", "version": ASR_CHECKPOINT_VERSION,
                    "audio_sha256": hashlib.sha256(content).hexdigest(), "configuration": configuration,
                    "segment_sha256": hashes}
        saved = []
        for item in resume_evidence if isinstance(resume_evidence, list) else []:
            if isinstance(item, dict) and all(item.get(k) == v for k, v in identity.items()):
                saved = item.get("segments", [])
                break
        if not isinstance(saved, list):
            saved = []
        # Only a contiguous, validated prefix can be reused. Legacy evidence
        # contains no text and is deliberately not accepted as a checkpoint.
        completed = []
        for index, item in enumerate(saved[:len(parts)]):
            if (not isinstance(item, dict) or type(item.get("segment")) is not int or item["segment"] != index + 1
                    or item.get("sha256") != hashes[index] or not isinstance(item.get("text"), str)
                    or not item["text"].strip() or "\x00" in item["text"]
                    or len("\n".join([*(s["text"] for s in completed), item["text"]])) > MAX_TEXT):
                break
            completed.append({"segment": index + 1, "sha256": hashes[index], "text": item["text"],
                              "trace_id": item.get("trace_id") if isinstance(item.get("trace_id"), str) else None})
        if checkpoint:
            await checkpoint([{**identity, "segments": list(completed)}])
        persisted_count = len(completed)
        texts = []
        evidence = []
        for index, part in enumerate(parts):
            if index < len(completed):
                item = completed[index]
            else:
                try:
                    result = await client.transcribe(
                        filename=part.name, content=await file_io(part.read_bytes), mime_type="audio/wav"
                    )
                except SenseAudioError as exc:
                    raise AudioImportError(exc, index + 1, len(parts), persisted_count) from exc
                item = {"segment": index + 1, "sha256": hashes[index], "text": result.text,
                        "trace_id": result.trace_id if isinstance(result.trace_id, str) else None}
                if len("\n".join([*texts, result.text])) > MAX_TEXT:
                    raise ValueError("转写文字超过5万字，请拆分录音")
                completed.append(item)
                if checkpoint:
                    await checkpoint([{**identity, "segments": list(completed)}])
                    persisted_count = len(completed)
            texts.append(item["text"])
            evidence.append({"segment": index + 1, "trace_id": item["trace_id"]})
        text = "\n".join(texts).strip()
        if not text:
            raise ValueError("未识别到有效语音，请确认录音清晰后重试")
        if len(text) > MAX_TEXT:
            raise ValueError("转写文字超过5万字，请拆分录音")
        return text, evidence


class VisitImportHandler:
    def __init__(self, database):
        self.database = database

    async def handle(self, import_id, actor):
        async with self.database.transaction(actor) as connection:
            await require_capability(connection, actor, "visit.upload")
            row = await connection.fetchrow(
                "SELECT * FROM activity.visit_import WHERE id=$1::uuid AND created_by_user_ref_id=$2::uuid",
                import_id,
                actor.user_id,
            )
            if not row:
                raise ValueError("文件任务不存在或不可见")
            if Path(row["filename"]).suffix.lower() in AUDIO_EXTENSIONS:
                await require_capability(connection, actor, "visit.transcribe")
            if row["status"] == "succeeded":
                return
            await connection.execute(
                """UPDATE activity.visit_import SET status='processing',error_message=NULL,updated_at=clock_timestamp()
                WHERE id=$1::uuid""",
                import_id,
            )

        from sales_backend.services.material_storage import MaterialStorage

        content = await MaterialStorage().read(self.database, actor, dict(row))
        if Path(row["filename"]).suffix.lower() in DOCUMENT_EXTENSIONS:
            text = await asyncio.to_thread(document_text, row["filename"], content)
            evidence = [{"source": "document_text"}]
        else:
            runtime = await load_runtime_configuration(self.database, actor, self.database.settings, purpose="asr")
            client = SenseAudioClient(
                runtime.settings,
                observer=DatabaseModelObserver(self.database, actor, "visit_import", operation_id=import_id),
            )

            async def checkpoint(evidence):
                # Each successful segment commits independently, without holding
                # a connection during ASR. Existing lease/RLS and capabilities
                # are rechecked; partial text is never exposed as extracted_text.
                for attempt in range(3):
                    try:
                        async with self.database.transaction(actor) as connection:
                            await require_capability(connection, actor, "visit.upload")
                            await require_capability(connection, actor, "visit.transcribe")
                            changed = await connection.execute(
                                """UPDATE activity.visit_import SET evidence=$2::jsonb,updated_at=clock_timestamp()
                                WHERE id=$1::uuid AND created_by_user_ref_id=$3::uuid
                                AND workspace_id=common.current_workspace_id() AND status='processing'""",
                                import_id, evidence, actor.user_id,
                            )
                            if changed != "UPDATE 1":
                                raise PermissionError("录音处理状态已变化，请重新查询")
                        return
                    except (DatabaseBusy, TimeoutError, ConnectionError, asyncpg.PostgresConnectionError,
                            asyncpg.SerializationError, asyncpg.DeadlockDetectedError):
                        # Retry this idempotent checkpoint, not the billed ASR
                        # call. Cancellation, authority or lease loss are not retried.
                        if attempt == 2:
                            raise
                        await asyncio.sleep(.1 * (attempt + 1))

            try:
                text, evidence = await audio_text(
                    row["filename"], content, client, resume_evidence=row["evidence"], checkpoint=checkpoint,
                    configuration=asr_configuration_fingerprint(runtime.settings),
                )
            finally:
                await client.close()
        async with self.database.transaction(actor) as connection:
            await require_capability(connection, actor, "visit.upload")
            await connection.execute(
                """UPDATE activity.visit_import SET
                status='succeeded',extracted_text=$2,evidence=$3::jsonb,error_message=NULL,updated_at=clock_timestamp()
                WHERE id=$1::uuid""",
                import_id,
                text,
                evidence,
            )
            if Path(row["filename"]).suffix.lower() in AUDIO_EXTENSIONS:
                await require_capability(connection, actor, "visit.transcribe")
            await record_job_effect(connection, actor.workspace_id)
