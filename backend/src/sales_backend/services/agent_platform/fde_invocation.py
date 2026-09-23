"""FDE wire errors -> existing inference routing, without granting runtime access.

This callable does not satisfy PlatformRuntime: its owner still needs a trusted
release/version binding and a sealed tool-operation lifetime. It is also useful
for isolated pilot checks that must never persist business output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sales_backend.domain.model_contract import ModelContractError
from sales_backend.integrations.supreme_fde import (
    FdeClient,
    FdeError,
    FdeHistoryPage,
    FdeResult,
    FdeStopAck,
    FdeTransportProgress,
    RunIds,
)
from sales_backend.services.agent_platform.routing import InvalidAgentResult, ProviderUnavailable


@dataclass(frozen=True)
class FdeJsonInvocation:
    client: FdeClient = field(repr=False)
    query: str = field(repr=False)
    # Caller authenticates the actor and prepares the variables from /parameters.
    user: str = field(repr=False)
    inputs: dict = field(default_factory=dict, repr=False)
    ids: RunIds = field(default_factory=RunIds, init=False)
    result: FdeResult | None = field(default=None, init=False, repr=False)
    error_code: str | None = field(default=None, init=False)
    http_status: int | None = field(default=None, init=False)
    transport: FdeTransportProgress = field(default_factory=FdeTransportProgress, init=False)
    _started: bool = field(default=False, init=False, repr=False)

    def _record_ids(self, ids: RunIds):
        object.__setattr__(self, "ids", ids)

    def _record_progress(self, progress: FdeTransportProgress):
        object.__setattr__(self, "transport", progress)
        object.__setattr__(self, "http_status", progress.http_status)

    async def __call__(self) -> dict:
        if self._started:
            raise RuntimeError("FDE invocation cannot be replayed")
        object.__setattr__(self, "_started", True)
        try:
            # Each inference gets a fresh conversation. Existing browser/user
            # history must not carry stale permissions into a new operation.
            result = await self.client.chat(
                query=self.query, user=self.user, inputs=self.inputs, on_ids=self._record_ids,
                on_progress=self._record_progress,
            )
            object.__setattr__(self, "result", result)
        except FdeError as error:
            object.__setattr__(self, "error_code", error.code)
            if error.status is not None:
                object.__setattr__(self, "http_status", error.status)
            # AgentRouter then seals and checks receipts before using direct AI.
            # This translation alone is never a no-write attestation.
            raise ProviderUnavailable(f"FDE {error.code}") from None
        try:
            return self.result.json_object()
        except FdeError as error:
            code = "fde_expected_json_object" if error.code == "expected_json_object" else "fde_invalid_json"
            object.__setattr__(self, "error_code", code)
            # Stable, content-free diagnostics survive routing wrappers and
            # distinguish malformed output from a valid low business score.
            raise InvalidAgentResult("FDE result is not an unambiguous JSON object") from ModelContractError(code)

    async def stop(self) -> FdeStopAck | None:
        if self.ids.task_id is None:
            return None  # Unknown task is not equivalent to stopped/no writes.
        return await self.client.stop(task_id=self.ids.task_id, user=self.user)

    async def history(self, *, first_id: str | None = None) -> FdeHistoryPage | None:
        """Inspect with the original identity, without replaying or accepting output.

        None means no conversation ID was captured. The caller must match the
        captured message ID in the returned page; no match is not a no-write
        receipt. This method never changes result/error state or routing.
        """
        if self.ids.conversation_id is None:
            return None
        return await self.client.history_page(
            conversation_id=self.ids.conversation_id, user=self.user, first_id=first_id
        )
