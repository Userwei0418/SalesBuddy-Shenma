"""Transport telemetry contract; this layer knows nothing about persistence."""

from contextvars import ContextVar
import math
from typing import Protocol

import httpx

attempt_response: ContextVar[httpx.Response | None] = ContextVar("model_attempt_response", default=None)
attempt_dispatch: ContextVar[tuple | None] = ContextVar("model_attempt_dispatch", default=None)
attempt_sent: ContextVar[bool] = ContextVar("model_attempt_sent", default=False)


async def prepare_observed(observer, invocation_id):
    if marker := getattr(observer, 'prepare_dispatch', None):
        await marker(invocation_id)


async def finish_observed(observer, invocation_id, response, error, *, network_sent, response_metadata=None):
    # Lightweight transport test observers may only implement the original
    # three-argument telemetry contract. Database observers require dispatch proof.
    kwargs = {'response_metadata': response_metadata} if response_metadata is not None else {}
    if getattr(observer, 'prepare_dispatch', None):
        kwargs['network_sent'] = network_sent
    await observer.finish(invocation_id, response, error, **kwargs)


class ModelObserver(Protocol):
    async def start(self, endpoint: str, model: str, metadata: dict, attempt: int) -> str: ...
    async def finish(
        self, invocation_id: str, response: httpx.Response | None, error: BaseException | None
    ) -> None: ...


def response_usage(response: httpx.Response | None) -> dict:
    """Only provider-reported usage. Missing or invalid numbers remain unknown."""
    try:
        payload = response.json() if response is not None else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    number = lambda value: value if type(value) is int and 0 <= value <= 2147483647 else None
    duration = payload.get("duration")
    extra = payload.get("extra_info") if isinstance(payload.get("extra_info"), dict) else {}
    return {
        "input_tokens": number(usage.get("prompt_tokens", usage.get("input_tokens"))),
        "output_tokens": number(usage.get("completion_tokens", usage.get("output_tokens"))),
        "audio_seconds": duration if type(duration) in (float, int) and 0 <= duration <= 1e100 and math.isfinite(duration) else None,
        "characters": number(extra.get("usage_characters")),
        "trace_id": (
            (response.headers.get("x-trace-id") if response is not None else None) or str(payload.get("trace_id") or "")
        )[:200],
        "http_status": response.status_code if response is not None else None,
    }
