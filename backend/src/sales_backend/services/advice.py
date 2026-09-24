"""Versioned advice generation and human decisions; no model-authorized business writes."""

from sales_backend.domain.advice import (
    ADVICE_CAPABILITIES,
    CONTRACT_VERSION,
    AdviceError,
    fingerprint,
    messages,
    prompt_text,
    require_advice_subject,
    validate_advice,
)
from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.advice import AdviceRepository
from sales_backend.repositories.advice_facts import AdviceFactsRepository
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.agent_business_rules import business_policy_metadata, prepare_business_rules
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService, binding_for
from sales_backend.services.agent_platform.pilot import business_advice_pilot_policy
from sales_backend.services.runtime_config import load_runtime_configuration
from sales_backend.services.tasks import TaskService


def configuration(runtime, actor, kind, section):
    capability = ADVICE_CAPABILITIES[kind]
    binding = binding_for(runtime.settings.agent_platform_bindings_json, actor.workspace_id, capability)
    return {
        "contract": CONTRACT_VERSION,
        "prompt_sha256": fingerprint(
            prompt_text(kind, section, runtime.prompt_overrides.get(capability, ""), actor_role=actor.role.value)
        ),
        "execution_policy": runtime.settings.agent_execution_policy,
        "business_policy": business_policy_metadata(getattr(runtime, "business_policy", None)),
        "direct_model": runtime.settings.llm_model,
        "platform_selected": business_advice_pilot_policy(runtime.settings, actor, capability) is not None,
        "binding": {
            "agent_id": binding.agent_id,
            "expected_snapshot_id": binding.snapshot_id,
            "execution_mode": binding.execution_mode,
        }
        if binding
        else None,
    }


def cache_key(actor, kind, subject_id, section, facts_digest, config, permission_version=None):
    identity = {**actor.model_dump(mode="json"), "team_ids": sorted(actor.team_ids)}
    if permission_version is not None:
        identity["permission_version"] = permission_version
    return fingerprint([identity, kind, str(subject_id), section, facts_digest, config])


def present(row, *, stale=False):
    return {
        "id": str(row["id"]),
        "subject_kind": row["subject_kind"],
        "subject_id": str(row["subject_id"]),
        "customer_id": str(row["customer_id"]),
        "opportunity_id": str(row["opportunity_id"]) if row["opportunity_id"] else None,
        "section": row["section"],
        "status": "superseded" if stale else row["status"],
        "summary": row["summary"] or "",
        "suggestions": row.get("suggestions", []),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "coverage": row["facts_snapshot"].get("coverage", {}),
        "facts_fingerprint": row["facts_fingerprint"],
        "contract_version": CONTRACT_VERSION,
        "error": "本次分析未完成，请重试" if row["status"] == "failed" else None,
    }


class AdviceService:
    def __init__(self, database, settings=None):
        self.database = database
        self.settings = settings or database.settings
        self.repo = AdviceRepository()
        self.facts = AdviceFactsRepository()

    async def runtime(self, actor, kind):
        require_advice_subject(kind, actor.role.value)
        return await load_runtime_configuration(
            self.database, actor, self.settings, capability=ADVICE_CAPABILITIES[kind]
        )

    async def assert_actor(self, connection, actor):
        current = await IdentityRepository().find_actor_by_id(
            connection,
            workspace_id=actor.workspace_id,
            user_id=actor.user_id,
            role=actor.role.value,
        )
        if not current or cache_key(current.context, "identity", "", "", "", {}) != (
            cache_key(actor, "identity", "", "", "", {})
        ):
            raise AdviceError("账号或数据权限已变化，请重新登录", 403)

    async def key_for(self, connection, actor, row, runtime):
        loaded = await self.facts.load(
            connection, actor, row["subject_kind"], str(row["subject_id"]), advice_id=str(row["id"])
        )
        config = configuration(runtime, actor, row["subject_kind"], row["section"])
        identity = await CapabilityRepository().analysis_identity(connection, actor)
        return cache_key(
            actor,
            row["subject_kind"],
            row["subject_id"],
            row["section"],
            loaded["fingerprint"],
            config,
            identity.get("permission_version"),
        )

    async def request(self, actor, request):
        runtime = await self.runtime(actor, request.subject_kind)
        config = configuration(runtime, actor, request.subject_kind, request.section)
        async with self.database.transaction(actor) as connection:
            await self.assert_actor(connection, actor)
            from sales_backend.services.authorization import require_permission

            await require_permission(connection, 'advice.request', **{request.subject_kind + '_id': request.subject_id})
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                cache_key(actor, request.subject_kind, request.subject_id, request.section, "serialize", {}),
            )
            # Keep a completed batch stable while its suggestions are being processed.
            # Its own unchanged task creations are excluded only for this batch. Other
            # records, permissions, configuration and subsequent task progress still expire it.
            latest = await connection.fetchrow(
                "SELECT * FROM insight.business_advice WHERE actor_user_ref_id=$1::uuid "
                "AND actor_role_code=$2 AND subject_kind=$3 AND subject_id=$4::uuid AND section=$5 "
                "AND status='succeeded' ORDER BY created_at DESC,id DESC LIMIT 1",
                actor.user_id,
                actor.role.value,
                request.subject_kind,
                request.subject_id,
                request.section,
            )
            if latest and await self.key_for(connection, actor, latest, runtime) == latest["cache_key"]:
                return present(await self.repo.get(connection, str(latest["id"])))
            loaded = await self.facts.load(connection, actor, request.subject_kind, request.subject_id)
            identity = await CapabilityRepository().analysis_identity(connection, actor)
            key = cache_key(
                actor,
                request.subject_kind,
                request.subject_id,
                request.section,
                loaded["fingerprint"],
                config,
                identity.get("permission_version"),
            )
            existing = await connection.fetchrow(
                "SELECT id::text,status,summary FROM insight.business_advice "
                "WHERE workspace_id=$1::uuid AND cache_key=$2",
                actor.workspace_id,
                key,
            )
            await connection.execute(
                "UPDATE insight.business_advice SET status='superseded' WHERE actor_user_ref_id=$1::uuid "
                "AND actor_role_code=$2 AND subject_kind=$3 AND subject_id=$4::uuid AND section=$5 "
                "AND cache_key<>$6 AND status IN ('queued','running','succeeded')",
                actor.user_id,
                actor.role.value,
                request.subject_kind,
                request.subject_id,
                request.section,
                key,
            )
            if existing:
                if existing["status"] == "superseded" and existing["summary"] is not None:
                    # Facts may return to an exact previously analyzed state. Reuse its immutable suggestions,
                    # including prior human decisions; do not duplicate model output or reset adoption.
                    await connection.execute(
                        "UPDATE insight.business_advice SET status='succeeded' WHERE id=$1::uuid",
                        existing["id"],
                    )
                    return present(await self.repo.get(connection, existing["id"]))
                if existing["status"] in {"failed", "superseded"} and request.retry:
                    await connection.execute(
                        "UPDATE insight.business_advice SET status='queued',error_code=NULL,completed_at=NULL "
                        "WHERE id=$1::uuid",
                        existing["id"],
                    )
                    await self.repo.enqueue(connection, actor, existing["id"])
                return present(await self.repo.get(connection, existing["id"]))
            subject = loaded["facts"]["subject"]
            analysis_id = await connection.fetchval(
                """INSERT INTO insight.business_advice(workspace_id,actor_user_ref_id,actor_role_code,subject_kind,
                subject_id,customer_id,opportunity_id,visit_id,section,cache_key,facts_fingerprint,
                configuration_fingerprint,identity_snapshot,facts_snapshot,configuration_snapshot)
                VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid,$6::uuid,$7::uuid,$8::uuid,$9,$10,$11,$12,$13::jsonb,
                $14::jsonb,$15::jsonb) RETURNING id::text""",
                actor.workspace_id,
                actor.user_id,
                actor.role.value,
                request.subject_kind,
                request.subject_id,
                loaded["customer_id"],
                request.subject_id if request.subject_kind == "opportunity" else subject.get("opportunity_id"),
                request.subject_id if request.subject_kind == "visit" else None,
                request.section,
                key,
                loaded["fingerprint"],
                fingerprint(config),
                identity,
                loaded["facts"],
                config,
            )
            await self.repo.enqueue(connection, actor, analysis_id)
            return present(await self.repo.get(connection, analysis_id))

    async def get(self, actor, analysis_id):
        async with self.database.transaction(actor, readonly=True) as connection:
            row = await self.repo.get(connection, analysis_id)
        runtime = await self.runtime(actor, row["subject_kind"])
        async with self.database.transaction(actor, readonly=True) as connection:
            await self.assert_actor(connection, actor)
            current = await self.key_for(connection, actor, row, runtime)
        return present(row, stale=current != row["cache_key"])

    async def decide(self, connection, actor, suggestion_id, decision, note, expected_version, task, runtime, *, tasks=None):
        suggestion = await connection.fetchrow(
            "SELECT * FROM insight.business_suggestion WHERE id=$1::uuid FOR UPDATE",
            suggestion_id,
        )
        if not suggestion:
            raise AdviceError("建议不存在或无权查看", 404)
        require_version(suggestion["version_no"], expected_version)
        if suggestion["decision"] != "pending":
            raise AdviceError("这条建议已处理，请刷新查看")
        row = await self.repo.get(connection, str(suggestion["advice_id"]))
        from sales_backend.services.authorization import require_permission

        await require_permission(connection, 'advice.decide', **{row['subject_kind'] + '_id': str(row['subject_id'])})
        await self.assert_actor(connection, actor)
        if row["status"] != "succeeded" or await self.key_for(connection, actor, row, runtime) != row["cache_key"]:
            raise AdviceError("相关资料或规则已变化，请先更新建议")
        if tasks is not None:
            from sales_backend.contracts.models import TaskBatchCreate
            if task is not None or decision != "adopted":
                raise AdviceError("建议处理方式不正确", 422)
            tasks = TaskBatchCreate(tasks=tasks).tasks
        selected_tasks = tasks if tasks is not None else ([task] if task else [])
        task_results = []
        if decision == "adopted":
            if not selected_tasks:
                raise AdviceError("请确认任务内容、接收人和截止时间", 422)
            for task in selected_tasks:
                if (task.customer_id and str(task.customer_id) != str(row["customer_id"])) or (
                    row["opportunity_id"] and task.opportunity_id and str(task.opportunity_id) != str(row["opportunity_id"])
                ):
                    raise AdviceError("任务关联对象必须与建议一致", 422)
                daily_visit = (
                    row["subject_kind"] == "visit" and not row["opportunity_id"]
                )
                if daily_visit and (task.association_kind != "daily" or task.customer_id or task.opportunity_id):
                    raise AdviceError("未关联商机的拜访建议应创建日常待办，不关联客户或商机", 422)
                if not daily_visit and task.association_kind == "daily":
                    raise AdviceError("经营建议必须创建客户任务，请选择该客户下的商机", 422)
                selected_opportunity = row["opportunity_id"] or task.opportunity_id
                if not daily_visit and not selected_opportunity:
                    raise AdviceError("请补充该客户下的商机，再确认创建客户任务", 422)
                task_result = await TaskService().create(
                    connection,
                    actor=actor,
                    description=task.description,
                    due_at=task.due_at,
                    priority_code=task.priority_code,
                    assignee_account_code=task.assignee_account_code,
                    target_position=task.target_position,
                    customer_id=None if daily_visit else str(row["customer_id"]),
                    opportunity_id=None if daily_visit else str(selected_opportunity),
                    source_suggestion_id=suggestion_id,
                    association_kind="daily" if daily_visit else "customer",
                )
                task_results.append(task_result)
        elif decision != "no_task" or selected_tasks:
            raise AdviceError("建议处理方式不正确", 422)
        task_result = task_results[0] if task_results else None
        await connection.execute(
            """UPDATE insight.business_suggestion SET decision=$2,decision_note=$3,decided_by_user_ref_id=$4::uuid,
            decided_at=clock_timestamp(),task_id=$5::uuid WHERE id=$1::uuid""",
            suggestion_id,
            decision,
            note,
            actor.user_id,
            task_result["id"] if task_result else None,
        )
        return {"suggestion_id": suggestion_id, "decision": decision, "task": task_result, "tasks": task_results}


class AdviceHandler:
    def __init__(self, database):
        self.database = database
        self.service = AdviceService(database)

    async def handle(self, analysis_id, actor):
        async with self.database.transaction(actor) as connection:
            row = await self.service.repo.get(connection, analysis_id, lock=True)
            if row["status"] in {"succeeded", "superseded"}:
                await record_job_effect(connection, actor.workspace_id)
                return
            await self.service.assert_actor(connection, actor)
            await connection.execute(
                "UPDATE insight.business_advice SET status='running' WHERE id=$1::uuid", analysis_id
            )
        runtime = await self.service.runtime(actor, row["subject_kind"])
        async with self.database.transaction(actor) as connection:
            latest = await self.service.repo.get(connection, analysis_id, lock=True)
            if (
                latest["status"] == "superseded"
                or await self.service.key_for(connection, actor, row, runtime) != row["cache_key"]
            ):
                await connection.execute(
                    "UPDATE insight.business_advice SET status='superseded' WHERE id=$1::uuid", analysis_id
                )
                await record_job_effect(connection, actor.workspace_id)
                return
        capability = ADVICE_CAPABILITIES[row["subject_kind"]]
        pilot = business_advice_pilot_policy(runtime.settings, actor, capability)
        platform = (
            filtered_facts_runtime(
                self.database, runtime.settings, capability=capability, block_requests=pilot.block_platform_requests
            )
            if pilot
            else None
        )
        from dataclasses import replace

        settings = runtime.settings if pilot else replace(runtime.settings, agent_platform_bindings_json="{}")
        # A historical facts snapshot is business material, not a policy source.
        # Strip the reserved root before constructing either provider's prompt.
        source_facts = {key: value for key, value in row["facts_snapshot"].items() if key != "agent_business_policy"}
        facts, prepared_messages = prepare_business_rules(
            runtime, source_facts, messages(
                row["subject_kind"], row["section"], source_facts, runtime.prompt_overrides.get(capability, "")
            ),
        )
        evaluated = await InferenceService(self.database, settings, platform=platform).evaluate(
            actor=actor,
            mode=capability,
            facts=facts,
            user_text="根据授权事实提出经营建议",
            messages=prepared_messages,
            validate=lambda value: validate_advice(value, row["facts_snapshot"]),
        )
        # Re-resolve both configuration and data after the external call. An obsolete result is never actionable.
        runtime = await self.service.runtime(actor, row["subject_kind"])
        async with self.database.transaction(actor) as connection:
            await self.service.assert_actor(connection, actor)
            latest = await self.service.repo.get(connection, analysis_id, lock=True)
            if latest["status"] in {"succeeded", "superseded"}:
                await record_job_effect(connection, actor.workspace_id)
                return
            current = await self.service.key_for(connection, actor, row, runtime)
            if current != row["cache_key"]:
                await connection.execute(
                    "UPDATE insight.business_advice SET status='superseded' WHERE id=$1::uuid", analysis_id
                )
            else:
                for i, suggestion in enumerate(evaluated.payload["suggestions"], 1):
                    await connection.execute(
                        """INSERT INTO insight.business_suggestion(
                        workspace_id,advice_id,ordinal,title,evidence,action,evidence_refs)
                        VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7::jsonb)""",
                        actor.workspace_id,
                        analysis_id,
                        i,
                        suggestion["title"],
                        suggestion["evidence"],
                        suggestion["action"],
                        suggestion["evidence_refs"],
                    )
                await connection.execute(
                    "UPDATE insight.business_advice SET status='succeeded',summary=$2,inference_trace=$3::jsonb,"
                    "completed_at=clock_timestamp() WHERE id=$1::uuid",
                    analysis_id,
                    evaluated.payload["summary"],
                    evaluated.trace,
                )
            await record_job_effect(connection, actor.workspace_id)
