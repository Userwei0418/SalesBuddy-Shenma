"""Backend-owned per-capability rollout; pilots expire, production stays enabled."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class PilotPolicy:
    block_platform_requests: bool = False


def chatbi_pilot_policy(settings, actor, mode, *, now=None):
    if mode not in {"chatbi", "customer_chatbi"}:
        return None
    return _pilot_policy(settings, actor, "chatbi", now=now)


def battle_map_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "battle_map_review", now=now)


def opportunity_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "opportunity_draft", now=now)


def personal_risk_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "personal_risks", now=now)


def visit_quality_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "visit_quality", now=now)


def visit_entry_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "visit_entry", now=now)


def today_tasks_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "today_tasks", now=now)


def operating_report_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "operating_report", now=now)


def competency_review_pilot_policy(settings, actor, *, now=None):
    return _pilot_policy(settings, actor, "competency_review", now=now)


def business_advice_pilot_policy(settings, actor, capability, *, now=None):
    from sales_backend.domain.advice import ADVICE_LABELS

    return _pilot_policy(settings, actor, capability, now=now) if capability in ADVICE_LABELS else None


def _pilot_policy(settings, actor, capability, *, now=None):
    execution = getattr(settings, "agent_execution_policy", {})
    if execution.get("definition", {}).get("strategy") == "direct_only":
        return None
    # Neither a company budget nor an inherit switch expands the existing scope.
    # Neither user text nor request payload can activate the pilot.
    try:
        # An operator-owned file allows an atomic test switch without restarting
        # other users' in-flight jobs. It contains no credentials or model text.
        raw = settings.agent_fde_pilot_json
        if settings.agent_fde_pilot_path:
            with Path(settings.agent_fde_pilot_path).open(encoding="utf-8") as stream:
                raw = stream.read(64001)
        if not isinstance(raw, str) or len(raw) > 64000:
            return None
        config = json.loads(raw).get(actor.workspace_id, {})
        if capability != "chatbi":
            # The original flat allowlist ONLY enables ChatBI. Each new pilot needs
            # its own explicit allowlist, expiry and fault flag.
            config = config.get("capabilities", {}).get(capability, {})
        if config.get("enabled") is not True:
            return None
        if capability in {
            "battle_map_review",
            "opportunity_draft",
            "personal_risks",
            "visit_entry",
            "visit_quality",
            "today_tasks",
            "operating_report",
            "customer_advice",
            "opportunity_advice",
            "visit_advice",
            "competency_review",
        }:
            rollout = config.get("rollout", "pilot")
            if rollout == "production":
                # Production was explicitly enabled by the operator after
                # acceptance. Test fault injection is never carried into it.
                return PilotPolicy(block_platform_requests=False)
            if rollout != "pilot":
                return None
        users = config["user_ids"]
        if not isinstance(users, list) or not users or len(users) > 20:
            return None
        if any(not isinstance(user, str) or str(UUID(user)) != user for user in users):
            return None
        expires = datetime.fromisoformat(config["expires_at"])
        if expires.tzinfo is None or expires <= (now or datetime.now(UTC)):
            return None
        if actor.user_id not in users:
            return None
        return PilotPolicy(block_platform_requests=config.get("block_platform_requests") is True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
