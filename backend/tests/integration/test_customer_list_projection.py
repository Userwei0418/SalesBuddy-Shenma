"""Frozen legacy-query oracle for the customer-list performance rewrite.

Synthetic fixtures use current business APIs under a non-bypass runtime role.
No production connection, external provider, or worker is used. All writes roll back.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.attribute_overlay import overlay_customer_attributes
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.provision import create_owned_customer
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import members
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio

# Intentionally frozen from CustomerRepository.list at baa612e451db. This is a
# regression oracle only; production must not select between two implementations.
LEGACY_LIST_SQL = r"""
            SELECT c.id::text, c.name, c.industry_code, c.customer_type_code, c.level_code,
                   c.lifecycle_status, c.source_code, c.data_kind, c.attributes,
                   c.import_meta, c.owner_user_ref_id::text,
                   u.display_name AS owner_name, CASE WHEN u.id IS NULL THEN '[]'::jsonb
                     ELSE jsonb_build_array(jsonb_build_object('id',u.id::text,'name',u.display_name))
                     END AS sales_members, c.owner_team_id::text,
                   t.name AS team_name, c.primary_partner_name,
                   c.next_action, c.operation_type, c.cooperation_years, c.main_business, c.customer_budget,
                   (SELECT s.input_snapshot->'company_policy' FROM insight.quadrant_score s
                     WHERE s.customer_id=c.id AND s.valid_to='infinity'
                     AND s.subject_user_ref_id=common.current_user_ref_id()) AS quadrant_policy,
                   q.potential_score, q.relationship_score, q.quadrant_code,
                   latest.interaction_at AS latest_visit_at,
                   COALESCE(latest.weekly_follow_up_count, 0) AS weekly_follow_up_count,
                   open_risk.title AS risk_title,
                   open_risk.severity_code AS risk_severity,
                   opportunity.name AS opportunity_name,
                   opportunity.amount AS opportunity_amount,
                   opportunity.stage_code AS opportunity_stage,
                   ARRAY(SELECT o.expected_close_date FROM crm.opportunity o WHERE o.customer_id=c.id
                     AND o.deleted_at IS NULL AND o.status='open') AS plan_close_dates
            FROM crm.customer c
            LEFT JOIN crm.customer_ownership ownership ON ownership.customer_id=c.id
            LEFT JOIN platform.user_ref u ON u.id = security.profile_customer_owner(c.id)
            LEFT JOIN LATERAL (
              SELECT string_agg(mu.display_name,'、' ORDER BY mu.display_name) AS names,
                jsonb_agg(jsonb_build_object('id',mu.id::text,'name',mu.display_name)
                  ORDER BY mu.display_name) AS people
              FROM crm.customer_sales_member m JOIN platform.user_ref mu ON mu.id=m.user_ref_id
              WHERE m.customer_id=c.id
            ) members ON true
            LEFT JOIN platform.team t ON t.id = c.owner_team_id
            LEFT JOIN crm.v_customer_current_quadrant q ON q.id = c.id
            LEFT JOIN LATERAL (
              SELECT max(v.interaction_at) AS interaction_at,
                     count(*) FILTER (
                       WHERE timezone('Asia/Shanghai', v.interaction_at) >=
                             date_trunc('week', timezone('Asia/Shanghai', clock_timestamp()))
                     )::integer AS weekly_follow_up_count
                FROM activity.visit v
               WHERE v.customer_id = c.id AND v.deleted_at IS NULL
                 AND v.status IN ('confirmed', 'archived')
            ) latest ON true
            LEFT JOIN LATERAL (
              SELECT r.title, r.severity_code FROM insight.risk r
              WHERE r.customer_id = c.id AND r.deleted_at IS NULL
                AND r.status IN ('new','pending','in_progress','escalated')
              ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
                       r.opened_at DESC LIMIT 1
            ) open_risk ON true
            LEFT JOIN LATERAL (
              SELECT o.name, o.stage_code,
                     (SELECT COALESCE(sum(o2.amount), 0)
                        FROM crm.opportunity o2
                       WHERE o2.customer_id = c.id AND o2.deleted_at IS NULL
                         AND o2.status = 'open') AS amount
                FROM crm.opportunity o
               WHERE o.customer_id = c.id AND o.deleted_at IS NULL AND o.status = 'open'
               ORDER BY o.updated_at DESC LIMIT 1
            ) opportunity ON true
            WHERE c.deleted_at IS NULL
              AND (common.current_role_code()<>'sales' OR ownership.owner_user_ref_id=common.current_user_ref_id())
              AND ($1::text IS NULL OR c.name ILIKE '%' || $1 || '%'
                   OR u.display_name ILIKE '%' || $1 || '%')
              AND ($2::text IS NULL OR c.level_code = $2)
              AND ($3::boolean IS NULL OR ($3 AND ownership.state='unclaimed')
                   OR (NOT $3 AND ownership.state='claimed'))
            ORDER BY latest.interaction_at DESC NULLS LAST, c.name
            LIMIT $4
            """


async def compared(connection, **overrides):
    params = {"query": None, "level": None, "unassigned": None, "limit": None, **overrides}
    expected = [overlay_customer_attributes(dict(row)) for row in await connection.fetch(
        LEGACY_LIST_SQL, params["query"], params["level"], params["unassigned"], params["limit"],
    )]
    actual = await CustomerRepository().list(connection, **params)
    # plan_close_dates has no SQL ORDER BY in the legacy contract: its multiset,
    # including NULL and duplicate dates, is meaningful; physical row order is not.
    for row in expected + actual:
        row["plan_close_dates"] = sorted(row["plan_close_dates"], key=str)
    assert actual == expected, params
    return actual


async def make_customer(connection, owner, name, level="Tier-2"):
    data = dict(name=name, industry="软件", customer_type="潜在客户", level_code=level,
                source="销售线索", target_team="南区", partner_name="", contact_name="测试联系人",
                contact_title="经理", contact_role="决策者")
    if owner:
        return await create_owned_customer(connection, owner, data=data)
    operator = await actor(connection, "OPS001")
    return await CustomerMutationRepository().create(
        connection, operator, data={**data, "company_reference": "ISOLATED-" + uuid4().hex},
    )


async def fixtures(connection):
    assert not await connection.fetchval(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
    ), "Projection/RLS equivalence requires the isolated non-bypass harness"
    await connection.execute("SET LOCAL statement_timeout='20s'")
    sales, project, people, _ = await fde_fixture(connection)
    prefix = "ListProjection-" + uuid4().hex[:8]
    await set_request_context(connection, sales)
    await connection.execute(
        "UPDATE crm.customer SET name=$2,level_code='Tier-1',next_action='正式列优先',"
        "attributes=$3::jsonb,import_meta=$4::jsonb WHERE id=$1::uuid",
        project["customer_id"], prefix + "-A", {"next_action": "旧属性", "custom": "保留"},
        {"fixture_source": "synthetic"},
    )
    await connection.execute(
        "UPDATE crm.opportunity SET updated_at=clock_timestamp()-interval '1 hour' WHERE id=$1::uuid",
        project["id"],
    )
    second = await save_opportunity(connection, sales, customer_id=project["customer_id"], data={
        "name": "最新商机", "amount": Decimal(250), "probability": 10,
        "expected_close_date": datetime.now(UTC).date() + timedelta(days=30),
    })
    # One older confirmed, one current archived, and one pending follow-up:
    # latest/count must exclude pending, while both formal states participate.
    await connection.execute(
        """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
        recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$4::uuid,
          (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
          status,on_date,'合成列表回归' FROM (VALUES
          ('confirmed',clock_timestamp()-interval '20 days'),
          ('archived',clock_timestamp()),('pending_confirm',clock_timestamp()+interval '1 day'))
          AS v(status,on_date)""",
        sales.workspace_id, project["customer_id"], project["id"], sales.user_id, sales.team_ids[0],
    )
    await connection.execute(
        """INSERT INTO insight.risk(workspace_id,customer_id,opportunity_id,owner_user_ref_id,
        owner_team_id,risk_type_code,title,description,severity_code,status,opened_at)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,'delivery',title,'合成风险',severity,status,
          clock_timestamp() FROM (VALUES ('最高未关闭风险','critical','new'),
          ('较低未关闭风险','low','pending'),('不计入的关闭风险','critical','resolved'))
          AS r(title,severity,status)""",
        sales.workspace_id, project["customer_id"], project["id"], sales.user_id, sales.team_ids[0],
    )
    fde = await actor(connection, people["first"]["code"])
    # Deliberately different subject scores catch accidental loss of private RLS.
    for person, score in ((sales, 71), (fde, 83)):
        await set_request_context(connection, person)
        await connection.execute(
            """INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,
            relationship_score,quadrant_code,rule_set_id,input_snapshot,subject_user_ref_id)
            VALUES($1::uuid,$2::uuid,$3,65,'customer_asset',
              (SELECT id FROM config.rule_set WHERE status='active' AND rule_code='customer_quadrant'
               ORDER BY version_no DESC LIMIT 1),$4::jsonb,$5::uuid)""",
            person.workspace_id, project["customer_id"], score,
            {"company_policy": {"fixture_subject": person.user_id}}, person.user_id,
        )
    peer = await actor(connection, "XS002")
    own_empty = await make_customer(connection, sales, prefix + "-B")
    peer_empty = await make_customer(connection, peer, prefix + "-C", "Tier-1")
    unclaimed = await make_customer(connection, None, prefix + "-D")
    return dict(sales=sales, fde=fde, peer=peer, project=project, second=second, prefix=prefix,
                own_empty=own_empty, peer_empty=peer_empty, unclaimed=unclaimed, people=people)


@pytest.mark.parametrize("role", ["sales", "supervisor", "manager", "fde"])
async def test_list_full_projection_filters_limits_and_private_scores(connection, role):
    data = await fixtures(connection)
    person = data[role] if role in {"sales", "fde"} else await actor(
        connection, {"supervisor": "ZJ001", "manager": "ZJL001"}[role],
    )
    await set_request_context(connection, person)
    initial = await compared(connection)
    visible = {row["id"] for row in initial}
    assert data["project"]["customer_id"] in visible
    assert (data["peer_empty"]["id"] in visible) == (role in {"supervisor", "manager"})
    rich = next(row for row in initial if row["id"] == data["project"]["customer_id"])
    assert rich["opportunity_name"] == "最新商机"
    assert rich["opportunity_amount"] == Decimal(100250)
    assert rich["risk_title"] == "最高未关闭风险"
    assert rich["weekly_follow_up_count"] == 1
    assert rich["attributes"]["next_action"] == "正式列优先"
    if role in {"sales", "fde"}:
        assert rich["potential_score"] == (71 if role == "sales" else 83)
        assert rich["quadrant_policy"] == {"fixture_subject": person.user_id}
    cases = [
        {"query": data["prefix"]}, {"query": "XS001"}, {"query": "no-such-customer"},
        {"level": "Tier-1"}, {"level": "Tier-2"}, {"level": "missing-tier"},
        {"unassigned": True}, {"unassigned": False}, {"unassigned": None},
        {"query": data["prefix"], "level": "Tier-1", "unassigned": False},
    ]
    for case in cases:
        for limit in (None, 1, 50):
            await compared(connection, **case, limit=limit)


async def test_list_page_crosses_visit_and_null_visit_boundary(connection):
    data = await fixtures(connection)
    for i in range(52):
        await make_customer(connection, None, f"{data['prefix']}-N{i:02d}")
    await actor(connection, "ZJL001")
    all_rows = await compared(connection, query=data["prefix"], limit=None)
    assert len(all_rows) == 56
    assert all_rows[0]["id"] == data["project"]["customer_id"]
    assert all(row["latest_visit_at"] is None for row in all_rows[1:])
    for limit in (1, 50):
        page = await compared(connection, query=data["prefix"], limit=limit)
        assert page == all_rows[:limit]
    # Management customer visibility does not bypass ownership-table RLS.
    assert await compared(connection, query=data["prefix"], unassigned=True, limit=50) == []
    await actor(connection, "OPS001")
    only_unclaimed = await compared(connection, query=data["prefix"], unassigned=True, limit=50)
    assert len(only_unclaimed) == 50
    assert all(row["owner_name"] is None for row in only_unclaimed)


async def test_list_rechecks_fde_revocation_and_workspace_on_reused_connection(connection):
    data = await fixtures(connection)
    fde, project = data["fde"], data["project"]
    for person in (data["sales"], await actor(connection, "ZJ001"),
                   await actor(connection, "ZJL001"), fde):
        await set_request_context(connection, person)
        assert await compared(connection)
        await set_request_context(connection, person.model_copy(update={"workspace_id": str(uuid4())}))
        assert await compared(connection) == []
    await members(connection, data["sales"], project, [data["people"]["second"]["id"]])
    await set_request_context(connection, fde)
    assert await compared(connection) == []
    # Reauthorization must not retain the previous empty plan/result.
    await members(connection, data["sales"], project, [fde.user_id, data["people"]["second"]["id"]])
    await set_request_context(connection, fde)
    assert [row["id"] for row in await compared(connection)] == [project["customer_id"]]
