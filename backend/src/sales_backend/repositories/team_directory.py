"""One effective organization directory for selectors; never derived from business rows.

An effective leaf is a business team. Parents remain aggregate nodes, even after
renaming. Empty teams remain selectable. Actor scope still bounds every option;
options confer no additional permission on any business read or write.
"""
from sales_backend.domain.team_directory import TeamPurpose, team_directory_permission
from sales_backend.repositories.authorization_checks import require_permission


async def selectable_teams(connection, actor, purpose: TeamPurpose = 'browse', *, permission=None, permissions=None):
    permission = team_directory_permission(purpose, permission)
    # Only an explicit server-resolved object from this transaction may be
    # reused. Other callers keep their existing live permission lookup.
    if permissions is None:
        await require_permission(connection, permission)
    else:
        permissions.require(permission)
    rows = await connection.fetch(
        """SELECT t.id::text,t.name,t.parent_team_id::text AS parent_id
        FROM platform.team t
        WHERE t.workspace_id=$1::uuid AND t.status='active' AND t.deleted_at IS NULL
          AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
          AND NOT EXISTS (
            SELECT 1 FROM platform.team child WHERE child.workspace_id=t.workspace_id
              AND child.parent_team_id=t.id AND child.deleted_at IS NULL)
          AND (security.authorization_subject($2,'team',NULL,t.id)
            OR ($2 IN ('customer.create','customer.create_request')
              AND security.authorization_allows($2,t.workspace_id,$3::uuid,ARRAY[t.id],false)))
        ORDER BY t.name,t.id""", actor.workspace_id, permission, actor.user_id)
    return [dict(row) for row in rows]


async def require_team(connection, actor, team_id, purpose: TeamPurpose = 'browse', *, permission=None):
    teams = await selectable_teams(connection, actor, purpose, permission=permission)
    selected = next((row for row in teams if row['id'] == str(team_id)), None)
    if selected is None:
        raise PermissionError('所选团队已失效或不在当前授权范围内，请刷新团队目录')
    return selected


async def attach_member_teams(connection, actor, members, teams):
    """Attach all effective selectable memberships, without adding visible people."""
    if not members:
        return []
    rows = await connection.fetch(
        """SELECT tm.user_ref_id::text,tm.team_id::text FROM platform.team_membership tm
        WHERE tm.workspace_id=$1::uuid AND tm.user_ref_id=ANY($2::uuid[])
          AND tm.team_id=ANY($3::uuid[])
          AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to""",
        actor.workspace_id, [member['id'] for member in members], [team['id'] for team in teams])
    memberships = {}
    for row in rows:
        memberships.setdefault(row['user_ref_id'], set()).add(row['team_id'])
    return [{**member, 'team_ids': sorted(memberships.get(member['id'], set()))} for member in members]


async def default_team_id(connection, actor, teams):
    """The actor's effective primary team, only when this directory permits it.

    Never substitute the alphabetically first team for a missing primary team.
    The directory remains usable without a default, including empty companies.
    """
    if not teams:
        return None
    return await connection.fetchval(
        """SELECT tm.team_id::text FROM platform.team_membership tm
        JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
        WHERE tm.workspace_id=$1::uuid AND tm.user_ref_id=$2::uuid AND tm.is_primary
          AND tm.team_id=ANY($3::uuid[])
          AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
          AND t.status='active' AND t.deleted_at IS NULL
          AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
        ORDER BY tm.valid_from DESC,tm.id LIMIT 1""",
        actor.workspace_id, actor.user_id, [team['id'] for team in teams])
