"""Read personal-risk source facts and recheck them before asynchronous writes."""

from sales_backend.domain.advice import fingerprint
from sales_backend.domain.business_time import localize_business_times
from sales_backend.integrations.senseaudio import SenseAudioError


class PersonalRiskSourceChanged(SenseAudioError):
    def __init__(self):
        super().__init__("风险分析来源已变更或不可访问，请按当前记录重新分析", retryable=False)


async def personal_risk_sources(connection, actor, *, source_ids=None):
    rows = await connection.fetch(
        """
        SELECT v.id::text AS source_visit_id, v.customer_id::text,
               v.opportunity_id::text, c.name AS customer_name,
               o.name AS opportunity_name, o.status AS opportunity_status,
               o.amount AS opportunity_amount,
               v.interaction_at, v.interaction_mode_code,
               v.visit_location, v.duration_minutes, v.expectation_code,
               v.follow_up_record, v.next_action, c.level_code
          FROM activity.visit v
          LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
          LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
         WHERE v.workspace_id=$1::uuid AND v.recorder_user_ref_id=$2::uuid
           AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
           AND v.interaction_at >= clock_timestamp() - interval '180 days'
           AND ($3::uuid[] IS NULL OR v.id=ANY($3::uuid[]))
         ORDER BY v.interaction_at DESC,v.created_at DESC,v.id
         LIMIT 12
        """, actor.workspace_id, actor.user_id, source_ids,
    )
    return [dict(row) for row in rows]


async def recheck_personal_risk_sources(connection, actor, visits):
    if not visits:
        return
    expected = {row.get("source_visit_id"): row for row in visits}
    if None in expected or len(expected) != len(visits) or len(visits) > 12:
        raise PersonalRiskSourceChanged()
    # Use exactly the feature scope that loaded model facts. RLS remains the
    # authority; an unrelated read permission must not widen this second read.
    await connection.execute("SELECT set_config('app.authorized_feature','agent.personal_risks',true)")
    # Hold every actual source row until the result commits. Fixed table names
    # and bounded IDs only; no caller or model content becomes SQL syntax.
    for table, field in (("crm.customer", "customer_id"), ("crm.opportunity", "opportunity_id"),
                         ("activity.visit", "source_visit_id")):
        ids = sorted({row[field] for row in visits if row.get(field)})
        if ids:
            await connection.fetch(
                f"SELECT id FROM {table} WHERE workspace_id=$1::uuid "  # noqa: S608
                "AND id=ANY($2::uuid[]) ORDER BY id FOR SHARE", actor.workspace_id, ids,
            )
    current = await personal_risk_sources(connection, actor, source_ids=list(expected))
    current = {row["source_visit_id"]: row for row in current}
    # Shanghai/UTC presentation of the same instant must compare identically.
    if fingerprint(localize_business_times(current)) != fingerprint(localize_business_times(expected)):
        raise PersonalRiskSourceChanged()
