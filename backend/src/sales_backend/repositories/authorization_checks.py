"""Server-side checks use current database configuration inside the business transaction."""

from sales_backend.domain.authorization import ObjectScope
from sales_backend.repositories.authorization import AuthorizationRepository


async def require_permission(connection, permission, *, opportunity_id=None, task_id=None, customer_id=None, visit_id=None):
    if task_id is not None:
        from sales_backend.domain.tasks import TaskForbidden, TaskNotFound

        if not (await AuthorizationRepository().effective(connection)).allows(permission):
            raise TaskForbidden("当前账号未获此待办功能的授权")
        if not await connection.fetchval("SELECT id FROM workflow.task WHERE id=$1::uuid AND deleted_at IS NULL", str(task_id)):
            raise TaskNotFound("TASK_NOT_FOUND")
    resource = next(((kind, value) for kind, value in (
        ("opportunity", opportunity_id), ("task", task_id), ("customer", customer_id), ("visit", visit_id)
    ) if value is not None), None)
    if resource:
        kind, value = resource
        allowed = await connection.fetchval(f"SELECT security.authorization_{kind}($1,$2::uuid)",
                                            permission, str(value))
        if not allowed:
            if task_id is not None:
                raise TaskForbidden("当前待办不在此操作的授权范围")
            raise PermissionError("当前账号未获此操作或数据范围的授权")
    else:
        (await AuthorizationRepository().effective(connection)).require(permission)


async def opportunity_creation_options(connection, actor, owner_user_id=None):
    effective = await AuthorizationRepository().effective(connection)
    effective.require('opportunity.create')
    user_id = owner_user_id or actor.user_id
    rows = await connection.fetch("""SELECT id::text,name,EXISTS(SELECT 1 FROM platform.team_membership tm
        WHERE tm.workspace_id=platform.team.workspace_id AND tm.team_id=platform.team.id
         AND tm.user_ref_id=$2::uuid AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to) AS owner_member
        FROM platform.team WHERE workspace_id=$1::uuid
        AND status='active' AND deleted_at IS NULL AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to
        ORDER BY name,id""", actor.workspace_id, user_id)
    teams = [dict(id=r['id'],name=r['name']) for r in rows if (
        r.get('owner_member', False) or effective.allows('opportunity.create', ObjectScope(actor.workspace_id, team_id=r['id']))
    ) and effective.allows('opportunity.create', ObjectScope(actor.workspace_id,user_id,r['id'],frozenset({user_id})))]
    allowed = {team['id'] for team in teams}
    default = next((team for team in actor.team_ids if team in allowed), None)
    if default is None and len(teams) == 1:
        default = teams[0]['id']
    return {'teams': teams, 'default_team_id': default}


async def opportunity_creation_form_options(connection, actor):
    """Optional owner choices for the existing create-options endpoint.

    Reuse self/team creation rules; delegated choices match the existing sales
    owner validator and must pass both create and create_for_others scopes.
    No organization-management directory or contact fields are exposed.
    """
    options = await opportunity_creation_options(connection, actor)
    effective = await AuthorizationRepository().effective(connection)
    owners = []
    # INSERT ... RETURNING also obeys opportunity.read RLS. Do not offer a
    # form target that the actor could create but cannot read back.
    self_teams = [team for team in options["teams"] if effective.allows("opportunity.read", ObjectScope(
        actor.workspace_id, actor.user_id, team["id"], frozenset({actor.user_id})))]
    if self_teams:
        person = await connection.fetchrow(
            "SELECT display_name,account_code FROM platform.user_ref WHERE id=$1::uuid AND workspace_id=$2::uuid",
            actor.user_id, actor.workspace_id,
        )
        owners.append({"id": actor.user_id, "name": person["display_name"],
                       "account_code": person["account_code"], "teams": self_teams,
                       "default_team_id": options["default_team_id"] if any(
                           t["id"] == options["default_team_id"] for t in self_teams) else None})
    if effective.allows("opportunity.create_for_others"):
        rows = await connection.fetch("""SELECT DISTINCT ON (u.id) u.id::text,u.display_name AS name,
            u.account_code,m.team_id::text,t.name AS team_name
            FROM platform.user_ref u
            JOIN platform.role_binding r ON r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
             AND r.role_code='sales' AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
            JOIN platform.team_membership m ON m.user_ref_id=u.id AND m.workspace_id=u.workspace_id
             AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
            JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=u.workspace_id
             AND t.status='active' AND t.deleted_at IS NULL
             AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
            WHERE u.workspace_id=$1::uuid AND u.id<>$2::uuid AND u.status='active' AND u.deleted_at IS NULL
            ORDER BY u.id,m.is_primary DESC,m.created_at DESC""", actor.workspace_id, actor.user_id)
        for row in rows:
            scope = ObjectScope(actor.workspace_id, row["id"], row["team_id"], frozenset({row["id"]}))
            if not all(effective.allows(p, scope) for p in ("opportunity.create", "opportunity.create_for_others", "opportunity.read")):
                continue
            owners.append({"id": row["id"], "name": row["name"], "account_code": row["account_code"],
                           "teams": [{"id": row["team_id"], "name": row["team_name"]}],
                           "default_team_id": row["team_id"]})
    return {**options, "owners": owners, "default_owner_id": actor.user_id if self_teams else None}


async def opportunity_creation_owner(connection, actor, data, *, delegated_owner=None):
    effective = await AuthorizationRepository().effective(connection)
    user_id = str(delegated_owner['user_id']) if delegated_owner else actor.user_id
    options = await opportunity_creation_options(connection, actor, user_id)
    allowed = {team['id'] for team in options['teams']}
    selected = delegated_owner['team_id'] if delegated_owner else data.get('owner_team_id')
    if delegated_owner and user_id != actor.user_id and data.get('owner_team_id') is not None:
        # Delegated form choices expose the owner's current primary team only.
        # A stale explicit choice must not silently become another team's fact;
        # older clients that omit it retain the existing primary-team default.
        if str(data['owner_team_id']) != str(selected):
            raise ValueError('负责人所属团队已变化，请刷新后重新选择团队')
    if selected:
        selected = str(selected)
        if selected not in allowed:
            raise PermissionError('所选团队不在创建商机的授权范围')
    else:
        selected = options['default_team_id']
        if selected is None:
            raise ValueError('请选择商机所属团队')
    target = ObjectScope(actor.workspace_id, user_id, selected, frozenset({user_id}))
    effective.require('opportunity.create', target)
    if user_id != actor.user_id:
        effective.require('opportunity.create_for_others', target)
    return {'user_id': user_id, 'team_id': selected}
