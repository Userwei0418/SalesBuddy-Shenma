"""A separate platform identity never doubles as a company administrator."""
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictInt

from sales_backend.api.console_auth import same_origin
from sales_backend.contracts.platform_configuration import AIServicePolicy, TenantConfigurationStatus
from sales_backend.contracts.platform_runtime import PlatformRuntimePage
from sales_backend.services.platform_control import PlatformService, digest
from sales_backend.security.local_qr import company_entry_scene, company_web_origin, qr_svg

router = APIRouter(tags=['Platform administration'])
COOKIE = 'sales_platform_session'
COOKIE_PATH = '/api/v1/platform'


class PlatformLoginStart(BaseModel):
    account_code: str = Field(min_length=3, max_length=64)
    password: SecretStr


class PlatformLogin(PlatformLoginStart):
    otp: SecretStr = Field(default=SecretStr(''), max_length=6)


class PlatformOperator(BaseModel):
    id: UUID
    account_code: str
    display_name: str
    mfa_enrolled: bool
    mfa_verified: bool
    sensitive_until: AwareDatetime | None
    expires_at: AwareDatetime
    expires_in: int = Field(ge=0, le=1800)


class PlatformSession(BaseModel):
    operator: PlatformOperator
    expires_in: int = Field(ge=0, le=1800)


class PlatformMFAEnrollment(BaseModel):
    secret: str
    uri: str
    qr_svg: str


class CompanyEntryLinks(BaseModel):
    company_code: str
    company_name: str
    admin_path: str
    activation_path: str
    web_qr_svg: str | None
    web_qr_unavailable_reason: str | None
    mini_program_path: str
    mini_program_query: str
    mini_program_scene: str | None
    mini_program_qr_available: Literal[False] = False


class TenantProvision(BaseModel):
    company_code: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{2,47}$')
    name: str = Field(min_length=1, max_length=100)
    purpose: Literal['internal', 'demo', 'customer']
    administrator_account: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$')
    administrator_name: str = Field(min_length=1, max_length=100)


class TenantTransition(BaseModel):
    status: Literal['active', 'suspended', 'closed']
    version_no: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class ActivationReissue(BaseModel):
    version_no: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class TenantActivation(BaseModel):
    company_code: str = Field(min_length=3, max_length=48)
    activation_token: SecretStr
    new_password: SecretStr = Field(min_length=8, max_length=128)


def platform_service(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    database = getattr(request.app.state, 'platform_database', None)
    if database is None:
        raise HTTPException(503, 'PLATFORM_NOT_CONFIGURED')
    return PlatformService(database, getattr(request.app.state, 'platform_mfa_cipher', None),
                           getattr(request.app.state, 'settings', None))


def platform_token(request: Request):
    # Company Authorization headers cannot select a platform identity.
    token = request.cookies.get(COOKIE, '')
    if not 32 <= len(token) <= 256:
        raise HTTPException(401, 'PLATFORM_AUTH_REQUIRED')
    return token


@router.get('/platform', response_class=HTMLResponse)
async def platform_page():
    return HTMLResponse((Path(__file__).parents[1] / 'web/platform.html').read_text(),
                        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@router.get('/company-activate', response_class=HTMLResponse)
async def company_activation_page():
    return HTMLResponse((Path(__file__).parents[1] / 'web/company-activate.html').read_text(),
                        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


def platform_session_response(request, response, token, profile):
    expires_in = int(profile['expires_in'])
    response.set_cookie(COOKIE, token, max_age=expires_in, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', path=COOKIE_PATH)
    response.headers['Cache-Control'] = 'no-store'
    return {'operator': profile, 'expires_in': expires_in}


@router.post('/api/v1/platform/auth/login', dependencies=[Depends(same_origin)], response_model=PlatformSession)
async def platform_login(body: PlatformLogin, request: Request, response: Response,
                         service: PlatformService = Depends(platform_service)):
    token, profile = await service.login(body.account_code, body.password.get_secret_value(),
                                        request.client.host if request.client else 'unknown', body.otp.get_secret_value())
    return platform_session_response(request, response, token, profile)


@router.post('/api/v1/platform/auth/start', dependencies=[Depends(same_origin)], response_model=PlatformSession)
async def platform_start(body: PlatformLoginStart, request: Request, response: Response,
                         service: PlatformService = Depends(platform_service)):
    token, profile = await service.start_login(body.account_code, body.password.get_secret_value(),
                                              request.client.host if request.client else 'unknown')
    return platform_session_response(request, response, token, profile)


@router.get('/api/v1/platform/auth/me')
async def platform_me(token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return {'operator': await service.profile(token)}


@router.post('/api/v1/platform/auth/logout', dependencies=[Depends(same_origin)])
async def platform_logout(request: Request, response: Response, service: PlatformService = Depends(platform_service)):
    token = request.cookies.get(COOKIE, '')
    if token:
        await service.call('logout(text)', digest(token))
    response.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite='strict', secure=request.url.scheme == 'https')
    return {'logged_out': True}


@router.get('/api/v1/platform/tenants')
async def platform_tenants(token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return {'items': await service.call('tenants(text)', digest(token))}


@router.get('/api/v1/platform/tenants/{workspace_id}/entry', response_model=CompanyEntryLinks)
async def tenant_entry(workspace_id: UUID, request: Request, token: str = Depends(platform_token),
                       service: PlatformService = Depends(platform_service)):
    tenants = await service.call('tenants(text)', digest(token))
    selected = next((item for item in tenants if str(item['id']) == str(workspace_id)), None)
    if selected is None:
        raise HTTPException(404, 'TENANT_NOT_FOUND')
    code = selected['company_code']
    query = urlencode({'company': code})
    admin_path, activation_path = '/admin?' + query, '/company-activate?' + query
    origin = company_web_origin(request.app.state.settings, str(request.base_url))
    return CompanyEntryLinks(
        company_code=code, company_name=selected['name'], admin_path=admin_path,
        activation_path=activation_path, web_qr_svg=qr_svg(origin + admin_path) if origin else None,
        web_qr_unavailable_reason=None if origin else 'PUBLIC_WEB_ORIGIN_NOT_CONFIGURED',
        mini_program_path='pages/login/index', mini_program_query=query,
        mini_program_scene=company_entry_scene(code),
    )


@router.get('/api/v1/platform/runtime', response_model=PlatformRuntimePage)
async def platform_runtime(after_id: UUID | None = None, wait_minutes: int = Query(default=15, ge=1, le=1440),
                           token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('runtime_overview(text,uuid,integer)', digest(token), after_id, wait_minutes)


@router.get('/api/v1/platform/tenants/{workspace_id}/configuration', response_model=TenantConfigurationStatus)
async def tenant_configuration(workspace_id: UUID, token: str = Depends(platform_token),
                               service: PlatformService = Depends(platform_service)):
    return await service.call('configuration_status(text,uuid,jsonb)', digest(token), workspace_id,
                              service.shared_presence)


@router.post('/api/v1/platform/tenants', dependencies=[Depends(same_origin)], status_code=201)
async def provision_tenant(body: TenantProvision, idempotency_key: UUID = Header(alias='Idempotency-Key'),
                           token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.provision(token, idempotency_key, body.model_dump())


@router.post('/api/v1/platform/tenants/{workspace_id}/state', dependencies=[Depends(same_origin)])
async def transition_tenant(workspace_id: UUID, body: TenantTransition,
                            token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('transition(text,uuid,integer,text,text,jsonb)', digest(token), workspace_id,
                              body.version_no, body.status, body.reason, service.shared_presence)


@router.post('/api/v1/platform/tenants/{workspace_id}/activation', dependencies=[Depends(same_origin)])
async def reissue_tenant_activation(workspace_id: UUID, body: ActivationReissue,
                                    token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.reissue(token, workspace_id, body.version_no, body.reason)


@router.post('/api/v1/platform/activate', dependencies=[Depends(same_origin)])
async def activate_tenant(body: TenantActivation, request: Request, service: PlatformService = Depends(platform_service)):
    return await service.activate(body.company_code, body.activation_token.get_secret_value(), body.new_password.get_secret_value(),
                                  request.client.host if request.client else 'unknown')


@router.get('/api/v1/platform/audit')
async def platform_audit(before_id: int | None = None, token: str = Depends(platform_token),
                         service: PlatformService = Depends(platform_service)):
    return {'items': await service.call('audit_page(text,bigint)', digest(token), before_id)}


class ModuleSetting(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=50)
    enabled: bool
    expires_at: AwareDatetime | None = None


class TenantModulesSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    modules: list[ModuleSetting] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=500)


@router.get('/api/v1/platform/tenants/{workspace_id}/modules')
async def tenant_modules(workspace_id: UUID, token: str = Depends(platform_token),
                         service: PlatformService = Depends(platform_service)):
    return await service.call('module_settings(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/modules', dependencies=[Depends(same_origin)])
async def save_modules(workspace_id: UUID, body: TenantModulesSave,
                        token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('save_modules(text,uuid,integer,jsonb,text,jsonb)', digest(token), workspace_id,
                              body.version_no, [m.model_dump(mode='json') for m in body.modules], body.reason,
                              service.shared_presence)


class MFAVerification(BaseModel):
    otp: SecretStr = Field(min_length=6, max_length=6)


class TenantJobSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    concurrency_limit: int = Field(ge=1, le=16)
    reason: str = Field(min_length=1, max_length=500)


@router.get('/api/v1/platform/tenants/{workspace_id}/jobs')
async def tenant_job_settings(workspace_id: UUID, token: str = Depends(platform_token),
                              service: PlatformService = Depends(platform_service)):
    return await service.call('job_settings(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/jobs', dependencies=[Depends(same_origin)])
async def save_tenant_job_settings(workspace_id: UUID, body: TenantJobSettings,
                                   token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('save_job_settings(text,uuid,integer,integer,text)', digest(token), workspace_id,
                              body.version_no, body.concurrency_limit, body.reason)


class PlatformReauthentication(MFAVerification):
    password: SecretStr


class TenantAccountQuota(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    account_limit: StrictInt | None = Field(ge=0, le=9007199254740991)
    reason: str = Field(min_length=1, max_length=500)


class TenantStorageQuota(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    limit_bytes: StrictInt | None = Field(ge=0, le=9007199254740991)
    reason: str = Field(min_length=1, max_length=500)


class TenantHTTPLimit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    bucket: Literal['read', 'write', 'upload']
    rate_per_minute: StrictInt | None = Field(ge=1, le=1000000)
    burst: StrictInt | None = Field(ge=1, le=10000)
    reason: str = Field(min_length=1, max_length=500)


@router.get('/api/v1/platform/tenants/{workspace_id}/http-limits')
async def http_limits(workspace_id: UUID, token: str = Depends(platform_token),
                      service: PlatformService = Depends(platform_service)):
    return await service.call('http_limits(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/http-limits', dependencies=[Depends(same_origin)])
async def save_http_limit(workspace_id: UUID, body: TenantHTTPLimit,
                          token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('save_http_limit(text,uuid,integer,text,integer,integer,text)', digest(token), workspace_id,
                              body.version_no, body.bucket, body.rate_per_minute, body.burst, body.reason)


class TenantCallQuota(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    resource: str = Field(pattern='^(ai_calls|asr_calls|tts_calls)$')
    call_limit: StrictInt | None = Field(ge=0, le=9007199254740991)
    period: str = Field(pattern='^(lifetime|month_utc)$')
    reason: str = Field(min_length=1, max_length=500)


@router.get('/api/v1/platform/tenants/{workspace_id}/call-quotas')
async def call_quotas(workspace_id: UUID, token: str = Depends(platform_token),
                      service: PlatformService = Depends(platform_service)):
    return await service.call('call_quotas(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/call-quotas', dependencies=[Depends(same_origin)])
async def save_call_quota(workspace_id: UUID, body: TenantCallQuota,
                          token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('save_call_quota(text,uuid,integer,text,bigint,text,text)', digest(token), workspace_id,
                              body.version_no, body.resource, body.call_limit, body.period, body.reason)


@router.get('/api/v1/platform/tenants/{workspace_id}/storage-quota')
async def storage_quota(workspace_id: UUID, token: str = Depends(platform_token),
                        service: PlatformService = Depends(platform_service)):
    return await service.call('storage_quota(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/storage-quota', dependencies=[Depends(same_origin)])
async def save_storage_quota(workspace_id: UUID, body: TenantStorageQuota,
                             token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('save_storage_quota(text,uuid,integer,bigint,text)', digest(token), workspace_id,
                              body.version_no, body.limit_bytes, body.reason)


@router.get('/api/v1/platform/tenants/{workspace_id}/account-quota')
async def account_quota(workspace_id: UUID, token: str = Depends(platform_token),
                        service: PlatformService = Depends(platform_service)):
    return await service.call('account_quota(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/account-quota', dependencies=[Depends(same_origin)])
async def save_account_quota(workspace_id: UUID, body: TenantAccountQuota,
                             token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.call('save_account_quota(text,uuid,integer,bigint,text)', digest(token), workspace_id,
                              body.version_no, body.account_limit, body.reason)


@router.post('/api/v1/platform/auth/mfa/enroll', dependencies=[Depends(same_origin)], response_model=PlatformMFAEnrollment)
async def begin_mfa(request: Request, token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    return await service.prepare_mfa(token, request.client.host if request.client else 'unknown')


@router.post('/api/v1/platform/auth/mfa/confirm', dependencies=[Depends(same_origin)], response_model=PlatformSession)
async def confirm_mfa(body: MFAVerification, request: Request, response: Response, token: str = Depends(platform_token),
                      service: PlatformService = Depends(platform_service)):
    replacement, profile = await service.complete_mfa(
        token, body.otp.get_secret_value(), request.client.host if request.client else 'unknown', purpose='enroll')
    return platform_session_response(request, response, replacement, profile)


@router.post('/api/v1/platform/auth/mfa/login', dependencies=[Depends(same_origin)], response_model=PlatformSession)
async def finish_platform_login(body: MFAVerification, request: Request, response: Response,
                                token: str = Depends(platform_token), service: PlatformService = Depends(platform_service)):
    replacement, profile = await service.complete_mfa(
        token, body.otp.get_secret_value(), request.client.host if request.client else 'unknown', purpose='login')
    return platform_session_response(request, response, replacement, profile)


@router.post('/api/v1/platform/auth/verify', dependencies=[Depends(same_origin)])
async def verify_sensitive(body: PlatformReauthentication, request: Request, token: str = Depends(platform_token),
                           service: PlatformService = Depends(platform_service)):
    profile = await service.verify_mfa(token, body.otp.get_secret_value(), request.client.host if request.client else 'unknown',
                                       purpose='verify', password=body.password.get_secret_value())
    return {'operator': profile}


class AIServiceSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_no: int = Field(ge=1)
    shared_enabled: bool
    reason: str = Field(min_length=1, max_length=500)


@router.get('/api/v1/platform/tenants/{workspace_id}/ai-service', response_model=AIServicePolicy)
async def tenant_ai_service(workspace_id: UUID, token: str = Depends(platform_token),
                            service: PlatformService = Depends(platform_service)):
    return await service.call('ai_service_settings(text,uuid)', digest(token), workspace_id)


@router.post('/api/v1/platform/tenants/{workspace_id}/ai-service', dependencies=[Depends(same_origin)], response_model=AIServicePolicy)
async def save_tenant_ai_service(workspace_id: UUID, body: AIServiceSave, token: str = Depends(platform_token),
                                 service: PlatformService = Depends(platform_service)):
    return await service.call('save_ai_service(text,uuid,integer,boolean,text)', digest(token), workspace_id,
                              body.version_no, body.shared_enabled, body.reason)
