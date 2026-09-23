from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sales_backend.config import Settings
from sales_backend.contracts.battle_map import validate_battle_map_result
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, ChatMessage, RoleCode
from sales_backend.domain.company_rules import QuadrantPolicy
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.job_context import current_job_lease
from sales_backend.repositories.attribute_overlay import overlay_customer_attributes, overlay_visit_attributes
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.agent_business_rules import prepare_business_rules, supplementary_prompt
from sales_backend.services.agent_platform.audit import audit_original
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_platform.pilot import battle_map_pilot_policy
from sales_backend.services.runtime_config import load_runtime_configuration

QUADRANT_RULE_SET_CODE = "customer_quadrant"


def _changed_scoring(policy):
    defaults = QuadrantPolicy().model_dump()
    return any(policy["definition"][key] != defaults[key] for key in (
        "potential_guidance", "relationship_guidance", "calibration_examples"))


def quadrant_code(potential_score: float, relationship_score: float, policy=None) -> str:
    return QuadrantPolicy(**(policy or {})).classify(potential_score, relationship_score)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class BattleMapReviewHandler:
    """Re-evaluates one customer's battle-map position from current business facts."""

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    async def handle(
        self,
        customer_id: str,
        actor: ActorContext,
        *,
        trigger: dict[str, Any] | None = None,
    ) -> None:
        facts = await self._load_facts(customer_id, actor)
        result = await self._evaluate(actor, facts)
        await self._persist(customer_id, actor, facts, result, trigger or {})

    async def _load_facts(self, customer_id: str, actor: ActorContext) -> dict[str, Any]:
        async with self.database.transaction(actor, readonly=True) as connection:
            policy = await CompanyRulesRepository().active(connection, "customer_quadrant")
            customer = await connection.fetchrow(
                """
                SELECT id::text, name, industry_code, customer_type_code,
                       lifecycle_status, level_code, demand_summary, attributes, import_meta,
                       next_action, operation_type, cooperation_years, main_business, customer_budget,
                       owner_team_id::text
                  FROM crm.customer
                 WHERE id = $1::uuid AND deleted_at IS NULL
                """,
                customer_id,
            )
            if not customer:
                raise LookupError("customer not found or outside review scope")
            opportunities = await connection.fetch(
                """
                SELECT id::text, name, stage_code, amount, probability,
                       expected_close_date, status, updated_at
                  FROM crm.opportunity
                 WHERE customer_id = $1::uuid AND deleted_at IS NULL
                 ORDER BY (status = 'open') DESC, updated_at DESC
                 LIMIT 20
                """,
                customer_id,
            )
            visits = await connection.fetch(
                """
                SELECT id::text, interaction_at, expectation_code, follow_up_record,
                       next_action, contact_title_snapshot, contact_name_snapshot,
                       attributes, import_meta, quality_review, first_visit_profile,
                       is_first_visit, follow_up_score
                  FROM activity.visit
                 WHERE customer_id = $1::uuid AND deleted_at IS NULL
                   AND status IN ('confirmed', 'archived')
                 ORDER BY interaction_at DESC, created_at DESC
                 LIMIT 20
                """,
                customer_id,
            )
            contacts = await connection.fetch(
                """
                SELECT id::text, name, title, relationship_role_code, is_primary
                  FROM crm.contact
                 WHERE customer_id = $1::uuid AND deleted_at IS NULL
                 ORDER BY is_primary DESC, updated_at DESC
                 LIMIT 20
                """,
                customer_id,
            )
            current = await connection.fetchrow(
                """
                SELECT potential_score, relationship_score, quadrant_code, calculated_at
                  FROM insight.quadrant_score
                 WHERE customer_id = $1::uuid AND valid_to = 'infinity'
                   AND subject_user_ref_id=common.current_user_ref_id()
                """,
                customer_id,
            )
        return {
            "company_policy": policy,
            "customer": overlay_customer_attributes(dict(customer)),
            "opportunities": [dict(row) for row in opportunities],
            "visits": [overlay_visit_attributes(dict(row)) for row in visits],
            "contacts": [dict(row) for row in contacts],
            "previous_score": dict(current) if current else None,
            "data_as_of": datetime.now(UTC).isoformat(),
        }

    async def _evaluate(self, actor: ActorContext, facts: dict[str, Any]) -> dict[str, Any]:
        runtime = await load_runtime_configuration(self.database, actor, self.settings, capability="battle_map_review")
        prompt_override = runtime.prompt_overrides.get("battle_map_review")
        system = supplementary_prompt(
            "你是企业销售作战地图Agent。只基于输入的客户、拜访、联系人和商机事实，重新评判客户。"
            "只输出一个JSON对象，不要前言或Markdown。"
            "输出potential_score和relationship_score，范围均为0到100。潜力重点考察明确需求、预算、"
            "商机金额、阶段/概率、预计成单时间、产品匹配和决策链；关系深度重点考察拜访频次与时效、"
            "沟通达成度、客户互动意愿、下一步闭环、联系人层级与决策角色。最新记录权重更高，禁止臆测。"
            "输出JSON：{potential_score,relationship_score,summary,rationale:{potential,relationship},"
            "evidence:[{source_type,source_id,detail}]}。source_type只能是visit、opportunity、contact，"
            "source_id必须来自输入事实。不要输出象限，象限由系统阈值统一计算。",
            prompt_override,
        )
        policy = facts.get("company_policy")
        if policy:
            if _changed_scoring(policy):
                policy["required_output_receipt"] = {"company_policy_id": policy["id"]}
            system += "\n本次公司评分政策优先于旧评分细则，但不能改变事实权限、证据引用和输出字段。"
            system += json.dumps(policy, ensure_ascii=False)
            if _changed_scoring(policy):
                system += f"\n输出额外字段company_policy_id，值必须为{policy['id']}；表示使用上述本次评分政策。"
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=json.dumps(facts, ensure_ascii=False, default=str),
            ),
        ]
        facts, messages = prepare_business_rules(runtime, facts, messages)
        pilot = battle_map_pilot_policy(runtime.settings, actor)
        if pilot is not None:
            evaluated = await InferenceService(
                self.database, runtime.settings,
                platform=filtered_facts_runtime(
                    self.database, runtime.settings, capability="battle_map_review",
                    block_requests=pilot.block_platform_requests,
                ),
            ).evaluate(
                actor=actor, mode="battle_map_review", facts=facts, messages=messages,
                user_text="请根据提供的客户事实评估潜力与关系，并返回对应评分和证据。",
                validate=lambda value: self._normalize_result(value, facts),
            )
            result = dict(evaluated.payload)
            lease = current_job_lease.get()
            # This trace is generated by our router, never trusted from model output.
            result["_inference_trace"] = {**evaluated.trace, "job_id": lease.job_id if lease else None}
            return result
        async def original(observer):
            client = SenseAudioClient(runtime.settings, observer=observer)
            try:
                return await client.chat_json(messages=messages, temperature=0.1)
            finally:
                await client.close()
        result, trace = await audit_original(
            database=self.database, actor=actor, mode="battle_map_review", facts=facts,
            model=runtime.settings.llm_model, invoke=original, execution_policy=runtime.settings.agent_execution_policy,
            validate=lambda value: self._normalize_result(value, facts),
        )
        result["_inference_trace"] = trace
        return result

    @staticmethod
    def _normalize_result(result: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
        policy = facts.get("company_policy")
        validated = validate_battle_map_result(
            result, facts, policy_id=policy["id"] if policy and _changed_scoring(policy) else None,
        )
        for key in ("potential_score", "relationship_score"):
            validated[key] = round(validated[key], 1)
        return {**validated, "evaluation_mode": "agent"}

    async def _persist(
        self,
        customer_id: str,
        actor: ActorContext,
        facts: dict[str, Any],
        result: dict[str, Any],
        trigger: dict[str, Any],
    ) -> None:
        potential = result["potential_score"]
        relationship = result["relationship_score"]
        policy = facts.get("company_policy")
        code = quadrant_code(potential, relationship, policy["definition"] if policy else None)
        snapshot = {
            "data_as_of": facts["data_as_of"],
            "customer_id": customer_id,
            "visit_ids": [item["id"] for item in facts["visits"]],
            "opportunity_ids": [item["id"] for item in facts["opportunities"]],
            "contact_ids": [item["id"] for item in facts["contacts"]],
            "previous_score": facts["previous_score"],
            "trigger": {
                "type": trigger.get("trigger_type"),
                "id": trigger.get("trigger_id"),
            },
            "evaluation_mode": result["evaluation_mode"],
            "summary": result["summary"],
            "rationale": result["rationale"],
            "company_policy": policy,
        }
        if "_inference_trace" in result:
            snapshot["inference_route"] = result["_inference_trace"]
        async with self.database.transaction(actor) as connection:
            # Serialize the private score even when no previous row exists. A
            # customer FOR UPDATE lock would incorrectly require commercial
            # UPDATE permission from an FDE who may read the customer's facts.
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                f"quadrant-score:{actor.workspace_id}:{customer_id}:{actor.user_id}",
            )
            visible_customer = await connection.fetchval(
                "SELECT id FROM crm.customer WHERE id = $1::uuid AND deleted_at IS NULL",
                customer_id,
            )
            if not visible_customer:
                raise LookupError("customer not found or outside review scope at result persistence")
            rule_set_id = policy["id"] if policy else await connection.fetchval(
                """
                SELECT id::text FROM config.rule_set
                 WHERE rule_code = $1 AND status = 'active'
                   AND effective_from <= clock_timestamp() AND effective_to > clock_timestamp()
                   AND (workspace_id IS NULL OR workspace_id = $2::uuid)
                 ORDER BY (workspace_id IS NOT NULL) DESC, version_no DESC
                 LIMIT 1
                """,
                QUADRANT_RULE_SET_CODE,
                actor.workspace_id,
            )
            if not rule_set_id:
                raise LookupError("active customer quadrant rule set not found")
            await connection.execute(
                """
                UPDATE insight.quadrant_score
                   SET valid_to = clock_timestamp()
                 WHERE customer_id = $1::uuid AND valid_to = 'infinity'
                   AND subject_user_ref_id=common.current_user_ref_id()
                """,
                customer_id,
            )
            await connection.execute(
                """
                INSERT INTO insight.quadrant_score (
                  workspace_id, customer_id, potential_score, relationship_score,
                  quadrant_code, rule_set_id, input_snapshot, evidence, subject_user_ref_id
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7::jsonb, $8::jsonb,common.current_user_ref_id())
                """,
                actor.workspace_id,
                customer_id,
                Decimal(str(potential)),
                Decimal(str(relationship)),
                code,
                rule_set_id,
                json.loads(json.dumps(snapshot, ensure_ascii=False, default=str)),
                result["evidence"],
            )
            if trigger.get("trigger_type") in {"opportunity.updated", "opportunity.created", "customer.updated"}:
                previous = facts.get("previous_score") or {}
                await connection.execute(
                    "SELECT workflow.complete_business_review($1::uuid,$2::jsonb)",
                    trigger.get("trigger_id"),
                    json.loads(
                        json.dumps(
                            {
                                "relationship_before": previous.get("relationship_score"),
                                "relationship_after": relationship,
                                "potential_before": previous.get("potential_score"),
                                "potential_after": potential,
                                "summary": result["summary"],
                                "evidence": result["evidence"],
                                "label": "AI评估建议；关系红灯阈值待确认",
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                )
            # FDE scores are private analysis, not a sales hierarchy broadcast.
            # V069's visit/actual-participant notifications already carry the
            # archived collaboration event. Keep the legacy sales fanout intact.
            if trigger.get("trigger_type") == "visit.archived" and actor.role not in {
                RoleCode.FDE, RoleCode.FDE_LEAD,
            }:
                actor_name = await connection.fetchval(
                    "SELECT display_name FROM platform.user_ref WHERE id = $1::uuid",
                    actor.user_id,
                )
                title = f"客户作战地图已更新：{facts['customer']['name']}"
                quadrant_label = {
                    "main_attack": "主攻区",
                    "customer_asset": "客户资产",
                    "order_driven": "见单打单",
                    "customer_resource": "客户资源",
                }[code]
                body = (
                    f"{actor_name or '销售'}已归档客户拜访，作战地图已重评为"
                    f"{quadrant_label}"
                    f"（潜力 {potential:g}，关系 {relationship:g}）。"
                )
                await connection.fetchval(
                    """
                    SELECT workflow.enqueue_battle_map_leader_notifications(
                      $1::uuid, $2::uuid, $3::uuid, $4, $5,
                      $6::numeric, $7::numeric, $8
                    )
                    """,
                    actor.workspace_id,
                    customer_id,
                    trigger.get("trigger_id"),
                    title,
                    body,
                    Decimal(str(potential)),
                    Decimal(str(relationship)),
                    code,
                )
            await record_job_effect(connection, actor.workspace_id)
