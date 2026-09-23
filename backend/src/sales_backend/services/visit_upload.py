"""Upload ownership ends only after the import and its job commit together.

An uncertain commit retains the ready original for explicit reconciliation. No
automatic orphan deletion is performed here or against historical uploads.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from sales_backend.async_resources import file_io
from sales_backend.repositories.visit_imports import create_import
from sales_backend.services.capabilities import require_capability
from sales_backend.services.visit_import import AUDIO_EXTENSIONS, DOCUMENT_EXTENSIONS, MAX_DOCUMENT, MAX_UPLOAD

logger = logging.getLogger(__name__)
CHUNK_BYTES = 1024 * 1024


class UploadRejected(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class UploadSource(Protocol):
    async def read(self, size: int) -> bytes: ...


class UploadPhase(StrEnum):
    RECEIVING = "receiving"
    READY = "ready"
    REGISTERING = "registering"
    REGISTERED = "registered"


@dataclass(slots=True)
class LocalVisitUpload:
    import_id: str
    path: Path
    partial_path: Path
    stream: BinaryIO
    phase: UploadPhase = UploadPhase.RECEIVING
    size: int = 0
    commit_may_have_started: bool = False
    owns_partial: bool = True
    owns_ready: bool = False

    async def write(self, content: bytes) -> None:
        await file_io(self.stream.write, content)
        self.size += len(content)

    def _finish_file(self) -> None:
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        # UUID names are internal. Never overwrite even an unexpected collision.
        # link publishes the complete same-filesystem inode atomically and with
        # exclusive creation; both names are owned until the partial is removed.
        os.link(self.partial_path, self.path)
        self.owns_ready = True
        self.partial_path.unlink()
        self.owns_partial = False
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self.phase = UploadPhase.READY

    async def ready(self) -> None:
        await file_io(self._finish_file)

    def _discard_unregistered(self) -> None:
        errors = []
        try:
            self.stream.close()
        except OSError as exc:
            errors.append(exc)
        for owned, path in ((self.owns_partial, self.partial_path), (self.owns_ready, self.path)):
            if owned:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(exc)
        if errors:
            raise ExceptionGroup("upload resource cleanup failed", errors)

    async def release(self) -> None:
        if self.phase is UploadPhase.REGISTERED:
            return
        if self.commit_may_have_started:
            # A raised/cancelled COMMIT can still have committed. RLS-hidden or
            # temporarily unavailable reads are not proof that no row exists.
            logger.warning(
                "visit_upload_commit_unknown import_id=%s",
                self.import_id,
                extra={"system_event": True, "event_type": "visit_upload_commit_unknown"},
            )
            return
        try:
            await file_io(self._discard_unregistered)
        except Exception:
            logger.exception(
                "visit_upload_cleanup_failed import_id=%s",
                self.import_id,
                extra={"event_type": "visit_upload_cleanup_failed"},
            )


@dataclass(slots=True)
class LocalVisitImportStorage:
    root: Path

    def begin(self, import_id: str, suffix: str) -> LocalVisitUpload:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / (import_id + suffix)
        partial = self.root / (import_id + suffix + ".part")
        # O_EXCL and the initial mode avoid symlink following and the temporary
        # world-readable interval of open() followed by chmod().
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            stream = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            partial.unlink(missing_ok=True)
            raise
        return LocalVisitUpload(import_id, path, partial, stream)


@dataclass(slots=True)
class VisitUploadService:
    database: object
    storage: LocalVisitImportStorage
    _id_factory: Callable = field(default=uuid4, repr=False)
    registry: object | None = None

    async def upload(self, actor, *, source: UploadSource, filename: str) -> dict:
        async with self.database.transaction(actor, readonly=True) as connection:
            await require_capability(connection, actor, "visit.create")
        filename = Path(filename).name[:200]
        suffix = Path(filename).suffix.lower()
        if suffix not in DOCUMENT_EXTENSIONS | AUDIO_EXTENSIONS:
            raise UploadRejected(422, "支持录音、PDF、DOCX、PPTX、MD、TXT")
        limit = MAX_DOCUMENT if suffix in DOCUMENT_EXTENSIONS else MAX_UPLOAD
        upload = self.storage.begin(str(self._id_factory()), suffix)
        try:
            while chunk := await source.read(CHUNK_BYTES):
                if upload.size + len(chunk) > limit:
                    raise UploadRejected(413, "文档最大20MB，录音最大100MB")
                await upload.write(chunk)
            if not upload.size:
                raise UploadRejected(422, "文件为空")
            await upload.ready()
            location = await self.registry.prepare(actor, upload) if self.registry else None
            async with self.database.transaction(actor) as connection:
                await require_capability(connection, actor, "visit.create")
                upload.phase = UploadPhase.REGISTERING
                await create_import(connection, actor, upload.import_id, filename, upload.path, upload.size)
                if self.registry:
                    await self.registry.register(connection, actor, upload, location)
                # No await separates this marker from the transaction's exit.
                # Earlier exceptions force rollback; here COMMIT may be sent.
                upload.commit_may_have_started = True
            upload.phase = UploadPhase.REGISTERED
            if location and location["driver"] != "local":
                try:
                    await file_io(upload.path.unlink)
                except OSError:
                    logger.warning("Registered original staging cleanup deferred import_id=%s", upload.import_id)
            return {"id": upload.import_id, "status": "queued", "filename": filename}
        finally:
            await upload.release()
