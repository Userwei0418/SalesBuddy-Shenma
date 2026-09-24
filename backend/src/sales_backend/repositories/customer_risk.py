"""Customer-scoped risk facts and receipts. All reads use the caller's RLS."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sales_backend.domain.advice import fingerprint
from sales_backend.repositories.identity import IdentityRepository

CONTRACT = "customer-risk.v1"
MODEL_LIMIT = 80
TEXT_LIMIT = 4000
MAX_RECEIPT_AGE = timedelta(days=7)


async def load_customer_risk_facts(connection, customer_id):
    customer = await connection.fetchrow(
        "SELECT id::text,name,version_no,industry_code,customer_type_code,demand_summary,"
        "main_business,customer_budget,owner_team_id::text FROM crm.customer "
        "WHERE id=$1::uuid AND deleted_at IS NULL", customer_id,
    )
    if not customer:
        raise PermissionError("客户不存在或已不在授权范围内")
    owner = await connection.fetchval("SELECT security.customer_risk_assessment_owner($1::uuid)::text", customer_id)
    facts = {"contract_version": CONTRACT, "customer": {**dict(customer), "owner_user_ref_id": owner}}
    coverage = {"complete": True, "sources": {}, "contract_version": CONTRACT}
    # Identifiers and predicates are backend constants, never model or request input.
    sources = (
        ("visits", "activity.visit", "AND status IN ('confirmed','archived')",
         "id::text,version_no,customer_id::text,opportunity_id::text,interaction_at,"
         "follow_up_record,next_action,expectation_code", "interaction_at DESC,id"),
        ("opportunities", "crm.opportunity", "", "id::text,version_no,name,status,stage_code,"
         "probability,amount,expected_close_date", "updated_at DESC,id"),
        ("contacts", "crm.contact", "", "id::text,version_no,name,title,relationship_role_code,is_primary",
         "updated_at DESC,id"),
    )
    for key, table, predicate, columns, order in sources:
        meta = dict(await connection.fetchrow(
            f"SELECT count(*)::int AS eligible_count, "  # noqa: S608
            f"md5(COALESCE(string_agg(id::text || ':' || version_no::text,',' ORDER BY id),'')) AS digest "
            f"FROM {table} WHERE customer_id=$1::uuid AND deleted_at IS NULL {predicate}", customer_id,
        ))
        rows = [dict(r) for r in await connection.fetch(
            f"SELECT {columns} FROM {table} WHERE customer_id=$1::uuid "  # noqa: S608
            f"AND deleted_at IS NULL {predicate} ORDER BY {order} LIMIT $2", customer_id, MODEL_LIMIT,
        )]
        truncated = False
        for row in rows:
            for field, value in row.items():
                if isinstance(value, str) and len(value) > TEXT_LIMIT:
                    row[field] = value[:TEXT_LIMIT]
                    truncated = True
        facts[key] = rows
        complete = len(rows) == meta["eligible_count"] and not truncated
        coverage["sources"][key] = {
            **meta, "included_count": len(rows), "has_more": len(rows) < meta["eligible_count"],
            "text_truncated": truncated, "records": [{"id": r["id"], "version": r["version_no"]} for r in rows],
        }
        coverage["complete"] &= complete
    # Hashes cover ALL visible source identities/versions, even outside model bounds.
    facts["policy_revision"] = dict(await connection.fetchrow(
        "SELECT security.active_company_rule('agent_business.personal_risks')::text AS business_rule,"
        "security.active_company_rule('agent_execution.personal_risks')::text AS execution_rule,"
        "(SELECT revision_no FROM config.agent_runtime_config "
        "WHERE workspace_id=common.current_workspace_id()) AS runtime_revision"
    ))
    facts["coverage"] = coverage
    facts["fingerprint"] = fingerprint(facts)
    facts["data_as_of"] = datetime.now(UTC).isoformat()
    return facts


async def enqueue_customer_risk_review(connection, actor, *, customer_id, trigger_type, trigger_id):
    """Queue a customer owner's real identity, preserving the actual initiator.

    FDE can trigger evaluation of a visible customer, but gains no risk-write permission.
    The database independently checks the current owner and active execution identity.
    """
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "customer-risk:" + str(customer_id),
    )
    owner = await connection.fetchval("SELECT security.customer_risk_assessment_owner($1::uuid)::text", customer_id)
    if not owner:
        return None  # An unclaimed customer has no accountable business owner yet.
    execution = None
    for role in ("sales", "supervisor", "manager", "fde", "fde_lead", "operations", "administrator"):
        execution = await IdentityRepository().find_actor_by_id(
            connection, workspace_id=actor.workspace_id, user_id=owner, role=role,
        )
        if execution:
            break
    if not execution:
        return None
    # Source fingerprint also distinguishes repeated edits of the same customer.
    facts = await load_customer_risk_facts(connection, customer_id)
    key = fingerprint([customer_id, owner, trigger_type, str(trigger_id), facts["fingerprint"]])
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "customer-risk:" + key)
    existing = await connection.fetchval(
        "SELECT id::text FROM insight.customer_risk_assessment WHERE workspace_id=$1::uuid AND idempotency_key=$2",
        actor.workspace_id, key,
    )
    if existing:
        return existing
    assessment_id, job_id = str(uuid4()), str(uuid4())
    execution = execution.context
    await connection.execute(
        "INSERT INTO ops.job(id,workspace_id,job_type,aggregate_type,aggregate_id,payload,priority) "
        "VALUES($1::uuid,$2::uuid,'customer.risk.review','customer_risk_assessment',$3::uuid,$4::jsonb,70)",
        job_id, actor.workspace_id, assessment_id, {
            **execution.model_dump(mode="json"), "customer_id": customer_id,
            "trigger_type": trigger_type, "trigger_id": str(trigger_id),
            "initiated_by_user_ref_id": actor.user_id,
        },
    )
    await connection.execute(
        "INSERT INTO insight.customer_risk_assessment(id,workspace_id,customer_id,actor_user_ref_id,"
        "actor_role_code,initiated_by_user_ref_id,job_id,trigger_type,trigger_id,idempotency_key,identity_snapshot) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6::uuid,$7::uuid,$8,$9,$10,$11::jsonb)",
        assessment_id, actor.workspace_id, customer_id, execution.user_id, execution.role.value,
        actor.user_id, job_id, trigger_type, str(trigger_id), key, execution.model_dump(mode="json"),
    )
    return assessment_id


async def current_clear_assessment(connection, customer_id):
    """No success-only filter: a newer failure must never expose an older all-clear."""
    row = await connection.fetchrow(
        "SELECT status,outcome,coverage,facts_fingerprint,actor_user_ref_id::text,actor_role_code,identity_snapshot,"
        "fact_scope_version,completed_at "
        "FROM insight.customer_risk_assessment WHERE customer_id=$1::uuid ORDER BY created_at DESC,id DESC LIMIT 1",
        customer_id,
    )
    if not row or row["status"] != "succeeded" or row["outcome"] != "no_risk_identified":
        return False
    if not row["coverage"].get("complete") or not row["completed_at"]:
        return False
    if row["completed_at"] < datetime.now(UTC) - MAX_RECEIPT_AGE:
        return False
    if row["fact_scope_version"] != await connection.fetchval("SELECT security.current_fact_scope_version()"):
        return False
    current = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=row["identity_snapshot"]["workspace_id"],
        user_id=row["actor_user_ref_id"], role=row["actor_role_code"],
    )
    if not current or fingerprint(current.context.model_dump(mode="json")) != fingerprint(row["identity_snapshot"]):
        return False
    facts = await load_customer_risk_facts(connection, customer_id)
    return bool(facts["visits"] and facts["coverage"]["complete"]
                and facts["customer"]["owner_user_ref_id"] == row["actor_user_ref_id"]
                and facts["fingerprint"] == row["facts_fingerprint"])
