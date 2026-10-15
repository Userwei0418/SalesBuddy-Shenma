"""Platform authority uses its own restricted pool and opaque session domain."""
import asyncio
import base64

from sales_backend.security.platform_mfa import authenticator, new_seed, match_step
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from sales_backend.security.local_qr import qr_svg
import hashlib
import json
import secrets

import asyncpg

from sales_backend.auth.passwords import DUMMY_HASH, encode_password, verify_password


class PlatformError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


PUBLIC_FUNCTIONS = (
    'configuration_status(text,uuid,jsonb)', 'transition(text,uuid,integer,text,text,jsonb)',
    'save_modules(text,uuid,integer,jsonb,text,jsonb)',
    'ai_service_settings(text,uuid)', 'save_ai_service(text,uuid,integer,boolean,text)',
    'configuration_status(text,uuid)',
    'runtime_overview(text,uuid,integer)',
    'support_assignments(text)', 'support_read(text,uuid,text,uuid)',
    'consume_attempt(text,text)', 'login_candidate(text)', 'issue_session(uuid,text,text,text,integer,bigint)',
    'begin_login(uuid,text,text,text,integer)',
    'complete_login(text,jsonb,integer,bigint,text,text)',
    'confirm_mfa(text,jsonb,integer,bigint,text,text)',
    'session_profile(text)', 'logout(text)', 'tenants(text)', 'provision(text,uuid,text,jsonb,text)',
    'reissue_activation(text,uuid,integer,text,text)', 'activate(text,text,text)',
    'transition(text,uuid,integer,text,text)', 'audit_page(text,bigint)',
    'module_settings(text,uuid)', 'save_modules(text,uuid,integer,jsonb,text)',
    'job_settings(text,uuid)', 'save_job_settings(text,uuid,integer,integer,text)',
    'account_quota(text,uuid)', 'save_account_quota(text,uuid,integer,bigint,text)',
    'storage_quota(text,uuid)', 'save_storage_quota(text,uuid,integer,bigint,text)',
    'http_limits(text,uuid)', 'save_http_limit(text,uuid,integer,text,integer,integer,text)',
    'call_quotas(text,uuid)', 'save_call_quota(text,uuid,integer,text,bigint,text,text)',
    'prepare_mfa(text,jsonb)', 'mfa_candidate(text,text)', 'finish_mfa(text,text,jsonb,integer,bigint,text)',
)


async def verify_platform_pool(database):
    async with database.connection() as connection:
        elevated = await connection.fetchval(
            'SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=current_user')
        if elevated:
            raise RuntimeError('Platform runtime requires a restricted database role')
        application_schemas = ['platform','crm','activity','workflow','insight','agent','config','ops','security','control']
        if await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[])
            AND c.relkind IN ('r','p','v','m')
            AND has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))""", application_schemas):
            raise RuntimeError('Platform runtime must not directly read or write application tables')
        if await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])
            AND p.prosecdef AND has_schema_privilege(current_user,n.oid,'USAGE')
            AND has_function_privilege(current_user,p.oid,'EXECUTE')
            AND p.oid<>ALL(ARRAY(SELECT f::regprocedure::oid FROM unnest($2::text[]) f)))""",
            application_schemas, ['control.'+signature for signature in PUBLIC_FUNCTIONS]):
            raise RuntimeError('Platform runtime has unexpected elevated function permissions')
        for signature in PUBLIC_FUNCTIONS:
            if not await connection.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", 'control.' + signature):
                raise RuntimeError('Platform runtime function grants are incomplete')


class PlatformService:
    def __init__(self, database, cipher=None, settings=None):
        self.database = database
        self.cipher = cipher
        from sales_backend.services.configuration_readiness import shared_presence
        self.shared_presence = shared_presence(settings)

    async def call(self, signature, *args):
        # SQL names are constants owned by this module, never request parameters.
        if signature not in PUBLIC_FUNCTIONS:
            raise ValueError('Unknown platform operation')
        name, types = signature[:-1].split('(')
        parameters = ','.join(f'${i}::{kind}' for i, kind in enumerate(types.split(','), 1))
        try:
            async with self.database.connection() as connection:
                return await connection.fetchval(f'SELECT control.{name}({parameters})', *args)  # noqa: S608
        except asyncpg.PostgresError as exc:
            errors = {'P7401': (401, 'PLATFORM_AUTH_REQUIRED'), 'P7404': (404, 'TENANT_NOT_FOUND'),
                      'P7403': (403, exc.message), 'P7409': (409, exc.message), 'P7422': (422, exc.message),
                      '23505': (409, 'COMPANY_CODE_ALREADY_EXISTS')}
            if exc.sqlstate in errors:
                raise PlatformError(*errors[exc.sqlstate]) from None
            raise

    async def throttle(self, purpose, account, network):
        key = digest(f'{purpose}:{account.upper()}')
        if not await self.call('consume_attempt(text,text)', key, digest(f'network:{network}')):
            raise PlatformError(429, 'PLATFORM_TRY_LATER')
        return key

    async def login(self, account, password, network, otp=''):
        key = await self.throttle('login', account, network)
        candidate = await self.call('login_candidate(text)', account)
        valid = await asyncio.to_thread(verify_password, password, candidate['password_hash'] if candidate else DUMMY_HASH)
        if not candidate or not valid:
            raise PlatformError(401, 'PLATFORM_LOGIN_FAILED')
        step = self.check_code(candidate, otp) if candidate.get('mfa_secret') else None
        token = secrets.token_urlsafe(48)
        profile = await self.call('issue_session(uuid,text,text,text,integer,bigint)', candidate['id'], candidate['password_hash'], digest(token), key, candidate['mfa_revision'], step)
        return token, profile

    async def start_login(self, account, password, network):
        key = await self.throttle('login', account, network)
        candidate = await self.call('login_candidate(text)', account)
        valid = await asyncio.to_thread(verify_password, password, candidate['password_hash'] if candidate else DUMMY_HASH)
        if not candidate or not valid:
            raise PlatformError(401, 'PLATFORM_LOGIN_FAILED')
        token = secrets.token_urlsafe(48)
        profile = await self.call('begin_login(uuid,text,text,text,integer)', candidate['id'],
                                 candidate['password_hash'], digest(token), key, candidate['mfa_revision'])
        return token, profile

    async def complete_mfa(self, token, otp, network, *, purpose):
        """Promote a restricted session atomically; the browser never retains its old token."""
        if purpose not in {'login', 'enroll'}:
            raise ValueError('Unsupported MFA completion purpose')
        profile = await self.profile(token)
        await self.throttle('mfa', str(profile['id']), network)
        candidate = await self.call('mfa_candidate(text,text)', digest(token), purpose)
        if not candidate:
            raise PlatformError(403, 'PLATFORM_MFA_REQUIRED')
        step = self.check_code(candidate, otp)
        replacement = secrets.token_urlsafe(48)
        function = 'complete_login' if purpose == 'login' else 'confirm_mfa'
        profile = await self.call(function + '(text,jsonb,integer,bigint,text,text)', digest(token),
                                  candidate['mfa_secret'], candidate['mfa_revision'], step,
                                  candidate['password_hash'], digest(replacement))
        return replacement, profile

    async def profile(self, token):
        profile = await self.call('session_profile(text)', digest(token))
        if not profile:
            raise PlatformError(401, 'PLATFORM_AUTH_REQUIRED')
        return profile

    async def provision(self, token, key, body):
        activation = secrets.token_urlsafe(48)
        result = await self.call('provision(text,uuid,text,jsonb,text)', digest(token), key,
                                digest(json.dumps(body, sort_keys=True, separators=(',', ':'))), body, digest(activation))
        return {**result, 'activation_token': None if result['replayed'] else activation,
                'company_code': body['company_code'], 'activation_expires_hours': 24}

    async def activate(self, company, token, password, network):
        await self.throttle('activate', company, network)
        encoded = await asyncio.to_thread(encode_password, password)
        if not await self.call('activate(text,text,text)', company, digest(token), encoded):
            raise PlatformError(422, 'ACTIVATION_INVALID_OR_EXPIRED')
        return {'activated': True, 'awaiting_platform_enable': True}

    async def reissue(self, token, workspace, version, reason):
        activation = secrets.token_urlsafe(48)
        await self.call('reissue_activation(text,uuid,integer,text,text)', digest(token), workspace, version, digest(activation), reason)
        return {'activation_token': activation, 'activation_expires_hours': 24}


    def check_code(self, candidate, otp):
        try:
            if self.cipher is None:
                raise RuntimeCredentialUnavailable()
            seed = self.cipher.open(str(candidate['id']), candidate['mfa_secret'])
        except RuntimeCredentialUnavailable:
            raise PlatformError(503, 'PLATFORM_MFA_NOT_CONFIGURED') from None
        step = match_step(seed, otp)
        if step is None:
            raise PlatformError(403, 'PLATFORM_MFA_INVALID')
        return step

    async def prepare_mfa(self, token, network):
        profile = await self.profile(token)
        await self.throttle('mfa-enroll', str(profile['id']), network)
        if profile['mfa_enrolled']:
            raise PlatformError(403, 'PLATFORM_MFA_ALREADY_ENROLLED')
        if self.cipher is None:
            raise PlatformError(503, 'PLATFORM_MFA_NOT_CONFIGURED')
        seed = new_seed()
        encrypted = self.cipher.seal(str(profile['id']), seed)
        await self.call('prepare_mfa(text,jsonb)', digest(token), encrypted)
        uri = authenticator(seed).get_provisioning_uri(profile['account_code'], 'SalesBuddy Platform')
        return {'secret': base64.b32encode(seed).decode(), 'uri': uri, 'qr_svg': qr_svg(uri)}

    async def verify_mfa(self, token, otp, network, *, purpose, password=None):
        profile = await self.profile(token)
        await self.throttle('mfa', str(profile['id']), network)
        candidate = await self.call('mfa_candidate(text,text)', digest(token), purpose)
        if not candidate:
            raise PlatformError(403, 'PLATFORM_MFA_REQUIRED')
        if purpose == 'verify' and not await asyncio.to_thread(verify_password, password or '', candidate['password_hash']):
            raise PlatformError(403, 'PLATFORM_MFA_INVALID')
        step = self.check_code(candidate, otp)
        return await self.call('finish_mfa(text,text,jsonb,integer,bigint,text)', digest(token), purpose,
                               candidate['mfa_secret'], candidate['mfa_revision'], step, candidate['password_hash'])
