"""Explain one authenticated reference, without recalculating or writing facts.

The optional provider owns its bounded model call and strict model validator.
It receives a disposable copy, not authority to change the backend reference.
Every authority/version check uses a new actor-scoped readonly RR transaction.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import re
from uuid import UUID

import asyncpg

from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.materials import OpportunityMaterialRepository
from sales_backend.services.agent_platform.routing import AgentUnavailable, ProviderUnavailable


class MaterialExplanationError(Exception):
    def __init__(self, code: str, status: int, detail: str):
        super().__init__(detail)
        self.code, self.status, self.detail = code, status, detail


def _permission_changed():
    return MaterialExplanationError('MATERIAL_EXPLANATION_PERMISSION_CHANGED', 403,
                                    '当前账号权限或数据范围已变化，请刷新后重试')


def _reference_changed():
    return MaterialExplanationError('MATERIAL_EXPLANATION_REFERENCE_CHANGED', 409,
                                    '商机或物料参考已变化，请重新获取预览')


def _identifier(value):
    result = UUID(str(value))
    if not result.int:
        raise ValueError('nonzero identifier required')
    return str(result)


def _version(value):
    # PostgreSQL/Pydantic can supply int or its decimal JSON string. Compare
    # their integer meaning, never bool, floats, decimal fractions or whitespace.
    if isinstance(value, bool) or not (isinstance(value, int) or
                                     isinstance(value, str) and re.fullmatch(r'[0-9]+', value)):
        raise _reference_changed()
    try:
        result = int(value)
    except (ValueError, OverflowError):
        raise _reference_changed() from None
    if result < 1:
        raise _reference_changed()
    return str(result)


def _version_map(items):
    if not isinstance(items, dict):
        raise _reference_changed()
    result = {}
    try:
        for key, version in items.items():
            ident = _identifier(key)
            if ident in result:
                raise _reference_changed()
            result[ident] = _version(version)
    except (ValueError, TypeError, AttributeError):
        raise _reference_changed() from None
    return result


def _fingerprint(preview):
    try:
        fingerprint = preview['reference_fingerprint']
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
            raise _reference_changed()
        raw = json.dumps({key: value for key, value in preview.items() if key != 'reference_fingerprint'},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if sha256(raw.encode('utf-8')).hexdigest() != fingerprint:
            raise _reference_changed()
        return fingerprint
    except (KeyError, TypeError, ValueError):
        raise _reference_changed() from None


def _reference(preview, opportunity_id, year, quarter):
    try:
        if (not isinstance(preview, dict) or preview['reference_kind'] != 'sales_forecast_reference'
                or _identifier(preview['opportunity_id']) != _identifier(opportunity_id)
                or _version(preview['period']['year']) != _version(year)
                or _version(preview['period']['quarter']) != _version(quarter)):
            raise _reference_changed()
        calculator = preview['calculation_trace']['calculator_version']
        if not isinstance(calculator, str) or not calculator:
            raise _reference_changed()
        return (_version(preview['opportunity_version']), _version_map(preview['material_versions']),
                calculator, _fingerprint(preview))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise _reference_changed() from None


async def _authorize(connection, actor, opportunity_id):
    await connection.execute('SELECT security.authorization_refresh()')
    await require_permission(connection, 'opportunity.read', opportunity_id=opportunity_id)
    await require_permission(connection, 'agent.finance_assistant', opportunity_id=opportunity_id)
    snapshot = await AuthorizationRepository().snapshot(connection)
    try:
        if (not isinstance(snapshot, dict)
                or _identifier(snapshot['workspace_id']) != _identifier(actor.workspace_id)
                or _identifier(snapshot['user_id']) != _identifier(actor.user_id)):
            raise _permission_changed()
        version = snapshot['permission_version']
        if not isinstance(version, str) or not version.strip():
            raise _permission_changed()
        return version
    except (KeyError, ValueError, TypeError, AttributeError):
        raise _permission_changed() from None


def _check_current_rows(data, opportunity_id, opportunity_version, material_versions):
    try:
        if (_identifier(data['opportunity_id']) != _identifier(opportunity_id)
                or _version(data['version_no']) != opportunity_version or not isinstance(data['items'], list)):
            raise _reference_changed()
        items = {}
        # read().items is the full active set. Retirement/addition/reactivation
        # changes this set; retired catalog history never replaces the snapshot.
        for row in data['items']:
            ident = _identifier(row['id'])
            if ident in items or row.get('is_active', True) is not True:
                raise _reference_changed()
            items[ident] = _version(row['version_no'])
        if items != material_versions:
            raise _reference_changed()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise _reference_changed() from None


async def explain_material_reference(database, actor, opportunity_id, *, year, quarter, user_text='', provider=None):
    repository = OpportunityMaterialRepository()
    try:
        async with database.transaction(actor, readonly=True, isolation='repeatable_read') as connection:
            permission_version = await _authorize(connection, actor, opportunity_id)
            # Exactly one calculation. Provider callbacks only read versions.
            preview = deepcopy(await repository.preview(connection, opportunity_id, year, quarter))
        opportunity_version, material_versions, calculator, fingerprint = _reference(preview, opportunity_id, year, quarter)
    except (PermissionError, LookupError, asyncpg.InsufficientPrivilegeError):
        raise _permission_changed() from None

    current_failure = None

    async def assert_current():
        nonlocal current_failure
        try:
            async with database.transaction(actor, readonly=True, isolation='repeatable_read') as connection:
                if await _authorize(connection, actor, opportunity_id) != permission_version:
                    raise _permission_changed()
                data = await repository.read(connection, opportunity_id)
                _check_current_rows(data, opportunity_id, opportunity_version, material_versions)
        except (PermissionError, LookupError, asyncpg.InsufficientPrivilegeError):
            current_failure = _permission_changed()
            raise current_failure from None
        except BaseException as exc:
            # Even a provider that catches/wraps a failed callback cannot turn
            # permission/version loss (or cancellation) into a stale response.
            current_failure = exc
            raise

    await assert_current()
    explanation = {'status': 'unavailable', 'provider': None, 'result': None,
                   'reason_code': 'provider_not_configured'}
    if provider is not None:
        try:
            result = await provider(deepcopy(preview), user_text=user_text, assert_current=assert_current)
            if not isinstance(result, dict):
                raise ValueError('validated explanation must be an object')
            # The injected adapter must validate the model contract. Also deny
            # non-JSON objects/NaN so malformed output is never labelled usable.
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            explanation = {'status': 'available', 'provider': 'agent_platform', 'result': deepcopy(result)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if current_failure is not None:
                raise current_failure
            if isinstance(exc, MaterialExplanationError):
                raise
            if isinstance(exc, (PermissionError, asyncpg.InsufficientPrivilegeError)):
                raise _permission_changed() from None
            if isinstance(exc, TimeoutError):
                explanation['reason_code'] = 'provider_timeout'
            elif isinstance(exc, (ProviderUnavailable, AgentUnavailable)):
                explanation['reason_code'] = 'provider_unavailable'
            elif isinstance(exc, (ValueError, TypeError)):
                explanation['reason_code'] = 'invalid_provider_result'
            else:
                raise
    if current_failure is not None:
        raise current_failure
    await assert_current()
    if _fingerprint(preview) != fingerprint:
        raise _reference_changed()
    return {'preview': deepcopy(preview), 'provenance': {
        'source': 'authenticated_backend', 'reference_kind': 'sales_forecast_reference',
        'opportunity_id': preview['opportunity_id'], 'opportunity_version': preview['opportunity_version'],
        'material_versions': deepcopy(preview['material_versions']), 'period': deepcopy(preview['period']),
        'calculator_version': calculator, 'reference_fingerprint': fingerprint,
    }, 'explanation': explanation}
