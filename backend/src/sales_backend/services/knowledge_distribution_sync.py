"""Transaction-local publication outbox hook. Never performs provider I/O."""
from uuid import UUID
from sales_backend import knowledge_availability


async def enqueue_knowledge_distribution_event(connection, *, workspace_id: UUID,
        material_id: UUID, publication_id: UUID, version_id: UUID, event_kind: str,
        revision: int, actor_id: UUID):
    if not knowledge_availability.KNOWLEDGE_AVAILABLE:
        return None
    if event_kind not in {"published", "version_published", "metadata_updated", "withdrawn"}:
        raise ValueError("KNOWLEDGE_SYNC_EVENT_INVALID")
    # SQL rechecks caller identity and the actual committed publication. It derives
    # metadata itself; no source URL, share token, questionnaire or answer is accepted.
    return await connection.fetchval(
        "SELECT security.knowledge_distribution_enqueue($1,$2,$3,$4,$5,$6,$7)",
        workspace_id, material_id, publication_id, version_id, event_kind, revision, actor_id)
