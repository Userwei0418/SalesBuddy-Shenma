"""Directory and selection bounds for dashboard facts; never impersonate a member."""
from dataclasses import dataclass

from sales_backend.repositories.team_directory import selectable_teams

GROUPS = ({"code": "north_east", "name": "北区＋东区"}, {"code": "south_hkmo", "name": "南区＋港澳"})


def _team_group_code(team_id):
    return f"team:{team_id}"


async def dashboard_team_groups(connection, actor):
    """Return current active departments for the manager selector.

    The legacy ranking groups remain accepted by resolve_selection so old clients
    and saved links continue to work; new clients use stable team IDs.
    """
    rows = await selectable_teams(connection, actor, 'dashboard')
    return [{"code": _team_group_code(row["id"]), "name": row["name"], "team_id": row["id"], "kind": ""}
            for row in rows]



async def dashboard_members(connection, actor):
    rows = await connection.fetch(
        """SELECT u.id::text,u.display_name AS name,u.account_code,role.role_code AS role,
          primary_team.name AS team,security.ranking_team_group(primary_team.name) AS group_code
        FROM platform.user_ref u
        JOIN LATERAL (
          SELECT b.role_code FROM platform.role_binding b
          WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
            AND (b.role_code IN ('sales','supervisor','manager')
              OR ($3='manager' AND b.role_code IN ('fde','fde_lead')))
            AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
          ORDER BY CASE b.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1
            WHEN 'sales' THEN 2 WHEN 'fde_lead' THEN 3 ELSE 4 END,b.id LIMIT 1
        ) role ON true
        LEFT JOIN LATERAL (
          SELECT t.name FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
          WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND t.deleted_at IS NULL
            AND t.status='active' AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
            AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
          ORDER BY tm.is_primary DESC,tm.valid_from DESC,tm.id LIMIT 1
        ) primary_team ON true
        WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
          AND (u.id=$2::uuid OR $3='manager' OR ($3='supervisor' AND EXISTS(
            SELECT 1 FROM platform.team_membership tm WHERE tm.workspace_id=u.workspace_id
              AND tm.user_ref_id=u.id AND security.supervises_team(tm.team_id)
              AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)))
        ORDER BY u.display_name,u.id""", actor.workspace_id, actor.user_id, actor.role.value)
    return [dict(row) for row in rows]


async def own_region_codes(connection, actor):
    return await connection.fetchval(
        """SELECT COALESCE(array_agg(DISTINCT security.ranking_team_group(t.name)),ARRAY[]::text[])
          FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
          WHERE tm.workspace_id=$1::uuid AND tm.user_ref_id=$2::uuid AND t.deleted_at IS NULL
            AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to""",
        actor.workspace_id, actor.user_id)


async def supervised_region_codes(connection, actor):
    return await connection.fetchval(
        """SELECT COALESCE(array_agg(DISTINCT security.ranking_team_group(t.name)),ARRAY[]::text[])
          FROM platform.team t WHERE t.workspace_id=$1::uuid AND t.deleted_at IS NULL AND t.status='active'
            AND security.supervises_team(t.id)""", actor.workspace_id)


@dataclass(frozen=True)
class DashboardSelection:
    personal: bool
    member_id: str
    team_ids: tuple[str, ...] | None = None
    team_groups: tuple[str, ...] = ()


async def resolve_selection(connection, actor, *, personal=False, member_id=None, team_groups=()):
    personal = personal or actor.role.value == 'sales'
    groups = tuple(sorted(set(team_groups or ())))
    dynamic_groups = await dashboard_team_groups(connection, actor) if actor.role.value == 'manager' else []
    dynamic_codes = {g['code'] for g in dynamic_groups}
    legacy_codes = {g['code'] for g in GROUPS}
    if set(groups) - legacy_codes - dynamic_codes:
        raise ValueError('请选择有效的团队分组')
    if groups and (actor.role.value != 'manager' or personal):
        raise PermissionError('仅总经理团队视角可筛选团队')
    if set(groups) & legacy_codes and set(groups) & dynamic_codes:
        raise ValueError('团队分组不能混用旧区域和实际部门')
    if member_id and not personal:
        raise ValueError('选择成员时请使用个人视角')
    target = str(member_id or actor.user_id)
    if target != actor.user_id:
        if actor.role.value not in {'supervisor', 'manager'}:
            raise PermissionError('当前身份仅可查看本人经营数据')
        if target not in {row['id'] for row in await dashboard_members(connection, actor)}:
            raise PermissionError('所选成员不在可查看范围内或已停用')
    teams = None
    selected_dynamic = [g['team_id'] for g in dynamic_groups if g['code'] in groups]
    if selected_dynamic:
        teams = tuple(sorted(set(selected_dynamic)))
    elif len(groups) == 1:
        rows = await connection.fetch(
            """SELECT id::text FROM platform.team WHERE workspace_id=$1::uuid AND deleted_at IS NULL
              AND security.ranking_team_group(name)=ANY($2::text[])""", actor.workspace_id, list(groups))
        teams = tuple(row['id'] for row in rows)
    return DashboardSelection(personal, target, teams, groups)
