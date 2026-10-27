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
from sales_backend.repositories import visit_upload_intents as intents
from sales_backend.services.capabilities import require_capability
from sales_backend.services.storage_quota import StorageQuotaExceeded, TenantStorageQuota
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.visit_import import AUDIO_EXTENSIONS, DOCUMENT_EXTENSIONS, MAX_DOCUMENT, MAX_UPLOAD

logger = logging.getLogger(__name__)
CHUNK_BYTES = 1024 * 1024


class UploadRejected(ValueError):
    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code or {413: "UPLOAD_TOO_LARGE", 422: "INVALID_UPLOAD"}.get(status_code, "UPLOAD_REJECTED")


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
    filename: str = ""
    quota_receipt: str = field(default_factory=lambda: str(uuid4()))
    quota_commit_may_have_started: bool = False
    quota_reserved: bool = False
    external_pending: bool = False
    intent_key: str | None = None
    intent_owned: bool = False
    intent_claim_uncertain: bool = False

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

    async def release(self) -> bool:
        if self.phase is UploadPhase.REGISTERED:
            return False
        if self.intent_claim_uncertain or self.commit_may_have_started or self.external_pending or (
            self.quota_commit_may_have_started and not self.quota_reserved
        ):
            # A raised/cancelled COMMIT can still have committed. RLS-hidden or
            # temporarily unavailable reads are not proof that no row exists.
            logger.warning(
                "visit_upload_commit_unknown import_id=%s",
                self.import_id,
                extra={"system_event": True, "event_type": "visit_upload_commit_unknown"},
            )
            return False
        try:
            await file_io(self._discard_unregistered)
            return True
        except Exception:
            logger.exception(
                "visit_upload_cleanup_failed import_id=%s",
                self.import_id,
                extra={"event_type": "visit_upload_cleanup_failed"},
            )
            return False


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
    quota: object | None = None

    def __post_init__(self):
        if self.quota is None:
            self.quota = TenantStorageQuota(self.database)

    async def _check_replayed_asr_source(self, actor, receipt, source_kind, session_id):
        if not receipt or not receipt.get('id'):
            return
        async with self.database.transaction(actor, readonly=True) as conn:
            await require_capability(conn, actor, 'visit.upload')
            row = await conn.fetchrow('''SELECT source_kind,asr_session_id FROM activity.visit_import
                WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()
                AND created_by_user_ref_id=common.current_user_ref_id()''', receipt['id'])
            if not row or row['source_kind'] != source_kind or (
                    session_id and str(row['asr_session_id']) != str(session_id)):
                raise UploadRejected(409, '上传来源与原操作不一致，请先核对原任务', 'ASR_OPERATION_CONFLICT')

    async def upload(self, actor, *, source: UploadSource, filename: str, intent_key=None, source_kind='legacy', asr_session_id=None) -> dict:
        async with self.database.transaction(actor, readonly=True) as connection:
            await require_capability(connection, actor, "visit.upload")
        if source_kind not in {'legacy', 'recording', 'file'}:
            raise UploadRejected(422, '录音来源不受支持', 'ASR_HOTWORD_INVALID')
        filename = Path(filename).name[:200]
        suffix = Path(filename).suffix.lower()
        if suffix not in DOCUMENT_EXTENSIONS | AUDIO_EXTENSIONS:
            raise UploadRejected(422, "支持录音、PDF、DOCX、PPTX、MD、TXT")
        if suffix in AUDIO_EXTENSIONS:
            async with self.database.transaction(actor, readonly=True) as connection:
                await require_capability(connection, actor, "visit.transcribe")
        session = None
        if suffix in AUDIO_EXTENSIONS and (asr_session_id or source_kind != 'legacy'):
            from sales_backend.services.asr import prepare_session
            from sales_backend.services.runtime_config import load_runtime_configuration
            runtime = await load_runtime_configuration(self.database, actor, self.database.settings, purpose='asr')
            scene = {'recording': 'visit_entry', 'file': 'visit_import', 'legacy': 'legacy'}[source_kind]
            session = await prepare_session(self.database, actor, runtime.settings, scene=scene, session_id=asr_session_id)
        limit = MAX_DOCUMENT if suffix in DOCUMENT_EXTENSIONS else MAX_UPLOAD
        upload = self.storage.begin(str(self._id_factory()), suffix)
        upload.filename = filename
        upload.intent_key = str(intent_key) if intent_key is not None else None
        try:
            while chunk := await source.read(CHUNK_BYTES):
                if upload.size + len(chunk) > limit:
                    raise UploadRejected(413, "文档最大20MB，录音最大100MB")
                await upload.write(chunk)
            if not upload.size:
                raise UploadRejected(422, "文件为空")
            await upload.ready()
            if self.registry:
                location = await self.registry.describe(actor, upload)
            else:
                location = {'driver': 'local', 'profile': 'legacy-local', 'key': str(upload.path),
                            'sha256': await file_io(MaterialStorage._hash, upload.path)}
            try:
                if upload.intent_key:
                    async with self.database.transaction(actor) as connection:
                        # Same order as maintenance: quota first, then intent.
                        # The intent cannot survive a crash without an allocation
                        # that the existing owner reconciliation tool can recover.
                        await connection.execute("SELECT security.lock_visit_upload_quota()")
                        upload.intent_owned, existing = await intents.claim(connection, actor, intent_key, upload, location)
                        if upload.intent_owned:
                            await self.quota.reserve_in_transaction(connection, upload, location)
                            upload.intent_claim_uncertain = True
                            upload.quota_commit_may_have_started = True
                    upload.intent_claim_uncertain = False
                    if not upload.intent_owned:
                        if suffix in AUDIO_EXTENSIONS:
                            await self._check_replayed_asr_source(actor, existing, source_kind, asr_session_id)
                        return existing
                    upload.quota_reserved = True
                else:
                    await self.quota.reserve(actor, upload, location)
            except StorageQuotaExceeded as exc:
                raise UploadRejected(409, str(exc), "STORAGE_QUOTA_EXCEEDED") from exc
            if location['driver'] == 's3':
                # Persist the uncertainty marker before any external PUT. A lost
                # acknowledgment must retain both the original and its capacity.
                upload.external_pending = True
                await self.quota.mark_external(actor, upload)
            if self.registry:
                await self.registry.prepare(actor, upload, location=location)
            async with self.database.transaction(actor) as connection:
                await require_capability(connection, actor, "visit.upload")
                if upload.intent_key:
                    existing = await intents.lock_registration(connection, intent_key, upload)
                    if existing:
                        upload.phase = UploadPhase.REGISTERED
                        return existing
                upload.phase = UploadPhase.REGISTERING
                await create_import(connection, actor, upload.import_id, filename, upload.path, upload.size, location)
                if suffix in AUDIO_EXTENSIONS:
                    await connection.execute("""UPDATE activity.visit_import SET source_kind=$2,asr_session_id=$3::uuid
                        WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()
                        AND created_by_user_ref_id=common.current_user_ref_id()""",
                        upload.import_id, source_kind, str(session['id']) if session else None)
                if self.registry:
                    await self.registry.register(connection, actor, upload, location)
                if upload.intent_key:
                    await intents.complete(connection, intent_key, upload)
                # No await separates this marker from the transaction's exit.
                # Earlier exceptions force rollback; here COMMIT may be sent.
                upload.commit_may_have_started = True
            upload.phase = UploadPhase.REGISTERED
            if location and location["driver"] != "local":
                try:
                    await file_io(upload.path.unlink)
                except OSError:
                    logger.warning("Registered original staging cleanup deferred import_id=%s", upload.import_id)
            if upload.intent_key:
                async with self.database.transaction(actor, readonly=True) as connection:
                    current = await intents.lookup(connection, intent_key)
                    if current is None:
                        raise intents.UploadStatusUnavailable("上传结果暂不可查，请保留原文件稍后重试查询")
                    return intents.receipt(current, replayed=False)
            return {"id": upload.import_id, "status": "queued", "filename": filename}
        finally:
            may_remove = True
            if upload.quota_reserved and upload.phase is not UploadPhase.REGISTERED and not (
                upload.commit_may_have_started or upload.external_pending
            ):
                try:
                    # A recovery can have registered this original meanwhile.
                    # Fence registration before touching its only local copy.
                    may_remove = await self.quota.fence(actor, upload)
                except Exception:
                    may_remove = False
                    logger.exception('storage_cleanup_fence_pending import_id=%s', upload.import_id)
            removed = may_remove and await upload.release()
            cleanup_complete = bool(removed and not upload.quota_reserved)
            if removed and upload.quota_reserved:
                try:
                    cleanup_complete = await self.quota.release(actor, upload)
                    if not cleanup_complete:
                        logger.warning('storage_capacity_release_pending import_id=%s', upload.import_id)
                except Exception:
                    # Cleanup must not replace the upload's primary error. The
                    # durable reservation remains visible for reconciliation.
                    logger.exception('storage_capacity_release_pending import_id=%s', upload.import_id)
            if upload.intent_owned and upload.phase is not UploadPhase.REGISTERED and not upload.intent_claim_uncertain:
                try:
                    async with self.database.transaction(actor) as connection:
                        await intents.finish_failure(connection, upload.intent_key, upload,
                                                     cleanup_complete=cleanup_complete)
                except Exception:
                    # Do not turn failed cleanup bookkeeping into proof that a
                    # new attempt is safe. Keep processing/uncertain durable.
                    logger.exception('upload_intent_reconciliation_pending import_id=%s', upload.import_id)
