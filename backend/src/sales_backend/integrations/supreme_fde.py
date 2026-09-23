"""Supreme FDE v1 transport, based on the supplied 2026-09-11 deployment guide.

This is deliberately not a PlatformRuntime: the documented API cannot pin or
attest an execution revision, or delegate per-operation MCP authorization yet.
No retries, credentials in prompts, model-billing records or business writes.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import math
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_FRAME_CHARS = 256 * 1024
MAX_ANSWER_CHARS = 1024 * 1024
MAX_EVENTS = 10000
REFERENCE = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
TextProtocol = Literal["text_deltas", "agent_final"]


@dataclass(frozen=True)
class RunIds:
    task_id: str | None = None
    message_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class FdeTransportProgress:
    """Observed transport milestones; no headers, body, identity or credentials.

    Milliseconds are relative to entering this request's network attempt. A
    missing milestone means unobserved, not zero or a remote execution failure.
    HTTP 200 and stream completion do not attest business-result acceptance.
    """

    http_status: int | None = None
    request_started_ms: int | None = None
    headers_received_ms: int | None = None
    first_body_byte_ms: int | None = None
    first_data_event_ms: int | None = None
    first_text_ms: int | None = None
    stream_completed_ms: int | None = None


class FdeError(RuntimeError):
    """Fixed error codes only; never expose upstream bodies/URLs/credentials.

    dispatch_started means a POST may have reached the platform, even if no task
    ID was received. It is NOT a statement about writes or retry safety.
    """

    def __init__(self, code: str, *, status: int | None = None, ids: RunIds | None = None):
        super().__init__(f"FDE {code}")
        self.code, self.status, self.ids = code, status, ids or RunIds()
        self.dispatch_started = False


@dataclass(frozen=True)
class FdeConfig:
    base_url: str
    api_key: str = field(repr=False)
    # The deployment guide shows message deltas; upstream Agent New instead
    # ends agent_message deltas with a full message. Select using a verified
    # stream fixture, never silently guess on a mixed stream.
    text_protocol: TextProtocol
    timeout_seconds: float = 12.0

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            valid_url = (
                url.scheme == "https"
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and url.path.rstrip("/") == "/v1"
                and not re.search(r"[\s\\]", self.base_url)
                and (url.port is None or url.port > 0)
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("FDE requires a credential-free HTTPS /v1 base URL")
        if not re.fullmatch(r"app-[A-Za-z0-9_-]{1,240}", self.api_key):
            raise ValueError("FDE requires an application runtime key")
        if self.text_protocol not in ("text_deltas", "agent_final"):
            raise ValueError("FDE text protocol must be explicitly selected")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("FDE timeout must be positive and finite")


def _strict_json(value: str) -> Any:
    def reject_constant(_):
        raise ValueError("non-finite JSON")

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON")
        return number

    try:
        return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique, parse_float=finite_float)
    except (ValueError, RecursionError):
        raise FdeError("invalid_json") from None


@dataclass(frozen=True)
class FdeResult:
    # Neither result nor error carries a claimed/guessed execution revision.
    answer: str = field(repr=False)
    ids: RunIds
    input_tokens: int | None
    output_tokens: int | None

    def json_object(self) -> dict[str, Any]:
        # A single complete Markdown JSON fence is presentation, not extra
        # business content. Strip only that envelope; never extract a plausible
        # object from prose, multiple blocks, or an incomplete model answer.
        answer = self.answer.strip()
        fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*)\r?\n```", answer, re.DOTALL | re.IGNORECASE)
        if fence:
            answer = fence.group(1)
        try:
            result = _strict_json(answer)
        except FdeError as error:
            error.ids = self.ids
            raise
        if not isinstance(result, dict):
            raise FdeError("expected_json_object", ids=self.ids)
        return result


@dataclass(frozen=True)
class FdeStopAck:
    """Platform acknowledged stop; says nothing about business writes or receipts."""

    task_id: str
    acknowledged: bool


@dataclass(frozen=True)
class FdeHistoryMessage:
    """An observation, not a stream completion, execution version or write receipt."""

    ids: RunIds
    status: str
    answer: str = field(repr=False)
    error_present: bool
    retriever_resources_count: int

    def json_object(self) -> dict[str, Any]:
        # The deployed history API can report normal with an empty answer.
        # Even valid JSON remains a candidate for the shared business validator;
        # it must never replace a fallback already accepted for this operation.
        if self.status != "normal" or self.error_present or not self.answer.strip():
            raise FdeError("history_answer_unavailable", ids=self.ids)
        return FdeResult(self.answer, self.ids, None, None).json_object()


@dataclass(frozen=True)
class FdeHistoryPage:
    messages: tuple[FdeHistoryMessage, ...]
    has_more: bool

    def find(self, message_id: str) -> FdeHistoryMessage | None:
        """None means absent from this page only, never proof of no execution."""
        return next((message for message in self.messages if message.ids.message_id == message_id), None)


async def _frames(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Bounded UTF-8 SSE parsing, including split CRLF/UTF-8 and multiline data."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, data = "", []
    size = frame_size = 0
    first_text = True
    async for chunk in chunks:
        size += len(chunk)
        if size > MAX_STREAM_BYTES:
            raise FdeError("stream_too_large")
        try:
            text = decoder.decode(chunk)
        except UnicodeError:
            raise FdeError("invalid_utf8") from None
        if text and first_text:
            text = text.removeprefix("\ufeff")
            first_text = False
        buffer += text
        while match := re.search(r"[\r\n]", buffer):
            end = match.start()
            if buffer[end] == "\r" and end + 1 == len(buffer):
                break  # Could be the first half of CRLF in the next chunk.
            line = buffer[:end]
            width = 2 if buffer[end : end + 2] == "\r\n" else 1
            buffer = buffer[end + width :]
            frame_size += len(line) + width
            if frame_size > MAX_FRAME_CHARS:
                raise FdeError("frame_too_large")
            if not line:
                if data:
                    yield "\n".join(data)
                data, frame_size = [], 0
            elif line == "data" or line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            # Comments, event, id and retry do not become answer text; no reconnect.
        if len(buffer) + frame_size > MAX_FRAME_CHARS:
            raise FdeError("frame_too_large")
    try:
        decoder.decode(b"", final=True)
    except UnicodeError:
        raise FdeError("invalid_utf8") from None
    # A last CR is a valid line terminator. It only dispatches an already
    # accumulated frame if it is an empty line, never an incomplete data line.
    if buffer == "\r" and data:
        yield "\n".join(data)


class _Collector:
    def __init__(self, protocol: TextProtocol, conversation_id: str):
        self.protocol = protocol
        self.ids = RunIds(conversation_id=conversation_id or None)
        self.parts: list[str] = []
        self.size = self.events = 0
        self.final: str | None = None

    def consume(self, raw: str) -> FdeResult | None:
        self.events += 1
        if self.events > MAX_EVENTS:
            raise FdeError("too_many_events")
        event = _strict_json(raw)
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise FdeError("invalid_event")
        kind = event["event"]
        # Tool/thought events can use their own message identifiers. Only message
        # lifecycle frames attest this run's IDs; their content is never retained.
        if kind in ("message", "agent_message", "message_end", "error"):
            ids = {}
            for name in ("task_id", "message_id", "conversation_id"):
                previous, current = getattr(self.ids, name), event.get(name)
                if current is not None:
                    if not isinstance(current, str) or not REFERENCE.fullmatch(current):
                        raise FdeError("invalid_run_id")
                    if previous is not None and previous != current:
                        raise FdeError("run_identity_changed")
                ids[name] = current or previous
            self.ids = RunIds(**ids)
        if kind == "error":
            raise FdeError("stream_error")
        if kind in ("message", "agent_message"):
            answer = event.get("answer")
            if not isinstance(answer, str):
                raise FdeError("invalid_answer")
            if self.final is not None:
                raise FdeError("text_after_final")
            if self.protocol == "agent_final" and kind == "message":
                if len(answer) > MAX_ANSWER_CHARS:
                    raise FdeError("answer_too_large")
                self.final = answer  # The authoritative full answer, not a delta.
            else:
                self.size += len(answer)
                if self.size > MAX_ANSWER_CHARS:
                    raise FdeError("answer_too_large")
                self.parts.append(answer)
        if kind != "message_end":
            return None
        if not all((self.ids.task_id, self.ids.message_id, self.ids.conversation_id)):
            raise FdeError("missing_run_ids")
        if self.protocol == "agent_final" and self.final is None:
            raise FdeError("missing_final_answer")
        answer = self.final if self.final is not None else "".join(self.parts)
        if not answer.strip():
            raise FdeError("empty_answer")
        metadata = event.get("metadata")
        usage = metadata.get("usage") if isinstance(metadata, dict) else None
        usage = usage if isinstance(usage, dict) else {}

        def count(key):
            value = usage.get(key)
            return value if type(value) is int and value >= 0 else None

        return FdeResult(answer, self.ids, count("prompt_tokens"), count("completion_tokens"))


class FdeClient:
    """Server-side wire client; caller must authorize the run and its input first.

    Each call is one HTTP attempt. Closing/cancelling its stream does not prove
    that the platform stopped, and must not authorize fallback or another write.
    No automatic conversation cache: trusted business code owns any continuation.
    """

    def __init__(self, config: FdeConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        await self._client.aclose()

    @staticmethod
    def _check_response(response: httpx.Response, expected: str):
        if response.status_code != 200:
            raise FdeError("http_error", status=response.status_code)
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != expected:
            raise FdeError("unexpected_content_type", status=response.status_code)

    async def parameters(self) -> dict[str, Any]:
        """Read variable definitions; does not infer variables from the prompt."""
        result = await self._json_request("GET", "parameters")
        if not isinstance(result.get("user_input_form"), list):
            raise FdeError("invalid_parameters")
        return result

    async def history_page(
        self, *, conversation_id: str, user: str, limit: int = 20, first_id: str | None = None
    ) -> FdeHistoryPage:
        """One bounded, read-only page using the original backend-owned identity.

        Pagination is explicit: the caller owns first_id and message matching.
        A missing record, normal status, or nonempty answer proves neither that
        a run ended nor that no tool wrote. No thoughts, inputs or raw errors are
        retained, and historical usage is not charged as a new model attempt.
        """
        for value in (conversation_id, user):
            if not isinstance(value, str) or not REFERENCE.fullmatch(value):
                raise ValueError("FDE history requires the original conversation and user")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("FDE history limit must be between 1 and 100")
        if first_id is not None and (not isinstance(first_id, str) or not REFERENCE.fullmatch(first_id)):
            raise ValueError("FDE history first_id is invalid")
        params = {"conversation_id": conversation_id, "user": user, "limit": limit}
        if first_id is not None:
            params["first_id"] = first_id
        result = await self._json_request(
            "GET", "messages", params=params, timeout=min(2.0, self._config.timeout_seconds)
        )
        rows = result.get("data")
        if not isinstance(rows, list) or len(rows) > limit or type(result.get("has_more")) is not bool:
            raise FdeError("invalid_history")
        messages, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise FdeError("invalid_history")
            message_id = row.get("id")
            answer, status, error, resources = (
                row.get("answer"), row.get("status"), row.get("error"), row.get("retriever_resources")
            )
            if (
                not isinstance(message_id, str)
                or not REFERENCE.fullmatch(message_id)
                or message_id in seen
                or row.get("conversation_id") != conversation_id
                or not isinstance(answer, str)
                or len(answer) > MAX_ANSWER_CHARS
                or not isinstance(status, str)
                or not REFERENCE.fullmatch(status)
                or (error is not None and not isinstance(error, str))
                or not isinstance(resources, list)
            ):
                raise FdeError("invalid_history")
            seen.add(message_id)
            messages.append(
                FdeHistoryMessage(
                    RunIds(message_id=message_id, conversation_id=conversation_id),
                    status, answer, bool(error), len(resources),
                )
            )
        return FdeHistoryPage(tuple(messages), result["has_more"])

    async def stop(self, *, task_id: str, user: str) -> FdeStopAck:
        """Use the original backend-bound user and ID, not model-provided values.

        This is a single bounded request; success does not prove the task stopped
        before a write. The operation owner must separately seal tool delegation
        and verify durable receipts before accepting results or falling back.
        """
        if not isinstance(task_id, str) or not REFERENCE.fullmatch(task_id) or task_id in (".", ".."):
            raise ValueError("FDE stop requires a valid task ID")
        if not isinstance(user, str) or not REFERENCE.fullmatch(user):
            raise ValueError("FDE stop requires the backend-bound user")
        result = await self._json_request(
            "POST", f"chat-messages/{task_id}/stop", body={"user": user}, timeout=min(2.0, self._config.timeout_seconds)
        )
        if result.get("result") != "success":
            raise FdeError("stop_not_acknowledged")
        return FdeStopAck(task_id=task_id, acknowledged=True)

    async def _json_request(self, method: str, path: str, *, body=None, params=None, timeout=None) -> dict[str, Any]:
        """Fixed internal JSON endpoints only; no arbitrary URL or automatic retry."""
        try:
            async with asyncio.timeout(timeout or self._config.timeout_seconds):
                async with self._client.stream(
                    method, path, headers={"Accept": "application/json"}, json=body, params=params
                ) as response:
                    self._check_response(response, "application/json")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_FRAME_CHARS:
                            raise FdeError("json_response_too_large")
                    try:
                        text = content.decode("utf-8")
                    except UnicodeError:
                        raise FdeError("invalid_utf8") from None
                    result = _strict_json(text)
                    if not isinstance(result, dict):
                        raise FdeError("invalid_json_response")
                    return result
        except (TimeoutError, httpx.TimeoutException):
            raise FdeError("timeout") from None
        except httpx.HTTPError:
            raise FdeError("transport_error") from None

    async def chat(
        self,
        *,
        query: str,
        user: str,
        inputs: dict[str, Any],
        conversation_id: str = "",
        on_ids: Callable[[RunIds], None] | None = None,
        on_progress: Callable[[FdeTransportProgress], None] | None = None,
    ) -> FdeResult:
        # Identity and optional continuation must come from authenticated backend
        # state. Never forward user/conversation IDs supplied by the model.
        if not isinstance(user, str) or not REFERENCE.fullmatch(user):
            raise ValueError("FDE user must be a backend-owned stable identifier")
        if not isinstance(conversation_id, str) or (conversation_id and not REFERENCE.fullmatch(conversation_id)):
            raise ValueError("FDE conversation ID is invalid")
        if not isinstance(query, str) or not query.strip() or not isinstance(inputs, dict):
            raise ValueError("FDE requires a non-empty query and an inputs object")
        try:
            body = json.dumps(
                {
                    "query": query,
                    "inputs": inputs,
                    "user": user,
                    "conversation_id": conversation_id,
                    "response_mode": "streaming",
                    "auto_generate_name": False,
                },
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            raise ValueError("FDE input must be finite JSON") from None
        if len(body) > MAX_STREAM_BYTES:
            raise ValueError("FDE request is too large")
        collector = _Collector(self._config.text_protocol, conversation_id)
        started = monotonic()
        progress = FdeTransportProgress()

        def observe(**changes):
            nonlocal progress
            progress = replace(progress, **changes)
            if on_progress:
                on_progress(progress)

        def elapsed():
            return round((monotonic() - started) * 1000)

        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                observe(request_started_ms=elapsed())
                async with self._client.stream(
                    "POST",
                    "chat-messages",
                    content=body,
                    headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                ) as response:
                    observe(http_status=response.status_code, headers_received_ms=elapsed())
                    self._check_response(response, "text/event-stream")

                    async def observed_bytes():
                        async for chunk in response.aiter_bytes():
                            if chunk and progress.first_body_byte_ms is None:
                                observe(first_body_byte_ms=elapsed())
                            yield chunk

                    async for frame in _frames(observed_bytes()):
                        if progress.first_data_event_ms is None:
                            observe(first_data_event_ms=elapsed())
                        previous_ids = collector.ids
                        try:
                            result = collector.consume(frame)
                            if progress.first_text_ms is None and (collector.size or collector.final):
                                observe(first_text_ms=elapsed())
                        finally:
                            if on_ids and collector.ids != previous_ids:
                                # Synchronous trusted callback allows the owning
                                # operation to retain IDs even after cancellation.
                                on_ids(collector.ids)
                        if result is not None:
                            observe(stream_completed_ms=elapsed())
                            return result
                    raise FdeError("incomplete_stream")
        except (TimeoutError, httpx.TimeoutException):
            error = FdeError("timeout", ids=collector.ids)
        except httpx.HTTPError:
            error = FdeError("transport_error", ids=collector.ids)
        except FdeError as exc:
            error = exc
            error.ids = collector.ids
        # External CancelledError deliberately propagates unchanged; the response
        # context closes the connection. Caller must still seal the operation.
        error.dispatch_started = True
        raise error from None
