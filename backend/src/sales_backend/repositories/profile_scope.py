"""Stable, authorized profile subjects; selecting a member never changes the actor."""

from sales_backend.repositories.team_directory import selectable_teams

SALES_ROLES = ('sales', 'supervisor', 'manager')


async def scope_options(connection, actor, *, permission='profile.sales_read'):
    teams = await selectable_teams(connection, actor, 'profile', permission=permission)
    members = [dict(row) for row in await connection.fetch(
        """SELECT u.id::text,u.id::text AS user_id,u.account_code,u.display_name,
          active_role.role_code AS role,
          COALESCE((SELECT array_agg(DISTINCT tm.team_id::text ORDER BY tm.team_id::text)
            FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
            WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id
              AND t.status='active' AND t.deleted_at IS NULL
              AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
              AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to),ARRAY[]::text[]) AS team_ids
        FROM platform.user_ref u JOIN LATERAL (
          SELECT b.role_code FROM platform.role_binding b WHERE b.workspace_id=u.workspace_id
            AND b.user_ref_id=u.id AND b.role_code IN ('sales','supervisor','manager')
            AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
          ORDER BY CASE b.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1 ELSE 2 END LIMIT 1
        ) active_role ON true
        WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
          AND security.authorization_subject($2,'person',u.id,NULL)
        ORDER BY u.display_name,u.id""", actor.workspace_id, permission)]
    department = await connection.fetchval("SELECT security.authorization_subject($1,'department',NULL,NULL)", permission)
    own = any(member['id'] == actor.user_id for member in members)
    return {
        'data_source': 'database', 'teams': teams, 'members': members,
        'allowed_scopes': (['self'] if own else []) + (['person'] if members else [])
            + (['team'] if teams else []) + (['department'] if department else []),
        'defaults': {'scope': 'department' if department else 'team' if teams else 'self',
                     'member_id': actor.user_id, 'team_id': teams[0]['id'] if teams else None},
    }


async def resolve_scope(connection, actor, *, scope, member_id=None, team_id=None,
                        account_code=None, team=None, write=False):
    directory = await scope_options(connection, actor)
    if scope not in directory['allowed_scopes']:
        raise PermissionError('所选查看范围不在当前身份权限内')
    selected_member = selected_team = None
    if scope in ('self', 'person'):
        target = str(actor.user_id) if scope == 'self' else str(member_id) if member_id else None
        if scope == 'person' and target is None and account_code:
            target = next((m['id'] for m in directory['members'] if m['account_code'] == account_code), None)
        selected_member = next((m for m in directory['members'] if m['id'] == target), None)
        if selected_member is None:
            raise PermissionError('请选择可查看范围内的具体成员')
        if member_id and scope == 'self' and str(member_id) != str(actor.user_id):
            raise PermissionError('本人视角不能指定其他成员')
        if account_code and selected_member['account_code'] != account_code:
            raise ValueError('所选成员信息不一致，请重新选择')
        if team_id or team:
            raise ValueError('个人视角请只指定成员')
    elif scope == 'team':
        selected_team = next((t for t in directory['teams'] if
            (team_id and t['id'] == str(team_id)) or (not team_id and team and t['name'] == team)), None)
        if selected_team is None:
            # Old clients may omit the only available team, but never silently merge several teams.
            if not team_id and not team and len(directory['teams']) == 1:
                selected_team = directory['teams'][0]
            else:
                raise PermissionError('请选择授权范围内的具体团队')
        if member_id or account_code:
            raise ValueError('团队视角不能同时指定个人')
    elif member_id or team_id or account_code or team:
        raise ValueError('部门视角不能同时指定团队或个人')
    editable = bool(await connection.fetchval("SELECT security.authorization_subject('target.submit',$1,$2::uuid,$3::uuid)",
        'person' if selected_member else scope, selected_member['id'] if selected_member else None,
        selected_team['id'] if selected_team else None))
    if write and not editable:
        raise PermissionError('所选目标不在当前设置权限的范围内')
    return {
        'scope': 'person' if selected_member else scope,
        'user_id': selected_member['id'] if selected_member else None,
        'team_id': selected_team['id'] if selected_team else None,
        'team_ids': [selected_team['id']] if selected_team else [],
        'member_ids': [selected_member['id']] if selected_member else [
            m['id'] for m in directory['members']
            if selected_team is None or selected_team['id'] in m['team_ids']],
        'subject_role': selected_member['role'] if selected_member else None,
        'label': selected_member['display_name'] if selected_member else
                 selected_team['name'] if selected_team else '销售部门',
        'editable': editable,
    }
