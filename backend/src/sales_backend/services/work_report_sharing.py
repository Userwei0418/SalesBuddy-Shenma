"""Read-only visibility preview using each recipient's real, restricted actor context.

A preview never grants access. Sharing retains the requested audience and the
normal document/source RLS continues to decide who can read it on every request.
"""
from contextlib import asynccontextmanager

from sales_backend.contracts.work_report import ShareVisibility
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError, canonical_hash
from sales_backend.services import work_report_access as access
from sales_backend.permission_context import current_feature

# One batch resolves active accounts using the same database resolver as login.
# No caller-supplied roles, no account enumeration outside the authorized audience.
RECIPIENTS_SQL = """SELECT u.id::text AS recipient_id,a.*
 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 LEFT JOIN LATERAL security.resolve_account_actor(w.external_workspace_id,u.account_code,NULL) a ON true
 WHERE u.workspace_id=$1::uuid AND u.id<>$2::uuid AND u.status='active' AND u.deleted_at IS NULL
 AND ($3='company' OR ($3='members' AND u.id=ANY($4::uuid[])) OR ($3='team' AND EXISTS(
   SELECT 1 FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.team_id=$5::uuid
     AND t.status='active' AND t.deleted_at IS NULL
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
     AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to)))
 ORDER BY u.id"""


@asynccontextmanager
async def report_read_context(connection, actor):
    """Nested, rollback-only identity scope. Never commits another actor's GUCs."""
    savepoint = connection.transaction()
    await savepoint.start()
    token = current_feature.set('work_report.read')
    try:
        await set_request_context(connection, actor)
        yield
    finally:
        try:
            await savepoint.rollback()
        finally:
            current_feature.reset(token)


async def recipient_access(connection, actor, document, check_sources):
    async with report_read_context(connection, actor):
        version = await access.permission_version(connection, actor)
        allowed = await connection.fetchval("""SELECT security.authorization_has('work_report.read')
          AND security.work_report_source_access($1::jsonb,$2::jsonb)""",
          document['source_manifest'], document['selected_sources'])
        if allowed:
            try:
                await check_sources(connection, actor, document)
            except WorkReportError as error:
                if error.status not in (403, 404):
                    raise
                allowed = False
        return bool(allowed), version


async def preview_visibility(connection, author, document, audience, check_sources):
    await access.validate_audience(connection, author, audience)
    recipients = await connection.fetch(RECIPIENTS_SQL, author.workspace_id, author.user_id, audience.kind,
        [str(x) for x in audience.member_ids], str(audience.team_id) if audience.team_id else None)
    results = []
    for row in recipients:
        allowed, version = False, None
        if row['user_id'] is not None:
            if row['workspace_id'] != author.workspace_id or row['user_id'] != row['recipient_id']:
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '共享对象身份已变化，请重新预检', status=403)
            recipient = ActorContext(workspace_id=row['workspace_id'], user_id=row['user_id'],
                role=row['role_code'], data_scope=row['data_scope_code'], team_ids=tuple(row['team_ids'] or ()))
            allowed, version = await recipient_access(connection, recipient, document, check_sources)
        results.append(dict(id=row['recipient_id'], readable=allowed, permission_version=version))
    readable = sum(row['readable'] for row in results)
    unreadable = len(results) - readable
    binding = canonical_hash(dict(document_id=str(document['id']), version_no=document['version_no'],
        revision_id=str(document['latest_revision_id']), author=author.user_id,
        audience=audience.model_dump(mode='json'), source_manifest=document['source_manifest'],
        context_scope=document['context']['scope'], recipients=results))
    return ShareVisibility(version_no=document['version_no'], can_share=readable > 0,
        recipient_count=len(results), readable_count=readable, unreadable_count=unreadable,
        reason='NO_RECIPIENT' if not readable else 'PARTIAL_SOURCE_ACCESS' if unreadable else None,
        preview_hash=binding, confirmation_required=bool(readable and unreadable))
