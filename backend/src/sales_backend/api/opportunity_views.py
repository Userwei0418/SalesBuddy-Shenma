"""Saved v2 views are presentation configuration; query always rechecks scope."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.opportunity_views import OpportunityViewDeleted
from sales_backend.contracts.opportunity_workspace import (
    OpportunityWorkspaceCreate, OpportunityWorkspaceDelete, OpportunityWorkspaceItem,
    OpportunityWorkspaceList, OpportunityWorkspacePreference, OpportunityWorkspaceQuery, OpportunityWorkspaceUpdate,
)
from sales_backend.db import Database
from sales_backend.repositories.opportunity_views import OpportunityViewRepository
from sales_backend.repositories.opportunity_workspace_query import query_workspace
from sales_backend.services.operations import management_write

router = APIRouter(prefix='/api/v1/opportunity-views', tags=['Opportunity views'])
repository = OpportunityViewRepository()


def require_key(key):
    if key is None:
        raise HTTPException(422, '保存视图必须携带 Idempotency-Key，重试请沿用同一标识')


@router.get('/options')
async def options(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.options(connection, identity.actor)


@router.post('/query')
async def query(body: OpportunityWorkspaceQuery, identity: RequestIdentity = Depends(get_identity),
                database: Database = Depends(get_database)):
    try:
        async with database.transaction(identity.actor, readonly=True, isolation='repeatable_read') as connection:
            return await query_workspace(connection, identity.actor, body)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/preference')
async def preference(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.preference(connection, identity.actor)


@router.put('/preference')
async def set_preference(body: OpportunityWorkspacePreference, idempotency_key: MutationKey = None,
                         identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    require_key(idempotency_key)
    return await management_write(database, identity.actor, idempotency_key, 'opportunity_view.preference', body.model_dump(),
        lambda c: repository.save_preference(c, identity.actor, body),
        authorize=lambda c: repository.authorize_preference(c, identity.actor, body))


@router.get('', response_model=OpportunityWorkspaceList)
async def list_views(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
                     identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.list(connection, limit=limit, offset=offset)


@router.get('/{view_id}', response_model=OpportunityWorkspaceItem)
async def detail(view_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await repository.get(connection, view_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post('', status_code=201, response_model=OpportunityWorkspaceItem)
async def create(body: OpportunityWorkspaceCreate, idempotency_key: MutationKey = None,
                 identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    require_key(idempotency_key)
    return await management_write(database, identity.actor, idempotency_key, 'opportunity_view.create', body.model_dump(),
        lambda c: repository.create(c, identity.actor, body),
        authorize=lambda c: repository.authorize_mutation(c, identity.actor, body))


@router.put('/{view_id}', response_model=OpportunityWorkspaceItem)
async def update(view_id: UUID, body: OpportunityWorkspaceUpdate, idempotency_key: MutationKey = None,
                 identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    require_key(idempotency_key)
    return await management_write(database, identity.actor, idempotency_key, f'opportunity_view.update:{view_id}', body.model_dump(),
        lambda c: repository.update(c, identity.actor, view_id, body),
        authorize=lambda c: repository.authorize_mutation(c, identity.actor, body, view_id=view_id))


@router.delete('/{view_id}', response_model=OpportunityViewDeleted)
async def delete(view_id: UUID, body: OpportunityWorkspaceDelete, idempotency_key: MutationKey = None,
                 identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    require_key(idempotency_key)
    return await management_write(database, identity.actor, idempotency_key, f'opportunity_view.delete:{view_id}', body.model_dump(),
        lambda c: repository.update(c, identity.actor, view_id, body, delete=True),
        authorize=lambda c: repository.authorize_mutation(c, identity.actor, body, view_id=view_id, delete=True))
