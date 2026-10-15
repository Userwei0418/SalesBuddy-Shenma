"""Customer-map projection and bounded recovery of missing, never invented, scores."""

from math import isfinite
from uuid import uuid4

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.collaboration import scope_members
from sales_backend.repositories.customer_map import CustomerMapRepository
from sales_backend.repositories.jobs import enqueue_battle_map_review
from sales_backend.repositories.profile_scope import scope_options


def has_map_scores(item):
    try:
        return all(item.get(key) is not None and not isinstance(item[key], bool)
                   and isfinite(float(item[key])) and 0 <= float(item[key]) <= 100
                   for key in ("potential_score", "relationship_score"))
    except (ValueError, TypeError, OverflowError):
        return False


async def read_map(
    connection, actor, *, as_of, activity_window=None, scope=None, member_id=None, member_ids=(), team_id=None,
    row_limit=None, offset=0, business_identity="all",
):
    selected_ids = None
    if scope or member_id or member_ids or team_id:
        _, selected_ids, _ = await scope_members(
            connection, actor, scope, member_id, member_ids, team_id, permission="battle_map.read")
    directory = await scope_options(connection, actor, permission="battle_map.read")
    people = [member["id"] for member in directory["members"]
              if selected_ids is None or member["id"] in selected_ids]
    selected_scope = "person" if len(people) == 1 and not team_id else "team" if team_id else "department"
    # FDE selectors restrict collaboration customers, not sales-score subjects.
    # Pass only the permission-filtered directory to the combined projection;
    # its customer and assessment RLS checks remain authoritative.
    explicit_subject = actor.role not in (RoleCode.FDE, RoleCode.FDE_LEAD) and bool(
        scope in ("self", "person") or member_id or member_ids)
    return await CustomerMapRepository().read(
        connection, fde_user_ids=selected_ids, as_of=as_of, activity_window=activity_window,
        subject_member_ids=people, subject_scope=selected_scope,
        subject_team_ids=[str(team_id)] if team_id else [team["id"] for team in directory["teams"]],
        explicit_subject=explicit_subject,
        row_limit=row_limit, offset=offset, business_identity=business_identity,
    )


async def refresh_missing_map(
    connection, actor, *, as_of, activity_window=None, scope=None, member_id=None, member_ids=(), team_id=None,
    row_limit=None, offset=0, business_identity="all",
):
    """Only enqueue missing current-actor scores; never act as another member.

    Lock per tenant/actor before re-reading coverage. Existing active jobs are
    reused, and a terminal attempt has a ten-minute cooldown. Worker retries,
    tenant quotas and validation remain authoritative; GET never writes jobs.
    """
    if activity_window == "all":
        # Validate the same permission-filtered subject scope and RLS projection,
        # but never acquire a write lock or fan out historical scoring jobs.
        await read_map(connection, actor, as_of=as_of, activity_window="all", scope=scope,
                       member_id=member_id, member_ids=member_ids, team_id=team_id, row_limit=1, business_identity=business_identity)
        return {"status": "unavailable", "enqueued_count": 0, "refresh_allowed": False,
                "code": "BATTLE_MAP_ALL_REFRESH_DISABLED",
                "reason": "全部范围仅展示已有评分；本页未评估客户不会自动补评，请选择活动时间范围后更新"}
    if actor.role not in (RoleCode.FDE, RoleCode.FDE_LEAD) and (
            (member_id and str(member_id) != actor.user_id)
            or any(str(value) != actor.user_id for value in member_ids)):
        return {"status": "unavailable", "enqueued_count": 0,
                "reason": "所选成员的地图尚未更新，请由该成员打开地图更新"}
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        f"map-recovery:{actor.workspace_id}:{actor.user_id}",
    )
    items = await read_map(
        connection, actor, as_of=as_of, activity_window=activity_window, scope=scope, member_id=member_id,
        member_ids=member_ids, team_id=team_id, row_limit=row_limit, offset=offset, business_identity=business_identity,
    )
    missing = [item["id"] for item in items if not has_map_scores(item)]
    if not missing:
        return {"status": "ready", "enqueued_count": 0}
    jobs = await connection.fetch(
        """SELECT DISTINCT ON (aggregate_id) aggregate_id::text,status,
          COALESCE(completed_at,updated_at,created_at)>clock_timestamp()-interval '10 minutes' AS recent
        FROM ops.job
        WHERE workspace_id=$1::uuid AND job_type='battle_map.review'
          AND aggregate_id=ANY($2::uuid[]) AND payload->>'user_id'=$3
        ORDER BY aggregate_id,(status IN ('queued','running','failed')) DESC,created_at DESC,id DESC""",
        actor.workspace_id, missing, actor.user_id,
    )
    latest = {job["aggregate_id"]: job for job in jobs}
    enqueued, unavailable = 0, False
    for customer_id in missing:
        previous = latest.get(customer_id)
        if previous and previous["status"] in ("queued", "running", "failed"):
            continue
        if previous and previous["recent"]:
            unavailable = True
            continue
        # Preserve the pre-existing analysis boundary: no membership changes,
        # privileged actor fabrication or recovery of somebody else's scores.
        permitted = await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer_id)
        if not permitted:
            unavailable = True
            continue
        await enqueue_battle_map_review(
            connection, actor, customer_id=customer_id,
            trigger_type="map.coverage_recovery", trigger_id=str(uuid4()),
        )
        enqueued += 1
    return {
        "status": "unavailable" if unavailable else "updating", "enqueued_count": enqueued,
        **({"reason": "地图更新暂未完成，请稍后重试；持续失败请联系管理员检查评估任务"} if unavailable else {}),
    }
