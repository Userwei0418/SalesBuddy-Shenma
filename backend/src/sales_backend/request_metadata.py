from contextvars import ContextVar
from dataclasses import dataclass

from sales_backend.domain.agent import ActorContext


@dataclass(frozen=True)
class RequestMetadata:
    request_id: str = ""
    client_ip: str = ""
    user_agent: str = ""


request_metadata: ContextVar[RequestMetadata] = ContextVar("request_metadata", default=RequestMetadata())
current_actor: ContextVar[ActorContext | None] = ContextVar("current_actor", default=None)
