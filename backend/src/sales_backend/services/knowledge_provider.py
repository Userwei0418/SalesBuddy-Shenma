"""Small, vendor-neutral adapter for the configured knowledge provider.

The provider is intentionally behind the contracts in ``contracts.knowledge``.
No provider response is allowed to become a SalesBuddy citation until the
request's exact material/version allowlist has been validated by the domain
layer.  An absent or incomplete configuration is reported as unconfigured,
never as an empty knowledge base.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from sales_backend.config import Settings
from sales_backend.knowledge_availability import require_knowledge_available
from sales_backend.contracts.knowledge import (
    KnowledgeChunk,
    KnowledgeCount,
    KnowledgeRetrievalRequest,
)
from sales_backend.domain.knowledge_access import validate_chunks


class KnowledgeProviderError(RuntimeError):
    """A provider failure which must remain distinguishable from no data."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def agent_message_text(payload: dict[str, Any]) -> str:
    """Extract one textual answer from the documented OpenAI-compatible shape.

    The provider may return either a plain string or the common content-part
    array.  We keep this normalization at the adapter boundary so generation
    code never trusts arbitrary provider JSON as a stored work artifact.
    """
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        text = "\n".join(parts).strip()
    else:
        text = ""
    if not text:
        raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
    return text


def _dataset_ids(raw: str) -> tuple[str, ...]:
    value = (raw or "").strip()
    if not value:
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in value.split(",")]
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return ()
    result = tuple(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))
    return result


class KnowledgeProviderClient:
    """Async client for the confirmed ``/api/v1/datasets`` provider API."""

    source = "knowledge.provider"

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = settings.knowledge_provider_base_url.rstrip("/")
        self.api_key = settings.knowledge_provider_api_key
        self.auth_scheme = settings.knowledge_provider_auth_scheme
        self.dataset_ids = _dataset_ids(settings.knowledge_provider_dataset_ids)
        self.retrieval_path = getattr(settings, "knowledge_provider_retrieval_path", "/api/v1/retrieval")
        self.agent_id = getattr(settings, "knowledge_provider_agent_id", "")
        self.source_only_reviewed = bool(getattr(settings, "knowledge_provider_source_only_reviewed", False))
        self.timeout = httpx.Timeout(settings.knowledge_provider_timeout_seconds)
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.dataset_ids)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        if self.auth_scheme in {"x-api-key", "api-key", "apikey"}:
            return {"X-API-Key": self.api_key}
        if self.auth_scheme == "query":
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _params(self) -> dict[str, str]:
        if self.api_key and self.auth_scheme == "query":
            return {"apiKey": self.api_key}
        return {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        require_knowledge_available()
        if not self.configured:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNCONFIGURED")
        kwargs.setdefault("headers", self._headers())
        if self.auth_scheme == "query":
            params = dict(kwargs.get("params") or {})
            params.update(self._params())
            kwargs["params"] = params
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                async with client.stream(method, path, **kwargs) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 4 * 1024 * 1024:
                            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
                        content.extend(chunk)
                    payload = json.loads(content)
        except (httpx.HTTPError, ValueError):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNAVAILABLE") from None
        if not isinstance(payload, dict) or payload.get("code") not in (0, "0", None):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_REJECTED")
        return payload

    async def datasets(self) -> tuple[dict[str, Any], ...]:
        payload = await self._request("GET", "/api/v1/datasets")
        data = payload.get("data", [])
        if isinstance(data, dict):
            data = data.get("items", data.get("datasets", []))
        if not isinstance(data, list):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
        rows = tuple(item for item in data if isinstance(item, dict))
        if not rows:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DATASET_NOT_FOUND")
        return rows

    def _require_dataset(self, dataset_id: str) -> str:
        dataset_id = str(dataset_id or "").strip()
        if not self.configured:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNCONFIGURED")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", dataset_id) or dataset_id not in self.dataset_ids:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DATASET_NOT_FOUND")
        return dataset_id

    async def upload_document(
        self, dataset_id: str, filename: str, content: bytes, media_type: str = "application/octet-stream"
    ) -> tuple[dict[str, Any], ...]:
        """Upload an internal material to the provider's documented local mode.

        This method deliberately returns the provider receipt without writing the
        SalesBuddy binding. The caller must persist the returned document id and
        only then expose the document to retrieval.
        """
        dataset_id = self._require_dataset(dataset_id)
        if not filename or not content:
            raise ValueError("KNOWLEDGE_PROVIDER_DOCUMENT_INVALID")
        payload = await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/documents",
            params={"type": "local"},
            files={"file": (filename, content, media_type or "application/octet-stream")},
        )
        data = payload.get("data")
        if isinstance(data, dict):
            data = next((data[k] for k in ("records", "items") if isinstance(data.get(k), list)), None)
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
        return tuple(data)

    async def update_document(self, dataset_id: str, document_id: str, **fields: Any) -> dict[str, Any]:
        dataset_id = self._require_dataset(dataset_id)
        document_id = str(document_id or "").strip()
        if not document_id:
            raise ValueError("KNOWLEDGE_PROVIDER_DOCUMENT_INVALID")
        if not fields:
            raise ValueError("KNOWLEDGE_PROVIDER_DOCUMENT_UPDATE_EMPTY")
        payload = await self._request("PUT", f"/api/v1/datasets/{dataset_id}/documents/{document_id}", json=fields)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
        return data

    async def delete_documents(
        self, dataset_id: str, document_ids: tuple[str, ...] = (), *, delete_all: bool = False
    ) -> None:
        dataset_id = self._require_dataset(dataset_id)
        ids = [str(value).strip() for value in document_ids if str(value).strip()]
        if delete_all and ids:
            raise ValueError("KNOWLEDGE_PROVIDER_DELETE_SELECTOR_CONFLICT")
        if not ids and not delete_all:
            raise ValueError("KNOWLEDGE_PROVIDER_DELETE_SELECTOR_REQUIRED")
        await self._request(
            "DELETE",
            f"/api/v1/datasets/{dataset_id}/documents",
            json={"ids": ids, "delete_all": delete_all},
        )

    async def parse_documents(self, dataset_id: str, document_ids: tuple[str, ...]) -> None:
        dataset_id = self._require_dataset(dataset_id)
        ids = [str(value).strip() for value in document_ids if str(value).strip()]
        if not ids:
            raise ValueError("KNOWLEDGE_PROVIDER_DOCUMENT_IDS_REQUIRED")
        await self._request("POST", f"/api/v1/datasets/{dataset_id}/chunks", json={"document_ids": ids})

    async def document_status(self, dataset_id: str, document_id: str) -> dict[str, Any]:
        """Read the exact document using the observed paginated documents route.

        No guessed single-document JSON route (that GET downloads raw bytes),
        no first-row/progress-only success. Unknown states cannot activate data.
        """
        dataset_id = self._require_dataset(dataset_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", document_id or ""):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DOCUMENT_INVALID")
        seen = set()
        for page in range(1, 21):
            payload = await self._request(
                "GET", f"/api/v1/datasets/{dataset_id}/documents", params={"page": page, "page_size": 100}
            )
            data = payload.get("data")
            rows = (
                data
                if isinstance(data, list)
                else next(
                    (
                        data[k]
                        for k in ("docs", "documents", "items")
                        if isinstance(data, dict) and isinstance(data.get(k), list)
                    ),
                    None,
                )
            )
            if rows is None or any(not isinstance(row, dict) for row in rows):
                raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
            matches = [row for row in rows if str(row.get("id", row.get("document_id", ""))) == document_id]
            if len(matches) > 1:
                raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
            if matches:
                row = matches[0]
                if row.get("dataset_id", dataset_id) != dataset_id:
                    raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
                raw = str(row.get("run", "")).upper()
                states = {
                    "0": "pending",
                    "UNSTART": "pending",
                    "1": "running",
                    "RUNNING": "running",
                    "2": "failed",
                    "CANCEL": "failed",
                    "3": "done",
                    "DONE": "done",
                    "4": "failed",
                    "FAIL": "failed",
                }
                if raw not in states:
                    raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_PARSE_STATE_UNKNOWN")
                count = row.get("chunk_count", row.get("chunk_num", 0))
                if type(count) is not int or count < 0:
                    raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
                return {"document_id": document_id, "state": states[raw], "chunk_count": count}
            ids = tuple(str(row.get("id", row.get("document_id", ""))) for row in rows)
            if not rows or ids in seen or len(rows) < 100:
                break
            seen.add(ids)
        raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DOCUMENT_NOT_FOUND")

    async def download_document(self, dataset_id: str, document_id: str) -> bytes:
        require_knowledge_available()
        dataset_id = self._require_dataset(dataset_id)
        document_id = str(document_id or "").strip()
        if not document_id:
            raise ValueError("KNOWLEDGE_PROVIDER_DOCUMENT_INVALID")
        if not self.configured:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNCONFIGURED")
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    f"/api/v1/datasets/{dataset_id}/documents/{document_id}",
                    headers=self._headers(),
                    params=self._params(),
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNAVAILABLE") from exc

    async def agent_completion(self, query: str, *, agent_id: str | None = None) -> dict[str, Any]:
        """Call the documented OpenAI-compatible Agent endpoint without streaming."""
        selected_agent = str(agent_id or self.agent_id or "").strip()
        if not self.configured:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNCONFIGURED")
        if not selected_agent:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_AGENT_UNCONFIGURED")
        if not str(query or "").strip():
            raise ValueError("KNOWLEDGE_PROVIDER_QUERY_REQUIRED")
        payload = await self._request(
            "POST",
            f"/api/v1/agents_openai/{selected_agent}/chat/completions",
            json={"model": "model", "messages": [{"role": "user", "content": query}], "stream": False},
        )
        if not isinstance(payload.get("choices"), list):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_RESPONSE_INVALID")
        return payload

    async def generate_material(self, prompt: str, *, agent_id: str | None = None) -> str:
        """Generate bounded text through the provider Agent contract.

        Persisting the returned text as a SalesBuddy work and archiving a
        DOCX/PPTX file remain the caller's responsibility.  This separation
        prevents a provider response from being presented as a completed work
        before the internal ACL and archive transaction succeeds.
        """
        return agent_message_text(await self.agent_completion(prompt, agent_id=agent_id))

    async def count(self) -> KnowledgeCount:
        require_knowledge_available()
        if not self.configured:
            return KnowledgeCount(status="unconfigured", count=None)
        observed_at = datetime.now(UTC)
        rows = await self.datasets()
        configured = set(self.dataset_ids)
        selected = [row for row in rows if str(row.get("id", row.get("dataset_id", ""))) in configured]
        if len(selected) != len(configured):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DATASET_NOT_FOUND")
        count = sum(
            int(row.get("document_count", row.get("doc_num", row.get("doc_count", 0))) or 0) for row in selected
        )
        return KnowledgeCount(status="available", count=count, source=self.source, observed_at=observed_at)

    @staticmethod
    def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", [])
        if isinstance(data, dict):
            for key in ("chunks", "records", "results", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def retrieve(self, request: KnowledgeRetrievalRequest) -> tuple[KnowledgeChunk, ...]:
        if not self.configured:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNCONFIGURED")
        doc_ids = [doc.provider_document_id for doc in request.documents]
        selected_datasets = {doc.provider_dataset_id for doc in request.documents if doc.provider_dataset_id}
        if any(doc.provider_dataset_id is None for doc in request.documents):
            if len(self.dataset_ids) != 1:
                raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DATASET_NOT_FOUND")
            selected_datasets.add(self.dataset_ids[0])
        if not selected_datasets or not selected_datasets.issubset(self.dataset_ids):
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_DATASET_NOT_FOUND")
        if self.retrieval_path == "/api/v1/datasets/search":
            # Keep the observed pre-documentation endpoint available only when
            # explicitly selected. Its request field names differ from the
            # official /api/v1/retrieval contract.
            body = {
                "dataset_ids": sorted(selected_datasets),
                "question": request.query,
                "page": 1,
                "size": 20,
                "doc_ids": doc_ids,
                "highlight": False,
            }
        else:
            body = {
                "dataset_ids": sorted(selected_datasets),
                "document_ids": doc_ids,
                "question": request.query,
                "page": 1,
                "page_size": 20,
                "top_k": 20,
                "similarity_threshold": 0.2,
                "vector_similarity_weight": 0.3,
                "highlight": False,
            }
        payload = await self._request(
            "POST",
            self.retrieval_path,
            json=body,
        )
        source_by_document = {doc.provider_document_id: doc.source for doc in request.documents}
        chunks: list[KnowledgeChunk] = []
        for index, row in enumerate(self._records(payload)):
            provider_document_id = str(row.get("doc_id", row.get("document_id", ""))).strip()
            text = str(row.get("content", row.get("content_with_weight", ""))).strip()
            chunk_id = str(row.get("chunk_id", row.get("id", index + 1))).strip()
            if not provider_document_id or not text or provider_document_id not in source_by_document:
                continue
            raw_page = row.get("page", row.get("page_num"))
            page = int(raw_page) if isinstance(raw_page, int) and raw_page >= 1 else None
            chunks.append(
                KnowledgeChunk(
                    source=source_by_document[provider_document_id],
                    provider_document_id=provider_document_id,
                    chunk_id=chunk_id,
                    text=text,
                    page=page,
                )
            )
        return validate_chunks(request, chunks)
