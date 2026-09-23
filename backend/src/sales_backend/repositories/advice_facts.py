"""Full authorized fact fingerprints, with explicit bounded model context."""

from sales_backend.db import json_value
from sales_backend.domain.advice import FDE_ADVICE_PERSPECTIVE, AdviceError, fingerprint, require_advice_subject
from sales_backend.domain.business_time import BUSINESS_TIMEZONE, business_json_default, localize_business_times

MODEL_RECORD_LIMIT = 50


class AdviceFactsRepository:
    async def load(self, connection, actor, kind, subject_id, *, advice_id=None):
        require_advice_subject(kind, actor.role.value)
        if kind == "customer":
            subject = await connection.fetchrow(
                "SELECT id::text,name,industry_code,customer_type_code,level_code,main_business,demand_summary,"
                "owner_user_ref_id::text,owner_team_id::text FROM crm.customer "
                "WHERE id=$1::uuid AND deleted_at IS NULL",
                subject_id,
            )
        elif kind == "opportunity":
            subject = await connection.fetchrow(
                "SELECT id::text,customer_id::text,name,status,stage_code,probability,amount,"
                "expected_close_date,closed_at,product_line,partner_id::text,sales_channel,follow_up_plan,"
                "owner_user_ref_id::text,owner_team_id::text FROM crm.opportunity "
                "WHERE id=$1::uuid AND deleted_at IS NULL",
                subject_id,
            )
        elif kind == "visit":
            subject = await connection.fetchrow(
                "SELECT id::text,customer_id::text,opportunity_id::text,interaction_at,follow_up_record,"
                "next_action,visit_goal,contact_name_snapshot,contact_title_snapshot,expectation_code,"
                "recorder_user_ref_id::text,recording_role_code_snapshot FROM activity.visit WHERE id=$1::uuid "
                "AND status IN ('confirmed','archived') AND deleted_at IS NULL",
                subject_id,
            )
        else:
            raise AdviceError("未知建议对象", 422)
        if not subject:
            raise AdviceError("记录不存在或无权查看", 404)
        subject = dict(subject)
        customer_id = subject_id if kind == "customer" else subject["customer_id"]
        customer = json_value(await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id))
        if not customer:
            raise AdviceError("客户不存在或无权查看", 404)
        records = {}
        if kind != "visit":
            # Both predicates are fixed SQL; values never form identifiers or SQL fragments.
            records["visits"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT id::text,opportunity_id::text,interaction_at,follow_up_record,next_action,visit_goal,"
                    "recorder_user_ref_id::text,recording_role_code_snapshot,expectation_code FROM activity.visit "
                    "WHERE customer_id=$1::uuid AND ($2::uuid IS NULL OR opportunity_id=$2::uuid) "
                    "AND deleted_at IS NULL AND status IN ('confirmed','archived') ORDER BY interaction_at DESC,id",
                    customer_id,
                    subject_id if kind == "opportunity" else None,
                )
            ]
            records["tasks"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT id::text,opportunity_id::text,title,description,status,due_at,completed_at,target_position,"
                    "EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=workflow.task.id "
                    "AND a.assignee_user_ref_id=common.current_user_ref_id()) AS assigned_to_actor,"
                    "(due_at<clock_timestamp() AND status NOT IN ('completed','cancelled')) AS overdue "
                    "FROM workflow.task WHERE customer_id=$1::uuid AND ($2::uuid IS NULL OR opportunity_id=$2::uuid) "
                    "AND deleted_at IS NULL "
                    # A new, unchanged task created by this very batch is a human decision,
                    # not new evidence that invalidates its sibling suggestions. Any update
                    # increments task.version_no; becoming overdue also makes it evidence.
                    "AND NOT ($3::uuid IS NOT NULL AND version_no=1 AND status='pending_confirm' "
                    "AND due_at>=clock_timestamp() AND EXISTS ("
                    "SELECT 1 FROM insight.business_suggestion s WHERE s.id=source_suggestion_id "
                    "AND s.advice_id=$3::uuid AND s.decision='adopted' AND s.task_id=workflow.task.id)) "
                    "ORDER BY created_at DESC,id",
                    customer_id,
                    subject_id if kind == "opportunity" else None,
                    advice_id,
                )
            ]
            records["risks"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT id::text,opportunity_id::text,title,description,severity_code,status,suggested_action "
                    "FROM insight.risk WHERE customer_id=$1::uuid AND ($2::uuid IS NULL OR opportunity_id=$2::uuid) "
                    "AND deleted_at IS NULL ORDER BY updated_at DESC,id",
                    customer_id,
                    subject_id if kind == "opportunity" else None,
                )
            ]
        if kind == "customer":
            records["opportunities"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT id::text,name,status,stage_code,probability,amount,expected_close_date,"
                    "owner_user_ref_id::text FROM crm.opportunity WHERE customer_id=$1::uuid "
                    "AND deleted_at IS NULL ORDER BY updated_at DESC,id",
                    customer_id,
                )
            ]
        if kind == "opportunity":
            # Read the same source rows as customer assets. Keep quarter precision,
            # unknown tax basis and original units; never add them to dated entries.
            records["historical_actuals"] = [dict(row) for row in await connection.fetch(
                "SELECT id::text,year,quarter,kind,source_field,raw_amount,source_unit,tax_basis,"
                "raw_amount*CASE source_unit WHEN 'wan_cny' THEN 10000 ELSE 1 END AS amount_cny "
                "FROM crm.opportunity_period_actual_snapshot WHERE opportunity_id=$1::uuid "
                "ORDER BY year DESC,quarter DESC,kind,id", subject_id,
            )]
            subject["quarterly_forecasts"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT year,quarter,recognized_amount,collection_amount FROM crm.opportunity_forecast "
                    "WHERE opportunity_id=$1::uuid ORDER BY year,quarter",
                    subject_id,
                )
            ]
            subject["actuals"] = dict(
                await connection.fetchrow(
                    "SELECT sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,"
                    "sum(amount) FILTER(WHERE kind='collection') AS collection_amount FROM crm.customer_actual "
                    "WHERE opportunity_id=$1::uuid AND voided_at IS NULL",
                    subject_id,
                )
            )
            if actor.role.value in {"fde", "fde_lead"}:
                subject["sales_owner_name"] = await connection.fetchval(
                    "SELECT display_name FROM platform.user_ref WHERE id=$1::uuid AND workspace_id=$2::uuid",
                    subject["owner_user_ref_id"],
                    actor.workspace_id,
                )
                subject["fde_members"] = [
                    dict(row)
                    for row in await connection.fetch(
                        "SELECT p.user_ref_id::text AS user_id,u.display_name AS name "
                        "FROM crm.opportunity_participant p JOIN platform.user_ref u "
                        "ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id "
                        "WHERE p.opportunity_id=$1::uuid AND p.participant_role='fde' "
                        "AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to "
                        "AND security.fde_user_is_active(p.user_ref_id) ORDER BY u.display_name,u.id",
                        subject_id,
                    )
                ]
        full = localize_business_times(
            {
                "subject_kind": kind,
                "subject": subject,
                "customer_name": customer["name"],
                "records": records,
                "business_timezone": BUSINESS_TIMEZONE,
                "date_instruction": "拜访日期使用visit_date；时间均按Asia/Shanghai理解，不截取UTC日期。",
                "actor_context": {
                    "user_id": actor.user_id,
                    "role": actor.role.value,
                    "perspective": FDE_ADVICE_PERSPECTIVE
                    if actor.role.value in {"fde", "fde_lead"}
                    else "sales_business_v1",
                },
            }
        )
        if actor.role.value in {"fde", "fde_lead"}:
            full["actor_context"]["scope"] = "技术方案、验证、交付风险与协作行动；商业事实只作背景"
            if kind == "opportunity":
                full["actor_context"]["direct_participant"] = any(
                    member["user_id"] == actor.user_id for member in subject["fde_members"]
                )
        digest = fingerprint(full)
        counts = {
            key: {"total": len(value), "included": min(len(value), MODEL_RECORD_LIMIT)}
            for key, value in records.items()
        }
        facts = {
            **full,
            "records": {key: value[:MODEL_RECORD_LIMIT] for key, value in full["records"].items()},
            "coverage": counts,
            "scope_note": "仅当前权限可见资料；记录超出上限时展示最近50条，不代表其余不存在",
        }
        # Return JSON-safe values for asyncpg and provider envelopes, without rounding money.
        import json

        facts = json.loads(json.dumps(facts, default=business_json_default, ensure_ascii=False))
        return {"customer_id": customer_id, "facts": facts, "fingerprint": digest}
