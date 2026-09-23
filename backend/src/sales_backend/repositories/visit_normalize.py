from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

QUADRANT_NAMES = {
    "main_attack": "主攻区",
    "customer_asset": "客户资产",
    "order_driven": "见单打单",
    "customer_resource": "客户资源",
}

CONTACT_ROLE_CODES = {
    "决策者": "decision_maker",
    "影响者": "influencer",
    "使用者": "user",
    "decision_maker": "decision_maker",
    "influencer": "influencer",
    "user": "user",
}

CONTACT_ROLE_ALIASES = {
    "董事长": "决策者", "总裁": "决策者", "ceo": "决策者", "coo": "决策者",
    "cio": "决策者", "cto": "决策者", "cfo": "决策者", "cxo": "决策者",
    "总经理": "影响者", "gm": "影响者", "总监": "影响者", "部门负责人": "影响者",
    "架构师": "影响者", "专家": "影响者", "一线": "使用者", "工程师": "使用者",
    "业务": "使用者", "开发": "使用者", "运维": "使用者", "数据人员": "使用者",
}

VISIT_CHOICE_LABELS = {
    "lead_source": {
        "self_developed": "自拓", "自拓": "自拓", "自主拓展": "自拓", "销售自拓": "自拓",
        "company_lead": "公司线索", "公司线索": "公司线索", "公司分配": "公司线索",
        "inbound线索": "公司线索", "inbound": "公司线索",
        "referral": "转介绍", "转介绍": "转介绍", "客户转介绍": "转介绍",
        "partner": "合作伙伴", "合作伙伴": "合作伙伴",
    },
    "contact_category": {
        "end_customer": "最终客户", "最终客户": "最终客户", "终端客户": "最终客户",
        "最终用户": "最终客户", "客户高层": "最终客户", "客户管理层": "最终客户",
        "platinum_partner": "白金伙伴", "白金伙伴": "白金伙伴",
        "gold_partner": "金牌伙伴", "金牌伙伴": "金牌伙伴",
        "opportunity_partner": "商机伙伴", "商机伙伴": "商机伙伴", "合作伙伴": "商机伙伴",
        "isv": "商机伙伴", "si": "商机伙伴",
    },
    "interaction_mode": {
        "offline_meeting": "线下会议", "线下会议": "线下会议", "线下拜访": "线下会议",
        "现场拜访": "线下会议", "上门拜访": "线下会议",
        "online_meeting": "线上会议", "线上会议": "线上会议", "线上": "线上会议",
        "视频会议": "线上会议",
        "phone_voice": "电话/语音", "电话/语音": "电话/语音", "电话": "电话/语音", "语音": "电话/语音",
        "social_meal": "饭局/聚会", "饭局/聚会": "饭局/聚会", "饭局聚会": "饭局/聚会", "饭局": "饭局/聚会",
    },
    "expectation_met": {
        "exceeded": "超出100%", "超出100%": "超出100%", "超预期": "超出100%",
        "met": "达成100%", "达成100%": "达成100%", "100%达成": "达成100%",
        "达成预期": "达成100%", "是": "达成100%",
        "met_50_100": "达成50-100%", "达成50-100%": "达成50-100%",
        "met_30_50": "达成30-50%", "达成30-50%": "达成30-50%",
        "met_10_30": "达成10-30%", "达成10-30%": "达成10-30%",
        "not_met": "未达成", "未达成": "未达成", "否": "未达成",
    },
}

VISIT_CHOICE_CODES = {
    "interaction_mode": {
        "线下会议": "offline_meeting", "线上会议": "online_meeting",
        "电话/语音": "phone_voice", "饭局/聚会": "social_meal",
    },
    "expectation_met": {
        "超出100%": "exceeded", "达成100%": "met", "达成50-100%": "met_50_100",
        "达成30-50%": "met_30_50", "达成10-30%": "met_10_30", "未达成": "not_met",
    },
}


def _amount(value: Any) -> Decimal:
    text = re.sub(r"[,，￥¥\s]", "", str(value or "").strip())
    multiplier = Decimal("1")
    for suffix, factor in (("亿元", "100000000"), ("亿", "100000000"), ("万元", "10000"), ("万", "10000"), ("元", "1")):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = Decimal(factor)
            break
    try:
        amount = Decimal(text) * multiplier
    except InvalidOperation as exc:
        raise ValueError("预计金额格式不正确") from exc
    if amount <= 0:
        raise ValueError("预计金额必须大于0")
    return amount.quantize(Decimal("0.01"))


def _duration_minutes(value: Any) -> int:
    text = str(value or "").strip()
    hours = [Decimal(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*(?:小时|h)", text, re.I)]
    minutes = [Decimal(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*(?:分钟|min)", text, re.I)]
    duration_value = sum(hours, Decimal("0")) * 60 + sum(minutes, Decimal("0"))
    if not duration_value:
        numbers = [Decimal(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
        duration_value = max(numbers) if numbers else Decimal("0")
    if not duration_value:
        raise ValueError("沟通时长格式不正确")
    duration = int(duration_value.quantize(Decimal("1")))
    return max(1, min(duration, 1440))


def _interaction_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        chinese = re.fullmatch(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日?"
            r"(?:\s*(上午|下午|晚上|中午)?\s*(\d{1,2})(?:[:点时](\d{1,2}))?分?)?",
            text,
        )
        if chinese:
            year, month, day = (int(chinese.group(index)) for index in range(1, 4))
            meridiem = chinese.group(4)
            hour = int(chinese.group(5) or 0)
            minute = int(chinese.group(6) or 0)
            if meridiem in {"下午", "晚上"} and hour < 12:
                hour += 12
            elif meridiem == "中午" and hour < 11:
                hour += 12
            elif meridiem == "上午" and hour == 12:
                hour = 0
            return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=8)))
        text = text.replace(" ", "T")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("拜访及沟通日期格式不正确") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed


def _normalize_visit_choice(key: str, value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    compact = re.sub(r"\s+", "", text).lower()
    choices = VISIT_CHOICE_LABELS[key]
    normalized = choices.get(text) or choices.get(text.lower()) or choices.get(compact)
    partner_pattern = r"伙伴|渠道|\bsi\b|俏皮|信息侠|联邦云|连邦云|道汇"
    if not normalized and key == "lead_source" and re.search(partner_pattern, text, re.I):
        normalized = "合作伙伴"
    if not normalized and key == "contact_category" and re.search(r"伙伴|渠道|\bisv\b|\bsi\b", text, re.I):
        normalized = "商机伙伴"
    if not normalized:
        field_names = {
            "lead_source": "客户/线索来源", "contact_category": "拜访对象类别",
            "interaction_mode": "拜访及沟通方式", "expectation_met": "预期达成情况",
        }
        allowed = "、".join(dict.fromkeys(choices.values()))
        raise ValueError(f"{field_names[key]}格式不正确，请选择：{allowed}")
    return normalized


def _normalize_contact_role(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if text in CONTACT_ROLE_CODES:
        return text
    normalized = CONTACT_ROLE_ALIASES.get(text) or CONTACT_ROLE_ALIASES.get(text.lower())
    if not normalized:
        for alias, role in CONTACT_ROLE_ALIASES.items():
            if alias.lower() in text.lower():
                normalized = role
                break
    if not normalized:
        raise ValueError("联系人角色必须是决策者、影响者或使用者")
    return normalized


def _review_next_action(value: Any) -> dict[str, Any]:
    """Apply the non-negotiable next-action rules to the current submitted text."""
    text = " ".join(str(value or "").strip().split())
    absolute_date = re.search(
        r"(?:20\d{2}[年./-]\d{1,2}(?:月|[./-])\d{1,2}日?|\d{1,2}月\d{1,2}日)",
        text,
    )
    relative_deadline = re.search(
        r"(?:今天|今日|明天|明日|后天|本周|下周|本月|下月|月底|"
        r"周[一二三四五六日天])(?:[^，。；;]{0,12}(?:前|内|上午|下午|晚上|\d{1,2}(?::\d{2}|点)))?",
        text,
    )
    time_found = bool(absolute_date or relative_deadline)
    plan_found = len(text) >= 10 and bool(
        re.search(
            r"目标|计划|负责|组织|安排|提交|完成|确认|验证|演示|测试|拜访|沟通|"
            r"报价|交付|输出|形成|获得|推进|跟进|协调|解决|签署|上线|验收|复盘|提供",
            text,
        )
    )
    suggestions = []
    if not time_found:
        suggestions.append("补充明确日期、截止时间或可执行时间范围")
    if not plan_found:
        suggestions.append("补充责任人、具体目标或可执行行动计划")
    return {
        "passed": time_found and plan_found,
        "time_found": time_found,
        "goal_or_plan_found": plan_found,
        "suggestions": suggestions,
    }
