from datetime import UTC, datetime, timedelta

from typing import Literal

from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
