"""Safe AI-result provenance for an already authorized visible revision.

Recipients cannot read private runs or older revisions. This deliberately narrow
projection returns only a fixed diagnostic, never those records or their content.
"""
from sales_backend.domain.agent import ActorContext
from sales_backend.services.work_report_generation.service import generation_issue
from sales_backend.services.work_report_sharing import report_read_context

LINEAGE_SQL = """SELECT run.result->>'status' AS result_status,
 run.result #>> '{trace,generation_issue,code}' AS issue_code
 FROM agent.work_report_revision r JOIN agent.work_report_run run
 ON run.id=r.run_id AND run.workspace_id=r.workspace_id
 WHERE r.workspace_id=$1::uuid AND r.document_id=$2::uuid AND r.revision_no<=$3
 AND r.origin<>'manual' AND run.owner_user_ref_id=$4::uuid
 AND run.document_id=r.document_id AND run.snapshot_id=$5::uuid
 AND run.status='succeeded' AND run.revision_id=r.id
 ORDER BY r.revision_no DESC LIMIT 1"""


def public_issue(code):
    reasons = {
        'AI_SOURCE_REFERENCES_MISSING': {'contract_code': 'work_report.missing_source_refs'},
        'AI_OUTPUT_INVALID': {'error_code': 'InvalidAgentResult'},
        'AI_PROVIDER_UNAVAILABLE': {'error_code': 'ProviderUnavailable'},
        'AI_GENERATION_TIMEOUT': {'error_code': 'TimeoutError'},
    }
    return generation_issue(reasons.get(code))


async def document_issue(connection, actor, document):
    # Caller has already passed _document_sources, including the exact shared
    # revision and full frozen subject/source permissions. No request-supplied IDs.
    if document['context'].get('meeting_facts') is not None:
        return None
    async def read():
        row = await connection.fetchrow(LINEAGE_SQL, actor.workspace_id, str(document['id']),
            document['visible_revision_no'], str(document['owner_user_ref_id']), str(document['snapshot_id']))
        if row and row['result_status'] == 'basic_summary':
            return public_issue(row['issue_code'])
        return None
    if str(document['owner_user_ref_id']) == actor.user_id:
        return await read()
    owner = await connection.fetchrow("""SELECT a.* FROM platform.user_ref u
      JOIN platform.workspace w ON w.id=u.workspace_id
      CROSS JOIN LATERAL security.resolve_account_actor(w.external_workspace_id,u.account_code,NULL) a
      WHERE u.workspace_id=$1::uuid AND u.id=$2::uuid AND a.workspace_id=$1::uuid::text AND a.user_id=$2::uuid::text""",
      actor.workspace_id, str(document['owner_user_ref_id']))
    if owner is None:
        return None  # Inactive author doesn't block an otherwise readable report.
    author = ActorContext(workspace_id=owner['workspace_id'], user_id=owner['user_id'],
        role=owner['role_code'], data_scope=owner['data_scope_code'], team_ids=tuple(owner['team_ids'] or ()))
    async with report_read_context(connection, author):
        return await read()
