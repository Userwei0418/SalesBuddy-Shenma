"""Frozen sales assessments: no paid calls below the evidence threshold."""
from copy import deepcopy
from dataclasses import replace

from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.competency_review import (
    CONTRACT_VERSION, framework_definitions, has_minimum_evidence,
    insufficient_competency_result, scored_review, validate_competency_result,
)
from sales_backend.repositories.competency_reviews import CompetencyReviewRepository
from sales_backend.services.agent_business_rules import prepare_business_rules
from sales_backend.services.agent_platform.authorization_snapshot import PermissionCheckedObserver
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_platform.pilot import competency_review_pilot_policy
from sales_backend.services.competency_prompt import build_competency_messages
from sales_backend.services.runtime_config import load_runtime_configuration


class FrozenCompetencyInference(InferenceService):
    """Use the shared router, with the same frozen-source fence on each dispatch."""
    def __init__(self, *args, assert_review, **kwargs):
        super().__init__(*args, **kwargs)
        self.assert_review = assert_review

    async def assert_actor_current(self, actor, permission):
        await super().assert_actor_current(actor, permission)
        await self.assert_review()


class CompetencyReviewHandler:
    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings
        self.repository = CompetencyReviewRepository()

    async def handle(self, review_id: str, actor: ActorContext) -> None:
        review, framework = await self._load_and_start(review_id, actor)
        framework_definitions(framework)
        facts = await self._load_facts(actor, review)
        facts = {**facts, "contract_version": CONTRACT_VERSION, "framework": framework,
                 "company_policy": deepcopy(review["input_snapshot"]["score_basis"]["score_policy"])}
        if not has_minimum_evidence(framework, facts):
            reason = "no_visits" if not facts.get("visits") else "below_minimum_visit_count"
            result = insufficient_competency_result(framework, reason_code=reason)
            await self._persist(actor, review_id, framework, facts, result,
                                {"provider": "deterministic", "model_ref": None, "reason_code": reason})
            return
        runtime = await load_runtime_configuration(self.database, actor, self.settings, capability="competency_review")
        snapshot = review["input_snapshot"]
        # Runtime supplies the currently authorized transport. Business judgment
        # and the exact text stay on the admitted generation across both routes.
        runtime = replace(runtime, business_policy=deepcopy(snapshot["business_rules"]))
        messages = self._messages(framework, facts, snapshot["prompt_override"]["text"])
        facts, messages = prepare_business_rules(runtime, facts, messages)
        pilot = competency_review_pilot_policy(runtime.settings, actor)
        platform = filtered_facts_runtime(
            self.database, runtime.settings, capability="competency_review",
            block_requests=pilot.block_platform_requests,
        ) if pilot else None

        async def assert_review():
            async with self.database.transaction(actor, readonly=True) as connection:
                await self.repository.assert_active(connection, actor, review_id)

        if platform:
            original_observer = platform.observer_factory
            platform.observer_factory = lambda *args, **kwargs: PermissionCheckedObserver(
                original_observer(*args, **kwargs), assert_review)
        settings = runtime.settings if pilot else replace(runtime.settings, agent_platform_bindings_json="{}")
        evaluated = await FrozenCompetencyInference(
            self.database, settings, platform=platform, assert_review=assert_review,
        ).evaluate(
            actor=actor, mode="competency_review", facts=facts, messages=messages,
            user_text="根据本代次已冻结的本人跟进事实完成六维销售能力复盘。",
            validate=lambda result: validate_competency_result(result, framework, facts),
        )
        await self._persist(actor, review_id, framework, facts, evaluated.payload, evaluated.trace)

    async def _load_and_start(self, review_id, actor):
        async with self.database.transaction(actor) as connection:
            return await self.repository.start(connection, actor, review_id)

    async def _load_facts(self, actor, review):
        async with self.database.transaction(actor, readonly=True) as connection:
            return await self.repository.frozen_facts(connection, actor, review["id"])

    _messages = staticmethod(build_competency_messages)

    async def _persist(self, actor, review_id, framework, facts, result, inference_trace):
        values = scored_review(result, framework, facts)
        metadata = {"review_id": review_id, "inference_route": inference_trace}
        async with self.database.transaction(actor) as connection:
            await self.repository.save(connection, actor, review_id, values, metadata, inference_trace["model_ref"])
