"""Self-scoped portrait evidence and reuse of native asynchronous Agent runs."""

from sales_backend.domain.fde_coaching import TASK_LIMIT, VISIT_LIMIT, coaching_inputs
from sales_backend.domain.fde_profile import PORTRAIT_PROMPT_VERSION, portrait, window


async def profile_facts(connection, actor, days, *, member_ids=None, team_id=None):
    ids = member_ids if member_ids is not None else [actor.user_id]
    now = await connection.fetchval("SELECT clock_timestamp()")
    start, _ = window(now, days)
    permission_version = await connection.fetchval("SELECT security.authorization_snapshot()->>'permission_version'")
    runtime = await connection.fetchrow(
        """SELECT enabled,llm_model,updated_at::text,
          md5(COALESCE(prompt_overrides->>'operating_report','')) AS prompt_digest
        FROM config.agent_runtime_config WHERE workspace_id=$1::uuid""",
        actor.workspace_id,
    )
    policy = await connection.fetchrow(
        "SELECT id::text,version_no,revision_no FROM config.rule_set "
        "WHERE id=security.active_company_rule('agent_execution.operating_report')",
    )
    business_policy = await connection.fetchrow(
        "SELECT id::text,version_no,revision_no FROM config.rule_set "
        "WHERE id=security.active_company_rule('agent_business.operating_report')",
    )
    configuration = {
        "portrait_prompt_version": PORTRAIT_PROMPT_VERSION,
        "runtime": dict(runtime) if runtime else None,
        "execution_policy": dict(policy) if policy else None,
        "business_policy": dict(business_policy) if business_policy else None,
    }
    visits = [
        dict(r)
        for r in await connection.fetch(
            """SELECT h.visit_id::text AS id,h.customer_id::text,h.opportunity_id::text,h.interaction_at,
          v.id IS NOT NULL AS detail_visible,v.version_no,v.follow_up_score,
          CASE WHEN v.id IS NULL THEN false ELSE NULLIF(btrim(v.next_action),'') IS NOT NULL END AS has_next_action
        FROM security.fde_recorded_visit_history($2,$3,NULL,NULL,NULL) h LEFT JOIN activity.visit v ON v.id=h.visit_id
        WHERE (($4::uuid IS NULL AND h.user_ref_id=ANY($1::uuid[])) OR h.team_id_at_event=$4::uuid)
          AND h.interaction_at >= $2 AND h.interaction_at <= $3
        ORDER BY h.visit_id""",
            ids,
            start,
            now,
            str(team_id) if team_id else None,
        )
    ]
    projects = [
        dict(r)
        for r in await connection.fetch(
            """SELECT o.id::text,o.customer_id::text,o.version_no FROM crm.opportunity o
        WHERE o.workspace_id=$1::uuid AND o.deleted_at IS NULL AND EXISTS(
          SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=o.id
            AND p.user_ref_id=ANY($2::uuid[]) AND p.participant_role='fde'
            AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
            AND security.fde_user_is_active(p.user_ref_id)) ORDER BY o.id""",
            actor.workspace_id,
            ids,
        )
    ]
    tasks = [
        dict(r)
        for r in await connection.fetch(
            """SELECT DISTINCT t.id::text,t.version_no,t.status,t.due_at,t.completed_at
        FROM workflow.task t JOIN workflow.task_assignee a ON a.task_id=t.id AND a.workspace_id=t.workspace_id
        WHERE t.workspace_id=$1::uuid AND a.assignee_user_ref_id=ANY($2::uuid[]) AND a.responsibility='owner'
          AND t.deleted_at IS NULL AND ((t.due_at >= $3 AND t.due_at <= $4)
            OR (t.completed_at >= $3 AND t.completed_at <= $4)) ORDER BY t.id::text""",
            actor.workspace_id,
            ids,
            start,
            now,
        )
    ]
    # Display task denominators above remain unchanged. Coaching also needs
    # future-due and older-overdue unfinished tasks, with no period cutoff.
    coaching_visits = [
        dict(row)
        for row in await connection.fetch(
            """SELECT v.id::text,v.version_no,v.interaction_at,v.follow_up_record AS communication,
          v.next_action,o.name AS project_name
        FROM security.fde_recorded_visit_history($2,$3,NULL,NULL,NULL) h JOIN activity.visit v ON v.id=h.visit_id
        LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id
        WHERE (($5::uuid IS NULL AND h.user_ref_id=ANY($1::uuid[])) OR h.team_id_at_event=$5::uuid)
          AND h.interaction_at >= $2 AND h.interaction_at <= $3
        ORDER BY v.interaction_at DESC,v.id LIMIT $4""",
            ids,
            start,
            now,
            VISIT_LIMIT,
            str(team_id) if team_id else None,
        )
    ]
    coaching_tasks = [
        dict(row)
        for row in await connection.fetch(
            """SELECT t.id::text,t.version_no,t.title,t.status,t.due_at,o.name AS project_name,
          true AS can_execute
        FROM workflow.task t JOIN workflow.task_assignee a ON a.task_id=t.id AND a.workspace_id=t.workspace_id
        LEFT JOIN crm.opportunity o ON o.id=t.opportunity_id
        WHERE t.workspace_id=$1::uuid AND a.assignee_user_ref_id=ANY($2::uuid[]) AND a.responsibility='owner'
          AND t.deleted_at IS NULL AND t.status NOT IN ('completed','cancelled')
        GROUP BY t.id,o.name
        HAVING bool_or(security.fde_task_eligible(t.id,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
        ORDER BY t.due_at ASC NULLS LAST,t.id LIMIT $3""",
            actor.workspace_id,
            ids,
            TASK_LIMIT,
        )
    ]
    if member_ids is not None:
        configuration["selected_member_ids"] = sorted(ids)
        configuration["selected_team_id"] = str(team_id) if team_id else None
    coaching = coaching_inputs(coaching_visits, coaching_tasks)
    versions = [
        {"type": kind, "id": row["id"], "version": row["version_no"]}
        for kind, rows in (("visit", coaching_visits), ("task", coaching_tasks))
        for row in rows
    ]
    return portrait(
        actor,
        days=days,
        now=now,
        permission_version=permission_version,
        visits=visits,
        projects=projects,
        tasks=tasks,
        configuration=configuration,
        coaching=coaching,
        coaching_versions=versions,
    )


async def matching_run(connection, actor, facts, *, reusable=False):
    row = await connection.fetchrow(
        """SELECT r.id::text,r.status,r.completed_at,r.error_code,m.structured_content AS result
        FROM agent.run r JOIN agent.conversation c ON c.id=r.conversation_id
        LEFT JOIN LATERAL(SELECT structured_content FROM agent.message WHERE source_run_id=r.id
          AND sender_type='assistant' ORDER BY created_at DESC,id DESC LIMIT 1) m ON true
        WHERE r.workspace_id=$1::uuid AND c.user_ref_id=$2::uuid
          AND r.identity_context->>'user_id'=$2::text AND r.identity_context->>'role'=$3
          AND r.identity_context->>'permission_version'=$4
          AND r.business_context->>'surface'='fde_profile'
          AND r.business_context->>'facts_fingerprint'=$5
          AND r.business_context->>'profile_days'=$6
          AND (NOT $7::boolean OR r.status IN ('queued','running','succeeded'))
        ORDER BY r.created_at DESC,r.id DESC LIMIT 1""",
        actor.workspace_id,
        actor.user_id,
        actor.role.value,
        facts["permission_version"],
        facts["facts_fingerprint"],
        str(facts["period"]["days"]),
        reusable,
    )
    return dict(row) if row else None


async def lock_profile(connection, actor, days):
    # Serialize the entire read/create decision before recomputing fresh facts.
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
        f"fde-profile:{actor.workspace_id}:{actor.user_id}:{days}",
    )


async def bind_profile_run(connection, run_id, facts):
    await connection.execute(
        "UPDATE agent.run SET business_context=business_context || $2::jsonb WHERE id=$1::uuid",
        run_id,
        {
            "surface": "fde_profile",
            "profile_days": facts["period"]["days"],
            "facts_fingerprint": facts["facts_fingerprint"],
            "profile_snapshot": facts,
        },
    )


async def profile_history(connection, user_id, days):
    rows = await connection.fetch("SELECT * FROM security.fde_profile_history($1::uuid,$2,30)", user_id, days)
    return [dict(row) for row in rows]
