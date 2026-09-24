# ruff: noqa: S608 -- HISTORY is a constant SQL projection; all request values are bound.
"""FDE reporting uses personally archived visits, project facts and owned tasks."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sales_backend.domain.reporting_period import reporting_period, rhythm_axes
from sales_backend.repositories.collaboration import scope_members, scoped_opportunity_ids

TZ = ZoneInfo("Asia/Shanghai")


def period_contains(value, year, quarters):
    if value is None:
        return False
    if isinstance(value, datetime):
        value = value.astimezone(TZ)
    return value.year == year and ((value.month - 1) // 3 + 1) in quarters


def history_arguments(
    actor, *, scope, member_id, year, quarters, opportunity_id=None, all_history=False, selected=None,
    user_ids=None, team_id=None
):
    selected = selected or reporting_period(
        datetime.now(TZ), year=year, quarters=quarters, period="all" if all_history else None
    )
    return (
        None if all_history else selected.start_at,
        None if all_history else selected.end_at,
        actor.user_id if scope == "self" else str(member_id) if member_id else None,
        str(opportunity_id) if opportunity_id else None,
        list(selected.quarters) or None,
        user_ids,
        str(team_id) if team_id else None,
    )


HISTORY = (
    "(SELECT * FROM security.fde_recorded_visit_history($1,$2,$3::uuid,$4::uuid,$5::int[]) "
    "WHERE ($6::uuid[] IS NULL OR user_ref_id=ANY($6::uuid[])) "
    "AND ($7::uuid IS NULL OR team_id_at_event=$7::uuid)) guarded_history"
)


async def history_page(connection, arguments, *, offset, limit):
    # Filters belong to the guarded function's source query; LIMIT bounds rows
    # transferred to Python regardless of the total authorized history size.
    rows = await connection.fetch(
        "WITH page AS MATERIALIZED (SELECT * FROM "
        + HISTORY
        + " ORDER BY interaction_at DESC NULLS LAST,visit_id DESC LIMIT $8 OFFSET $9) "
        "SELECT page.*,security.has_customer_access(page.customer_id) AS can_read_detail "
        "FROM page ORDER BY interaction_at DESC NULLS LAST,visit_id DESC",
        *arguments,
        limit,
        offset,
    )
    return [
        {
            "id": str(row["visit_id"]),
            "customer_id": str(row["customer_id"]),
            "customer_name": row["customer_name"],
            "opportunity_id": str(row["opportunity_id"]) if row["opportunity_id"] else None,
            "opportunity_name": row["opportunity_name"],
            "interaction_at": row["interaction_at"],
            "recorder_name": row["recorder_name"],
            "recorder_user_id": str(row["user_ref_id"]),
            "participant_names": [row["recorder_name"] or "历史成员"],
            "can_read_detail": row["can_read_detail"],
        }
        for row in rows
    ]


async def history_statistics(connection, arguments):
    # One filtered materialization serves the card, member ranking and daily
    # rhythm. No complete visit history is transferred to Python for aggregation.
    row = await connection.fetchrow(
        "WITH history AS MATERIALIZED (SELECT * FROM " + HISTORY + "), "
        "people AS (SELECT user_ref_id::text AS id,count(DISTINCT visit_id) AS visits, "
        "count(DISTINCT opportunity_id) AS opportunities "
        "FROM history GROUP BY user_ref_id), "
        "rhythm AS (SELECT timezone('Asia/Shanghai',interaction_at)::date AS day,"
        "count(DISTINCT visit_id) AS visits,count(*) AS person_times FROM history "
        "WHERE interaction_at IS NOT NULL GROUP BY day) "
        "SELECT count(DISTINCT visit_id) AS visits,count(DISTINCT user_ref_id) AS active_recorders,"
        "count(DISTINCT customer_id) AS customers,count(DISTINCT opportunity_id) AS opportunities,"
        "COALESCE((SELECT jsonb_object_agg(id,visits) FROM people),'{}'::jsonb) AS by_person,"
        "COALESCE((SELECT jsonb_object_agg(id,opportunities) FROM people),'{}'::jsonb) AS projects_by_person,"
        "COALESCE((SELECT jsonb_agg(jsonb_build_object('date',day,'visits',visits,"
        "'person_times',person_times) ORDER BY day) FROM rhythm),'[]'::jsonb) AS rhythm FROM history",
        *arguments,
    )
    return dict(row)


async def owned_task_statistics(connection, user_ids, *, year, quarters, now, selected=None):
    """Aggregate current owners in SQL; a shared team task is counted only once."""
    selected = selected or reporting_period(now, year=year, quarters=quarters)
    row = await connection.fetchrow(
        """WITH owned AS MATERIALIZED (
          SELECT DISTINCT t.id,a.assignee_user_ref_id AS user_id,t.status,t.completed_at,t.due_at
          FROM workflow.task t JOIN workflow.task_assignee a ON a.task_id=t.id
          WHERE t.deleted_at IS NULL AND a.responsibility='owner' AND a.assignee_user_ref_id=ANY($1::uuid[])
        ), facts AS MATERIALIZED (
          SELECT id,user_id,status NOT IN ('completed','cancelled') AS pending,
            status='completed' AND completed_at >= $2 AND completed_at < $3
              AND ($4::int[] IS NULL OR
                extract(quarter FROM timezone('Asia/Shanghai',completed_at))::int=ANY($4::int[])) AS completed,
            status NOT IN ('completed','cancelled') AND due_at < $5 AS overdue FROM owned
        ), people AS (
          SELECT user_id,count(DISTINCT id) FILTER(WHERE completed) AS completed_tasks,
            count(DISTINCT id) FILTER(WHERE pending) AS pending_tasks,
            count(DISTINCT id) FILTER(WHERE overdue) AS overdue_tasks FROM facts GROUP BY user_id
        ) SELECT count(DISTINCT id) FILTER(WHERE completed) AS completed_tasks,
          count(DISTINCT id) FILTER(WHERE pending) AS pending_tasks,
          count(DISTINCT id) FILTER(WHERE overdue) AS overdue_tasks,
          COALESCE((SELECT jsonb_object_agg(user_id::text,jsonb_build_object(
            'completed_tasks',completed_tasks,'pending_tasks',pending_tasks,'overdue_tasks',overdue_tasks))
            FROM people),'{}'::jsonb) AS by_person FROM facts""",
        user_ids,
        selected.start_at,
        selected.end_at,
        list(selected.quarters) or None,
        now,
    )
    return dict(row)


async def fde_activity(
    connection,
    actor,
    *,
    scope=None,
    member_id=None,
    member_ids=None,
    team_id=None,
    year=None,
    quarters=None,
    offset=0,
    limit=50,
    opportunity_id=None,
    all_history=False,
    period=None,
    date_from=None,
    date_to=None,
):
    now = datetime.now(TZ)
    selected = reporting_period(
        now, year=year, quarters=quarters, period="all" if all_history else period, date_from=date_from, date_to=date_to
    )
    scope, ids, _ = await scope_members(connection, actor, scope, member_id, member_ids, team_id, permission="profile.fde_activity")
    arguments = history_arguments(
        actor,
        scope=scope,
        member_id=member_id,
        year=year,
        quarters=quarters,
        opportunity_id=opportunity_id,
        all_history=all_history,
        selected=selected,
        user_ids=ids if member_ids or member_id or scope == "self" else None,
        team_id=team_id,
    )
    total = await connection.fetchval("SELECT count(*) FROM " + HISTORY, *arguments)
    items = await history_page(connection, arguments, offset=offset, limit=limit)
    more = offset + len(items) < total
    return {"items": items, "total": total, "has_more": more, "next_offset": offset + len(items) if more else None}


async def fde_dashboard(
    connection,
    actor,
    *,
    scope=None,
    member_id=None,
    member_ids=None,
    team_id=None,
    year=None,
    quarters=None,
    period=None,
    date_from=None,
    date_to=None,
    permission="profile.fde_read",
):
    now = datetime.now(TZ)
    requested_quarters = sorted(set(quarters or []))
    selected = reporting_period(now, year=year, quarters=quarters, period=period, date_from=date_from, date_to=date_to)
    year, quarters = year or now.year, list(selected.quarters) or [1, 2, 3, 4]
    scope, ids, members, scoped = await scoped_opportunity_ids(connection, actor, scope, member_id, member_ids, team_id,
                                                                permission=permission, fde_cohort=True)
    opp_ids = [r["id"] for r in scoped]
    opportunities = (
        [
            dict(r)
            for r in await connection.fetch(
                "SELECT id::text,customer_id::text,name,status,stage_code,probability,amount "
                "FROM crm.opportunity WHERE id=ANY($1::uuid[]) AND deleted_at IS NULL",
                opp_ids,
            )
        ]
        if opp_ids
        else []
    )
    history_args = history_arguments(
        actor,
        scope=scope,
        member_id=member_id,
        year=year,
        quarters=quarters,
        selected=selected,
        user_ids=ids if member_ids or member_id or scope == "self" else None,
        team_id=team_id,
    )
    history = await history_statistics(connection, history_args)
    historical_ids = set(history["by_person"])
    if historical_ids - {r["id"] for r in members}:
        extra = await connection.fetch(
            "SELECT id::text,display_name AS name FROM platform.user_ref WHERE id=ANY($1::uuid[])",
            list(historical_ids - {r["id"] for r in members}),
        )
        members += [{**dict(r), "team": "历史参与", "is_active": False} for r in extra]
    report_ids = sorted(set(ids) | historical_ids)
    # Only owned tasks contribute; one task assigned to multiple people remains one team fact.
    task_statistics = await owned_task_statistics(
        connection, ids if team_id else report_ids, year=year, quarters=quarters, now=now, selected=selected
    )
    task_by_person = task_statistics.pop("by_person")
    ledger = (
        await connection.fetch(
            """SELECT kind,COALESCE(sum(amount),0) AS amount FROM crm.customer_actual
        WHERE voided_at IS NULL AND opportunity_id=ANY($1::uuid[])
        AND occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
        AND occurred_on BETWEEN $2 AND $3
        AND ($4::int[] IS NULL OR extract(quarter FROM occurred_on)::int=ANY($4::int[]))
        GROUP BY kind""",
            opp_ids,
            selected.start,
            selected.end,
            list(selected.quarters) or None,
        )
        if opp_ids
        else []
    )
    money = {r["kind"]: r["amount"] for r in ledger}
    relations = (
        await connection.fetch(
            """SELECT opportunity_id::text,user_ref_id::text FROM crm.opportunity_participant
        WHERE opportunity_id=ANY($1::uuid[]) AND user_ref_id=ANY($2::uuid[]) AND participant_role='fde'
          AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to
          AND security.fde_user_is_active(user_ref_id)""",
            opp_ids,
            ids,
        )
        if opp_ids
        else []
    )

    # Aggregate member metrics once; joint projects deliberately contribute to each
    # participant row, while the headline ledger above counts the union only once.
    person_money = {
        r["user_id"]: dict(r)
        for r in await connection.fetch(
            """WITH relation AS (SELECT DISTINCT opportunity_id,user_ref_id FROM crm.opportunity_participant
          WHERE opportunity_id=ANY($1::uuid[]) AND user_ref_id=ANY($2::uuid[]) AND participant_role='fde'
            AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to)
        SELECT r.user_ref_id::text AS user_id,
          COALESCE(sum(a.amount) FILTER(WHERE a.kind='recognized'),0) AS recognized_amount,
          COALESCE(sum(a.amount) FILTER(WHERE a.kind='collection'),0) AS collection_amount
        FROM relation r JOIN crm.customer_actual a ON a.opportunity_id=r.opportunity_id
        WHERE a.voided_at IS NULL AND a.occurred_on BETWEEN $3 AND $4
          AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
          AND ($5::int[] IS NULL OR extract(quarter FROM a.occurred_on)::int=ANY($5::int[]))
        GROUP BY r.user_ref_id""",
            opp_ids,
            ids,
            selected.start,
            selected.end,
            list(selected.quarters) or None,
        )
    }
    demos = await connection.fetchval(
        "SELECT security.fde_demo_statistics($1::uuid[],$2::uuid[],$3,$4,$5::int[])",
        [uid for uid in ids if any(p["id"] == uid and p["is_active"] for p in members)],
        opp_ids,
        selected.start_at,
        selected.end_at,
        list(selected.quarters) or None,
    )
    can_rank = await connection.fetchval("SELECT security.authorization_has('dashboard.ranking')")
    company = await connection.fetchval(
        "SELECT security.fde_dashboard_rankings($1,$2,$3::int[],$4::uuid)",
        selected.start,
        selected.end,
        list(selected.quarters) or None,
        str(member_id) if member_id else actor.user_id if scope == "self" else None,
    ) if can_rank else {"rows": [], "groups": [], "selection": {}}
    if team_id:
        company = {**company, "selection": {**company.get("selection", {}), "team_ids": [str(team_id)]}}
    rhythm, rhythm_weeks = rhythm_axes(history["rhythm"], selected, now)
    ranking = []
    for person in members:
        uid = person["id"]
        if uid not in report_ids:
            continue
        personal_ids = {r["opportunity_id"] for r in relations if r["user_ref_id"] == uid}
        own = [o for o in opportunities if o["id"] in personal_ids]
        ranking.append(
            {
                "user_id": uid,
                "name": person["name"],
                "team": person["team"],
                "is_active": person["is_active"],
                "customers": len({o["customer_id"] for o in own}),
                "opportunities": len(own),
                "open_acv": sum((o["amount"] or Decimal(0) for o in own if o["status"] == "open"), Decimal(0)),
                "visits": history["by_person"].get(uid, 0),
                "followup_count": history["by_person"].get(uid, 0),
                "period_opportunities": history["projects_by_person"].get(uid, 0),
                "demo_scene_count": demos["by_person"].get(uid, 0),
                "recognized_amount": person_money.get(uid, {}).get("recognized_amount", 0),
                "collection_amount": person_money.get(uid, {}).get("collection_amount", 0),
                **task_by_person.get(uid, {"completed_tasks": 0, "pending_tasks": 0, "overdue_tasks": 0}),
            }
        )
    ranking.sort(key=lambda r: (-r["opportunities"], -r["visits"], r["name"]))
    stages = {}
    labels = {10: "意向沟通", 30: "商机确认", 50: "方案沟通", 70: "商务谈判", 90: "客户签约", 100: "赢单"}
    for opp in opportunities:
        stage = stages.setdefault(
            opp["stage_code"],
            {
                "code": opp["stage_code"],
                "label": "丢单" if opp["status"] == "lost" else labels.get(opp["probability"], "待完善"),
                "probability": opp["probability"],
                "count": 0,
                "amount": Decimal(0),
            },
        )
        stage["count"] += 1
        stage["amount"] += opp["amount"] or 0
    return {
        "data_source": "database",
        "contract_version": 2,
        "visit_count_basis": "self_recorded_archived",
        "as_of": now,
        "scope": scope,
        "scope_label": "团队协作" if scope == "team" and not member_id else "个人协作",
        "filters": {"date_from": selected.start, "date_to": selected.end, "period": selected.kind,
                    "member_ids": ids, "team_id": str(team_id) if team_id else None},
        "year": year,
        "quarters": quarters,
        "members": members,
        "summary": {
            "customers": len({o["customer_id"] for o in opportunities}),
            "opportunities": len(opportunities),
            "open_opportunities": sum(o["status"] == "open" for o in opportunities),
            "open_acv": sum((o["amount"] or Decimal(0) for o in opportunities if o["status"] == "open"), Decimal(0)),
            "period_visits": history["visits"],
            "period_visit_people": history["visits"],
            "active_recorders": history["active_recorders"],
            "period_customers": history["customers"],
            "period_opportunities": history["opportunities"],
            "demo_scene_count": demos["project_count"],
            "own_demo_scene_count": demos["own_count"],
            "won_amount": sum((o["amount"] or Decimal(0) for o in opportunities if o["status"] == "won"), Decimal(0)),
            **task_statistics,
            "recognized_amount": money.get("recognized", 0),
            "collection_amount": money.get("collection", 0),
        },
        "stages": sorted(stages.values(), key=lambda r: r["probability"] or 0),
        "rhythm": rhythm,
        "rhythm_weeks": rhythm_weeks,
        "company_rankings": {**company, "year": year, "quarters": requested_quarters},
        "ranking": ranking,
        "recent_visits": await history_page(connection, history_args, offset=0, limit=20),
    }
