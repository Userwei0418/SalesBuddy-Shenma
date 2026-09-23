"""Read-only business creation dates; import timestamps remain audit facts."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def creation_date_projection(alias="o"):
    """Project only approved V3 date provenance, never whole imported records."""
    if alias != "o":
        raise ValueError("Unknown internal opportunity alias")
    return """jsonb_build_object(
      'created_at',o.created_at,
      'historical',COALESCE(o.import_meta->>'import_type'='crm_history',false),
      'source_created',o.import_meta->'source_fields'->'商机创建时间',
      'legacy_created',o.import_meta->'raw_fields'->'商机创建时间')"""


def business_created_on(fact):
    """Unknown historical dates never become the date of the import operation.

    V3 source fields take precedence over the earlier import's raw fields. A
    present but invalid newer source date stays unknown instead of silently
    taking a different older date. Naive source dates are Shanghai business dates.
    """
    value = fact.get("created_at")
    if fact.get("historical"):
        value = fact.get("source_created")
        if value is None or (isinstance(value, str) and not value.strip()):
            value = fact.get("legacy_created")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(raw, "%Y/%m/%d")
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is not None:
        try:
            parsed = parsed.astimezone(SHANGHAI)
        except (OverflowError, ValueError):
            return None
    return parsed.date()
