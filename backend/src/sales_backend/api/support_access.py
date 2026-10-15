"""Company approval and bounded support reads use separate authentication domains."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.platform_control import platform_service, platform_token
from sales_backend.api.console_auth import same_origin
from sales_backend.db import Database
from sales_backend.services.platform_control import PlatformService, digest
from sales_backend.services.support_access import company_support_call

router = APIRouter(tags=['Temporary support access'])
Scope = Literal['organization', 'customers', 'opportunities', 'visits', 'tasks']


class SupportReason(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def strip_reason(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError('请填写操作原因')
        return self


class SupportApproval(SupportReason):
    id: UUID
    operator_id: UUID
    scopes: list[Scope] = Field(min_length=1, max_length=5)
    expires_at: AwareDatetime

    @model_validator(mode='after')
    def unique_scopes(self):
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError('只读功能不能重复')
        return self


async def company_call(database, identity, response, operation, *args):
    response.headers['Cache-Control'] = 'private, no-store'
    return await company_support_call(database, identity.actor, operation, *args)


@router.get('/api/v1/console/support-access/directory')
async def support_directory(response: Response, account: str = Query('', max_length=64),
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    return await company_call(database, identity, response, 'directory', account)


@router.get('/api/v1/console/support-access')
async def support_grants(response: Response, identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database)):
    return {'items': await company_call(database, identity, response, 'grants')}


@router.post('/api/v1/console/support-access', dependencies=[Depends(same_origin)], status_code=201)
async def approve_support(body: SupportApproval, response: Response,
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    return await company_call(database, identity, response, 'approve',
                              body.id, body.operator_id, body.scopes, body.expires_at, body.reason)


@router.post('/api/v1/console/support-access/{grant_id}/revoke', dependencies=[Depends(same_origin)])
async def revoke_support(grant_id: UUID, body: SupportReason, response: Response,
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    return await company_call(database, identity, response, 'revoke', grant_id, body.reason)


@router.get('/api/v1/console/support-access/audit')
async def support_audit(response: Response, before_id: int | None = Query(None, ge=1),
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    return {'items': await company_call(database, identity, response, 'audit', before_id)}


@router.get('/api/v1/platform/support-access')
async def support_assignments(token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return {'items': await service.call('support_assignments(text)', digest(token))}


@router.get('/api/v1/platform/support-access/{grant_id}/records/{scope}')
async def support_records(grant_id: UUID, scope: Scope, after_id: UUID | None = None,
    token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    result = await service.call('support_read(text,uuid,text,uuid)', digest(token), grant_id, scope, after_id)
    # The SQL function returns a denial result so its audit transaction commits.
    if result.get('error'):
        raise HTTPException(403, 'SUPPORT_ACCESS_DENIED')
    return result
