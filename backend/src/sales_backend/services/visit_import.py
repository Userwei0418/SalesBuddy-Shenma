"""Durable document/audio ingestion. OCR is deliberately not part of this contract."""

from __future__ import annotations

import asyncio
import io
import os
import wave
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from defusedxml import ElementTree

from sales_backend.async_resources import file_io
from sales_backend.integrations.ffmpeg import run_ffmpeg
from sales_backend.integrations.senseaudio import SenseAudioClient
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


async def audio_text(filename, content, client):
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
        seconds = 0
        for part in parts:
            with wave.open(str(part)) as wav:
                seconds += wav.getnframes() / wav.getframerate()
        if seconds > 3600:
            raise ValueError("单次录音最长60分钟，请拆分上传")
        texts = []
        evidence = []
        for index, part in enumerate(parts):
            result = await client.transcribe(
                filename=part.name, content=await file_io(part.read_bytes), mime_type="audio/wav"
            )
            texts.append(result.text)
            evidence.append({"segment": index + 1, "trace_id": result.trace_id})
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
            await require_capability(connection, actor, "visit.create")
            row = await connection.fetchrow(
                "SELECT * FROM activity.visit_import WHERE id=$1::uuid AND created_by_user_ref_id=$2::uuid",
                import_id,
                actor.user_id,
            )
            if not row:
                raise ValueError("文件任务不存在或不可见")
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
            try:
                text, evidence = await audio_text(row["filename"], content, client)
            finally:
                await client.close()
        async with self.database.transaction(actor) as connection:
            await require_capability(connection, actor, "visit.create")
            await connection.execute(
                """UPDATE activity.visit_import SET
                status='succeeded',extracted_text=$2,evidence=$3::jsonb,error_message=NULL,updated_at=clock_timestamp()
                WHERE id=$1::uuid""",
                import_id,
                text,
                evidence,
            )
            await record_job_effect(connection, actor.workspace_id)
