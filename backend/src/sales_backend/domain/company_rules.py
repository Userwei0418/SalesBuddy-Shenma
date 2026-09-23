"""Typed company policies. Safety and workflow contracts are not editable rules."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sales_backend.domain.profile_scores import DIMENSION_LABELS, SCORE_LABELS, SCORE_MODELS


class FdeCapabilitiesPolicy(BaseModel):
    """FDE normally record their own visits; operational overrides never grant commercial writes."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    visit_entry_enabled: bool = True
    role_overrides: dict[Literal["fde", "fde_lead"], bool] = Field(default_factory=dict)
    user_overrides: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_user_ids(self):
        from uuid import UUID

        for value in self.user_overrides:
            if str(UUID(value)) != value:
                raise ValueError("例外账号必须使用有效用户ID")
        return self

    def enabled(self, role, user_id=None):
        if role not in {"fde", "fde_lead"}:
            return False
        return self.user_overrides.get(str(user_id), self.role_overrides.get(role, self.visit_entry_enabled))


class QuadrantPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    potential_threshold: int = Field(70, ge=1, le=99)
    relationship_threshold: int = Field(70, ge=1, le=99)
    inclusive: bool = True
    potential_guidance: str = Field(
        "依据明确需求、预算、商机金额、阶段/概率、预计成单时间、产品匹配和决策链综合评分。",
        min_length=1,
        max_length=3000,
    )
    relationship_guidance: str = Field(
        "依据拜访频次与时效、沟通达成度、客户互动意愿、下一步闭环、联系人层级与决策角色综合评分。",
        min_length=1,
        max_length=3000,
    )
    calibration_examples: str = Field("最新事实权重更高；无证据不臆测；所有证据必须引用本次可见记录。", max_length=3000)

    def classify(self, potential, relationship):
        high = potential >= self.potential_threshold if self.inclusive else potential > self.potential_threshold
        deep = (
            relationship >= self.relationship_threshold
            if self.inclusive
            else relationship > self.relationship_threshold
        )
        return (
            ("customer_asset" if deep else "main_attack") if high else ("customer_resource" if deep else "order_driven")
        )


class VisitAdmissionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    score_threshold: int = Field(60, ge=0, le=98)
    inclusive: bool = False
    good_score: int = Field(80, ge=1, le=99)
    excellent_score: int = Field(90, ge=2, le=100)
    scoring_guidance: str = Field(
        "以原文和两段正文的事实完整性、结论具体性、下一步可执行性综合判断，只输出总分0-100，不输出分项。"
        "不得因缺少拜访目标、行业、金额、伙伴、职位、协同人等选填项扣分。",
        min_length=1,
        max_length=3000,
    )
    calibration_examples: str = Field(
        "只有‘聊了聊、继续跟进’等泛泛描述通常低于40；有具体沟通但没有结果或可执行下一步通常不高于60；"
        "客户实际反馈和结论、明确时间及行动的记录通常80-95。",
        min_length=1,
        max_length=3000,
    )
    next_action_guidance: str = Field(
        "下一步必须含明确日期、截止时间或可执行时间范围，以及目标或行动；缺一则passed=false。",
        min_length=1,
        max_length=3000,
    )
    suggestion_count: int = Field(4, ge=1, le=6)

    @model_validator(mode="after")
    def grade_order(self):
        if not self.score_threshold < self.good_score < self.excellent_score:
            raise ValueError("必须满足准入分界 < 良好分数 < 优秀分数")
        return self

    def admits(self, score):
        return (
            type(score) is int
            and 0 <= score <= 100
            and (score >= self.score_threshold if self.inclusive else score > self.score_threshold)
        )

    def grade(self, score):
        if not self.admits(score):
            return "待完善"
        return "优秀" if score >= self.excellent_score else "良好" if score >= self.good_score else "合格"

    def requirement(self):
        return f"{'不低于' if self.inclusive else '高于'} {self.score_threshold} 分"


def visit_semantics_changed(snapshot):
    d = (snapshot or {}).get("definition") or {}
    baseline = VisitAdmissionPolicy().model_dump()
    return any(
        d.get(key, baseline[key]) != baseline[key]
        for key in (
            "scoring_guidance",
            "calibration_examples",
            "next_action_guidance",
        )
    )


class HomeDisplayPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    message_order: Literal["desc", "asc"] = "desc"


class TaskSchedulePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    timezone: Literal["UTC", "Asia/Shanghai"] = "UTC"
    today_at: str = Field("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    next_day_at: str = Field("02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    minimum_lead_minutes: int = Field(5, ge=1, le=120)

    def due(self, value, now=None):
        now = now or datetime.now(UTC)
        earliest = now + timedelta(minutes=self.minimum_lead_minutes)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)  # Preserve historical explicit naive-date handling.
            if parsed > earliest:
                return parsed.astimezone(UTC)
        except (ValueError, TypeError):
            pass
        local = now.astimezone(ZoneInfo(self.timezone))
        hour, minute = map(int, self.today_at.split(":"))
        today = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if today > earliest:
            return today.astimezone(UTC)
        hour, minute = map(int, self.next_day_at.split(":"))
        result = (local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        # A large configured lead time can span midnight; never create an already-due task.
        while result <= earliest:
            result += timedelta(days=1)
        return result.astimezone(UTC)


RULE_MODELS = {
    "fde_capabilities": FdeCapabilitiesPolicy,
    "customer_quadrant": QuadrantPolicy,
    "visit_admission": VisitAdmissionPolicy,
    "home_display": HomeDisplayPolicy,
    "task_schedule": TaskSchedulePolicy,
}
RULE_LABELS = {
    "fde_capabilities": "FDE自主拜访录入",
    "customer_quadrant": "客户作战地图：四象限与评分政策",
    "visit_admission": "拜访准入：评分与下一步审核",
    "home_display": "首页基础展示顺序",
    "task_schedule": "今日待办：默认未来执行时间",
}
RULE_FIELDS = {
    "fde_capabilities": [
        {"key": "visit_entry_enabled", "label": "默认允许FDE自主录入拜访", "type": "boolean"},
        {"key": "role_overrides", "label": "按岗位设置", "type": "capability_roles"},
        {"key": "user_overrides", "label": "例外账号设置", "type": "capability_users"},
    ],
    "customer_quadrant": [
        {"key": "potential_threshold", "label": "高潜力分界", "type": "number", "min": 1, "max": 99},
        {"key": "relationship_threshold", "label": "深关系分界", "type": "number", "min": 1, "max": 99},
        {"key": "inclusive", "label": "等于分界也计为高潜力 / 深关系", "type": "boolean"},
        {"key": "potential_guidance", "label": "潜力评分细则", "type": "textarea", "max": 3000},
        {"key": "relationship_guidance", "label": "关系评分细则", "type": "textarea", "max": 3000},
        {"key": "calibration_examples", "label": "证据要求与校准案例", "type": "textarea", "max": 3000},
    ],
    "visit_admission": [
        {"key": "score_threshold", "label": "质量准入分界", "type": "number", "min": 0, "max": 98},
        {"key": "inclusive", "label": "等于准入分界也可通过", "type": "boolean"},
        {"key": "good_score", "label": "良好起始分数", "type": "number", "min": 1, "max": 99},
        {"key": "excellent_score", "label": "优秀起始分数", "type": "number", "min": 2, "max": 100},
        {"key": "scoring_guidance", "label": "评分细则", "type": "textarea", "max": 3000},
        {"key": "calibration_examples", "label": "评分校准案例", "type": "textarea", "max": 3000},
        {"key": "next_action_guidance", "label": "下一步审核细则", "type": "textarea", "max": 3000},
        {"key": "suggestion_count", "label": "修改建议最多条数", "type": "number", "min": 1, "max": 6},
    ],
    "home_display": [
        {
            "key": "message_order",
            "label": "首页动态排列方式",
            "type": "select",
            "options": [["desc", "倒序：最新在上"], ["asc", "正序：最新在下"]],
        },
    ],
    "task_schedule": [
        {
            "key": "timezone",
            "label": "执行时间与日界时区",
            "type": "select",
            "options": [["UTC", "UTC（现有日界）"], ["Asia/Shanghai", "北京时间"]],
        },
        {"key": "today_at", "label": "当日默认执行时刻", "type": "time"},
        {"key": "next_day_at", "label": "当日已过时改用次日时刻", "type": "time"},
        {"key": "minimum_lead_minutes", "label": "至少预留分钟", "type": "number", "min": 1, "max": 120},
    ],
}


class AgentExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    strategy: Literal["inherit", "direct_only"] = "inherit"
    override_budget: bool = False
    platform_seconds: int = Field(12, ge=1, le=60)
    total_seconds: int = Field(45, ge=16, le=180)

    @model_validator(mode="after")
    def fallback_reserve(self):
        if self.total_seconds - self.platform_seconds < 15:
            raise ValueError("总预算须为原 API 接续至少保留15秒")
        return self


TECHNICAL_CAPABILITIES = {
    "visit_quality": "拜访记录质检",
    "customer_advice": "客户经营建议",
    "opportunity_advice": "商机经营建议",
    "visit_advice": "单次拜访建议",
    "battle_map_review": "客户作战地图评估",
    "opportunity_draft": "商机新建/更新判断",
    "personal_risks": "个人客户风险识别",
    "visit_entry": "拜访记录结构化",
    "today_tasks": "今日待办规划",
    "operating_report": "经营即时总结",
    "competency_review": "销售六维能力复盘",
    "chatbi": "经营问数（二期）",
}


class AgentBusinessPolicy(BaseModel):
    """Supplementary business judgement only; the output and authority contract stays in code."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    guidance: str = Field("", max_length=2000)
    calibration_examples: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def bounded_definition(self):
        import json

        # The existing rule publication function limits JSON to 16 KB, measured
        # in UTF-8 bytes rather than characters (including escaped characters).
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")) > 16000:
            raise ValueError("业务规则内容超过保存长度，请缩短说明或案例")
        return self


BUSINESS_RULES = frozenset("agent_business." + capability for capability in TECHNICAL_CAPABILITIES)
for capability, name in TECHNICAL_CAPABILITIES.items():
    code = "agent_business." + capability
    RULE_MODELS[code] = AgentBusinessPolicy
    RULE_LABELS[code] = name + "：业务判断规则"
    RULE_FIELDS[code] = [
        {"key": "guidance", "label": "业务判断补充指引", "type": "textarea", "max": 2000, "required": False},
        {"key": "calibration_examples", "label": "判断案例与说明", "type": "textarea", "max": 2000, "required": False},
    ]

TECHNICAL_RULES = frozenset("agent_execution." + capability for capability in TECHNICAL_CAPABILITIES)
for capability, name in TECHNICAL_CAPABILITIES.items():
    code = "agent_execution." + capability
    RULE_MODELS[code] = AgentExecutionPolicy
    RULE_LABELS[code] = name
    RULE_FIELDS[code] = [
        {
            "key": "strategy",
            "label": "调用方式",
            "type": "select",
            "options": [["inherit", "沿用已批准的中台接入范围"], ["direct_only", "关闭中台，使用原 API"]],
        },
        {"key": "override_budget", "label": "覆盖此能力的等待预算", "type": "boolean"},
        {"key": "platform_seconds", "label": "中台等待秒数（启用覆盖时）", "type": "number", "min": 1, "max": 60},
        {"key": "total_seconds", "label": "中台与接续总秒数（启用覆盖时）", "type": "number", "min": 16, "max": 180},
    ]


# Score policies reuse the same draft, publish, restore and immutable version lifecycle.
RULE_MODELS.update(SCORE_MODELS)
RULE_LABELS.update(SCORE_LABELS)
for score_code, score_model in SCORE_MODELS.items():
    RULE_FIELDS[score_code] = [
        {"key": key, "label": DIMENSION_LABELS[key] + "权重（%）", "type": "number", "min": 0, "max": 100}
        for key in score_model.model_fields
        if key != "schema_version"
    ]


def validate_policy(code, definition):
    if code not in RULE_MODELS:
        raise ValueError("该规则尚未接通执行器")
    return RULE_MODELS[code].model_validate(definition).model_dump(mode="json")


def policy_snapshot(code, row=None):
    definition = (row or {}).get("definition") or {}
    # Historical global seeds described dimensions but did not drive classification.
    # Preserve the existing 70/70 execution baseline until a typed version is published.
    if definition.get("schema_version") != 1:
        definition = {}
    return {
        "id": str(row["id"]) if row else None,
        "code": code,
        "version": row["version_no"] if row else 0,
        "definition": validate_policy(code, definition),
        "source": "company" if row and row["workspace_id"] else "baseline",
    }
