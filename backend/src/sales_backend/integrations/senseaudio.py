from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import httpx

from sales_backend.config import Settings
from sales_backend.domain.agent import ChatMessage
from sales_backend.integrations.circuit_breaker import CircuitOpenError, GatewayCircuitBreaker
from sales_backend.integrations.model_observer import ModelObserver, attempt_response

MAX_ASR_FILE_BYTES = 10 * 1024 * 1024
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
BUSY_MARKERS = ("繁忙", "稍后", "overloaded", "try again", "too many requests", "temporar")
_SHARED_BREAKERS: dict[str, GatewayCircuitBreaker] = {}
AUDIO_MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
}


def normalize_audio_upload(filename: str, mime_type: str | None, content: bytes) -> tuple[str, str]:
    """Normalize generic mini-program uploads before forwarding multipart data."""
    safe_name = PurePath(filename or "recording").name or "recording"
    suffix = PurePath(safe_name).suffix.lower()
    detected_mime = AUDIO_MIME_BY_SUFFIX.get(suffix)
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        detected_mime = "audio/wav"
        suffix = ".wav"
    elif content.startswith(b"ID3") or (len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0):
        detected_mime = "audio/mpeg"
        suffix = ".mp3"
    elif content.startswith(b"OggS"):
        detected_mime = "audio/ogg"
        suffix = ".ogg"
    elif content.startswith(b"fLaC"):
        detected_mime = "audio/flac"
        suffix = ".flac"
    elif len(content) >= 12 and content[4:8] == b"ftyp":
        detected_mime = "audio/mp4"
        suffix = ".m4a"

    declared_mime = (mime_type or "").lower().split(";", 1)[0].strip()
    normalized_mime = detected_mime or (declared_mime if declared_mime.startswith("audio/") else "audio/mpeg")
    if PurePath(safe_name).suffix.lower() not in AUDIO_MIME_BY_SUFFIX:
        safe_name = f"{safe_name}{suffix or '.mp3'}"
    return safe_name, normalized_mime


def shared_breaker(settings: Settings) -> GatewayCircuitBreaker:
    key = settings.senseaudio_base_url + json.dumps(settings.model_api_metadata, sort_keys=True)
    breaker = _SHARED_BREAKERS.get(key)
    if breaker is None:
        breaker = GatewayCircuitBreaker(
            failure_threshold=settings.circuit_failure_threshold,
            recovery_seconds=settings.circuit_recovery_seconds,
        )
        _SHARED_BREAKERS[key] = breaker
    return breaker


def is_busy_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered or marker in text for marker in BUSY_MARKERS)


class SenseAudioError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    duration_seconds: float | None
    trace_id: str | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    audio_bytes: bytes
    format: str
    sample_rate: int | None
    trace_id: str | None
    usage_characters: int | None


class SenseAudioClient:
    """服务端模型网关；Authorization 永不暴露给调用方或异常正文。"""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        breaker: GatewayCircuitBreaker | None = None,
        observer: ModelObserver | None = None,
    ):
        if settings.model_api_metadata.get("connection_mode") != "disabled":
            settings.require_model_gateway()
        self._settings = settings
        self._observer = observer
        self._owns_client = client is None
        self._breaker = breaker if breaker is not None else shared_breaker(settings)
        from sales_backend.integrations.public_model_transport import PublicModelTransport
        transport_options = ({"transport": PublicModelTransport(), "trust_env": False, "follow_redirects": False}
                             if settings.model_api_endpoint and client is None else {})
        self._client = client or httpx.AsyncClient(
            base_url=settings.senseaudio_base_url,
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers={"Authorization": f"Bearer {settings.senseaudio_api_key}"},
            **transport_options,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        language: str = "zh",
        hotwords: tuple[str, ...] = (),
    ) -> Transcription:
        self._settings.require_model_gateway()
        if not content:
            raise ValueError("audio file is empty")
        if len(content) > MAX_ASR_FILE_BYTES:
            raise ValueError("audio file exceeds SenseAudio ASR Lite 10 MB limit")

        filename, mime_type = normalize_audio_upload(filename, mime_type, content)

        data = {
            "model": self._settings.asr_model,
            "language": language,
            "response_format": "json",
        }
        if hotwords:
            data["hotwords"] = ",".join(hotwords)
        response = await self._request(
            "POST",
            "/v1/audio/transcriptions",
            data=data,
            files={"file": (filename, content, mime_type)},
        )
        payload = self._json(response)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SenseAudioError("ASR response did not contain non-empty text")
        duration = payload.get("duration")
        return Transcription(
            text=text.strip(),
            duration_seconds=float(duration) if isinstance(duration, int | float) else None,
            trace_id=response.headers.get("x-trace-id") or payload.get("trace_id"),
            raw=payload,
        )

    async def chat_json(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float = 0.1,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._settings.require_model_gateway()
        body: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [message.model_dump() for message in messages],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools

        async def once() -> dict[str, Any]:
            response = await self._send("POST", "/v1/chat/completions", json=body)
            if response.status_code >= 400:
                raise SenseAudioError(
                    self._safe_error_message(response),
                    status_code=response.status_code,
                    retryable=response.status_code in RETRYABLE_STATUS_CODES,
                )
            try:
                payload = self._json(response)
            except SenseAudioError as exc:
                # A truncated/malformed successful HTTP envelope is as transient
                # as malformed LLM content. Retry only this inference request,
                # within the existing attempt limit and caller timeout budget.
                raise SenseAudioError(
                    "LLM response envelope was not valid JSON", status_code=response.status_code, retryable=True,
                ) from exc
            self._raise_if_gateway_busy(payload, response.status_code)
            try:
                choice = payload["choices"][0]
                content = choice["message"]["content"]
                if isinstance(content, str) and is_busy_text(content):
                    raise SenseAudioError(content.strip()[:300], status_code=503, retryable=True)
                parsed = json.loads(content) if isinstance(content, str) else content
            except SenseAudioError:
                raise
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                finish_reason = None
                try:
                    finish_reason = payload["choices"][0].get("finish_reason")
                except (KeyError, IndexError, TypeError):
                    pass
                suffix = f" (finish_reason={finish_reason})" if finish_reason else ""
                raise SenseAudioError(
                    f"LLM response was not valid JSON content{suffix}",
                    retryable=True,
                ) from exc
            if not isinstance(parsed, dict):
                raise SenseAudioError("LLM JSON content must be an object", retryable=True)
            return parsed

        return await self._run_with_retry(
            once,
            endpoint="/v1/chat/completions",
            model=self._settings.llm_model,
            metadata={"message_count": len(messages)},
        )

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        audio_format: str = "mp3",
        sample_rate: int = 32_000,
    ) -> SynthesizedAudio:
        self._settings.require_model_gateway()
        if not text.strip():
            raise ValueError("TTS text is empty")
        if len(text) > 10_000:
            raise ValueError("TTS text exceeds 10000 characters")
        body = {
            "model": self._settings.tts_model,
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": voice_id, "speed": 1, "vol": 1, "pitch": 0},
            "audio_setting": {
                "format": audio_format,
                "sample_rate": sample_rate,
                "bitrate": 128_000,
                "channel": 1,
            },
        }
        response = await self._request("POST", "/v1/t2a_v2", json=body)
        payload = self._json(response)
        try:
            encoded = payload["data"]["audio"]
            if not isinstance(encoded, str) or not encoded:
                raise ValueError("audio payload is empty")
            # SenseAudio TTS 1.5 currently returns hexadecimal audio bytes,
            # while older/compatible gateways may return base64. Support both
            # wire formats so a gateway rollout does not break playback.
            try:
                audio_bytes = bytes.fromhex(encoded)
            except ValueError:
                audio_bytes = base64.b64decode(encoded, validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise SenseAudioError("TTS response did not contain valid encoded audio") from exc
        info = payload.get("extra_info") or {}
        return SynthesizedAudio(
            audio_bytes=audio_bytes,
            format=str(info.get("audio_format") or audio_format),
            sample_rate=info.get("audio_sample_rate"),
            trace_id=payload.get("trace_id") or response.headers.get("x-trace-id"),
            usage_characters=info.get("usage_characters"),
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def once() -> httpx.Response:
            response = await self._send(method, path, **kwargs)
            if response.status_code < 400:
                payload = None
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    self._raise_if_gateway_busy(payload, response.status_code)
                return response
            retryable = response.status_code in RETRYABLE_STATUS_CODES or is_busy_text(
                self._safe_error_message(response)
            )
            raise SenseAudioError(
                self._safe_error_message(response),
                status_code=response.status_code,
                retryable=retryable,
            )

        body = kwargs.get("json") or kwargs.get("data") or {}
        metadata = {"input_characters": len(body.get("text", ""))} if "text" in body else {}
        if kwargs.get("files"):
            metadata["input_bytes"] = sum(len(f[1]) for f in kwargs["files"].values())
        return await self._run_with_retry(once, endpoint=path, model=body.get("model", "unknown"), metadata=metadata)

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._settings.model_api_endpoint:
            from sales_backend.domain.model_api import PURPOSES
            purpose = self._settings.model_api_metadata["connection_purpose"]
            if PURPOSES[purpose]["path"] != path:
                raise SenseAudioError("接口用途与调用类型不一致")
            path = self._settings.model_api_endpoint
        response = await self._client.request(method, path, **kwargs)
        if self._settings.model_api_endpoint and 300 <= response.status_code < 400:
            raise SenseAudioError("模型接口不允许重定向", status_code=response.status_code)
        attempt_response.set(response)
        return response

    async def _run_with_retry(self, operation, *, endpoint="unknown", model="unknown", metadata=None):
        metadata = {**(metadata or {}), **self._settings.model_api_metadata}
        attempts = self._settings.max_retries + 1
        for attempt in range(attempts):
            try:
                await self._breaker.before_call()
            except CircuitOpenError as exc:
                raise SenseAudioError(str(exc), status_code=503, retryable=True) from exc
            invocation_id = (
                await self._observer.start(endpoint, model, metadata or {}, attempt + 1) if self._observer else None
            )
            token = attempt_response.set(None)
            error = None
            try:
                result = await operation()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                error = SenseAudioError("SenseAudio request failed", retryable=True)
                error.__cause__ = exc
            except BaseException as exc:
                error = exc
            finally:
                if self._settings.model_api_endpoint and isinstance(error, SenseAudioError):
                    # Untrusted providers may echo headers in an error. Never log their raw text.
                    error = SenseAudioError("模型接口请求失败，请检查服务状态和返回格式",
                                            status_code=error.status_code, retryable=error.retryable)
                try:
                    if invocation_id:
                        await self._observer.finish(invocation_id, attempt_response.get(), error)
                finally:
                    attempt_response.reset(token)
            if error is None:
                await self._breaker.record_success()
                return result
            if isinstance(error, SenseAudioError) and error.retryable:
                await self._breaker.record_failure()
                if attempt + 1 < attempts:
                    await self._backoff(attempt)
                    continue
            raise error
        raise SenseAudioError("SenseAudio request failed after retries", retryable=True)

    async def _backoff(self, attempt: int) -> None:
        delay = min(4.0, 0.35 * (2**attempt)) + (attempt % 3) * 0.05
        await asyncio.sleep(delay)

    def _raise_if_gateway_busy(self, payload: dict[str, Any], status_code: int | None) -> None:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        candidates = [
            str(payload.get("message") or ""),
            str(error.get("message") or ""),
            str(payload.get("code") or ""),
        ]
        if not any(is_busy_text(item) for item in candidates):
            return
        raise SenseAudioError(
            self._safe_error_from_payload(payload),
            status_code=status_code or 503,
            retryable=True,
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SenseAudioError("SenseAudio returned non-JSON response") from exc
        if not isinstance(payload, dict):
            raise SenseAudioError("SenseAudio returned an unexpected response shape")
        return payload

    @staticmethod
    def _safe_error_from_payload(payload: dict[str, Any]) -> str:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = payload.get("code") or error.get("code")
        message = payload.get("message") or error.get("message")
        safe_code = str(code)[:100] if code is not None else "upstream_error"
        safe_message = str(message)[:300] if message is not None else "request failed"
        return f"SenseAudio {safe_code}: {safe_message}"

    @staticmethod
    def _safe_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"SenseAudio request failed with HTTP {response.status_code}"
        if isinstance(payload, dict):
            return SenseAudioClient._safe_error_from_payload(payload)
        return f"SenseAudio request failed with HTTP {response.status_code}"
