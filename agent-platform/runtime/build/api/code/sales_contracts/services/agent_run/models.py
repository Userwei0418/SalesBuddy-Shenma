from __future__ import annotations

from dataclasses import dataclass

from sales_contracts.domain.agent import ActorContext

@dataclass(frozen=True, slots=True)
class RunInput:
    run_id: str
    conversation_id: str
    text: str
    mode: str
    customer_id: str | None
    actor: ActorContext
    permission_version: str | None = None
    opportunity_id: str | None = None
    # Server-owned surfaces are never accepted by the public conversation API.
    surface: str | None = None
    profile_days: int | None = None
    facts_fingerprint: str | None = None
    visit_request: dict | None = None

    @property
    def capability(self):
        if self.mode == "visit_entry" and (self.visit_request or {}).get("stage") == "quality":
            return "visit_quality"
        return "chatbi" if self.mode == "customer_chatbi" else self.mode
