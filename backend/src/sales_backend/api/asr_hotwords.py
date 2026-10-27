"""Company terminology administration is independent of model configuration rights."""
from uuid import UUID

from fastapi import APIRouter, Depends

from sales_backend.api.dependencies import get_database, get_settings
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.domain.asr import (AsrBudgetPolicy, AsrCapacityRelease, AsrError, HotwordRestore, HotwordSave, LIMITS, Scene,
                                      model_info, selected_words)
from sales_backend.repositories import asr_hotwords as repo
from sales_backend.services.authorization import require_permission
from sales_backend.services.runtime_config import load_runtime_configuration

router = APIRouter(prefix='/api/v1/admin/asr-hotwords', tags=['ASR terminology'])


async def authorize(connection, write=False):
    await require_permission(connection, 'access.console')
    await require_permission(connection, 'asr_hotword.read')
    if write:
        await require_permission(connection, 'asr_hotword.manage')


async def presentation(row, database, actor, settings):
    from sales_backend.services.ai_service_policy import AIServiceUnavailable
    from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
    try:
        runtime = await load_runtime_configuration(database, actor, settings, purpose='asr')
        model = model_info(runtime.settings)
    except (AIServiceUnavailable, RuntimeCredentialUnavailable):
        model = {'supported': False, 'available': False, 'name': ''}
    return {k: v for k, v in {**row, 'limits': LIMITS, 'model': model}.items() if k != 'workspace_id'}


@router.get('')
async def list_hotwords(identity=Depends(get_management_identity), database=Depends(get_database), settings=Depends(get_settings)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        await authorize(conn)
        row = await repo.current(conn)
    return await presentation(row, database, identity.actor, settings)


@router.put('')
async def save_hotwords(body: HotwordSave, identity=Depends(get_management_identity), database=Depends(get_database), settings=Depends(get_settings)):
    async with database.transaction(identity.actor) as conn:
        await authorize(conn, True)
        row = await repo.save(conn, identity.actor, body)
    return await presentation(row, database, identity.actor, settings)


@router.get('/versions')
async def list_versions(identity=Depends(get_management_identity), database=Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        await authorize(conn)
        return {'items': await repo.versions(conn)}


@router.get('/versions/{number}')
async def get_version(number: int, identity=Depends(get_management_identity), database=Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        await authorize(conn)
        row = await repo.version(conn, number)
        return {k: v for k, v in row.items() if k != 'workspace_id'}


@router.post('/restore')
async def restore_version(body: HotwordRestore, identity=Depends(get_management_identity), database=Depends(get_database), settings=Depends(get_settings)):
    async with database.transaction(identity.actor) as conn:
        await authorize(conn, True)
        row = await repo.save(conn, identity.actor, body, source_version=body.source_version)
    return await presentation(row, database, identity.actor, settings)


@router.get('/preview')
async def preview_hotwords(scene: Scene = 'all', identity=Depends(get_management_identity), database=Depends(get_database), settings=Depends(get_settings)):
    result = await list_hotwords(identity, database, settings)
    words = selected_words(result['items'], scene)
    return {'version': result['version'], 'scene': scene, 'terms': list(words), 'count': len(words),
            'model': result['model'], 'limits': LIMITS}


@router.get('/operations/{operation_id}')
async def get_operation(operation_id: UUID, identity=Depends(get_management_identity), database=Depends(get_database), settings=Depends(get_settings)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        await authorize(conn)
        operation = await repo.operation(conn, operation_id)
        if not operation:
            raise AsrError('ASR_OPERATION_NOT_FOUND', 404)
        row = await repo.version(conn, operation['version'])
    return await presentation({**row, 'status': 'completed'}, database, identity.actor, settings)


@router.get('/budget-policy')
async def get_budget_policy(identity=Depends(get_management_identity), database=Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        await require_permission(conn, 'ai.usage_rules_manage')
        row = await conn.fetchrow('''SELECT daily_seconds,monthly_seconds,user_daily_seconds,
            company_concurrency,user_concurrency FROM config.asr_budget_policy
            WHERE workspace_id=common.current_workspace_id()''')
        return dict(row) if row else AsrBudgetPolicy().model_dump()


@router.put('/budget-policy')
async def save_budget_policy(body: AsrBudgetPolicy, identity=Depends(get_management_identity), database=Depends(get_database)):
    async with database.transaction(identity.actor) as conn:
        await require_permission(conn, 'ai.usage_rules_manage')
        await conn.execute('''INSERT INTO config.asr_budget_policy(workspace_id,daily_seconds,monthly_seconds,
            user_daily_seconds,company_concurrency,user_concurrency) VALUES($1::uuid,$2,$3,$4,$5,$6)
            ON CONFLICT(workspace_id) DO UPDATE SET daily_seconds=EXCLUDED.daily_seconds,
            monthly_seconds=EXCLUDED.monthly_seconds,user_daily_seconds=EXCLUDED.user_daily_seconds,
            company_concurrency=EXCLUDED.company_concurrency,user_concurrency=EXCLUDED.user_concurrency,
            updated_at=clock_timestamp()''', identity.actor.workspace_id, body.daily_seconds, body.monthly_seconds,
            body.user_daily_seconds, body.company_concurrency, body.user_concurrency)
    return body.model_dump()


@router.get('/unknown-attempts')
async def unknown_attempts(identity=Depends(get_management_identity), database=Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        await require_permission(conn, 'ai.usage_rules_manage')
        return {'items': await conn.fetchval('SELECT security.asr_unknown_attempts()')}


@router.post('/attempts/{operation_id}/release-capacity')
async def release_capacity(operation_id: UUID, body: AsrCapacityRelease,
                           identity=Depends(get_management_identity), database=Depends(get_database)):
    async with database.transaction(identity.actor) as conn:
        await require_permission(conn, 'ai.usage_rules_manage')
        resolved = await conn.fetchval('SELECT security.release_asr_capacity($1::uuid,$2::uuid,$3,$4)',
                                      str(operation_id), str(body.owner_id), body.evidence, body.resolution_kind)
        if not resolved:
            raise AsrError('ASR_OPERATION_NOT_FOUND', 404)
        kind = await conn.fetchval('''SELECT resolution_kind FROM config.asr_capacity_resolution
            WHERE workspace_id=common.current_workspace_id() AND owner_id=$1::uuid AND operation_id=$2::uuid''',
            str(body.owner_id), str(operation_id))
    return {'status': 'capacity_released', 'result_status': 'unknown', 'automatic_retry': False,
            'capacity_scope': 'local_dispatch', 'resolution_kind': kind,
            'provider_outcome_confirmed': kind == 'provider_finished'}
