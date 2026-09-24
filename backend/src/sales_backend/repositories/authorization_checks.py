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


async def opportunity_creation_owner(connection, actor, data, *, delegated_owner=None):
    effective = await AuthorizationRepository().effective(connection)
    user_id = str(delegated_owner['user_id']) if delegated_owner else actor.user_id
    options = await opportunity_creation_options(connection, actor, user_id)
    allowed = {team['id'] for team in options['teams']}
    selected = delegated_owner['team_id'] if delegated_owner else data.get('owner_team_id')
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
