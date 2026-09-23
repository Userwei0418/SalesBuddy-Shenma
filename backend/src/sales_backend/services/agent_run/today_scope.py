"""Optional operator-owned narrowing for today's scoped acceptance, never write authority.

Absent configuration leaves ordinary business behavior unchanged. A configured
scope that expires, changes or cannot be verified fails closed, including when
the platform route is off. Scope hashes and permissions never enter model facts.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sales_backend.integrations.senseaudio import SenseAudioError

SOURCE_FIELDS = (
    "source_id",
    "source_type",
    "customer_id",
    "opportunity_id",
    "customer_name",
    "follow_up_record",
    "next_action",
    "interaction_at",
)


def source_fingerprint(item):
    def scalar(value):
        return value.isoformat() if isinstance(value, datetime) else str(value)

    source = {field: item.get(field) for field in SOURCE_FIELDS}
    instant = source["interaction_at"]
    if isinstance(instant, str):
        try:
            instant = datetime.fromisoformat(instant.replace("Z", "+00:00"))
        except ValueError:
            pass
    if isinstance(instant, datetime) and instant.tzinfo is not None and instant.utcoffset() is not None:
        # Canonicalize only an explicitly known instant. Existing approval
        # hashes created from PostgreSQL's UTC datetime stay unchanged; a
        # presentation timezone must not invalidate the same source record.
        source["interaction_at"] = instant.astimezone(UTC).isoformat()
    return hashlib.sha256(
        json.dumps(
            source,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=scalar,
        ).encode()
    ).hexdigest()


class TodayScopeChanged(SenseAudioError):
    def __init__(self):
        super().__init__("今日待办联调范围已失效或来源已变化，请重新核验", retryable=False)


class TodaySourceChanged(TodayScopeChanged):
    def __init__(self):
        SenseAudioError.__init__(self, "拜访来源已变更或不可访问，请确认当前记录后重新生成待办", retryable=False)


async def recheck_follow_up_sources(connection, actor, candidates):
    """Lock and compare the authorized source snapshot on every materialization.

    This protects ordinary runs as well as operator-narrowed acceptance. The
    optional scope governs additional narrowing, never the core write boundary.
    """
    if not candidates:
        return []
    expected = {item.get("source_id"): source_fingerprint(item) for item in candidates}
    if None in expected or len(expected) != len(candidates):
        raise TodaySourceChanged()
    rows = await connection.fetch(
        """
        SELECT v.id::text AS source_id, 'visit_follow_up' AS source_type,
               v.customer_id::text, v.opportunity_id::text, c.name AS customer_name,
               v.follow_up_record, v.next_action, v.interaction_at
          FROM activity.visit v
          JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
         WHERE v.id=ANY($1::uuid[]) AND v.recorder_user_ref_id=$2::uuid
           AND v.workspace_id=$3::uuid
                   AND v.follow_up_task_mode='legacy'
           AND v.deleted_at IS NULL AND c.deleted_at IS NULL
           AND v.status IN ('confirmed','archived')
           AND v.interaction_at >= clock_timestamp() - interval '90 days'
         FOR SHARE OF v,c
        """,
        list(expected),
        actor.user_id,
        actor.workspace_id,
    )
    if len(rows) != len(expected) or any(source_fingerprint(row) != expected.get(row["source_id"]) for row in rows):
        raise TodaySourceChanged()
    return [dict(row) for row in rows]


def load_today_scope(settings, actor, *, now=None):
    if not settings.agent_today_tasks_acceptance_path and not settings.agent_today_tasks_acceptance_target:
        return None
    try:
        target = settings.agent_today_tasks_acceptance_target.split(":")
        if len(target) != 2 or any(str(UUID(part)) != part for part in target):
            raise ValueError("scope target invalid")
        if target != [actor.workspace_id, actor.user_id]:
            # Validate the exact operator-selected identity before file I/O:
            # a missing/expired scope must not disrupt other users' tasks.
            return None
        with Path(settings.agent_today_tasks_acceptance_path).open(encoding="utf-8") as stream:
            raw = stream.read(64001)
        if len(raw) > 64000:
            raise ValueError("scope too large")
        config = json.loads(raw)
        item = config.get(actor.workspace_id, {}).get(actor.user_id)
        if item is None:
            raise ValueError("configured target is missing its scope")
        if item.get("approval_status") != "approved":
            raise ValueError("scope not approved")
        if item.get("enabled") is False:
            return None  # An explicit operator action, never expiry or read failure.
        expires = datetime.fromisoformat(item["expires_at"])
        if expires.tzinfo is None or expires <= (now or datetime.now(UTC)):
            raise ValueError("scope expired")
        sources = item["sources"]
        if not isinstance(sources, dict) or not 1 <= len(sources) <= 30:
            raise ValueError("scope must contain 1 to 30 sources")
        for source, digest in sources.items():
            if str(UUID(source)) != source or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("scope source invalid")
        return TodayTasksScope(actor.workspace_id, actor.user_id, expires, tuple(sorted(sources.items())))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise TodayScopeChanged() from None


@dataclass(frozen=True)
class TodayTasksScope:
    workspace_id: str
    user_id: str
    expires_at: datetime
    sources: tuple[tuple[str, str], ...]

    @property
    def source_ids(self):
        return tuple(source for source, _ in self.sources)

    def check_candidates(self, candidates):
        expected = dict(self.sources)
        seen = set()
        for item in candidates:
            source_id = item.get("source_id")
            if (
                source_id not in expected
                or source_id in seen
                or not any(marker in (item.get("customer_name") or "") for marker in ("演示", "验收", "测试", "联调"))
                or source_fingerprint(item) != expected[source_id]
            ):
                raise TodayScopeChanged()
            seen.add(source_id)

    async def recheck_before_write(self, connection, actor, settings, candidates):
        if load_today_scope(settings, actor) != self:
            raise TodayScopeChanged()
        self.check_candidates(candidates)
        rows = await recheck_follow_up_sources(connection, actor, candidates)
        self.check_candidates(rows)
