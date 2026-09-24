"""One effective organization directory for selectors; never derived from business rows.

An effective leaf is a business team. Parents remain aggregate nodes, even after
renaming. Empty teams remain selectable. Actor scope still bounds every option;
options confer no additional permission on any business read or write.
"""
from typing import Literal
from sales_backend.repositories.authorization_checks import require_permission

TeamPurpose = Literal['browse', 'dashboard', 'profile', 'fde', 'assignment']


async def selectable_teams(connection, actor, purpose: TeamPurpose = 'browse', *, permission=None):
    permissions = {
        'browse': 'directory.read', 'dashboard': 'dashboard.read', 'profile': 'profile.sales_read',
        'fde': 'profile.fde_read', 'assignment': 'customer.create',
    }
    permission = permission or permissions.get(purpose)
    if permission is None:
        raise ValueError('未知团队目录用途')
    await require_permission(connection, permission)
    rows = await connection.fetch(
        """SELECT t.id::text,t.name,t.parent_team_id::text AS parent_id
        FROM platform.team t
        WHERE t.workspace_id=$1::uuid AND t.status='active' AND t.deleted_at IS NULL
          AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
          AND NOT EXISTS (
            SELECT 1 FROM platform.team child WHERE child.workspace_id=t.workspace_id
              AND child.parent_team_id=t.id AND child.deleted_at IS NULL)
          AND (security.authorization_subject($2,'team',NULL,t.id)
            OR ($2='customer.create' AND security.authorization_allows($2,t.workspace_id,$3::uuid,ARRAY[t.id],false)) OR ($2='directory.read'
            AND security.authorization_has('directory.read') AND EXISTS(
              SELECT 1 FROM platform.team_membership tm WHERE tm.workspace_id=t.workspace_id
              AND tm.team_id=t.id AND tm.user_ref_id=$3::uuid
              AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)))
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
