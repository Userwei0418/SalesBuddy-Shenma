from __future__ import annotations

import uuid
from typing import Any

from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.repositories.attribute_overlay import overlay_risk_attributes, overlay_task_attributes
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.agent_run.materialize import (
    follow_up_task_drafts,
    risk_card,
    risk_drafts,
    task_card,
)
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.today_scope import (
    TodayScopeChanged,
    load_today_scope,
    recheck_follow_up_sources,
)
from sales_backend.services.tasks import validate_task_links


class AgentRunStore:
    """把一次运行的模型调用记录、产物、消息和终态写库。settings 按运行时配置注入。"""

    def __init__(self, database: Database, settings: Settings, *, today_tasks_scope=None):
        self.database = database
        self.settings = settings
        self.today_tasks_scope = today_tasks_scope

    async def persist_result(
        self, run: RunInput, result: dict[str, Any], facts: dict[str, Any],
        *, inference_trace: dict[str, Any] | None = None,
    ) -> None:
        is_chatbi = run.mode in {
            "chatbi",
            "customer_chatbi",
            "today_tasks",
            "personal_risks",
            "operating_report",
        }
        status = "succeeded" if is_chatbi else "waiting_human"
        artifact_id = None if is_chatbi else str(uuid.uuid4())
        missing = result.get("missing_fields") if isinstance(result.get("missing_fields"), list) else []
        async with self.database.transaction(run.actor) as connection:
            await require_agent_access(connection, run.actor, run.mode, run.customer_id,
                                       opportunity_id=run.opportunity_id,
                                       permission_version=run.permission_version)
            if run.actor.role.value in {"fde", "fde_lead"} and not run.permission_version:
                raise PermissionError("FDE analysis snapshot is missing its permission version")
            if run.surface == "fde_profile":
                from sales_backend.services.fde_profile import current_run_facts

                # A valid model response cannot outlive its business facts or
                # permission snapshot while the asynchronous request was running.
                await current_run_facts(connection, run)
            if run.mode == "today_tasks":
                if self.today_tasks_scope is not None:
                    await self.today_tasks_scope.recheck_before_write(
                        connection, run.actor, self.settings, facts.get("follow_up_candidates", []),
                    )
                elif load_today_scope(self.settings, run.actor) is not None:
                    # An operator added a restriction after this run loaded full
                    # facts. Do not persist those old unscoped candidates.
                    raise TodayScopeChanged()
                else:
                    await recheck_follow_up_sources(connection, run.actor, facts.get("follow_up_candidates", []))
                result = await self._materialize_today_tasks(connection, run, result, facts)
            elif run.mode == "personal_risks":
                result = await self._materialize_personal_risks(
                    connection, run, result, facts,
                    model_ref=inference_trace["model_ref"] if inference_trace is not None else self.settings.llm_model,
                )
            if facts.get("company_policy"):
                await connection.execute(
                    "UPDATE agent.run SET business_context=business_context || $2::jsonb WHERE id=$1::uuid",
                    run.run_id, {"company_policy": facts["company_policy"]},
                )
            if artifact_id:
                await connection.execute(
                    """
                    INSERT INTO agent.artifact (
                      id, workspace_id, conversation_id, run_id, artifact_type,
                      schema_code, schema_version, status, payload, evidence,
                      model_ref, created_by_user_ref_id
                    ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, 1,
                              $7, $8::jsonb, $9::jsonb, $10, $11::uuid)
                    """,
                    artifact_id,
                    run.actor.workspace_id,
                    run.conversation_id,
                    run.run_id,
                    run.mode,
                    f"{run.mode}.v1",
                    "pending_supplement" if missing else "pending_confirm",
                    result,
                    [{"source": "user_input", "run_id": run.run_id}],
                    inference_trace["model_ref"] if inference_trace is not None else self.settings.llm_model,
                    run.actor.user_id,
                )
            structured = {
                **result,
                "run_id": run.run_id,
                "artifact_id": artifact_id,
                "mode": run.mode,
                "data_as_of": facts["data_as_of"],
            }
            await connection.execute(
                """
                INSERT INTO agent.message (
                  workspace_id, conversation_id, sender_type, content_type,
                  text_content, structured_content, source_run_id
                ) VALUES ($1::uuid, $2::uuid, 'assistant', 'card', $3, $4::jsonb, $5::uuid)
                """,
                run.actor.workspace_id,
                run.conversation_id,
                result.get("summary") or result.get("title") or "已生成结果",
                structured,
                run.run_id,
            )
            await connection.execute(
                """
                UPDATE agent.run SET status = $2, completed_at = clock_timestamp(),
                  business_context = business_context || $3::jsonb
                WHERE id = $1::uuid
                """,
                run.run_id,
                status,
                {"artifact_id": artifact_id} if artifact_id else {"data_as_of": facts["data_as_of"]},
            )
            if inference_trace is not None:
                # The selected read-only pilot shares this transaction and stores
                # route metadata on the run, never in the user-facing result card.
                await connection.execute(
                    """UPDATE agent.run SET business_context = business_context || $2::jsonb,
                              model_ref = $3 WHERE id = $1::uuid""",
                    run.run_id, {"inference_route": inference_trace}, inference_trace["model_ref"],
                )
            await record_job_effect(connection, run.actor.workspace_id)

    async def _materialize_personal_risks(
        self,
        connection: Any,
        run: RunInput,
        model_result: dict[str, Any],
        facts: dict[str, Any],
        *,
        model_ref: str,
    ) -> dict[str, Any]:
        visits = {
            str(item.get("source_visit_id")): item for item in facts.get("visits", []) if item.get("source_visit_id")
        }
        written_keys: list[tuple[str, str]] = []
        for draft in risk_drafts(model_result, visits):
            visit = draft.visit
            row = await connection.fetchrow(
                """
                INSERT INTO insight.risk (
                  workspace_id, customer_id, opportunity_id, source_visit_id,
                  risk_type_code, title, description, severity_code, status,
                  owner_user_ref_id, owner_team_id, model_ref, input_snapshot,
                  evidence, due_at, source_code, suggested_action, agent_key, source_run_id
                ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid,
                          $5, $6, $7, $8, 'pending', $9::uuid, $10::uuid,
                          $11, $12::jsonb, $13::jsonb, $14, 'personal_risk_agent', $15, $16, $17::uuid)
                ON CONFLICT (workspace_id, owner_user_ref_id, source_visit_id, risk_type_code)
                  WHERE source_visit_id IS NOT NULL AND deleted_at IS NULL
                DO UPDATE SET
                  title = EXCLUDED.title,
                  description = EXCLUDED.description,
                  severity_code = EXCLUDED.severity_code,
                  model_ref = EXCLUDED.model_ref,
                  input_snapshot = EXCLUDED.input_snapshot,
                  evidence = EXCLUDED.evidence,
                  due_at = EXCLUDED.due_at,
                  source_code = EXCLUDED.source_code,
                  suggested_action = EXCLUDED.suggested_action,
                  agent_key = EXCLUDED.agent_key,
                  source_run_id = EXCLUDED.source_run_id,
                  version_no = insight.risk.version_no + 1
                WHERE insight.risk.status NOT IN ('resolved', 'accepted')
                RETURNING id::text, status, (xmax = 0) AS inserted
                """,
                run.actor.workspace_id,
                visit.get("customer_id"),
                visit.get("opportunity_id"),
                draft.source_visit_id,
                draft.risk_type,
                draft.title,
                draft.description,
                draft.severity,
                run.actor.user_id,
                run.actor.team_ids[0] if run.actor.team_ids else None,
                model_ref,
                {
                    "interaction_at": str(visit.get("interaction_at") or ""),
                    "expectation_code": visit.get("expectation_code"),
                    "follow_up_record": visit.get("follow_up_record"),
                    "source_next_action": visit.get("next_action"),
                    "next_action": draft.suggested_action or visit.get("next_action"),
                    "source": "personal_risk_agent",
                },
                [
                    {
                        "type": "visit",
                        "visit_id": draft.source_visit_id,
                        "detail": draft.evidence_detail,
                    },
                    {
                        "type": "follow_up",
                        "detail": visit.get("follow_up_record") or "跟进记录未补充",
                    },
                ],
                draft.due_at,
                draft.suggested_action,
                f"{draft.source_visit_id}:{draft.risk_type}",
                run.run_id,
            )
            if not row:
                continue
            await connection.execute(
                """
                INSERT INTO insight.risk_event (
                  workspace_id, risk_id, event_type, to_status,
                  actor_user_ref_id, payload
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)
                """,
                run.actor.workspace_id,
                row["id"],
                "detected" if row["inserted"] else "reanalyzed",
                row["status"],
                run.actor.user_id,
                {"run_id": run.run_id, "source_visit_id": draft.source_visit_id},
            )
            written_keys.append(draft.key)

        rows = await connection.fetch(
            """
            SELECT r.id::text AS risk_id, r.source_visit_id::text,
                   r.risk_type_code, r.title, r.description,
                   r.severity_code, r.due_at, r.opened_at,
                   c.name AS customer_name, r.attributes, r.import_meta,
                   r.source_code, r.suggested_action, r.agent_key, r.source_run_id
              FROM insight.risk r
              LEFT JOIN crm.customer c ON c.id = r.customer_id
             WHERE r.deleted_at IS NULL
               AND r.owner_user_ref_id = $1::uuid
               AND r.status IN ('new', 'pending', 'in_progress', 'escalated')
             ORDER BY CASE r.severity_code
                        WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3 ELSE 4 END,
                      r.opened_at DESC
            """,
            run.actor.user_id,
        )
        return risk_card(
            model_result,
            [overlay_risk_attributes(dict(row)) for row in rows],
            written_keys=written_keys,
            scope=facts.get("scope"),
        )

    async def _materialize_today_tasks(
        self,
        connection: Any,
        run: RunInput,
        model_result: dict[str, Any],
        facts: dict[str, Any],
    ) -> dict[str, Any]:
        # Incomplete customer associations stay actionable suggestions, never
        # silently become daily tasks or acquire a guessed opportunity.
        pending_candidates = []
        candidates = {}
        for item in facts.get("follow_up_candidates", []):
            if not item.get("source_id"):
                continue
            if not item.get("customer_id") or not item.get("opportunity_id"):
                pending_candidates.append({
                    "source_visit_id": str(item["source_id"]), "customer_id": item.get("customer_id"),
                    "customer_name": item.get("customer_name"), "description": item.get("next_action"),
                    "reason": "请从拜访建议选择客户下的商机后确认创建任务",
                })
                continue
            candidates[str(item["source_id"])] = item
        for draft in follow_up_task_drafts(model_result, candidates, schedule_policy=facts.get("company_policy")):
            candidate = draft.candidate
            source_id = draft.source_visit_id
            await validate_task_links(connection, run.actor, candidate.get("customer_id"), candidate.get("opportunity_id"), "customer")
            inserted_id = await connection.fetchval(
                """
                INSERT INTO workflow.task (
                  id, workspace_id, task_type, title, description, customer_id,
                  opportunity_id, source_visit_id, creator_user_ref_id, creator_team_id,
                  priority_code, status, due_at, source_code, agent_reason,
                  source_follow_up_record, source_interaction_at, association_kind
                ) VALUES ($1::uuid, $2::uuid, 'visit_follow_up', $3, $4, $5::uuid,
                          $6::uuid, $7::uuid, $8::uuid, $9::uuid, $10,
                          'pending_execution', $11, 'today_task_agent', $12, $13, $14::timestamptz, 'customer')
                ON CONFLICT (workspace_id, source_visit_id)
                  WHERE task_type = 'visit_follow_up'
                    AND source_visit_id IS NOT NULL
                    AND deleted_at IS NULL
                DO NOTHING
                RETURNING id::text
                """,
                str(uuid.uuid4()),
                run.actor.workspace_id,
                draft.title,
                draft.description,
                candidate.get("customer_id"),
                candidate.get("opportunity_id"),
                source_id,
                run.actor.user_id,
                run.actor.team_ids[0] if run.actor.team_ids else None,
                draft.priority,
                draft.due_at,
                None if draft.agent_reason in (None, "") else str(draft.agent_reason),
                candidate.get("follow_up_record"),
                candidate.get("interaction_at"),
            )
            if not inserted_id:
                continue
            await connection.execute(
                """
                INSERT INTO workflow.task_assignee (
                  task_id, workspace_id, assignee_user_ref_id, assignee_team_id,
                  assignee_role, responsibility
                ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 'sales', 'owner')
                """,
                inserted_id,
                run.actor.workspace_id,
                run.actor.user_id,
                run.actor.team_ids[0] if run.actor.team_ids else None,
            )
            await connection.execute(
                """
                INSERT INTO workflow.task_event (
                  workspace_id, task_id, event_type, to_status,
                  actor_user_ref_id, note, payload
                ) VALUES ($1::uuid, $2::uuid, 'created', 'pending_execution',
                          $3::uuid, '由今日待办Agent从已归档拜访的下一步行动生成', $4::jsonb)
                """,
                run.actor.workspace_id,
                inserted_id,
                run.actor.user_id,
                {"source_visit_id": source_id, "run_id": run.run_id, "company_policy": facts.get("company_policy")},
            )

        rows = await connection.fetch(
            """
            SELECT t.id::text AS task_id, t.task_type, t.title, t.description,
                   t.source_visit_id::text, t.priority_code, t.due_at,
                   c.name AS customer_name, creator.display_name AS creator_name,
                   t.attributes, t.import_meta, t.source_code, t.agent_reason,
                   t.source_follow_up_record, t.source_interaction_at, t.rejection_comment
              FROM workflow.task t
              JOIN workflow.task_assignee ta
                ON ta.task_id = t.id
               AND ta.assignee_user_ref_id = $1::uuid
               AND ta.responsibility = 'owner'
              LEFT JOIN crm.customer c ON c.id = t.customer_id
              LEFT JOIN platform.user_ref creator ON creator.id = t.creator_user_ref_id
             WHERE t.deleted_at IS NULL
               AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred')
             ORDER BY t.due_at, t.created_at DESC
            """,
            run.actor.user_id,
        )
        card = task_card(
            model_result,
            [overlay_task_attributes(dict(row)) for row in rows],
            scope=facts.get("scope"),
        )
        card["pending_candidates"] = pending_candidates
        return card
