"""Reuse a surface check only inside its originating request and actor."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.domain.authorization import Authorization, Grant
from sales_backend.repositories.authorization import AuthorizationRepository


def permission(workspace='tenant', user='user', *, denied=False):
    return Authorization.combine(workspace, user, [Grant('access.console', 'workspace')],
                                 account_denies=['access.console'] if denied else [])


@pytest.mark.asyncio
@pytest.mark.parametrize('cache', ['same', 'none', 'different_identity', 'different_tenant', 'different_user'])
async def test_surface_snapshot_only_reuses_same_request_identity(monkeypatch, cache):
    actor = SimpleNamespace(workspace_id='tenant', user_id='user')
    identity = SimpleNamespace(actor=actor, must_change_password=False)
    calls = []

    class Database:
        @asynccontextmanager
        async def transaction(self, person, *, readonly):
            calls.append(person)
            yield object()

    async def effective(self, connection):
        return permission()

    monkeypatch.setattr(AuthorizationRepository, 'effective', effective)
    request = Request({'type': 'http', 'headers': []})
    request.state.verified_identity = identity if cache != 'different_identity' else object()
    request.state.permissions = {
        'same': permission(), 'none': None, 'different_identity': permission(),
        'different_tenant': permission(workspace='other'), 'different_user': permission(user='other'),
    }[cache]
    assert await get_management_identity(request, identity, Database()) is identity
    assert len(calls) == (0 if cache == 'same' else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('denied,must_change', [(True, False), (False, True)])
async def test_reuse_does_not_skip_account_deny_or_password_requirement(denied, must_change):
    identity = SimpleNamespace(actor=SimpleNamespace(workspace_id='tenant', user_id='user'),
                               must_change_password=must_change)
    request = Request({'type': 'http', 'headers': []})
    request.state.verified_identity = identity
    request.state.permissions = permission(denied=denied)
    with pytest.raises(HTTPException) as error:
        await get_management_identity(request, identity, object())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_new_request_does_not_inherit_an_earlier_surface_grant(monkeypatch):
    identity = SimpleNamespace(actor=SimpleNamespace(workspace_id='tenant', user_id='user'),
                               must_change_password=False)

    class Database:
        @asynccontextmanager
        async def transaction(self, person, *, readonly):
            yield object()

    async def effective(self, connection):
        return permission(denied=True)

    monkeypatch.setattr(AuthorizationRepository, 'effective', effective)
    request = Request({'type': 'http', 'headers': []})
    with pytest.raises(HTTPException) as error:
        await get_management_identity(request, identity, Database())
    assert error.value.status_code == 403
