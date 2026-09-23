from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any

from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, ChatMessage
from sales_backend.domain.business_time import BUSINESS_TIMEZONE, BUSINESS_TZ, localize_business_times
from sales_backend.domain.competency_review import (
    CONTRACT_VERSION,
    WINDOW_DAYS,
    framework_definitions,
    review_window,
    scored_review,
    validate_competency_result,
)
from sales_backend.repositories.competency_reviews import CompetencyReviewRepository
from sales_backend.services.agent_business_rules import prepare_business_rules, supplementary_prompt
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_platform.pilot import competency_review_pilot_policy
from sales_backend.services.runtime_config import load_runtime_configuration


class CompetencyReviewHandler:
    """Load self-scoped facts, evaluate either provider, then persist a validated review."""

    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings
        self.repository = CompetencyReviewRepository()

    async def handle(self, review_id: str, actor: ActorContext) -> None:
        review, framework = await self._load_and_start(review_id, actor)
        framework_definitions(framework)  # Bad configuration must not trigger paid provider calls.
        facts = await self._load_facts(actor, review)
        facts = {**facts, "contract_version": CONTRACT_VERSION, "framework": framework}
        runtime = await load_runtime_configuration(self.database, actor, self.settings, capability="competency_review")
        messages = self._messages(framework, facts, runtime.prompt_overrides.get("competency_review"))
        facts, messages = prepare_business_rules(runtime, facts, messages)
        pilot = competency_review_pilot_policy(runtime.settings, actor)
        platform = filtered_facts_runtime(
            self.database, runtime.settings, capability="competency_review",
            block_requests=pilot.block_platform_requests,
        ) if pilot else None
        settings = runtime.settings if pilot else replace(runtime.settings, agent_platform_bindings_json="{}")
        evaluated = await InferenceService(self.database, settings, platform=platform).evaluate(
            actor=actor, mode="competency_review", facts=facts, messages=messages,
            user_text="根据本人近30天拜访事实完成六维销售能力复盘。",
            # A competency review is not an agent.run. The worker job and the
            # saved inference operation receipt link this result without a false FK.
            validate=lambda result: validate_competency_result(result, framework, facts),
        )
        await self._persist(actor, review_id, framework, facts, evaluated.payload, evaluated.trace)

    async def _load_and_start(self, review_id, actor):
        async with self.database.transaction(actor) as connection:
            return await self.repository.start(connection, actor, review_id)

    async def _load_facts(self, actor, review):
        start, end = review_window(review["review_date"])
        async with self.database.transaction(actor, readonly=True) as connection:
            visits, prior = await self.repository.facts(connection, actor, review["review_date"], start, end)
        return localize_business_times({
            "subject_user_id": actor.user_id,
            "review_date": str(review["review_date"]),
            "window_days": WINDOW_DAYS,
            "window_start": start.isoformat(), "window_end_exclusive": end.isoformat(),
            "visit_count": len(visits), "visits": visits, "previous_review": prior,
            "data_as_of": datetime.now(BUSINESS_TZ).isoformat(),
            "business_timezone": BUSINESS_TIMEZONE,
        })

    @staticmethod
    def _messages(
        framework: dict[str, Any],
        facts: dict[str, Any],
        prompt_override: str | None = None,
    ) -> list[ChatMessage]:
        coaching_contract = (
            "你是一位资深企业级销售教练。只输出一个JSON对象，不要前言或Markdown。"
            "每天基于销售本人最近30天的真实拜访与跟进记录，"
            "按照给定六维能力模型完成证据化复盘。只能引用facts，禁止虚构拜访、客户或结果。"
            "每个维度评分0到100，缺失字段和行动不闭环应真实影响评分，但不能把数据缺失当作业务事实。"
            "你必须像资深销售而不是数据质检员一样给建议：围绕需求洞察、决策链经营、方案沟通、商机推进、客户关系、"
            "跟进执行六个能力维度逐项输出。每个coaching_action都要同时包含当前能力短板、可复用的销售工作方法，"
            "以及一个有数量或时限的下一步训练动作；不能只写‘补数据’、‘注意跟进’这类空泛建议。"
            "improvements必须严格输出6条，与六个维度一一对应，用‘维度名：方法+可验收动作’的结构表达。输出JSON："
            "{summary,strengths:[string],improvements:[string],dimensions:["
            "{code,score,assessment,evidence:[{visit_id,detail}],coaching_action}]}。"
            "code必须逐字使用框架定义且各出现一次；所有visit_id必须来自facts。"
            "没有支持证据时evidence输出空数组并明确证据不足，不编造客户事实。"
            "过去记录未展示执行结果不等于现在未完成，不据此认定违约或纪律问题；"
            "训练动作是建议，不新增公司审批、教练签字、服务承诺或客户已答应的条件。"
            "分数必须是有限数值，禁止null或字符串；strengths最多5条。"
            "不要输出整体总分或权重，服务端按已保存能力框架计算。"
        )
        system = supplementary_prompt(coaching_contract, prompt_override)
        user = json.dumps({**facts, "framework": framework}, ensure_ascii=False, default=str)
        return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]

    async def _persist(self, actor, review_id, framework, facts, result, inference_trace):
        values = scored_review(result, framework, facts)
        snapshot = {
            "review_id": review_id, "contract_version": CONTRACT_VERSION,
            "framework_version": framework["version_no"],
            "review_date": facts["review_date"], "window_days": facts["window_days"],
            "window_start": facts["window_start"], "window_end_exclusive": facts["window_end_exclusive"],
            "visit_count": facts["visit_count"], "visit_ids": sorted(row["visit_id"] for row in facts["visits"]),
            "data_as_of": facts["data_as_of"], "inference_route": inference_trace,
        }
        async with self.database.transaction(actor) as connection:
            await self.repository.save(
                connection, actor, review_id, values, snapshot, inference_trace["model_ref"]
            )
