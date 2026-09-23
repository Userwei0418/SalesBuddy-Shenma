"""Resolve only unambiguous source deadlines; models cannot silently reschedule them.

This is a bounded calendar-expression reader, not an NLP task/assignee extractor.
Multiple deadlines, relative/approximate expressions and unsupported date syntax
require human confirmation instead of being converted to a convenient future date.
"""

import re
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sales_backend.domain.business_time import BUSINESS_TIMEZONE, BUSINESS_TZ, business_datetime
from sales_backend.domain.company_rules import TaskSchedulePolicy
from sales_backend.domain.model_contract import ModelContractError

DATE = re.compile(
    r"(?<!\d)(?:(?P<iso_year>\d{4})[-/](?P<iso_month>\d{1,2})[-/](?P<iso_day>\d{1,2})(?!\d)"
    r"|(?:(?P<cn_year>\d{4})年)?(?P<cn_month>\d{1,2})月(?P<cn_day>\d{1,2})(?:日|(?!\d)))"
    r"(?:[T\s]*(?P<hour>\d{1,2})(?:[:：](?P<minute>\d{2})|[点时](?:(?P<cn_minute>\d{1,2})分?)?)"
    r"(?::(?P<second>\d{2}))?(?P<offset>Z|[+-]\d{2}:\d{2})?)?"
)
# Calendar hints remaining after supported expressions are removed are unsafe to
# default. No attempt is made to pick a date from "next week" or "within 7 days".
CALENDAR_HINT = re.compile(
    r"\d{4}[-/年]|(?:\d{1,2}|[一二三四五六七八九十]+)月|\d{1,2}[-/]\d{1,2}|"
    r"(?:\d+|[零〇一二两三四五六七八九十百半]+)\s*(?:个\s*)?"
    r"(?:工作日|自然日|日|天|周|星期|月|小时|分钟)\s*(?:之?[内后前]|以[内后前])|"
    r"(?:\d{1,2}|[一二三四五六七八九十]+)号|"
    r"(?:今|明|后|当|次)天|(?:本|下|这|上)周|[两二]周(?:内|后|前)|星期|周[一二三四五六日天末]|"
    r"月底|月初|月中|下月|本月|年底|年初|明年|今年|\d{1,2}[:：]\d{2}|"
    r"(?:\d{1,2}|[一二三四五六七八九十]+)[点时]|"
    r"当天|当日|次日|UTC|GMT|美东|纽约|伦敦"
)
CLAUSE_SPLIT = re.compile(r"[；;。\n，,]")


class FollowUpDeadlineNeedsConfirmation(ValueError):
    """Nonretryable source-data precondition, with authorized UUIDs but no raw text."""

    def __init__(self, source_ids):
        self.source_ids = tuple(source_ids)
        references = []
        for value in source_ids[:5]:
            try:
                references.append(str(UUID(str(value))))
            except (ValueError, TypeError, AttributeError):
                pass
        detail = "；来源拜访：" + "、".join(references) if references else ""
        super().__init__(
            f"有{len(source_ids)}条拜访的下一步期限无法唯一确定，请人工确认日期后重新生成。"
            f"本次未生成任何待办{detail}。"
        )


def deadline_context(candidate, schedule_policy=None):
    """Return a deterministic, inspectable source-date contract for model facts."""
    policy = TaskSchedulePolicy(**((schedule_policy or {}).get("definition") or {}))
    text = str(candidate.get("next_action") or "").strip()
    owner = str(candidate.get("recorder_name") or "").strip()
    clauses = [part.strip() for part in CLAUSE_SPLIT.split(text) if part.strip()]
    own = [part for part in clauses if owner and re.match(
        rf"^(?:\d+[.、）)]\s*)?{re.escape(owner)}(?=于|在|将|需|应|负责|提交|发送|组织|安排|\s)", part
    )]
    # If the recorder has an explicit action clause, customer/FDE/joint deadlines
    # in other clauses are not this person's deadline. Multiple own dates remain
    # ambiguous; we never choose the first or earliest one.
    source = "；".join(own) if own else text
    matches = list(DATE.finditer(source))
    result = {"timezone": BUSINESS_TIMEZONE, "source_quote": source, "owner_explicit": bool(own)}
    residue = DATE.sub("", source)
    if CALENDAR_HINT.search(residue):
        return {**result, "status": "needs_confirmation", "reason": "unsupported_or_relative_deadline"}
    if not matches:
        # A named owner without a date must not inherit another person's deadline.
        if own or not CALENDAR_HINT.search(text):
            return {**result, "status": "missing"}
        return {**result, "status": "needs_confirmation", "reason": "deadline_owner_ambiguous"}
    dates = set()
    try:
        for match in matches:
            raw_year = match["iso_year"] or match["cn_year"]
            year = int(raw_year) if raw_year else business_datetime(candidate["interaction_at"]).year
            month = int(match["iso_month"] or match["cn_month"])
            day = int(match["iso_day"] or match["cn_day"])
            if match["hour"] is None:
                # Date-only deadlines use the existing configured execution clock,
                # converted to the business day. The model cannot invent a clock.
                hour, minute = map(int, policy.today_at.split(":"))
                policy_clock = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(policy.timezone))
                clock = policy_clock.astimezone(BUSINESS_TZ).time()
                due = datetime(year, month, day, clock.hour, clock.minute, tzinfo=BUSINESS_TZ)
                precision = "date"
            else:
                due = datetime(year, month, day, int(match["hour"]),
                               int(match["minute"] or match["cn_minute"] or 0),
                               int(match["second"] or 0), tzinfo=BUSINESS_TZ)
                if match["offset"]:
                    due = datetime.fromisoformat(due.replace(tzinfo=None).isoformat() +
                                                 match["offset"].replace("Z", "+00:00")).astimezone(BUSINESS_TZ)
                precision = "minute"
            dates.add((due, precision))
    except (ValueError, TypeError, KeyError):
        return {**result, "status": "needs_confirmation", "reason": "invalid_or_unanchored_deadline"}
    if len(dates) != 1:
        return {**result, "status": "needs_confirmation", "reason": "multiple_deadlines"}
    due, precision = dates.pop()
    return {**result, "status": "explicit", "due_at": due.isoformat(), "precision": precision}


def parse_model_due(value):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("offset required")
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ModelContractError("today_tasks.due_offset_or_format") from exc


def follow_up_due(candidate, value, schedule_policy=None, *, now=None):
    context = deadline_context(candidate, schedule_policy)
    if context["status"] == "needs_confirmation":
        raise ModelContractError("today_tasks.source_deadline_needs_confirmation")
    supplied = parse_model_due(value)
    if context["status"] == "explicit":
        expected = business_datetime(context["due_at"]).astimezone(UTC)
        if supplied is not None and supplied != expected:
            raise ModelContractError("today_tasks.due_conflicts_with_source")
        # A past explicit deadline stays past. It is not evidence of noncompletion
        # and must never be silently rescheduled to tomorrow.
        return expected
    if supplied is not None:
        raise ModelContractError("today_tasks.due_without_source")
    return TaskSchedulePolicy(**((schedule_policy or {}).get("definition") or {})).due(None, now=now)


def validate_today_task_dates(items, facts):
    """Validate proposed task dates for both providers, including omitted sources.

    Existing tasks are ranking references only. Their official dates come from
    the final database query and are neither copied from AI nor updated here.
    """
    indexed = {(item["source_type"], item["source_id"]): item for item in items}
    for candidate in facts.get("follow_up_candidates", []):
        if not str(candidate.get("next_action") or "").strip():
            continue
        item = indexed.get((candidate["source_type"], candidate["source_id"]), {})
        follow_up_due(candidate, item.get("due_at"), facts.get("company_policy"))


def require_resolvable_follow_up_deadlines(facts):
    """Whole-batch preflight: do not pay providers to resolve known ambiguity."""
    unresolved = [
        item["source_id"] for item in facts.get("follow_up_candidates", [])
        if deadline_context(item, facts.get("company_policy"))["status"] == "needs_confirmation"
    ]
    if unresolved:
        raise FollowUpDeadlineNeedsConfirmation(unresolved)
