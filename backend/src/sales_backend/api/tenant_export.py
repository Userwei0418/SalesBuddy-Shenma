"""Company-authorized exports. Platform sessions and guessed tenant IDs confer no access."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.live_identity import live_identity_connection
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.async_resources import file_io, settle_owned_task
from sales_backend.db import Database
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.tenant_export import ExportRejected, Limits, build_package, record_prepared
from sales_backend.services.tenant_export_catalog import SECTIONS, require_permissions

router = APIRouter(prefix='/api/v1/console/company-data', tags=['Tenant data export'])
SectionCode = Literal['organization','customers','opportunities','visits','tasks','actuals','targets','partners']
# Bounds temp disk/memory per API process; database advisory locks serialize each
# company's snapshot across processes. No unbounded in-memory request queue.
_active: set[str] = set()


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sections: list[SectionCode] = Field(min_length=1, max_length=8)
    include_originals: bool = False
    reason: str = Field(min_length=3, max_length=200)

    @model_validator(mode='after')
    def validate_selection(self):
        if len(self.sections) != len(set(self.sections)): raise ValueError('导出分类不能重复')
        if self.include_originals and 'visits' not in self.sections: raise ValueError('原件必须与跟进记录一起导出')
        self.reason = self.reason.strip()
        if len(self.reason) < 3: raise ValueError('请填写导出用途')
        return self


class ExportResponse(StreamingResponse):
    def __init__(self, package, authorize: Callable[[], Awaitable], release, *, on_finish=None):
        self.package, self.release, self.on_finish = package, release, on_finish
        self.completed = False
        async def content():
            with package.path.open('rb') as stream:
                while chunk := await file_io(stream.read, 1024 * 1024):
                    # Fresh checks also stop a long download after logout,
                    # suspension, account changes or privilege revocation.
                    await authorize()
                    yield chunk
                self.completed = True
        super().__init__(content(), media_type='application/zip', headers={
            'Content-Disposition':f'attachment; filename="salesbuddy-{package.manifest["workspace_id"]}-{package.export_id}.zip"',
            'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff',
            'Content-Length':str(package.path.stat().st_size),
            'X-Export-Count':str(package.manifest['record_count']), 'X-Export-SHA256':package.sha256,
        })

    async def __call__(self, scope, receive, send):
        outcome = 'cancelled'
        try:
            await super().__call__(scope, receive, send)
            if self.completed:
                outcome = 'sent'
        finally:
            try:
                await settle_owned_task(asyncio.create_task(self.body_iterator.aclose()))
            finally:
                try: await file_io(self.package.cleanup)
                finally:
                    self.release()
                    if self.on_finish:
                        async def record_outcome():
                            try:
                                async with asyncio.timeout(3):
                                    await self.on_finish(outcome)
                            except Exception:
                                # The response is already underway; never turn a
                                # receipt failure into a blind export replay.
                                logging.getLogger(__name__).warning('export transport receipt unavailable')
                        await settle_owned_task(asyncio.create_task(record_outcome()))


def reserve_export(workspace_id):
    """One shared process admission bound for active and closed tenant exports."""
    if workspace_id in _active or len(_active) >= 2:
        raise HTTPException(429, '导出服务正在处理其他请求，请稍后重试', headers={'Retry-After':'10'})
    _active.add(workspace_id)
    return lambda: _active.discard(workspace_id)


@router.get('/options')
async def export_options(identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as conn:
        effective = await AuthorizationRepository().effective(conn)
    effective.require('company.export')
    items = []
    for code, section in SECTIONS.items():
        try: require_permissions(effective, [code], False); allowed = True
        except PermissionError: allowed = False
        items.append({'code':code,'label':section.label,'available':allowed})
    originals = any(g.scope == 'workspace' for g in effective.for_permission('visit.download_original'))
    try: limits = Limits.configured()
    except ExportRejected as exc: raise HTTPException(exc.status, str(exc)) from exc
    return {'sections':items, 'originals_available':originals,
            'max_bytes':limits.max_bytes, 'max_records':limits.max_rows, 'timeout_seconds':limits.timeout_seconds,
            'note':'导出所选分类中有权读取的本公司业务记录；不含草稿、密码、集成密钥或系统配置，不能用于整库恢复。'}


@router.post('/export', response_class=StreamingResponse, responses={
    200:{'description':'UTF-8 NDJSON and optional original files in a tenant-bound ZIP; manifest contains hashes.',
         'content':{'application/zip':{'schema':{'type':'string','format':'binary'}}}},
    409:{'description':'Original unavailable or incomplete'},413:{'description':'Package limits exceeded'},
    429:{'description':'Export busy'},504:{'description':'Export generation timed out'},
})
async def export_business_data(body: ExportRequest, request: Request,
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)):
    actor = identity.actor
    release = reserve_export(actor.workspace_id)
    package = None
    async def authorize():
        async with live_identity_connection(request, identity, database, require_password=True) as (_, effective):
            require_permissions(effective, body.sections, body.include_originals)
    try:
        package = await build_package(database, actor, body.sections, body.include_originals)
        await authorize()
        async with database.transaction(actor) as conn:
            require_permissions(await AuthorizationRepository().effective(conn), body.sections, body.include_originals)
            await record_prepared(conn, actor, package, body.reason)
        return ExportResponse(package, authorize, release)
    except BaseException as exc:
        try:
            if package: await file_io(package.cleanup)
        finally: release()
        if isinstance(exc, ExportRejected): raise HTTPException(exc.status, str(exc)) from exc
        if isinstance(exc, PermissionError): raise HTTPException(403, str(exc)) from exc
        if isinstance(exc, asyncpg.QueryCanceledError): raise HTTPException(504, '导出查询超时，请减少分类后重试') from exc
        if isinstance(exc, (OSError, ValueError)): raise HTTPException(409, '原件或临时存储不可用，未生成数据包，请联系管理员') from exc
        raise
