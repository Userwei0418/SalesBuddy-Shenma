"""Pure delivery decisions. Persist returned plans before sending or retrying."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sales_backend.domain.feishu_sync.config import ObjectKind, SyncConfig


@dataclass(frozen=True)
class SourceEvent:
    event_id: UUID
    workspace_id: UUID
    object_kind: ObjectKind
    object_id: UUID
    first_formal_create: bool
    historical: bool = False
    production: bool = True
    department_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class DeliveryPlan:
    connection_id: UUID
    config_revision: int
    event_id: UUID
    chat_id: str
    dedupe_key: str


def notification_plans(config: SyncConfig, event: SourceEvent) -> tuple[DeliveryPlan, ...]:
    if event.workspace_id != config.workspace_id:
        raise PermissionError("同步事件与连接不属于同一公司")
    notification = config.notification
    mapping = config.mappings.get(event.object_kind)
    if not (config.enabled and mapping and mapping.enabled and notification.enabled
            and event.first_formal_create and not event.historical and event.production
            and event.object_kind in notification.on_create):
        return ()
    chats = set()
    if notification.routing_mode == "department_routes":
        routes = {r.department_id: r.chat_ids for r in notification.routes}
        for department in event.department_ids:
            chats.update(routes.get(department, (notification.default_chat_id,)))
    if not chats:
        chats.add(notification.default_chat_id)
    plans = []
    for chat in sorted(chat for chat in chats if chat):
        # Config revisions MUST NOT change the identity of an already planned delivery.
        identity = f"{config.workspace_id}:{config.connection_id}:{event.object_kind}:{event.object_id}:{chat}"
        key = sha256(identity.encode()).hexdigest()
        plans.append(DeliveryPlan(config.connection_id, config.revision, event.event_id, chat, key))
    return tuple(plans)
