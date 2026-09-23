"""Workspace-scoped personal device login. No SSH, admin key or console JWT is issued."""
import os
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, make_response
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized, TooManyRequests, HTTPException
from sqlalchemy import select
from extensions.ext_redis import redis_client as redis
from core.db.session_factory import session_factory
from models import Account, TenantAccountJoin
from libs.passport import PassportService
from libs.token import extract_console_cookie_token, check_csrf_token
from fde_cli.worker import execute

bp = Blueprint('fde_cli', __name__, url_prefix='/fde-cli/v1')
WORKSPACE = os.environ.get('FDE_CLI_WORKSPACE_ID', '')
ALLOWED_ACCOUNTS = frozenset(filter(None, os.environ.get('FDE_CLI_ALLOWED_ACCOUNTS', '').split(',')))
ORIGIN = os.environ.get('CONSOLE_WEB_URL', '').rstrip('/')
TOKEN_TTL = 7 * 86400
DEVICE_TTL = 600
PREFIX = 'fde:personal-cli:v1:'
logger = logging.getLogger('fde_cli.audit')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def obj():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise BadRequest('JSON object required')
    return value


def limit(tag, count, seconds):
    key = PREFIX + 'rate:' + tag
    # Atomic fixed window; no immortal counters if a worker exits mid-request.
    n = redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n", 1, key, seconds)
    if n > count:
        raise TooManyRequests('请求过于频繁，请稍后重试')


def allowed(account_id):
    if account_id not in ALLOWED_ACCOUNTS:
        raise Forbidden('此账号尚未获准使用个人 CLI')
    with session_factory.create_session() as session:
        account = session.get(Account, account_id)
        member = session.scalar(select(TenantAccountJoin).where(TenantAccountJoin.account_id == account_id, TenantAccountJoin.tenant_id == WORKSPACE))
        if not account or str(account.status) != 'active' or not member or str(member.role) not in ('owner', 'admin', 'editor'):
            raise Forbidden('授权工作空间 成员资格或编辑权限已失效')
        return dict(id=account.id, email=account.email, name=account.name, role=str(member.role))


def browser_account(mutate=False):
    if request.headers.get('Authorization'):
        raise Unauthorized('请使用平台网页登录授权')
    token = extract_console_cookie_token(request)
    if not token:
        raise Unauthorized('请先登录平台')
    try:
        payload = PassportService().verify(token)
    except Exception:
        raise Unauthorized('网页登录已过期') from None
    account_id = payload.get('user_id')
    if not account_id or payload.get('token_source'):
        raise Unauthorized('无效的网页登录身份')
    if mutate:
        if request.headers.get('Origin') != ORIGIN:
            raise Forbidden('无效的请求来源')
        check_csrf_token(request, account_id)
    return allowed(account_id)


def auth():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer fde_cli_') or len(header) > 256:
        raise Unauthorized('缺少个人 CLI 凭据，请执行 auth login')
    key = PREFIX + 'token:' + digest(header[7:])
    value = redis.get(key)
    if not value:
        raise Unauthorized('CLI 授权已过期或撤销，请重新登录')
    grant = json.loads(value)
    if grant.get('workspace_id') != WORKSPACE or grant['expires_at'] <= time.time():
        raise Unauthorized('无效的授权范围或有效期')
    account = allowed(grant['account_id'])  # No cached membership decisions.
    return key, grant, account


@bp.before_request
def bounded_input():
    if request.content_length and request.content_length > 262144:
        raise BadRequest('请求超过 256KB')


@bp.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@bp.errorhandler(Exception)
def error(exc):
    if isinstance(exc, HTTPException):
        return jsonify(ok=False, error=exc.description), exc.code
    logger.exception('personal CLI server error')
    return jsonify(ok=False, error='服务执行失败，请联系管理员查看日志'), 500


@bp.post('/auth/start')
def start():
    limit('start:' + digest(request.remote_addr or 'unknown'), 15, 60)
    data = obj()
    label = str(data.get('label', 'Codex CLI'))[:80]
    device, approval = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    code = secrets.token_hex(3).upper()
    state = dict(status='pending', label=label, code=code)
    key = PREFIX + 'device:' + digest(device)
    with redis.pipeline(transaction=True) as pipe:
        pipe.set(key, json.dumps(state), ex=DEVICE_TTL)
        pipe.set(PREFIX + 'approval:' + digest(approval), key, ex=DEVICE_TTL)
        pipe.execute()
    return jsonify(ok=True, data=dict(device_code=device, user_code=code, verification_uri=ORIGIN+'/fde-cli/v1/authorize?request='+approval, expires_in=DEVICE_TTL, interval=3))


def approval_state(code):
    if not isinstance(code, str) or len(code) > 100:
        raise BadRequest('无效请求')
    key = redis.get(PREFIX + 'approval:' + digest(code))
    value = redis.get(key) if key else None
    if not value:
        raise BadRequest('授权请求已过期，请重新发起登录')
    return key, json.loads(value)


@bp.get('/auth/context')
def context():
    account = browser_account()
    _, state = approval_state(request.args.get('request'))
    return jsonify(ok=True, data=dict(account=account, workspace='授权工作空间', workspace_id=WORKSPACE, label=state['label'], user_code=state['code'], status=state['status'], expires_days=7))


@bp.post('/auth/approve')
def approve():
    account = browser_account(mutate=True)
    limit('approve:' + account['id'], 15, 60)
    data = obj()
    key, state = approval_state(data.get('request'))
    token = 'fde_cli_' + secrets.token_urlsafe(32)
    grant = dict(account_id=account['id'], workspace_id=WORKSPACE, expires_at=int(time.time())+TOKEN_TTL, label=state['label'])
    state.update(status='approved', token=token, grant=grant)
    token_key = PREFIX + 'token:' + digest(token)
    # Atomic pending->approved transition plus token creation; concurrent/replayed approvals fail.
    result = redis.eval("local v=redis.call('GET',KEYS[1]); if not v or cjson.decode(v).status~='pending' then return 0 end; local ttl=redis.call('TTL',KEYS[1]); if ttl<1 then return 0 end; redis.call('SET',KEYS[1],ARGV[1],'EX',ttl); redis.call('SET',KEYS[2],ARGV[2],'EX',ARGV[3]); return 1", 2, key, token_key, json.dumps(state), json.dumps(grant), TOKEN_TTL)
    if not result:
        raise BadRequest('请求已经处理或过期')
    logger.info('approved account=%s workspace=%s', account['id'], WORKSPACE)
    return jsonify(ok=True, data=dict(approved=True))


@bp.post('/auth/poll')
def poll():
    device = obj().get('device_code')
    if not isinstance(device, str) or len(device) > 100:
        raise BadRequest('无效请求')
    limit('poll:' + digest(device), 30, 60)
    key = PREFIX + 'device:' + digest(device)
    value = redis.eval("local v=redis.call('GET',KEYS[1]); if v and cjson.decode(v).status=='approved' then redis.call('DEL',KEYS[1]) end; return v", 1, key)
    if not value:
        raise BadRequest('授权请求已过期或已被领取，请重新登录')
    state = json.loads(value)
    if state['status'] == 'pending':
        return jsonify(ok=False, error='authorization_pending'), 202
    allowed(state['grant']['account_id'])
    return jsonify(ok=True, data=dict(token=state['token'], **state['grant']))


@bp.get('/auth/me')
def me():
    _, grant, account = auth()
    return jsonify(ok=True, data=dict(account=account, workspace_id=WORKSPACE, workspace='授权工作空间', expires_at=grant['expires_at']))


@bp.post('/auth/logout')
def logout():
    key, _, _ = auth()
    redis.delete(key)
    return jsonify(ok=True, data=dict(revoked=True))


@bp.post('/agent')
def manage():
    _, grant, account = auth()
    limit('manage:' + account['id'], 60, 60)
    data = obj()
    if data.get('workspace') not in ('fde', WORKSPACE):
        raise Forbidden('此 CLI 授权仅允许 授权工作空间')
    if data.get('action') not in ('doctor', 'list', 'get', 'create', 'configure', 'publish'):
        raise BadRequest('不允许的操作')
    if set(data) - {'workspace', 'action', 'id', 'spec', 'patch', 'revision', 'dry_run', 'note'}:
        raise BadRequest('不支持的参数')
    logger.info('request account=%s workspace=%s action=%s agent=%s', account['id'], WORKSPACE, data['action'], data.get('id'))
    try:
        result = execute(data, account_id=account['id'], allowed_workspaces={WORKSPACE}, allowed_roles=('owner', 'admin', 'editor'), app_instance=current_app._get_current_object())
    except (ValueError, PermissionError) as exc:
        return jsonify(ok=False, error=str(exc), created_agent_id=data.get('created_agent_id')), 409 if '版本不匹配' in str(exc) else 400
    logger.info('success account=%s workspace=%s action=%s agent=%s', account['id'], WORKSPACE, data['action'], result.get('id'))
    if data['action'] == 'doctor':
        result['transport'] = 'personal-https'
    return jsonify(ok=True, data=result)


@bp.get('/authorize')
def page():
    return (Path(__file__).with_name('authorize.html').read_text(), 200, {'Content-Type':'text/html; charset=utf-8'})


@bp.get('/authorize.js')
def js():
    return (Path(__file__).with_name('authorize.js').read_text(), 200, {'Content-Type':'application/javascript'})


@bp.get('/authorize.css')
def css():
    return (Path(__file__).with_name('authorize.css').read_text(), 200, {'Content-Type':'text/css'})
