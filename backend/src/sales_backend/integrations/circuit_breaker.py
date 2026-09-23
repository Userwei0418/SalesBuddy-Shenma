from __future__ import annotations

import asyncio
import time
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Gateway circuit is open; callers should fail fast and retry later."""


class GatewayCircuitBreaker:
    """Process-wide breaker for an unstable upstream. Not a business-rule fallback."""

    def __init__(self, *, failure_threshold: int = 5, recovery_seconds: float = 25.0):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(1.0, recovery_seconds)
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def before_call(self) -> None:
        async with self._lock:
            if self._state is CircuitState.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed < self.recovery_seconds:
                    raise CircuitOpenError(
                        f"SenseAudio circuit open; retry in {self.recovery_seconds - elapsed:.0f}s"
                    )
                self._state = CircuitState.HALF_OPEN

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
