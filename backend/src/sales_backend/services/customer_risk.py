"""Event-driven customer risk assessment; no client changes or GET side effects."""

import asyncio
import logging
from dataclasses import replace
from datetime import datetime

from sales_backend.domain.advice import fingerprint
from sales_backend.domain.agent import ChatMessage
from sales_backend.domain.customer_risk import (
    customer_risk_messages,
    customer_risk_output_checklist,
    validate_customer_risk_result,
)
from sales_backend.job_context import current_job_lease
from sales_backend.repositories.customer_risk import CONTRACT, load_customer_risk_facts
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.agent_business_rules import (
    business_policy_metadata,
    prepare_business_rules,
    supplementary_prompt,
)
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService, binding_for
from sales_backend.services.agent_platform.pilot import personal_risk_pilot_policy
from sales_backend.services.runtime_config import load_runtime_configuration

logger = logging.getLogger(__name__)


def configuration_fingerprint(runtime, actor):
    binding = binding_for(runtime.settings.agent_platform_bindings_json, actor.workspace_id, "personal_risks")
    return fingerprint({
        "contract": CONTRACT, "prompt": customer_risk_messages({"visits": []})[0]["content"],
        "output_checklist": customer_risk_output_checklist({"visits": []}),
        "override": runtime.prompt_overrides.get("personal_risks", ""),
        "business_policy": business_policy_metadata(runtime.business_policy),
        "execution_policy": runtime.settings.agent_execution_policy,
        "direct_model": runtime.settings.llm_model,
        "direct_connection": runtime.settings.model_api_metadata,
        "platform_selected": personal_risk_pilot_policy(runtime.settings, actor) is not None,
        "binding": {"agent_id": binding.agent_id, "snapshot_id": binding.snapshot_id,
                    "execution_mode": binding.execution_mode} if binding else None,
    })


async def assert_current_owner(connection, actor, row):
    current = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=actor.workspace_id, user_id=actor.user_id, role=actor.role.value,
    )
    owner = await connection.fetchval(
        "SELECT security.customer_risk_assessment_owner($1::uuid)::text", str(row["customer_id"]),
    )
    if (not current or owner != actor.user_id or str(row["actor_user_ref_id"]) != actor.user_id
        or row["actor_role_code"] != actor.role.value
        or current.context.model_copy(update={"team_ids": tuple(sorted(current.context.team_ids))})
        != actor.model_copy(update={"team_ids": tuple(sorted(actor.team_ids))})):
        raise PermissionError("客户负责人或评估权限已变化，本次结果不再采用")


class CustomerRiskReviewHandler:
    def __init__(self, database, settings=None):
        self.database = database
        self.settings = settings or database.settings

    async def runtime(self, actor):
        return await load_runtime_configuration(self.database, actor, self.settings, capability="personal_risks")

    async def handle(self, assessment_id, actor):
        runtime = await self.runtime(actor)
        config = configuration_fingerprint(runtime, actor)
        async with self.database.transaction(actor) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM insight.customer_risk_assessment WHERE id=$1::uuid FOR UPDATE", assessment_id,
            )
            if not row:
                raise PermissionError("客户风险评估不存在或已无权执行")
            lease = current_job_lease.get()
            if lease and str(row["job_id"]) != lease.job_id:
                raise PermissionError("客户风险评估任务回执不匹配")
            if row["status"] in {"succeeded", "superseded"}:
                await record_job_effect(connection, actor.workspace_id)
                return
            await assert_current_owner(connection, actor, row)
            if (row["fact_scope_version"] != await connection.fetchval("SELECT security.current_fact_scope_version()")
                or not await self._is_latest(connection, row)):
                await self._supersede(connection, assessment_id, actor)
                return
            facts = await load_customer_risk_facts(connection, str(row["customer_id"]))
            scope_version = await connection.fetchval("SELECT security.current_fact_scope_version()")
            updated = await connection.execute(
                "UPDATE insight.customer_risk_assessment SET status='running',outcome='unknown',error_code=NULL,"
                "started_at=clock_timestamp(),completed_at=NULL,inference_operation_id=NULL,"
                "coverage=$2::jsonb,facts_fingerprint=$3,"
                "configuration_fingerprint=$4,fact_scope_version=$5,data_as_of=$6 WHERE id=$1::uuid",
                assessment_id, facts["coverage"], facts["fingerprint"], config, scope_version,
                datetime.fromisoformat(facts["data_as_of"]),
            )
            if updated != "UPDATE 1":
                raise PermissionError("客户风险评估权限已变化")
        # Empty/truncated context cannot justify an all-clear. Preserve a concrete
        # insufficient-evidence receipt without spending a provider call on no input.
        if not facts["visits"]:
            result = {"outcome": "insufficient_evidence", "reviewed_visit_ids": [], "risks": [],
                      "reason": "该客户暂无已确认或已归档的跟进记录"}
            trace = {}
        else:
            messages = [ChatMessage(**message) for message in customer_risk_messages(facts)]
            messages[0] = ChatMessage(role="system", content=supplementary_prompt(
                messages[0].content, runtime.prompt_overrides.get("personal_risks"),
            ))
            prepared, messages = prepare_business_rules(runtime, facts, messages)
            messages[0] = ChatMessage(
                role="system", content=messages[0].content + "\n" + customer_risk_output_checklist(facts),
            )
            pilot = personal_risk_pilot_policy(runtime.settings, actor)
            platform = filtered_facts_runtime(
                self.database, runtime.settings, capability="personal_risks",
                block_requests=pilot.block_platform_requests,
            ) if pilot else None
            settings = runtime.settings if pilot else replace(runtime.settings, agent_platform_bindings_json="{}")
            try:
                evaluated = await InferenceService(self.database, settings, platform=platform).evaluate(
                    actor=actor, mode="personal_risks", surface="customer_risk", facts=prepared,
                    messages=messages, user_text="评估指定客户的风险，并明确返回事实覆盖与评估结论。",
                    validate=lambda value: validate_customer_risk_result(value, facts),
                )
            except Exception:
                await self._link_failed_inference(row, actor)
                raise
            result, trace = evaluated.payload, evaluated.trace
        current_runtime = await self.runtime(actor)
        async with self.database.transaction(actor) as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "customer-risk:" + str(row["customer_id"]),
            )
            await assert_current_owner(connection, actor, row)
            locked = await connection.fetchrow(
                "SELECT id FROM insight.customer_risk_assessment WHERE id=$1::uuid FOR UPDATE", assessment_id,
            )
            if not locked:
                raise PermissionError("客户风险评估权限已变化")
            fresh = await load_customer_risk_facts(connection, str(row["customer_id"]))
            if (fresh["fingerprint"] != facts["fingerprint"]
                or configuration_fingerprint(current_runtime, actor) != config
                or scope_version != await connection.fetchval("SELECT security.current_fact_scope_version()")
                or not await self._is_latest(connection, row)):
                await self._supersede(connection, assessment_id, actor)
                return
            await self._persist_risks(connection, actor, assessment_id, facts, result, trace)
            updated = await connection.execute(
                "UPDATE insight.customer_risk_assessment SET status='succeeded',outcome=$2,result=$3::jsonb,"
                "risk_count=$4,inference_operation_id=$5::uuid,completed_at=clock_timestamp() WHERE id=$1::uuid",
                assessment_id, result["outcome"], {**result, "inference_trace": trace}, len(result["risks"]),
                trace.get("operation_id"),
            )
            if updated != "UPDATE 1":
                raise PermissionError("客户风险评估权限已变化，结果未保存")
            await record_job_effect(connection, actor.workspace_id)

    async def _link_failed_inference(self, row, actor):
        # Inference audit is written independently. Keep its failed receipt linked
        # without claiming a business result, escalating authority or masking errors.
        try:
            async with asyncio.timeout(2):
                async with self.database.transaction(actor) as connection:
                    await connection.execute(
                        "UPDATE insight.customer_risk_assessment a SET inference_operation_id=("
                        "SELECT o.id FROM agent.inference_operation o WHERE o.workspace_id=a.workspace_id "
                        "AND o.job_id=a.job_id AND o.actor_user_ref_id=a.actor_user_ref_id "
                        "AND o.actor_role_code=a.actor_role_code AND o.capability='personal_risks' "
                        "AND o.started_at>=a.started_at "
                        "AND o.status IN ('failed','cancelled','reconciliation_required') "
                        "ORDER BY o.started_at DESC,o.id DESC LIMIT 1) "
                        "WHERE a.id=$1::uuid AND a.status='running' AND a.inference_operation_id IS NULL",
                        str(row["id"]),
                    )
        except Exception:
            # The audit still has job_id; the management projection can join it.
            logger.warning(
                "客户风险评估失败回执关联未完成", extra={"actor": actor, "event_type": "customer_risk_audit_gap"},
            )

    @staticmethod
    async def _is_latest(connection, row):
        latest = await connection.fetchval(
            "SELECT id FROM insight.customer_risk_assessment WHERE customer_id=$1::uuid "
            "ORDER BY created_at DESC,id DESC LIMIT 1", row["customer_id"],
        )
        return latest == row["id"]

    @staticmethod
    async def _supersede(connection, assessment_id, actor):
        updated = await connection.execute(
            "UPDATE insight.customer_risk_assessment SET status='superseded',outcome='unknown',"
            "completed_at=clock_timestamp(),error_code='facts_or_scope_changed' WHERE id=$1::uuid", assessment_id,
        )
        if updated != "UPDATE 1":
            raise PermissionError("客户风险评估权限已变化")
        await record_job_effect(connection, actor.workspace_id)

    @staticmethod
    async def _persist_risks(connection, actor, assessment_id, facts, result, trace):
        visits = {item["id"]: item for item in facts["visits"]}
        for risk in result["risks"]:
            visit = visits[risk["source_visit_id"]]
            row = await connection.fetchrow(
                "INSERT INTO insight.risk(workspace_id,customer_id,opportunity_id,source_visit_id,risk_type_code,"
                "title,description,severity_code,status,owner_user_ref_id,owner_team_id,model_ref,input_snapshot,"
                "evidence,due_at,source_code,suggested_action,agent_key) VALUES("
                "$1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6,$7,$8,'pending',$9::uuid,$10::uuid,$11,$12::jsonb,"
                "$13::jsonb,$14,'customer_risk_agent',$15,$16) "
                "ON CONFLICT(workspace_id,owner_user_ref_id,source_visit_id,risk_type_code) "
                "WHERE source_visit_id IS NOT NULL AND deleted_at IS NULL DO UPDATE SET "
                "title=EXCLUDED.title,description=EXCLUDED.description,severity_code=EXCLUDED.severity_code,"
                "model_ref=EXCLUDED.model_ref,input_snapshot=EXCLUDED.input_snapshot,evidence=EXCLUDED.evidence,"
                "due_at=EXCLUDED.due_at,source_code=EXCLUDED.source_code,suggested_action=EXCLUDED.suggested_action,"
                "agent_key=EXCLUDED.agent_key,version_no=insight.risk.version_no+1 "
                "WHERE insight.risk.status NOT IN ('resolved','accepted') RETURNING id,status,(xmax=0) AS inserted",
                actor.workspace_id, facts["customer"]["id"], visit.get("opportunity_id"), visit["id"],
                risk["risk_type"], risk["title"], risk["description"], risk["severity"], actor.user_id,
                facts["customer"]["owner_team_id"], trace.get("model_ref", "customer_risk_agent"),
                {"assessment_id": assessment_id, "facts_fingerprint": facts["fingerprint"]},
                [{"type": "visit", "visit_id": visit["id"], "detail": risk["evidence_detail"]}],
                datetime.fromisoformat(risk["due_at"].replace("Z", "+00:00")) if risk.get("due_at") else None,
                risk["suggested_action"], f"{visit['id']}:{risk['risk_type']}",
            )
            if row:
                await connection.execute(
                    "INSERT INTO insight.risk_event(workspace_id,risk_id,event_type,to_status,"
                    "actor_user_ref_id,payload) VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid,$6::jsonb)",
                    actor.workspace_id, row["id"], "detected" if row["inserted"] else "reanalyzed", row["status"],
                    actor.user_id, {"assessment_id": assessment_id, "source_visit_id": visit["id"]},
                )
