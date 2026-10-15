"""Deterministic first-threshold proof over immutable typed opportunity events."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

THRESHOLD = 30


class OpportunityHistoryChanged(ValueError):
    code = "WORK_REPORT_SNAPSHOT_CHANGED"


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value.normalize())
    return value


def history_hash(events):
    return hashlib.sha256(
        json.dumps(
            canonical(events), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _probability(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid typed probability")
    number = Decimal(str(value))
    if not number.is_finite() or number != number.to_integral_value() or not 0 <= number <= 100:
        raise ValueError("invalid typed probability")
    return int(number)


def _state(event, prefix):
    return (_probability(event[prefix + "_probability"]), event[prefix + "_status"], event[prefix + "_stage"])


def first_ge30(opportunity, events):
    """Never infer a past threshold from current probability or created_at alone.

    A complete, never-reached history is distinguishable from missing history by
    its reason and completeness even though the existing public enum is unknown.
    """
    result = dict(
        threshold=THRESHOLD,
        status="unknown",
        event_id=None,
        occurred_at=None,
        entry_kind=None,
        before_probability=None,
        after_probability=None,
        history_origin="unknown",
        trustworthy_from=None,
        complete_from_origin=False,
        reason_codes=[],
    )
    if not events:
        return result | {"reason_codes": ["threshold_history_incomplete", "threshold_origin_unknown"]}
    try:
        ordered = sorted(events, key=lambda e: (e["after_version"], str(e["id"])))
        first = ordered[0]
        if first["history_origin"] != "native_creation" or first["mutation_kind"] != "insert":
            return result | {"trustworthy_from": first["occurred_at"], "reason_codes": ["threshold_origin_unknown"]}
        if (
            first["after_version"] != 1
            or first["before_version"] is not None
            or _state(first, "before") != (None, None, None)
            or first["source_kind"] != "native_application"
            or first["occurred_at"] != first["opportunity_created_at"]
        ):
            raise ValueError("missing origin state")
        previous = None
        seen = set()
        for event in ordered:
            if str(event["id"]) in seen:
                raise ValueError("duplicate history event")
            seen.add(str(event["id"]))
            if (
                type(event["after_version"]) is not int
                or event["after_version"] < 1
                or event["source_version"] != event["after_version"]
                or not isinstance(event["occurred_at"], datetime)
                or event["occurred_at"].utcoffset() is None
                or event["trustworthy_from"] != first["occurred_at"]
                or event["source_kind"] == "historical_import"
            ):
                raise ValueError("invalid typed event metadata")
            if (str(event["workspace_id"]), str(event["opportunity_id"])) != (
                str(opportunity["workspace_id"]),
                str(opportunity["id"]),
            ):
                raise ValueError("wrong history subject")
            if event["history_origin"] != "native_creation" or event["complete_from_origin"] is not True:
                raise ValueError("untrusted or discontinuous history")
            if str(event["origin_event_id"]) != str(first["id"]):
                raise ValueError("changed origin")
            if event["opportunity_created_at"] != first["opportunity_created_at"]:
                raise ValueError("changed creation time")
            _state(event, "after")
            if previous is not None and (
                event["mutation_kind"] != "update"
                or event["before_version"] != previous["after_version"]
                or event["after_version"] != previous["after_version"] + 1
                or _state(event, "before") != _state(previous, "after")
                or event["occurred_at"] < previous["occurred_at"]
            ):
                raise ValueError("discontinuous typed transition")
            previous = event
        if (
            previous["after_version"] != opportunity["version_no"]
            or _state(previous, "after")
            != (_probability(opportunity["probability"]), opportunity["status"], opportunity["stage_code"])
            or first["opportunity_created_at"] != opportunity["created_at"]
        ):
            raise ValueError("current head is not covered")
        result.update(
            history_origin="native_creation", trustworthy_from=first["occurred_at"], complete_from_origin=True
        )
        # Unknown probability before the first qualifying value cannot prove it
        # was previously below the threshold. Explicit NULL is never zero.
        for event in ordered:
            probability = _probability(event["after_probability"])
            if probability is None:
                return result | {"complete_from_origin": False, "reason_codes": ["threshold_probability_unknown"]}
            if probability >= THRESHOLD:
                return result | dict(
                    status="proven",
                    event_id=str(event["id"]),
                    occurred_at=event["occurred_at"],
                    entry_kind="created_at_or_above" if event is first else "crossed_from_below",
                    before_probability=_probability(event["before_probability"]),
                    after_probability=probability,
                    reason_codes=[],
                )
        return result | {"reason_codes": ["threshold_not_reached"]}
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return result | {
            "history_origin": "unknown",
            "complete_from_origin": False,
            "reason_codes": ["threshold_history_incomplete"],
        }


def freeze_history_guard(opportunity, events, threshold):
    ordered = sorted(events, key=lambda e: (e["after_version"], str(e["id"])))
    first = next((e for e in ordered if str(e["id"]) == threshold.get("event_id")), None)
    return dict(
        opportunity_id=str(opportunity["id"]),
        current_version=opportunity["version_no"],
        event_count=len(ordered),
        head_event_id=str(ordered[-1]["id"]) if ordered else None,
        history_sha256=history_hash(ordered),
        first_event_id=str(first["id"]) if first else None,
        first_event_version=first["after_version"] if first else None,
    )
