"""Agent-first assessment with the original deterministic rules as the only fallback."""

from dataclasses import replace

from sales_backend.domain.opportunity_change import (
    TITLES,
    assessment_messages,
    rule_assessment,
    validate_assessment,
)
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.repositories.opportunity_changes import complete_change_review, review_is_pending
from sales_backend.services.agent_business_rules import prepare_business_rules, supplementary_prompt
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_platform.pilot import opportunity_pilot_policy
from sales_backend.services.runtime_config import load_runtime_configuration


class OpportunityChangeHandler:
    def __init__(self, database):
        self.database = database

    async def handle(self, event_id, actor, facts):
        async with self.database.transaction(actor, readonly=True) as connection:
            if not await review_is_pending(connection, event_id):
                return
        if facts["change"]["event_id"] != event_id:
            raise ValueError("change event snapshot mismatch")
        runtime = await load_runtime_configuration(
            self.database, actor, self.database.settings, capability="opportunity_draft",
        )
        policy = opportunity_pilot_policy(runtime.settings, actor)
        settings = runtime.settings
        if policy is None:
            settings = replace(settings, agent_platform_bindings_json="{}")
        platform = filtered_facts_runtime(
            self.database, settings, capability="opportunity_draft",
            block_requests=bool(policy and policy.block_platform_requests),
        )
        messages = assessment_messages(facts)
        messages[0] = messages[0].model_copy(update={"content": supplementary_prompt(
            messages[0].content, runtime.prompt_overrides.get("opportunity_draft"),
        )})
        facts, messages = prepare_business_rules(runtime, facts, messages)
        result = await InferenceService(self.database, settings, platform=platform).evaluate(
            actor=actor, mode="opportunity_change", facts=facts, messages=messages,
            user_text="评估这次已确认商机更新的变化方向及理由",
            validate=lambda value: validate_assessment(value, facts),
            rule_fallback=lambda: rule_assessment(facts),
        )
        review = {
            **result.payload, "status": "completed", "source": result.trace["provider"],
            "fallback_reason": result.trace["fallback_reason"], "operation_id": result.trace["operation_id"],
            "title": TITLES[result.payload["color"]],
        }
        async with self.database.transaction(actor) as connection:
            await complete_change_review(connection, event_id, review)
            await record_job_effect(connection, actor.workspace_id)
