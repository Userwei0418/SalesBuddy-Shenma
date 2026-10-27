"""Validation errors cannot become a second transport for raw credentials."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.exceptions import RequestValidationError

from sales_backend.api import closed_company_export, customers, model_api, platform_control
from sales_backend.api.dependencies import get_database, get_identity, get_settings
from sales_backend.api.management_dependencies import get_password_identity, get_system_identity
from sales_backend.api.permission_gate import enforce_route_permission
from sales_backend.main import app, request_validation_error_handler


SECRET = 'Synthetic-Private-Request-42'


@pytest.fixture
def isolated_validation():
    # No lifespan, database or provider is started. Replace only dependencies;
    # the real app, routes, request models and exception handling remain intact.
    dependency = AsyncMock()
    previous = dict(app.dependency_overrides)
    for provider in (get_database, get_settings, get_identity, enforce_route_permission,
                     get_password_identity, get_system_identity, platform_control.platform_service,
                     closed_company_export.service, model_api.service):
        app.dependency_overrides[provider] = lambda: dependency
    yield dependency
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('path,body,field', [
    ('/api/v1/auth/password/login', {'password': SECRET}, 'account_code'),
    ('/api/v1/console/auth/login', {'password': SECRET}, 'account_code'),
    ('/api/v1/platform/auth/login', {'password': SECRET, 'otp': '123456'}, 'account_code'),
    ('/api/v1/platform/activate', {'activation_token': SECRET, 'new_password': SECRET}, 'company_code'),
    ('/api/v1/auth/password', {'new_password': SECRET}, 'old_password'),
    ('/api/v1/console/auth/password', {'old_password': SECRET}, 'new_password'),
    ('/api/v1/auth/refresh', {'refresh_token': SECRET}, 'refresh_token'),
    ('/api/v1/platform/auth/login', {'account_code': 'FIXTURE', 'password': SECRET,
                                    'otp': SECRET}, 'otp'),
    ('/api/v1/platform/auth/mfa/confirm', {'otp': SECRET}, 'otp'),
    ('/api/v1/platform/auth/verify', {'otp': '123456', 'password': {'invalid': SECRET}}, 'password'),
    ('/api/v1/auth/password/login', {'account_code': 'FIXTURE', 'password': {'invalid': SECRET}}, 'password'),
])
async def test_credential_validation_keeps_field_contract_without_echo(path, body, field, isolated_validation):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://validation.invalid',
                                headers={'Origin': 'https://validation.invalid'},
                                cookies={platform_control.COOKIE: 'a'*64}) as client:
        response = await client.post(path, json=body)
    assert response.status_code == 422
    assert response.headers['cache-control'] == 'no-store'
    assert SECRET not in response.text and '123456' not in response.text
    errors = response.json()['detail']
    assert any(error['loc'] == ['body', field] for error in errors)
    assert all(set(error) == {'loc', 'type', 'msg'} for error in errors)
    assert all(error['msg'] == '请求字段不符合要求，请检查填写内容' for error in errors)
    assert not isolated_validation.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value,label', [
    ('source', '公司分配', '客户来源'),
    ('source', SECRET, '客户来源'),
    ('customer_type', '新拓客户', '客户类型'),
    ('customer_type', '存量客户', '客户类型'),
])
async def test_creation_choice_errors_are_actionable_without_input_echo(field, value, label, isolated_validation):
    body = {'name': '合成建档客户', 'industry': '企业软件', 'customer_type': '潜在客户',
            'level_code': 'Tier-1', 'source': '销售自拓', 'target_team': '合成目标团队',
            'contact_name': SECRET, 'contact_title': '总经理', 'contact_role': '决策者', field: value}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://validation.invalid') as client:
        response = await client.post('/api/v1/customers/creation-requests', json=body)
    assert response.status_code == 422 and response.headers['cache-control'] == 'no-store'
    assert SECRET not in response.text
    detail = response.json()['detail']
    assert len(detail) == 1 and detail[0]['loc'] == ['body', field]
    assert detail[0]['type'] == 'literal_error'
    assert detail[0]['msg'].startswith(label) and '请重新选择' in detail[0]['msg']
    assert set(detail[0]) == {'loc', 'type', 'msg'}
    assert not isolated_validation.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['自主拓展', '销售自拓'])
async def test_creation_alias_reaches_existing_mutation_boundary(source, isolated_validation, monkeypatch):
    # Stop at the existing mutation boundary: no repository or database writes.
    mutation = AsyncMock(return_value={'synthetic': True})
    monkeypatch.setattr(customers, 'management_write', mutation)
    body = {'name': '合成建档客户', 'industry': '企业软件', 'customer_type': '潜在客户',
            'level_code': 'Tier-1', 'source': source, 'target_team': '合成目标团队',
            'contact_name': '合成联系人', 'contact_title': '总经理', 'contact_role': '决策者'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://validation.invalid') as client:
        response = await client.post('/api/v1/customers/creation-requests', json=body,
                                     headers={'Idempotency-Key': 'ab3958b5-f01d-4ad6-a550-3e6a3c37f64b'})
    assert response.status_code == 201, response.text
    mutation.assert_awaited_once()
    database, actor, key, operation, normalized, callback = mutation.await_args.args
    assert database is isolated_validation and actor is isolated_validation.actor
    assert str(key) == 'ab3958b5-f01d-4ad6-a550-3e6a3c37f64b' and operation == 'customers.creation_request'
    assert callable(callback)
    assert normalized['source'] == '销售自拓'
    assert {field: normalized[field] for field in body if field != 'source'} == {
        field: value for field, value in body.items() if field != 'source'}
    assert normalized['company_reference'] is None and normalized['target_team_id'] is None
    assert body['source'] == source and not isolated_validation.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('path,method,location,error_type,actionable', [
    ('/api/v1/customers/creation-requests', 'POST', ('body', 'source'), 'literal_error', True),
    ('/api/v1/customers/creation-requests', 'POST', ('body', 'customer_type'), 'literal_error', True),
    ('/api/v1/customers/creation-requests', 'GET', ('body', 'source'), 'literal_error', False),
    ('/api/v1/customers', 'POST', ('body', 'source'), 'literal_error', False),
    ('/api/v1/customers/creation-requests', 'POST', ('body', 'source'), 'value_error', False),
    ('/api/v1/customers/creation-requests', 'POST', ('body', 'source', 'nested'), 'literal_error', False),
    ('/api/v1/customers/creation-requests', 'POST', ('body', 'password'), 'literal_error', False),
])
async def test_creation_messages_are_endpoint_field_and_type_scoped(path, method, location, error_type, actionable):
    request = SimpleNamespace(method=method, url=SimpleNamespace(path=path))
    error = RequestValidationError([{'loc': location, 'type': error_type, 'input': SECRET,
                                    'msg': SECRET, 'ctx': {'expected': SECRET}}])
    response = await request_validation_error_handler(request, error)
    assert SECRET.encode() not in response.body
    item = json.loads(response.body)['detail'][0]
    assert item['loc'] == list(location) and item['type'] == error_type
    assert ('请重新选择' in item['msg']) == actionable
    assert set(item) == {'loc', 'type', 'msg'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('path,body', [
    ('/api/v1/admin/model-apis/text/tests', {'api_key': SECRET}),
    ('/api/v1/closed-company-export/login', {'password': SECRET}),
])
async def test_existing_private_routes_still_hide_key_and_login_body(path, body, isolated_validation):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://validation.invalid',
                                headers={'Origin': 'https://validation.invalid'}) as client:
        response = await client.post(path, json=body)
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], str)
    assert response.headers['cache-control'] == 'no-store'
    assert SECRET not in response.text and 'input' not in response.text
    assert not isolated_validation.mock_calls


@pytest.mark.asyncio
async def test_custom_validator_message_context_and_input_are_not_repeated():
    error = RequestValidationError([{
        'loc': ('body', 'password'), 'type': 'value_error',
        'msg': 'Rejected value '+SECRET, 'input': {'password': SECRET},
        'ctx': {'error': ValueError(SECRET)},
    }])
    response = await request_validation_error_handler(None, error)
    assert SECRET.encode() not in response.body
    body = json.loads(response.body)
    assert body['code'] == 'VALIDATION_ERROR'
    assert body['retryable'] is False and body['recovery_action'] == 'correct_request'
    assert body['detail'] == [{
        'loc': ['body', 'password'], 'type': 'value_error',
        'msg': '请求字段不符合要求，请检查填写内容',
    }]
    assert response.headers['cache-control'] == 'no-store'
