"""File-backed contribution -> durable upload -> parsing -> exact-version activation.

No link crawling, no implicit provider dataset, no unknown-upload retry. Network
operations run outside database transactions and repeat current authority checks.
"""

import asyncio
import hashlib
import json
import logging
import re
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg

from sales_backend.db import json_value
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.repositories.knowledge_ingestion import KnowledgeIngestionRepository
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.knowledge_provider import KnowledgeProviderClient, KnowledgeProviderError


def configuration(settings, workspace_id):
    """A deployment administrator must explicitly map this company to one dataset."""
    if settings is None:
        return None
    try:
        mapping = json.loads(getattr(settings, "knowledge_ingestion_workspace_datasets", "{}"))
        dataset = mapping.get(str(workspace_id)) if isinstance(mapping, dict) else None
        provider = KnowledgeProviderClient(settings)
        u = urlsplit(provider.base_url)
        if (
            not provider.configured
            or not provider.api_key
            or not isinstance(dataset, str)
            or dataset not in provider.dataset_ids
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", dataset)
            or u.scheme not in {"http", "https"}
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.path not in {"", "/"}
        ):
            return None
        origin = f"{u.scheme}://{u.netloc}"
        identity = {
            "origin": origin,
            "dataset": dataset,
            "retrieval_path": provider.retrieval_path,
            "auth_scheme": provider.auth_scheme,
            "workspace_id": str(workspace_id),
        }
        return {**identity, "fingerprint": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    except (ValueError, TypeError):
        return None


def source_problem(version):
    content = json_value(version["contribution"])
    if content.get("kind") == "legacy_import" or not version["file_count"]:
        return "files_required", "KNOWLEDGE_INGESTION_FILES_REQUIRED"
    if content.get("selection", {}).get("content", {}).get("links"):
        return "links_not_archived", "KNOWLEDGE_INGESTION_LINKS_NOT_ARCHIVED"
    return None


class KnowledgeIngestionService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.repository = KnowledgeIngestionRepository()
        self.provider = KnowledgeProviderClient(settings) if settings is not None else None
        self.poll_attempts = 8
        self.poll_interval = 2.0

    async def view_in(self, c, actor, material_id, version_id):
        v = await self.repository.version(c, material_id, version_id)
        i = await self.repository.current(c, material_id, version_id)
        configured = configuration(self.settings, actor.workspace_id)
        problem = source_problem(v)
        error = i["error_code"] if i else None
        can = bool(v["can_process"])
        if i and i["status"] == "succeeded":
            available = "complete"
        elif v["latest_version_id"] != version_id:
            available = "stale"
        elif i and (
            i["status"] == "unknown" or (i["status"] == "uploading" and i["job_status"] not in {"queued", "running"})
        ):
            available = "unknown"
            error = "KNOWLEDGE_INGESTION_UPLOAD_UNKNOWN"
        elif problem:
            available, error = problem
        elif configured is None:
            available = "unconfigured"
            error = "KNOWLEDGE_PROVIDER_UNCONFIGURED"
        elif i and i["configuration_fingerprint"] != configured["fingerprint"]:
            available = "stale"
            error = "KNOWLEDGE_INGESTION_CONFIGURATION_CHANGED"
        elif i and i["job_status"] in {"queued", "running"}:
            available = "running"
        elif i:
            available = "retryable"
        else:
            available = "ready"
        return dict(
            material_id=material_id,
            version_id=version_id,
            ingestion_id=i["id"] if i else None,
            state=v["state"],
            availability=available,
            can_retry=can and available in {"ready", "retryable"},
            error_code=error,
            processed_files=i["processed_files"] if i else 0,
            total_files=v["file_count"],
            observed_at=i["updated_at"] if i else None,
        )

    async def view(self, actor, material_id, version_id):
        async with self.database.transaction(actor, readonly=True) as c:
            return await self.view_in(c, actor, material_id, version_id)

    async def auto_enqueue(self, c, actor, receipt):
        from sales_backend import knowledge_availability
        if not knowledge_availability.KNOWLEDGE_AVAILABLE:
            return receipt
        cfg = configuration(self.settings, actor.workspace_id)
        if cfg is None:
            return receipt
        mid, vid = UUID(str(receipt["material_id"])), UUID(str(receipt["version_id"]))
        v = await self.repository.version(c, mid, vid)
        # Saved link/legacy contributions remain honest directory entries.
        if source_problem(v) or not v["can_process"]:
            return receipt
        await self.repository.start(c, actor, mid, vid, receipt["material_revision"], cfg)
        return {**receipt, "state": "syncing"}

    async def start(self, actor, key, material_id, version_id, body):
        from sales_backend.knowledge_availability import require_knowledge_available
        require_knowledge_available()
        if key is None:
            raise ValueError("KNOWLEDGE_IDEMPOTENCY_KEY_REQUIRED")
        cfg = configuration(self.settings, actor.workspace_id)
        if cfg is None:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNCONFIGURED")
        async with self.database.transaction(actor) as c:

            async def authorize():
                v = await self.repository.version(c, material_id, version_id)
                if json_value(v["contribution"]).get("kind") == "legacy_import":
                    raise ValueError("KNOWLEDGE_IMPORT_NOT_RETRIEVABLE")
                if not v["can_process"]:
                    raise PermissionError("KNOWLEDGE_ACCESS_DENIED")

            await authorize()

            async def perform():
                await self.repository.start(c, actor, material_id, version_id, body.expected_material_revision, cfg)
                return {"accepted": True}

            await execute_mutation(
                c,
                actor,
                key,
                f"knowledge.ingestion:{material_id}:{version_id}",
                body.model_dump(mode="json"),
                perform,
                authorize=authorize,
            )
            return await self.view_in(c, actor, material_id, version_id)

    async def _checkpoint(self, actor, ingestion_id, action="validate", **kwargs):
        async with self.database.transaction(actor) as c:
            await c.execute("SELECT security.authorization_refresh()")
            await self.repository.step(c, ingestion_id, action, **kwargs)
            if action == "activate":
                await record_job_effect(c, actor.workspace_id)

    async def _read(self, actor, ingestion_id):
        async with self.database.transaction(actor) as c:
            await self.repository.step(c, ingestion_id, "validate")
            task = await self.repository.task(c, ingestion_id)
            cfg = configuration(self.settings, actor.workspace_id)
            if cfg is None or cfg["fingerprint"] != task["configuration_fingerprint"]:
                raise ValueError("KNOWLEDGE_INGESTION_CONFIGURATION_CHANGED")
            files = await self.repository.files(c, ingestion_id)
            if not files or any(f["sha256"] != f["source_sha256"] for f in files):
                raise ValueError("KNOWLEDGE_INGESTION_SOURCE_CHANGED")
            return task, files

    async def _content(self, actor, ingestion_id, f):
        async with self.database.transaction(actor) as c:
            await self.repository.step(c, ingestion_id, "validate")
            data = await self.repository.content(c, ingestion_id, f["file_id"])
            if (
                not isinstance(data, bytes)
                or len(data) != f["size_bytes"]
                or len(data) > 25 * 1024 * 1024
                or hashlib.sha256(data).hexdigest() != f["source_sha256"]
            ):
                raise ValueError("KNOWLEDGE_INGESTION_SOURCE_CHANGED")
            return data

    async def _provider_call(self, awaitable):
        try:
            return await asyncio.wait_for(awaitable, timeout=15)
        except TimeoutError:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNAVAILABLE") from None

    async def _process(self, ingestion_id, actor):
        task, files = await self._read(actor, ingestion_id)
        dataset = task["provider_dataset_id"]
        for f in files:
            fid = f["file_id"]
            doc = f["provider_document_id"]
            if f["status"] == "uploading":
                raise ValueError("KNOWLEDGE_INGESTION_UPLOAD_UNKNOWN")
            if f["status"] == "pending":
                content = await self._content(actor, ingestion_id, f)
                # This committed fence is never cleared after an ambiguous request.
                await self._checkpoint(actor, ingestion_id, "upload_started", file_id=fid)
                await self._checkpoint(actor, ingestion_id)
                uploaded = await self._provider_call(
                    self.provider.upload_document(dataset, f["filename"], content, f["media_type"])
                )
                del content
                if len(uploaded) != 1:
                    raise ValueError("KNOWLEDGE_INGESTION_UPLOAD_RESPONSE_INVALID")
                row = uploaded[0]
                doc = row.get("id", row.get("document_id"))
                if (
                    not isinstance(doc, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", doc)
                    or row.get("dataset_id", dataset) != dataset
                ):
                    raise ValueError("KNOWLEDGE_INGESTION_UPLOAD_RESPONSE_INVALID")
                await self._checkpoint(actor, ingestion_id, "uploaded", file_id=fid, document_id=doc)
            if f["status"] == "parsed":
                continue
            await self._checkpoint(actor, ingestion_id)
            status = await self._provider_call(self.provider.document_status(dataset, doc))
            if status["state"] != "done":
                await self._checkpoint(actor, ingestion_id, "parse_started", file_id=fid)
                if status["state"] != "running":
                    await self._checkpoint(actor, ingestion_id)
                    await self._provider_call(self.provider.parse_documents(dataset, (doc,)))
                for _ in range(self.poll_attempts):
                    await asyncio.sleep(self.poll_interval)
                    await self._checkpoint(actor, ingestion_id)
                    status = await self._provider_call(self.provider.document_status(dataset, doc))
                    if status["state"] in {"done", "failed"}:
                        break
            if status["state"] == "failed":
                raise ValueError("KNOWLEDGE_INGESTION_PARSE_FAILED")
            if status["state"] != "done":
                raise ValueError("KNOWLEDGE_INGESTION_PARSE_PENDING")
            if status["chunk_count"] < 1:
                raise ValueError("KNOWLEDGE_INGESTION_NO_CHUNKS")
            await self._checkpoint(actor, ingestion_id, "parsed", file_id=fid, chunks=status["chunk_count"])
        await self._checkpoint(actor, ingestion_id, "activate")

    async def handle(self, ingestion_id, actor):
        from sales_backend.knowledge_availability import require_knowledge_available
        require_knowledge_available()
        try:
            # One finite job attempt; completed file receipts survive an explicit
            # retry, while an in-flight upload remains unknown and is never resent.
            async with asyncio.timeout(180):
                await self._process(ingestion_id, actor)
        except (Exception, asyncio.CancelledError) as error:
            code = getattr(error, "code", None) or str(error)
            if not re.fullmatch(r"KNOWLEDGE_[A-Z0-9_]{1,90}", code or ""):
                code = (
                    "KNOWLEDGE_ACCESS_DENIED"
                    if isinstance(error, (PermissionError, asyncpg.InsufficientPrivilegeError))
                    else "KNOWLEDGE_INGESTION_FAILED"
                )
            try:
                await self._checkpoint(actor, ingestion_id, "failed", error=code)
            except (Exception, asyncio.CancelledError):
                # A lost lease cannot modify projections. A persisted upload fence
                # still makes the next read/retry report unknown rather than reupload.
                logging.getLogger(__name__).warning(
                    "knowledge ingestion failure projection deferred: lease or access changed"
                )
            if isinstance(error, asyncio.CancelledError):
                raise
            raise KnowledgeProviderError(code) from None
