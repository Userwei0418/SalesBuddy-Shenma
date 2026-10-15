"""The only HTTP capability available to a closed company's own administrator."""
import asyncio
import logging
from pathlib import Path
import re
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from sales_backend.api.console_auth import same_origin
from sales_backend.api.dependencies import get_database
from sales_backend.api.tenant_export import ExportRequest, ExportResponse, reserve_export
from sales_backend.async_resources import file_io, settle_owned_task
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.closed_company_export import ClosedExportService, ClosedExportSource, digest, export_actor
from sales_backend.services.password_auth import LoginThrottled
from sales_backend.services.tenant_export import ExportRejected, Limits, build_package
from sales_backend.services.tenant_export_catalog import SECTIONS, require_permissions

PREFIX = '/api/v1/closed-company-export'
COOKIE = 'sales_closed_export'
PAGE = Path(__file__).resolve().parent.parent / 'web' / 'closed-company-export.html'
PROFILE_FIELDS = ('session_id', 'workspace_id', 'user_id', 'company_code', 'company_name',
                  'account_code', 'display_name', 'expires_at')


class ClosedExportRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def handle(request):
            try:
                result = await handler(request)
                result.headers['Cache-Control'] = 'private, no-store'
                return result
            except RequestValidationError as exc:
                # Model-level errors retain raw input, including SecretStr
                # values before validation. Never echo the submitted body.
                raise HTTPException(422, '请求字段不符合要求，请检查填写内容',
                                    headers={'Cache-Control':'no-store'}) from exc
            except ExportRejected as exc:
                raise HTTPException(exc.status, str(exc), headers={'Cache-Control':'no-store'}) from exc
            except LoginThrottled as exc:
                raise HTTPException(429, str(exc), headers={'Retry-After':str(exc.retry_after),
                                                          'Cache-Control':'no-store'}) from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc), headers={'Cache-Control':'no-store'}) from exc
            except asyncpg.QueryCanceledError as exc:
                raise HTTPException(504, '导出查询超时，请减少分类后重试', headers={'Cache-Control':'no-store'}) from exc
            except TimeoutError as exc:
                raise HTTPException(504, '处理超时，请先核对会话和导出状态，再决定是否重试',
                                    headers={'Cache-Control':'no-store'}) from exc
        return handle


router = APIRouter(tags=['Closed company data export'], route_class=ClosedExportRoute)


class ClosedLogin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_code: str = Field(min_length=1, max_length=128)
    account_code: str = Field(min_length=1, max_length=64)
    password: SecretStr

    @model_validator(mode='after')
    def identifiers(self):
        self.company_code, self.account_code = self.company_code.strip(), self.account_code.strip()
        if not self.company_code or not self.account_code:
            raise ValueError('请填写公司码和账号名或手机号')
        return self


def public_profile(profile):
    return {**{field:profile[field] for field in PROFILE_FIELDS}, 'company_status':'closed'}


def read_cookie_hash(request: Request, *, optional=False):
    if request.headers.get('X-Company-ID') or request.headers.get('Authorization'):
        raise HTTPException(403, '请通过关闭公司专用入口登录，不能切换公司或复用普通登录凭据')
    token = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{64}', token):
        if optional:
            return None
        raise HTTPException(401, '导出会话无效或已过期，请重新登录')
    return digest(token)


def cookie_hash(request: Request):
    return read_cookie_hash(request)


def service(database=Depends(get_database)):
    return ClosedExportService(database)


@router.get('/closed-company-export', response_class=HTMLResponse, include_in_schema=False)
async def closed_export_page():
    return HTMLResponse(PAGE.read_text(encoding='utf-8'), headers={
        'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'same-origin',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })


@router.post(PREFIX+'/login', dependencies=[Depends(same_origin)])
async def closed_export_login(body: ClosedLogin, request: Request, response: Response,
                              exports: ClosedExportService = Depends(service)):
    previous_hash = read_cookie_hash(request, optional=True)
    token, profile = await exports.login(body.company_code, body.account_code, body.password.get_secret_value(),
                                        request.client.host if request.client else 'unknown', previous_hash=previous_hash)
    response.set_cookie(COOKIE, token, max_age=900, httponly=True, secure=request.url.scheme=='https',
                        samesite='strict', path=PREFIX)
    return public_profile(profile)


@router.get(PREFIX+'/session')
async def closed_export_session(token_hash: str = Depends(cookie_hash), exports: ClosedExportService = Depends(service)):
    return public_profile(await exports.call('status', token_hash))


@router.post(PREFIX+'/logout', dependencies=[Depends(same_origin)])
async def closed_export_logout(request: Request, response: Response, exports: ClosedExportService = Depends(service)):
    token_hash = read_cookie_hash(request, optional=True)
    if token_hash:
        await exports.call('logout', token_hash)
    response.delete_cookie(COOKIE, path=PREFIX, httponly=True, secure=request.url.scheme=='https', samesite='strict')
    return {'logged_out':True}


@router.get(PREFIX+'/options')
async def closed_export_options(token_hash: str = Depends(cookie_hash), exports: ClosedExportService = Depends(service)):
    profile = await exports.call('status', token_hash)
    effective = AuthorizationRepository.from_snapshot(profile)
    items = []
    for code, section in SECTIONS.items():
        try:
            require_permissions(effective, [code], False)
            allowed = True
        except PermissionError:
            allowed = False
        items.append({'code':code, 'label':section.label, 'available':allowed})
    limits = Limits.configured()
    originals = any(g.scope=='workspace' for g in effective.for_permission('visit.download_original'))
    return {'sections':items, 'originals_available':originals, 'max_bytes':limits.max_bytes,
            'max_records':limits.max_rows, 'timeout_seconds':limits.timeout_seconds,
            'note':'公司保持关闭；仅可导出获授权的历史业务数据，不含密码、集成密钥、系统配置或未确认草稿。'}


@router.post(PREFIX+'/export', dependencies=[Depends(same_origin)], response_class=StreamingResponse,
             responses={200:{'description':'Company-bound portability ZIP from a closed tenant',
                             'content':{'application/zip':{'schema':{'type':'string','format':'binary'}}}}})
async def closed_export_download(body: ExportRequest, token_hash: str = Depends(cookie_hash),
                                 exports: ClosedExportService = Depends(service)):
    export_id = uuid.uuid4().hex
    detail = {'sections':body.sections, 'include_originals':body.include_originals, 'reason':body.reason}
    package = None
    release = lambda: None

    async def authorize():
        current = await exports.call('authorize', token_hash, body.sections, body.include_originals)
        if any(current[key] != profile[key] for key in ('session_id','workspace_id','user_id')):
            raise ExportRejected('导出身份已变化，请重新登录', 401)

    async def event(outcome, *, error_code=None):
        await exports.call('event', token_hash, export_id, outcome,
                           {**detail, **({'error_code':error_code} if error_code else {})})

    try:
        profile = await exports.call('authorize', token_hash, body.sections, body.include_originals)
        actor = export_actor(profile)
        release = reserve_export(actor.workspace_id)
        source = ClosedExportSource(exports.database, token_hash, profile, body.sections, body.include_originals)
        package = await build_package(exports.database, actor, body.sections, body.include_originals,
                                      source=source, export_id=export_id)
        await authorize()
        detail.update(sha256=package.sha256, record_count=package.manifest['record_count'])
        # A durable prepared receipt is mandatory before sending any ZIP bytes.
        await event('prepared')
        return ExportResponse(package, authorize, release, on_finish=event)
    except BaseException as exc:
        cancelled = isinstance(exc, asyncio.CancelledError)
        denied = isinstance(exc, PermissionError) or (isinstance(exc, ExportRejected) and exc.status in (401,403))
        try:
            if package:
                await file_io(package.cleanup)
        finally:
            release()
        async def record_failure():
            try:
                async with asyncio.timeout(3):
                    # SQL attributes this only to an existing dedicated session;
                    # a guessed token cannot forge a company/user audit actor.
                    await event('cancelled' if cancelled else 'denied' if denied else 'failed',
                                error_code='CANCELLED' if cancelled else 'ACCESS_DENIED' if denied else 'EXPORT_FAILED')
            except Exception:
                logging.getLogger(__name__).warning('closed export failure receipt unavailable')
        await settle_owned_task(asyncio.create_task(record_failure()))
        if isinstance(exc, (OSError, ValueError)) and not isinstance(exc, (ExportRejected, TimeoutError)):
            raise ExportRejected('原件或临时存储不可用，未生成数据包，请联系管理员', 409) from exc
        raise
