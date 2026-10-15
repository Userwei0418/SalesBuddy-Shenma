"""Exact legacy-value parsing only. Unknown meanings remain explicit conflicts."""

from uuid import UUID
from sales_backend.contracts.opportunity_initialization import InitializationValues
from sales_backend.contracts.opportunity_fields import SCALAR_FIELDS
from sales_backend.domain.opportunity_field_catalog import FIELD_CATALOG, ENUM_OPTIONS

SOURCE_KEYS = {key: (key, FIELD_CATALOG[key]["label"]) for key in sorted(SCALAR_FIELDS | {"primary_fde_id"})}
SOURCE_KEYS.update(fde_member_ids=("fde_member_ids", "FDE"), participants=("participants", "参与人员"))


def empty(value):
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(
        isinstance(v, dict) and isinstance(v.get("text"), str) and set(v) <= {"type", "text"} for v in value
    ):
        return "".join(v["text"] for v in value).strip()
    raise ValueError("旧字段结构无法明确解释，请人工核对")


def person_id(value):
    if isinstance(value, dict):
        if "user_id" in value and "id" in value and value["user_id"] != value["id"]:
            raise ValueError("旧人员标识相互矛盾，请人工选择")
        value = value.get("user_id", value.get("id"))
    if not isinstance(value, str):
        raise ValueError("旧人员缺少本系统UUID，禁止按姓名猜测")
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError("旧人员无法定位本系统UUID，请人工选择") from None


def parse(key, value):
    if key in {"primary_fde_id", "fde_member_ids"}:
        values = value if isinstance(value, list) else [value]
        if not values or (key == "primary_fde_id" and len(values) != 1):
            raise ValueError("旧主责不是唯一人员，请人工选择")
        result = [person_id(v) for v in values]
        if len(set(result)) != len(result):
            raise ValueError("旧人员重复，请人工核对")
        return result[0] if key == "primary_fde_id" else sorted(result)
    if key == "participants":
        if not isinstance(value, list) or any(not isinstance(v, dict) or set(v) != {"user_id", "role"} for v in value):
            raise ValueError("旧参与人员缺少明确职责，请人工确认")
        return sorted([{"user_id": person_id(v), "role": v["role"]} for v in value], key=lambda v: v["user_id"])
    if key == "is_key":
        if isinstance(value, bool):
            return value
        normalized = text(value).casefold()
        if normalized not in {"true", "false", "是", "否"}:
            raise ValueError("旧重点文本不是明确的是/否，请人工确认")
        return normalized in {"true", "是"}
    if key in ENUM_OPTIONS:
        mapping = {candidate: code for code, label in ENUM_OPTIONS[key] for candidate in (code, label)}
        values = (
            value
            if key == "customer_source" and isinstance(value, list) and all(isinstance(v, str) for v in value)
            else [text(value)]
        )
        if any(v not in mapping for v in values):
            raise ValueError("旧选项不在现有枚举中，请人工确认")
        converted = [mapping[v] for v in values]
        return sorted(set(converted)) if key == "customer_source" else converted[0]
    return text(value)


def suggestions(source, current):
    """Only approved leaves were projected by the repository; never expose raw records."""
    result = dict(values={}, sources={}, missing=[], conflicts=[])
    for key, aliases in SOURCE_KEYS.items():
        if not empty(current.get(key)):
            continue
        candidates = []
        for container in ("source_fields", "raw_fields"):
            for alias in aliases:
                value = source.get(container, {}).get(alias)
                if not empty(value):
                    candidates.append((f"{container}.{alias}", value))
        if not candidates:
            result["missing"].append(key)
            continue
        try:
            parsed = [parse(key, value) for _, value in candidates]
            if any(value != parsed[0] for value in parsed[1:]):
                raise ValueError("多个旧来源值不一致，请人工确认")
            value = InitializationValues.model_validate({key: parsed[0]}).model_dump(mode="json", exclude_unset=True)[
                key
            ]
        except (ValueError, TypeError):
            result["conflicts"].append(
                {
                    "field": key,
                    "code": "source_requires_review",
                    "source_fields": [path for path, _ in candidates],
                    "message": "旧值、类型或多个来源无法确定，请人工核对后选择",
                }
            )
            continue
        result["values"][key] = value
        result["sources"][key] = [path for path, _ in candidates]
    return result
