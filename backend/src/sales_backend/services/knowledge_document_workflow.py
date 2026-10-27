"""Published document Workflow API; independent of the retrieval provider.

The public contract was observed on 2026-10-17: inputs.source_text, streaming,
outputs.report and outputs.files. No text-to-Office fallback, regeneration, redirects,
file credential forwarding, or raw provider error logging are permitted here.
Injected transports are only for isolated tests.
"""

import asyncio
import json
import math
import time
from dataclasses import dataclass
from uuid import UUID

import httpx

from sales_backend.config import Settings
from sales_backend.services.knowledge_artifact_download import (
    KnowledgeArtifactDownloadError,
    _url,
    download_knowledge_artifact,
)
from sales_backend.services.knowledge_artifacts import KnowledgeArtifactError, ValidatedKnowledgeArtifact
from sales_backend.services.knowledge_provider import KnowledgeProviderError

MAX_SOURCE_CHARS = 10_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_FILES = 4
MAX_TOTAL_FILE_BYTES = 50 * 1024 * 1024
SIGNED_FILE_TTL_SECONDS = 300


class DocumentWorkflowError(KnowledgeProviderError):
    def __init__(self, code: str, *, run_id: str | None = None):
        self.run_id = run_id
        super().__init__(code)


@dataclass(frozen=True)
class WorkflowDocuments:
    run_id: str
    files: tuple[ValidatedKnowledgeArtifact, ...]


def validate_document_input(source_text: str, user: str) -> None:
    """Reject correctable input before reserving a remote side effect."""
    if not isinstance(source_text, str) or not source_text.strip() or len(source_text) > MAX_SOURCE_CHARS:
        raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_SOURCE_LIMIT")
    if not isinstance(user, str) or not user or len(user) > 128 or any(c.isspace() for c in user):
        raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_INPUT_INVALID")


def _uuid(value):
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _safe_error(exc, run_id):
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        code = "KNOWLEDGE_DOCUMENT_TIMEOUT"
    elif isinstance(exc, KnowledgeArtifactDownloadError):
        code = "KNOWLEDGE_DOCUMENT_DOWNLOAD_UNAVAILABLE" if exc.code.endswith("HTTP_STATUS") else exc.code
    elif isinstance(exc, KnowledgeArtifactError):
        code = exc.code
    elif isinstance(exc, httpx.HTTPError):
        code = "KNOWLEDGE_DOCUMENT_NETWORK_UNAVAILABLE"
    else:
        code = "KNOWLEDGE_DOCUMENT_RESPONSE_INVALID"
    return DocumentWorkflowError(code, run_id=run_id)


class DocumentWorkflowClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        download_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = settings.knowledge_document_workflow_base_url.rstrip("/")
        self.api_key = settings.knowledge_document_workflow_api_key
        self.source_only_reviewed = settings.knowledge_document_workflow_source_only_reviewed
        self.timeout_seconds = settings.knowledge_document_workflow_timeout_seconds
        self.transport = transport
        self.download_transport = download_transport
        self.allowed_origins = ()
        self.valid_configuration = False
        try:
            base = _url(self.base_url)
            # Only the known public API base is supported, never a private console
            # URL or response-derived host. Validate the origin with the receiver.
            origin = str(base.copy_with(path="", query=None, fragment=None))
            _url(origin, origin_only=True)
            if base.path != "/v1" or base.query or base.fragment or base.userinfo:
                return
            origins = json.loads(settings.knowledge_document_workflow_download_origins or "[]")
            if not isinstance(origins, list) or not 1 <= len(origins) <= 8:
                return
            self.allowed_origins = tuple(str(_url(value, origin_only=True)) for value in origins)
            self.valid_configuration = bool(
                self.api_key
                and not any(c.isspace() or ord(c) < 32 for c in self.api_key)
                and isinstance(self.timeout_seconds, (int, float))
                and not isinstance(self.timeout_seconds, bool)
                and math.isfinite(self.timeout_seconds)
                and 0 < self.timeout_seconds <= 60
            )
        except (ValueError, TypeError, httpx.InvalidURL):
            pass

    @property
    def configured(self):
        return self.valid_configuration and self.source_only_reviewed is True

    @staticmethod
    def _files(final, run_id):
        if not isinstance(final, dict):
            raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID", run_id=run_id)
        status = final.get("status")
        if status != "succeeded":
            code = {
                "paused": "KNOWLEDGE_DOCUMENT_RUN_PAUSED",
                "stopped": "KNOWLEDGE_DOCUMENT_RUN_STOPPED",
                "failed": "KNOWLEDGE_DOCUMENT_RUN_FAILED",
                "partial-succeeded": "KNOWLEDGE_DOCUMENT_RUN_FAILED",
            }.get(status, "KNOWLEDGE_DOCUMENT_RESULT_UNKNOWN")
            raise DocumentWorkflowError(code, run_id=run_id)
        outputs = final.get("outputs")
        # GET run details can return the stored outputs as JSON text. The outer
        # body and this nested value are both bounded before parsing.
        if isinstance(outputs, str):
            if len(outputs.encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_RESPONSE_LIMIT", run_id=run_id)
            outputs = json.loads(outputs)
        if not isinstance(outputs, dict) or not isinstance(outputs.get("report"), str):
            raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID", run_id=run_id)
        files = outputs.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
            raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_FILES_INVALID", run_id=run_id)
        file_bytes = 0
        for item in files:
            if (
                not isinstance(item, dict)
                or any(not isinstance(item.get(k), str) or not item[k] for k in ("filename", "url", "mime_type"))
                or item.get("extension") != ".docx"
                or item.get("type") != "document"
                or item.get("transfer_method") != "tool_file"
                or any(item.get(k) is not None and not isinstance(item[k], str) for k in ("id", "related_id"))
                or isinstance(item.get("size"), bool)
                or not isinstance(item.get("size"), int)
                or not 0 < item["size"] <= 25 * 1024 * 1024
            ):
                raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_FILES_INVALID", run_id=run_id)
            file_bytes += item["size"]
            if file_bytes > MAX_TOTAL_FILE_BYTES:
                raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_FILES_LIMIT", run_id=run_id)
        return files

    @staticmethod
    def _file_identity(item):
        url = _url(item["url"])
        # Only the URL query may rotate. A different path, origin, file ID, or
        # metadata is a different artifact, even when the run ID is unchanged.
        return (
            str(url.copy_with(query=None)),
            *(
                item.get(k)
                for k in ("id", "related_id", "filename", "mime_type", "size", "extension", "type", "transfer_method")
            ),
        )

    @staticmethod
    def _expired_signature(url):
        # A 403 alone also means a bad signature/file ID, not necessarily expiry.
        # Only the documented timestamp-in-seconds shape supports a single retry.
        values = _url(url).params.get_list("timestamp")
        return (
            len(values) == 1
            and values[0].isascii()
            and values[0].isdecimal()
            and len(values[0]) == 10
            and time.time() - int(values[0]) >= SIGNED_FILE_TTL_SECONDS
        )

    async def _read_run(self, run_id):
        from sales_backend.knowledge_availability import require_knowledge_available
        require_knowledge_available()
        receiver = self.transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        request = httpx.Request(
            "GET",
            self.base_url + "/workflows/run/" + run_id,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            extensions={"timeout": httpx.Timeout(min(35, self.timeout_seconds)).as_dict()},
        )
        async with receiver:
            response = await receiver.handle_async_request(request)
            try:
                if response.status_code in {401, 403}:
                    raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_AUTH_UNAVAILABLE", run_id=run_id)
                if response.status_code != 200:
                    raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_HTTP_UNAVAILABLE", run_id=run_id)
                if (
                    response.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json"
                    or response.headers.get("content-encoding", "identity").strip().lower() != "identity"
                ):
                    raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID", run_id=run_id)
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_RESPONSE_LIMIT", run_id=run_id)
                    raw.extend(chunk)
            finally:
                await response.aclose()
        details = json.loads(raw)
        if not isinstance(details, dict) or _uuid(details.get("id")) != run_id:
            raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID", run_id=run_id)
        return self._files(details, run_id)

    async def _download_run(self, run_id, *, expected_files=None):
        files = await self._read_run(run_id)
        identity = tuple(self._file_identity(item) for item in files)
        if expected_files is not None and identity != tuple(self._file_identity(item) for item in expected_files):
            raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_FILES_INVALID", run_id=run_id)
        refreshed = False
        artifacts = []
        for index in range(len(files)):
            while True:
                item = files[index]
                try:
                    artifact = await download_knowledge_artifact(
                        item["url"],
                        filename=item["filename"],
                        media_type=item["mime_type"],
                        expected_format="docx",
                        allowed_origins=self.allowed_origins,
                        timeout_seconds=min(30, self.timeout_seconds),
                        transport=self.download_transport,
                    )
                except KnowledgeArtifactDownloadError as exc:
                    if exc.status_code != 403 or refreshed or not self._expired_signature(item["url"]):
                        raise
                    # Same run, at most once for the entire artifact set; never POST.
                    refreshed = True
                    files = await self._read_run(run_id)
                    if identity != tuple(self._file_identity(item) for item in files):
                        raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_FILES_INVALID", run_id=run_id) from None
                    continue
                if artifact.size_bytes != item["size"]:
                    raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_FILES_INVALID", run_id=run_id)
                artifacts.append(artifact)
                break
        return WorkflowDocuments(run_id, tuple(artifacts))

    async def download_run(self, run_id: str) -> WorkflowDocuments:
        """Receive an already-authorized run without generating anything.

        This is an internal receiver, not an authorization or public resume API.
        Callers must establish the run/work/source permissions before invoking it
        and revalidate them before persistence. Reading a known run does not need
        source-only generation approval; generate() continues to enforce that gate.
        """
        from sales_backend.knowledge_availability import require_knowledge_available
        require_knowledge_available()
        if not self.valid_configuration:
            raise DocumentWorkflowError("KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED")
        normalized = _uuid(run_id)
        if normalized is None:
            raise DocumentWorkflowError("KNOWLEDGE_DOCUMENT_INPUT_INVALID")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._download_run(normalized)
        except DocumentWorkflowError:
            raise
        except (
            TimeoutError,
            httpx.TimeoutException,
            KnowledgeArtifactDownloadError,
            KnowledgeArtifactError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            UnicodeError,
        ) as exc:
            raise _safe_error(exc, normalized) from None

    async def generate(self, source_text: str, *, user: str) -> WorkflowDocuments:
        from sales_backend.knowledge_availability import require_knowledge_available
        require_knowledge_available()
        if not self.configured:
            raise DocumentWorkflowError("KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED")
        validate_document_input(source_text, user)
        run_id = None

        def fail(code):
            raise DocumentWorkflowError(code, run_id=run_id)

        receiver = self.transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        request = httpx.Request(
            "POST",
            self.base_url + "/workflows/run",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
            },
            json={"inputs": {"source_text": source_text}, "response_mode": "streaming", "user": user},
            extensions={"timeout": httpx.Timeout(min(35, self.timeout_seconds)).as_dict()},
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with receiver:
                    response = await receiver.handle_async_request(request)
                    try:
                        if response.status_code in {401, 403}:
                            fail("KNOWLEDGE_DOCUMENT_AUTH_UNAVAILABLE")
                        if response.status_code != 200:
                            fail("KNOWLEDGE_DOCUMENT_HTTP_UNAVAILABLE")
                        if (
                            response.headers.get("content-type", "").partition(";")[0].strip().lower()
                            != "text/event-stream"
                            or response.headers.get("content-encoding", "identity") != "identity"
                        ):
                            fail("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID")
                        total = 0
                        pending = bytearray()
                        final = None
                        async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                            total += len(chunk)
                            if total > MAX_RESPONSE_BYTES:
                                fail("KNOWLEDGE_DOCUMENT_RESPONSE_LIMIT")
                            pending.extend(chunk)
                            while b"\n" in pending:
                                line, _, pending = pending.partition(b"\n")
                                if len(line) > MAX_EVENT_BYTES:
                                    fail("KNOWLEDGE_DOCUMENT_RESPONSE_LIMIT")
                                if not line.startswith(b"data:"):
                                    continue
                                event = json.loads(line[5:].strip())
                                if not isinstance(event, dict):
                                    fail("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID")
                                kind = event.get("event")
                                observed = _uuid(event.get("workflow_run_id"))
                                if kind == "workflow_started":
                                    if run_id is not None or observed is None:
                                        fail("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID")
                                    run_id = observed
                                elif observed is not None and observed != run_id:
                                    fail("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID")
                                if kind == "error":
                                    fail("KNOWLEDGE_DOCUMENT_RUN_FAILED")
                                if kind == "workflow_finished":
                                    if run_id is None or observed != run_id or not isinstance(event.get("data"), dict):
                                        fail("KNOWLEDGE_DOCUMENT_RESPONSE_INVALID")
                                    final = event["data"]
                                    break
                            if len(pending) > MAX_EVENT_BYTES:
                                fail("KNOWLEDGE_DOCUMENT_RESPONSE_LIMIT")
                            if final is not None:
                                break
                        if final is None:
                            fail("KNOWLEDGE_DOCUMENT_RESULT_UNKNOWN")
                    finally:
                        await response.aclose()
                expected_files = self._files(final, run_id)
                return await self._download_run(run_id, expected_files=expected_files)
        except DocumentWorkflowError:
            raise
        except (
            TimeoutError,
            httpx.TimeoutException,
            KnowledgeArtifactDownloadError,
            KnowledgeArtifactError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            UnicodeError,
        ) as exc:
            raise _safe_error(exc, run_id) from None
