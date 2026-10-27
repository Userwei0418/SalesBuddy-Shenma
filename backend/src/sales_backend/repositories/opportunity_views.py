"""Saved presentation configuration; current permissions always bound visibility."""

from sales_backend.contracts.opportunity_workspace import OpportunityWorkspaceConfig
from sales_backend.services.sales_opportunity_visibility import require_read_feature
from sales_backend.db import json_value
from sales_backend.domain.concurrency import require_version
from sales_backend.domain.opportunity_field_catalog import DEFINITION_VERSION, field_catalog
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.dashboard_scope import dashboard_members
from sales_backend.repositories.opportunity_fields import editable_fields
from sales_backend.repositories.team_directory import attach_member_teams, selectable_teams
from sales_backend.domain.opportunity_view_migration import convert_config

PROJECTION = """SELECT v.id,v.name,v.visibility,v.team_id,v.owner_user_ref_id,
 COALESCE(u.display_name,'成员') AS owner_name,t.name AS team_name,v.config,v.version_no,v.created_at,v.updated_at,
 v.owner_user_ref_id=common.current_user_ref_id() AS can_edit,
 v.owner_user_ref_id=common.current_user_ref_id() AS can_delete
 FROM crm.opportunity_saved_view v
 LEFT JOIN platform.user_ref u ON u.id=v.owner_user_ref_id AND u.workspace_id=v.workspace_id
 LEFT JOIN platform.team t ON t.id=v.team_id AND t.workspace_id=v.workspace_id
 WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL"""


def item(row):
    value = dict(row)
    try:
        value['config'], value['migration_notice'] = convert_config(json_value(row['config']))
        value['migration_blocked'] = False
    except ValueError as exc:
        value.update(config=None, migration_blocked=True,
                     migration_notice={'code': 'migration_required', 'message': str(exc)})
    return value


async def scope_options(connection, actor, *, permissions=None):
    permissions = await require_read_feature(connection, permissions)
    teams = await selectable_teams(connection, actor, permission='opportunity.read', permissions=permissions) if permissions.allows('opportunity.read') else []
    owners = await dashboard_members(connection, actor, permission='opportunity.read') if permissions.allows('opportunity.read') else []
    owners = await attach_member_teams(connection, actor, owners, teams)
    self_allowed = await connection.fetchval(
        "SELECT security.authorization_subject('opportunity.read','person',common.current_user_ref_id(),NULL)")
    kinds = ([{'value': 'self', 'label': '本人'}] if self_allowed else []) + [{'value': 'authorized', 'label': '全部可查看'}]
    return {'kinds': kinds, 'teams': [{'id': t['id'], 'name': t['name']} for t in teams],
            'owners': [{'id': u['id'], 'name': u['name'], 'team_ids': u['team_ids']} for u in owners],
            'default': {'kind': 'authorized'}}


async def _read_validation_options(connection, actor, config, permissions):
    """Load only directories actually referenced by this read's selection.

    Keep the exact shared directory SQL (leaf/current/authorized role rules).
    Membership decoration belongs to the options response, not ID validation.
    This partial shape is internal; callers never expose it as UI options.
    """
    await require_read_feature(connection, permissions)
    fields = {clause.field for clause in config.filters if clause.operator not in {'is_empty', 'is_not_empty'}}
    teams = (await selectable_teams(connection, actor, permission='opportunity.read', permissions=permissions)
             if permissions.allows('opportunity.read') and (config.scope.selected_team_ids or 'team' in fields) else [])
    owners = (await dashboard_members(connection, actor, permission='opportunity.read')
              if permissions.allows('opportunity.read') and (config.scope.selected_owner_ids or fields & {'owner', 'customer_owner'}) else [])
    kinds = [{'value': 'authorized'}]
    if config.scope.kind == 'self' and await connection.fetchval(
        "SELECT security.authorization_subject('opportunity.read','person',common.current_user_ref_id(),NULL)"
    ):
        kinds.append({'value': 'self'})
    return {'kinds': kinds, 'teams': teams, 'owners': owners}


async def validate_scope(connection, actor, config, *, permissions=None):
    used = {*(c.key for c in config.columns), *(f.field for f in config.filters), *(s.field for s in config.sorts), config.group_by}
    if used & {'actual_recognized', 'actual_collection'}:
        if permissions is None:
            await require_permission(connection, 'actual.read')
        else:
            permissions.require('actual.read')
    # Mutations retain the original path. Read endpoints pass the effective
    # permissions they have just loaded on this same connection/transaction.
    options = (await scope_options(connection, actor) if permissions is None else
               await _read_validation_options(connection, actor, config, permissions))
    scope = config.scope
    if scope.kind not in {item['value'] for item in options['kinds']}:
        raise PermissionError('所选范围已失效或不在当前授权范围内')
    for selected, values in [(scope.selected_team_ids, options['teams']),
                             (scope.selected_owner_ids, options['owners'])]:
        if {str(ident) for ident in selected} - {str(value['id']) for value in values}:
            raise PermissionError('所选团队或负责人已失效或不在当前授权目录，请刷新')
    # A UUID-shaped partner is not a person. Validate each filter against its
    # actual current directory; unknown/foreign/inactive choices never broaden.
    for clause in config.filters:
        if clause.operator in {'is_empty', 'is_not_empty'}:
            continue
        key = clause.field
        if key not in {'team', 'owner', 'customer_owner', 'partner', 'creator', 'primary_fde_id', 'fde_member_ids', 'participants'}:
            continue
        requested = set(clause.value if isinstance(clause.value, list) else [clause.value])
        if key in {'team', 'owner', 'customer_owner'}:
            valid = {str(row['id']) for row in options['teams' if key == 'team' else 'owners']}
        elif key == 'partner':
            valid = {row['id'] for row in await connection.fetch(
                "SELECT id::text FROM crm.partner WHERE workspace_id=common.current_workspace_id() "
                "AND status='active' AND id=ANY($1::uuid[])", sorted(requested))}
        else:
            valid = {row['id'] for row in await connection.fetch(
                "SELECT id::text FROM platform.user_ref WHERE workspace_id=common.current_workspace_id() "
                "AND status='active' AND deleted_at IS NULL AND id=ANY($1::uuid[]) "
                "AND (NOT $2::boolean OR security.fde_user_is_active(id))", sorted(requested),
                key in {'primary_fde_id', 'fde_member_ids'})}
        if requested - valid:
            raise PermissionError('筛选中的成员、团队或伙伴已失效或不在可选目录，请刷新')
    return options


class OpportunityViewRepository:
    async def options(self, connection, actor):
        permissions = await AuthorizationRepository().effective(connection)
        await require_read_feature(connection, permissions)
        teams = [dict(row) for row in await connection.fetch('SELECT * FROM security.opportunity_view_team_options()')] if permissions.allows('opportunity.read') else []
        publish = ['private'] + (['team'] if any(t['can_publish'] for t in teams) else [])
        if permissions.allows('opportunity.read') and await connection.fetchval('SELECT security.opportunity_view_company_allowed()'):
            publish.append('company')
        fields = field_catalog(editable_fields=editable_fields(can_edit=permissions.allows('opportunity.update'),
                              can_fde=permissions.allows('opportunity.fde_members')))
        if not permissions.allows('actual.read'):
            fields = [f for f in fields if f['key'] not in {'actual_recognized', 'actual_collection'}]
        elif permissions.allows('actual.create') and permissions.allows('access.console') and await connection.fetchval('SELECT security.management_actor()'):
            for field in fields:
                if field['key'] in {'actual_recognized', 'actual_collection'}:
                    field['editable'] = True
        return {'definition_version': DEFINITION_VERSION, 'fields': fields,
                'default_config': {**OpportunityWorkspaceConfig().model_dump(mode='json', exclude_none=True), 'group_by': None},
                'publish_scopes': publish, 'teams': teams,
                'scope_options': await scope_options(connection, actor, permissions=permissions)}

    async def list(self, connection, *, limit=100, offset=0):
        await require_read_feature(connection)
        total = await connection.fetchval('SELECT count(*) FROM crm.opportunity_saved_view WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL')
        rows = await connection.fetch(PROJECTION + ' ORDER BY v.updated_at DESC,v.id DESC LIMIT $1 OFFSET $2', limit, offset)
        return {'items': [item(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset, 'data_source': 'database'}

    async def get(self, connection, view_id, *, lock=False, include_deleted=False, permissions=None):
        if permissions is None:
            await require_read_feature(connection)
        else:
            await require_read_feature(connection, permissions)
        sql = PROJECTION.replace(' AND v.deleted_at IS NULL', '') if include_deleted else PROJECTION
        row = await connection.fetchrow(sql + ' AND v.id=$1::uuid' + (' FOR UPDATE OF v' if lock else ''), view_id)
        if row is None:
            raise LookupError('视图不存在或不在当前权限范围')
        return item(row)

    async def require_share(self, connection, body):
        if body.visibility == 'team' and not await connection.fetchval(
            'SELECT security.opportunity_view_team_allowed($1::uuid,true)', body.team_id):
            raise PermissionError('仅本人有效主管任职且具备该团队商机维护权限时可发布团队视图')
        if body.visibility == 'company' and not await connection.fetchval('SELECT security.opportunity_view_company_allowed()'):
            raise PermissionError('仅公司管理员且具有公司商机范围权限时可发布公司视图')

    async def authorize_mutation(self, connection, actor, body, *, view_id=None, delete=False):
        await require_permission(connection, 'opportunity.read')
        if view_id is not None:
            current = await self.get(connection, view_id, include_deleted=delete)
            if str(current['owner_user_ref_id']) != str(actor.user_id):
                raise PermissionError('只有创建人可修改或删除视图；可复制为本人方案')
        if not delete:
            await self.require_share(connection, body)
            # Withdrawing a stale shared view is always permitted for its owner;
            # the preserved filters still cannot be queried until corrected.
            if not (view_id is not None and body.visibility == 'private'):
                await validate_scope(connection, actor, body.config)

    async def create(self, connection, actor, body):
        await self.authorize_mutation(connection, actor, body)
        await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',
                                 f'opportunity-view:{actor.workspace_id}:{actor.user_id}')
        count = await connection.fetchval('SELECT count(*) FROM crm.opportunity_saved_view WHERE workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id() AND deleted_at IS NULL')
        if count >= 100:
            raise ValueError('最多保存100个本人创建的视图，请先删除不再使用的视图')
        vid = await connection.fetchval('INSERT INTO crm.opportunity_saved_view(workspace_id,owner_user_ref_id,name,visibility,team_id,config) '
            'VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid,$6::jsonb) RETURNING id', actor.workspace_id, actor.user_id,
            body.name, body.visibility, body.team_id, body.config.model_dump(mode='json', exclude_none=True))
        return await self.get(connection, vid)

    async def update(self, connection, actor, view_id, body, *, delete=False):
        await self.authorize_mutation(connection, actor, body, view_id=view_id, delete=delete)
        current = await self.get(connection, view_id, lock=True)
        require_version(current['version_no'], body.version_no)
        if delete:
            await connection.execute('UPDATE crm.opportunity_saved_view SET deleted_at=clock_timestamp() WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()', view_id)
            return {'id': str(view_id), 'deleted': True, 'version_no': current['version_no'] + 1}
        await connection.execute('UPDATE crm.opportunity_saved_view SET name=$2,visibility=$3,team_id=$4::uuid,config=$5::jsonb '
            'WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()', view_id, body.name, body.visibility,
            body.team_id, body.config.model_dump(mode='json', exclude_none=True))
        return await self.get(connection, view_id)

    async def preference(self, connection, actor):
        permissions = await AuthorizationRepository().effective(connection)
        await require_read_feature(connection, permissions)
        row = await connection.fetchrow('SELECT default_view_id,version_no FROM crm.opportunity_view_preference '
            'WHERE workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id()')
        if row is None:
            return {'default_view_id': None, 'version_no': 0, 'effective_view_id': None, 'unavailable_reason': None}
        value = dict(row)
        effective = None
        if value['default_view_id']:
            try:
                view = await self.get(connection, value['default_view_id'], permissions=permissions)
                if not view['migration_blocked']:
                    await validate_scope(connection, actor, OpportunityWorkspaceConfig.model_validate(view['config']),
                                         permissions=permissions)
                    effective = value['default_view_id']
            except (LookupError, PermissionError):
                pass
        return {**value, 'effective_view_id': effective,
                'unavailable_reason': 'unavailable' if value['default_view_id'] and effective is None else None}

    async def authorize_preference(self, connection, actor, body):
        await require_permission(connection, 'opportunity.read')
        if body.default_view_id is not None:
            view = await self.get(connection, body.default_view_id)
            if view['migration_blocked']:
                raise ValueError('请先核对并保存旧方案，再设为默认')
            await validate_scope(connection, actor, OpportunityWorkspaceConfig.model_validate(view['config']))

    async def save_preference(self, connection, actor, body):
        await self.authorize_preference(connection, actor, body)
        await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',
                                 f'opportunity-view-preference:{actor.workspace_id}:{actor.user_id}')
        current = await connection.fetchrow('SELECT version_no FROM crm.opportunity_view_preference '
            'WHERE workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id() FOR UPDATE')
        require_version(current['version_no'] if current else 0, body.version_no)
        if current:
            await connection.execute('UPDATE crm.opportunity_view_preference SET default_view_id=$1::uuid '
                'WHERE workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id()', body.default_view_id)
        else:
            await connection.execute('INSERT INTO crm.opportunity_view_preference(workspace_id,user_ref_id,default_view_id) '
                'VALUES($1::uuid,$2::uuid,$3::uuid)', actor.workspace_id, actor.user_id, body.default_view_id)
        return await self.preference(connection, actor)
