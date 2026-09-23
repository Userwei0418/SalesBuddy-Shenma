"""Select an inference provider before the existing business persistence boundary.

Callbacks must use the trusted operation ID; payloads never attest to write state.
This module does not guess the FDE runtime API and does not persist business data.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Any

from sales_backend.services.agent_platform.audit import result_shape

Payload = dict[str, Any]
Invoke = Callable[[], Awaitable[Payload]]
Validate = Callable[[Payload], Payload]


class ProviderUnavailable(RuntimeError):
    """A provider transport/service failure eligible for safe fallback."""


class ControlledPlatformBlock(ProviderUnavailable):
    """Operator-scoped test fault before dispatch, never a claimed remote HTTP error."""


class InvalidAgentResult(ValueError):
    """Result failed the common business contract."""


class AgentUnavailable(RuntimeError):
    """Neither route produced a valid result within the request budget."""


class ReconciliationRequired(RuntimeError):
    """A write was dispatched; inspect its durable receipt before continuing."""


class WriteState(StrEnum):
    NONE = "none"
    DISPATCHED = "dispatched"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RoutingPolicy:
    platform_enabled: bool = False
    total_seconds: float = 45.0
    platform_seconds: float = 12.0
    receipt_seconds: float = 2.0

    def __post_init__(self):
        values = (self.total_seconds, self.platform_seconds, self.receipt_seconds)
        if any(not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError("routing timeouts must be positive and finite")
        if self.platform_seconds + self.receipt_seconds >= self.total_seconds:
            raise ValueError("reserve time for the existing provider after platform and receipt checks")


@dataclass(frozen=True)
class RoutedResult:
    """Only payload goes to the existing UI. Diagnostics stay in backend traces."""

    payload: Payload
    provider: str
    fallback_reason: str | None = None


class AgentRouter:
    async def evaluate(
        self,
        *,
        policy: RoutingPolicy,
        platform: Invoke,
        direct: Invoke,
        validate: Validate,
        write_state: Callable[[], Awaitable[WriteState]],
        observe: Callable[..., None] | None = None,
        fallback_provider: str = "senseaudio",
    ) -> RoutedResult:
        deadline = monotonic() + policy.total_seconds
        failure = None
        if policy.platform_enabled:
            try:
                payload = await self._invoke(platform, validate, min(policy.platform_seconds, deadline - monotonic()),
                                             observe=observe, provider="agent_platform")
                return RoutedResult(payload=payload, provider="agent_platform")
            except (ProviderUnavailable, InvalidAgentResult, TimeoutError) as exc:
                failure = type(exc).__name__
                if observe:
                    observe("fallback_requested", "agent_platform", reason=failure)
                # This must query a backend-owned durable receipt, not model output.
                # Sealing/cancelling the platform operation against late tool calls is
                # the write gateway's responsibility before it can report NONE.
                try:
                    async with asyncio.timeout(min(policy.receipt_seconds, max(0, deadline - monotonic()))):
                        state = await write_state()
                except Exception as receipt_error:
                    raise ReconciliationRequired("write receipt unavailable; fallback withheld") from receipt_error
                if state is not WriteState.NONE:
                    raise ReconciliationRequired("write may have occurred; fallback withheld") from exc
        try:
            payload = await self._invoke(direct, validate, deadline - monotonic(),
                                         observe=observe, provider=fallback_provider)
        except (ProviderUnavailable, InvalidAgentResult, TimeoutError) as exc:
            raise AgentUnavailable("AI 暂时不可用，请稍后重试") from exc
        return RoutedResult(payload=payload, provider=fallback_provider, fallback_reason=failure)

    @staticmethod
    async def _invoke(invoke: Invoke, validate: Validate, seconds: float, *, observe=None, provider=None) -> Payload:
        if seconds <= 0:
            raise TimeoutError("request budget exhausted")

        active = True

        async def run():
            payload = await invoke()
            if not active:
                raise asyncio.CancelledError("late provider output discarded")
            if observe:
                observe("output_received", provider, shape=result_shape(payload))
            if not isinstance(payload, dict):
                raise InvalidAgentResult("expected a structured business result")
            try:
                validated = validate(payload)
                if observe:
                    observe("contract_accepted", provider)
                return validated
            except ValueError as exc:
                raise InvalidAgentResult("result failed the business contract") from exc

        def consume_late(task):
            if not task.cancelled():
                task.exception()

        task = asyncio.create_task(run())
        if observe:
            observe("route_started", provider, budget_ms=round(seconds * 1000))
        try:
            done, _ = await asyncio.wait({task}, timeout=seconds)
            if not done:
                raise TimeoutError("provider exceeded its inference budget")
            return await task
        except BaseException as exc:
            if observe:
                from sales_backend.domain.model_contract import contract_failure_details

                observe("route_failed", provider, error_code=type(exc).__name__, **contract_failure_details(exc))
            raise
        finally:
            active = False
            if not task.done():
                # Waiting for a provider that suppresses cancellation would consume
                # the fallback budget. Its late result is never persisted; the
                # caller seals tool access before starting the alternate provider.
                task.cancel()
                task.add_done_callback(consume_late)
