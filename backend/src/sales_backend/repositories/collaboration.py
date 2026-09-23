"""Normalized FDE relationships and event participants; no presentation-only facts."""

from uuid import UUID

FDE_ROLES = frozenset({"fde", "fde_lead"})


def member_ids(values):
    if not isinstance(values, (list, tuple)) or len(values) > 30:
        raise ValueError("协助 FDE 最多选择30人")
    try:
        return sorted({str(UUID(str(value))) for value in values})
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("请选择有效的 FDE 成员") from exc


async def fde_directory(connection, *, query=None):
    rows = await connection.fetch(
        """SELECT DISTINCT ON(u.id) u.id::text,u.display_name AS name,rb.role_code AS role,
          tm.team_id::text AS team_id,t.name AS team,true AS is_active
        FROM platform.user_ref u JOIN platform.role_binding rb ON rb.user_ref_id=u.id
        JOIN platform.team_membership tm ON tm.user_ref_id=u.id
        JOIN platform.team t ON t.id=tm.team_id
        WHERE security.fde_user_is_active(u.id,rb.role_code,tm.team_id)
          AND rb.role_code IN('fde','fde_lead') AND tm.membership_role IN('fde','fde_lead')
          AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
          AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
          AND ($1::text IS NULL OR u.display_name ILIKE '%'||$1||'%' OR u.account_code ILIKE '%'||$1||'%')
        ORDER BY u.id,tm.is_primary DESC,rb.valid_from DESC,tm.valid_from DESC""",
        query,
    )
    return sorted((dict(row) for row in rows), key=lambda r: (r["team"], r["name"], r["id"]))


async def validated_fde(connection, values):
    ids = member_ids(values)
    if not ids:
        return []
    available = {r["id"]: r for r in await fde_directory(connection)}
    if any(uid not in available for uid in ids):
        raise ValueError("FDE 成员不存在、已停用或不属于有效 FDE 部门，请重新选择")
    return [available[uid] for uid in ids]


async def members_by_opportunity(connection, ids):
    if not ids:
        return {}
    rows = await connection.fetch(
        """SELECT p.opportunity_id::text,u.id::text,u.display_name AS name,
            p.valid_from,COALESCE(d.role,'fde') AS role,d.team,d.team_id
        FROM crm.opportunity_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id
        LEFT JOIN LATERAL(SELECT rb.role_code AS role,t.name AS team,tm.team_id::text AS team_id
          FROM platform.role_binding rb JOIN platform.team_membership tm ON tm.user_ref_id=rb.user_ref_id
          JOIN platform.team t ON t.id=tm.team_id
          WHERE rb.user_ref_id=u.id AND rb.role_code IN('fde','fde_lead')
          AND tm.membership_role IN('fde','fde_lead') AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
          AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
          AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
          ORDER BY tm.is_primary DESC,rb.valid_from DESC LIMIT 1) d ON true
        WHERE p.opportunity_id=ANY($1::uuid[]) AND p.participant_role='fde'
          AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
          AND security.fde_user_is_active(u.id)
        ORDER BY u.display_name,u.id""",
        ids,
    )
    result = {}
    for row in rows:
        item = dict(row)
        result.setdefault(item.pop("opportunity_id"), []).append(item)
    return result


async def effective_ids(connection, opportunity_id):
    # Include disabled members when calculating explicit removals; disabling already revokes access.
    rows = await connection.fetch(
        """SELECT user_ref_id::text FROM crm.opportunity_participant WHERE opportunity_id=$1::uuid
        AND participant_role='fde' AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to""",
        opportunity_id,
    )
    return {r["user_ref_id"] for r in rows}


async def manageable_version(connection, opportunity_id):
    if not await connection.fetchval("SELECT security.can_manage_fde_members($1::uuid)", opportunity_id):
        raise PermissionError("无权调整该商机的协助 FDE")
    version = await connection.fetchval(
        "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid AND deleted_at IS NULL",
        opportunity_id,
    )
    if version is None:
        raise LookupError("商机不存在或不可见")
    return version


async def bump_membership_version(connection, opportunity_id, version):
    return await connection.fetchval(
        "SELECT security.bump_fde_membership_version($1::uuid,$2)",
        opportunity_id,
        version,
    )


async def archive_fde_collaboration(connection, actor, visit, participants):
    oid = visit.get("opportunity_id")
    if not oid or not participants:
        return
    current = await effective_ids(connection, oid)
    if set(participants) - current:
        version = await manageable_version(connection, oid)
        await bump_membership_version(connection, oid, version)
        # The roster may have changed before the aggregate lock was acquired.
        # Union against the locked state so another visit's additions survive.
        current = await effective_ids(connection, oid)
        await write_members(
            connection, actor, oid, current | set(participants), source="visit_archive", visit_id=visit["id"]
        )
    for uid in participants:
        await connection.fetchval(
            "SELECT workflow.enqueue_fde_collaboration_notification("
            "$1::uuid,$2::uuid,'visit_archived',$3::uuid,$4::jsonb)",
            oid,
            uid,
            visit["id"],
            {
                "visit_id": visit["id"],
                "customer_id": visit["customer_id"],
                "customer_name": visit["customer_name"],
                "opportunity_id": oid,
                "title": "参与的拜访已归档",
            },
        )


async def write_members(connection, actor, opportunity_id, desired, *, source="manual", visit_id=None):
    """Caller holds the opportunity aggregate lock. Triggers write relationship audit/cards."""
    desired = set(desired)
    before = await effective_ids(connection, opportunity_id)
    removed, added = before - desired, desired - before
    if actor.role.value == "fde_lead":
        for uid in added | removed:
            if not await connection.fetchval("SELECT security.fde_manages_user($1::uuid)", uid):
                raise PermissionError("FDE 负责人只能调整本部门成员")
    for uid in sorted(added):
        await connection.execute(
            """INSERT INTO crm.opportunity_participant
            (opportunity_id,user_ref_id,workspace_id,participant_role,assigned_by_user_ref_id,source_code,source_visit_id)
            VALUES($1::uuid,$2::uuid,$3::uuid,'fde',$4::uuid,$5,$6::uuid)""",
            opportunity_id,
            uid,
            actor.workspace_id,
            actor.user_id,
            source,
            visit_id,
        )
    for uid in sorted(removed):
        await connection.execute(
            """UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),
            ended_by_user_ref_id=$3::uuid,end_reason='manual'
            WHERE opportunity_id=$1::uuid AND user_ref_id=$2::uuid AND participant_role='fde'
              AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to""",
            opportunity_id,
            uid,
            actor.user_id,
        )
    return bool(added or removed)


async def write_visit_participants(connection, actor, visit_id, collaborators, fde_people):
    """Replace current attendance, retaining event snapshots on unchanged participants."""
    fde = {r["id"]: r for r in fde_people}
    ids = sorted(set(collaborators) | set(fde))
    await connection.execute(
        "DELETE FROM activity.visit_participant WHERE visit_id=$1::uuid AND NOT(user_ref_id=ANY($2::uuid[]))",
        visit_id,
        ids,
    )
    for uid in ids:
        person = fde.get(uid)
        await connection.execute(
            """INSERT INTO activity.visit_participant
            (visit_id,user_ref_id,workspace_id,participant_role,team_id_at_event,role_code_at_event,created_by_user_ref_id)
            VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5::uuid,$6,$7::uuid)
            ON CONFLICT(visit_id,user_ref_id) DO UPDATE SET participant_role=excluded.participant_role,
            team_id_at_event=CASE WHEN activity.visit_participant.participant_role=excluded.participant_role
              THEN activity.visit_participant.team_id_at_event ELSE excluded.team_id_at_event END,
            role_code_at_event=CASE WHEN activity.visit_participant.participant_role=excluded.participant_role
              THEN activity.visit_participant.role_code_at_event ELSE excluded.role_code_at_event END,
            updated_at=clock_timestamp()""",
            visit_id,
            uid,
            actor.workspace_id,
            "fde" if person else "collaborator",
            person["team_id"] if person else None,
            person["role"] if person else None,
            actor.user_id,
        )


async def scope_members(connection, actor, scope=None, selected=None, selected_ids=None, team_id=None):
    if actor.role.value not in FDE_ROLES:
        raise PermissionError("此视图仅供 FDE 成员与负责人使用")
    scope = scope or ("team" if actor.role.value == "fde_lead" else "self")
    if scope not in {"self", "team"} or (scope == "team" and actor.role.value != "fde_lead"):
        raise PermissionError("没有部门协作数据权限")
    available = await fde_directory(connection)
    managed = set()
    if actor.role.value == "fde_lead" and available:
        managed = {
            str(row["user_id"])
            for row in await connection.fetch(
                "SELECT user_id FROM unnest($1::uuid[]) AS person(user_id) WHERE security.fde_manages_user(user_id)",
                [person["id"] for person in available],
            )
        }
    visible = [person for person in available if person["id"] == actor.user_id or person["id"] in managed]
    ids = [p["id"] for p in visible] if scope == "team" else [actor.user_id]
    if team_id:
        if scope != 'team' or selected or selected_ids:
            raise ValueError('团队视角请只指定团队，个人视角请只指定成员')
        allowed = await connection.fetchval(
            "SELECT security.fde_user_is_active($1::uuid,'fde_lead',$2::uuid)", actor.user_id, str(team_id))
        if not allowed:
            raise PermissionError('所选团队不在当前负责范围内')
        team_people = {str(row['user_ref_id']) for row in await connection.fetch(
            "SELECT DISTINCT user_ref_id FROM platform.team_membership WHERE team_id=$1::uuid "
            "AND workspace_id=$2::uuid AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to "
            "AND membership_role IN ('fde','fde_lead')", str(team_id), actor.workspace_id)}
        ids = [uid for uid in ids if uid in team_people]
        visible = [person for person in visible if person['id'] in team_people]
    requested = sorted({str(UUID(str(value))) for value in [*(selected_ids or []), *([selected] if selected else [])]})
    if len(requested) > 100:
        raise ValueError("最多筛选100名成员")
    missing = set(requested) - set(ids)
    if missing:
        historical = set()
        if scope == "team":
            historical = {
                str(row["user_ref_id"])
                for row in await connection.fetch(
                    "SELECT DISTINCT user_ref_id FROM security.fde_recorded_visit_history() "
                    "WHERE user_ref_id=ANY($1::uuid[])",
                    list(missing),
                )
            }
        if missing - historical:
            raise PermissionError("所选成员不在当前数据范围内")
    if requested:
        ids = requested
    return scope, ids, visible


async def scoped_opportunity_ids(connection, actor, scope=None, member_id=None, member_ids=None, team_id=None):
    scope, ids, members = await scope_members(connection, actor, scope, member_id, member_ids, team_id)
    rows = await connection.fetch(
        """SELECT DISTINCT o.id::text,o.customer_id::text FROM crm.opportunity o
        JOIN crm.opportunity_participant p ON p.opportunity_id=o.id
        WHERE o.deleted_at IS NULL AND p.user_ref_id=ANY($1::uuid[])
          AND p.participant_role='fde' AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
          AND security.fde_user_is_active(p.user_ref_id)""",
        ids,
    )
    return scope, ids, members, [dict(r) for r in rows]


async def fde_scope_options(connection, actor):
    _, _, people = await scope_members(connection, actor)
    from sales_backend.repositories.team_directory import selectable_teams

    teams = await selectable_teams(connection, actor, 'fde')
    memberships = await connection.fetch(
        "SELECT user_ref_id::text,team_id::text FROM platform.team_membership WHERE workspace_id=$1::uuid "
        "AND user_ref_id=ANY($2::uuid[]) AND membership_role IN ('fde','fde_lead') "
        "AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to",
        actor.workspace_id, [p['id'] for p in people])
    return {'data_source': 'database', 'teams': teams,
            'members': [{**p, 'user_id': p['id'], 'display_name': p['name'],
                         'team_ids': sorted({r['team_id'] for r in memberships if r['user_ref_id'] == p['id']})}
                        for p in people],
            'allowed_scopes': ['self', 'person', 'team'] if actor.role.value == 'fde_lead' else ['self'],
            'defaults': {'scope': 'team' if teams else 'self', 'member_id': actor.user_id,
                         'team_id': teams[0]['id'] if teams else None}}
