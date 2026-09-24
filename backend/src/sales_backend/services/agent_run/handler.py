from __future__ import annotations

from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.domain.follow_up_schedule import require_resolvable_follow_up_deadlines
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.agent_business_rules import prepare_business_rules
from sales_backend.services.agent_platform.audit import audit_original
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_platform.pilot import (
    chatbi_pilot_policy,
    operating_report_pilot_policy,
    opportunity_pilot_policy,
    personal_risk_pilot_policy,
    today_tasks_pilot_policy,
    visit_entry_pilot_policy,
    visit_quality_pilot_policy,
)
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.agent_run.contract import ensure_instant_summary_contract
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.agent_run.prompts import AgentPromptBuilder
from sales_backend.services.agent_run.today_scope import load_today_scope
from sales_backend.services.runtime_config import load_runtime_configuration


class AgentRunHandler:
    """一次 Agent 运行的编排：装载事实 → 拼提示词 → 调模型 → 校验 → 落库。

    协作者在 handle() 里按本次运行时配置构造，所以 settings 不会被中途改写。
    """

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.facts_loader = AgentFactsLoader(database)
        self.prompts = AgentPromptBuilder()

    async def handle(self, run_id: str, actor: ActorContext) -> None:
        run = await self._load_and_start(run_id, actor)
        runtime = await load_runtime_configuration(
            self.database,
            run.actor,
            self.settings,
            capability=run.capability,
        )
        scope = load_today_scope(runtime.settings, run.actor) if run.mode == "today_tasks" else None
        store = AgentRunStore(self.database, runtime.settings, today_tasks_scope=scope)
        facts = await self.facts_loader.load(run, today_tasks_scope=scope)
        if run.mode == "today_tasks":
            require_resolvable_follow_up_deadlines(facts)
        messages = self.prompts.build(run, facts, runtime.prompt_overrides.get(run.capability))
        facts, messages = prepare_business_rules(runtime, facts, messages)
        pilot = chatbi_pilot_policy(runtime.settings, run.actor, run.mode)
        capability = "chatbi"
        if run.mode == "opportunity_draft":
            pilot = opportunity_pilot_policy(runtime.settings, run.actor)
            capability = "opportunity_draft"
        elif run.mode == "personal_risks":
            pilot = personal_risk_pilot_policy(runtime.settings, run.actor)
            capability = "personal_risks"
        elif run.mode == "visit_entry":
            pilot = (visit_quality_pilot_policy if run.capability == "visit_quality" else visit_entry_pilot_policy)(
                runtime.settings, run.actor
            )
            capability = run.capability
        elif run.mode == "today_tasks":
            pilot = today_tasks_pilot_policy(runtime.settings, run.actor)
            capability = "today_tasks"
        elif run.mode == "operating_report":
            pilot = operating_report_pilot_policy(runtime.settings, run.actor)
            capability = "operating_report"
        if pilot is not None:
            evaluated = await InferenceService(
                self.database,
                runtime.settings,
                platform=filtered_facts_runtime(
                    self.database,
                    runtime.settings,
                    block_requests=pilot.block_platform_requests,
                    capability=capability,
                ),
            ).evaluate(
                actor=run.actor,
                mode=run.capability if run.mode == "visit_entry" else run.mode,
                facts=facts,
                messages=messages,
                user_text=run.text,
                run_id=run.run_id,
                surface=run.surface,
                validate=lambda result: validate_run_result(run, result, facts),
            )
            await store.persist_result(run, evaluated.payload, facts, inference_trace=evaluated.trace)
            return

        async def original(observer):
            client = SenseAudioClient(runtime.settings, observer=observer)
            try:
                return await client.chat_json(messages=messages, temperature=0.1)
            finally:
                await client.close()

        result, trace = await audit_original(
            database=self.database,
            actor=run.actor,
            mode=run.capability if run.mode == "visit_entry" else run.mode,
            facts=facts,
            run_id=run.run_id,
            model=runtime.settings.llm_model,
            invoke=original,
            execution_policy=runtime.settings.agent_execution_policy,
            validate=lambda value: self._validate_original(run, value, facts),
        )
        await store.persist_result(run, result, facts, inference_trace=trace)

    @staticmethod
    def _validate_original(run, result, facts):
        if run.mode == "operating_report":
            result = ensure_instant_summary_contract(run, result, facts)
        if run.mode == "opportunity_draft":
            from sales_backend.contracts.opportunity_candidate import validate_candidate

            try:
                result = validate_candidate(result, facts)
            except ValueError as exc:
                raise SenseAudioError("商机候选不符合输出契约，请重试", retryable=True) from exc
        if run.mode in {"visit_entry", "today_tasks"}:
            # The off switch still uses the original model and prompt; both
            # providers must pass the same object and display contract.
            try:
                result = validate_run_result(run, result, facts)
            except InvalidAgentResult as exc:
                label = "拜访审核" if run.mode == "visit_entry" else "今日待办"
                raise SenseAudioError(f"{label}结果不符合输出契约，请重试", retryable=True) from exc
        return result

    async def _load_and_start(self, run_id: str, claimed_actor: ActorContext) -> RunInput:
        async with self.database.transaction(claimed_actor) as connection:
            row = await connection.fetchrow(
                """
                SELECT r.id::text AS run_id, r.conversation_id::text,
                       r.identity_context, r.business_context,
                       m.text_content
                FROM agent.run r
                JOIN agent.message m ON m.id = r.trigger_message_id
                WHERE r.id = $1::uuid AND r.status IN ('queued', 'running')
                """,
                run_id,
            )
        if not row:
            raise LookupError("agent run not found or not runnable")
        identity = row["identity_context"]
        actor = ActorContext(
            workspace_id=identity["workspace_id"],
            user_id=identity["user_id"],
            role=RoleCode(identity["role"]),
            data_scope=DataScope(identity["data_scope"]),
            team_ids=tuple(identity.get("team_ids") or ()),
        )
        if actor != claimed_actor:
            raise PermissionError("job actor snapshot does not match agent run")
        business = row["business_context"] or {}
        async with self.database.transaction(actor) as connection:
            await require_agent_access(
                connection,
                actor,
                business.get("mode", "chatbi"),
                business.get("customer_id"),
                opportunity_id=business.get("opportunity_id"),
                permission_version=identity.get("permission_version"),
            )
            if not identity.get("permission_version"):
                raise PermissionError("Analysis snapshot is missing its permission version")
            await connection.execute(
                """
                UPDATE agent.run SET status = 'running', started_at = COALESCE(started_at, clock_timestamp()),
                  error_code = NULL, error_detail = NULL
                WHERE id = $1::uuid
                """,
                run_id,
            )
        business = row["business_context"] or {}
        return RunInput(
            run_id=row["run_id"],
            conversation_id=row["conversation_id"],
            text=row["text_content"],
            mode=str(business.get("mode") or "chatbi"),
            customer_id=business.get("customer_id"),
            actor=actor,
            permission_version=identity.get("permission_version"),
            opportunity_id=business.get("opportunity_id"),
            surface=business.get("surface"),
            profile_days=business.get("profile_days"),
            facts_fingerprint=business.get("facts_fingerprint"),
            visit_request=business.get("visit_request"),
        )
