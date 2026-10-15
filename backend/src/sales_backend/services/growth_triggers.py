"""Optional competency generation cannot revoke a valid visit archive."""
import logging

from sales_backend.repositories.competency_lifecycle import (
    CompetencySnapshotInvalid, can_review, request_review,
)

logger = logging.getLogger(__name__)


async def enqueue_visit_review(connection, actor, visit, *, trigger_type):
    if not await can_review(connection, actor):
        return None
    try:
        # Roll back only the optional queue capture on a known admission error.
        async with connection.transaction():
            return await request_review(connection, actor, trigger_type=trigger_type,
                                        trigger_id=str(visit['id']), trigger_version=visit['version_no'])
    except (PermissionError, CompetencySnapshotInvalid, LookupError) as exc:
        logger.info('competency generation skipped reason=%s', type(exc).__name__)
        return None


async def visit_model_content(connection, actor, visit_id):
    """A supplement triggers only when selected business content actually changes."""
    if actor.role.value not in {"sales", "supervisor"}:
        return None
    from sales_backend.repositories.competency_lifecycle import VISIT_PROJECTION, VISIT_JOINS, canonical_hash
    row = await connection.fetchrow("SELECT " + VISIT_PROJECTION + " FROM activity.visit v " + VISIT_JOINS +
        " WHERE v.id=$1::uuid AND v.workspace_id=$2::uuid AND v.recorder_user_ref_id=$3::uuid"
        " AND v.deleted_at IS NULL AND security.authorization_visit('visit.read',v.id)",
        str(visit_id), actor.workspace_id, actor.user_id)
    return canonical_hash({key: value for key, value in dict(row).items() if key != "version_no"}) if row else None
