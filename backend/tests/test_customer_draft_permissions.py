"""Customer drafting must not confer the separate customer creation authority."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sales_backend.api import assistant, audio, permission_gate
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.permission_context import current_feature
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.agent_platform.inference import InferenceService
from tests.authorization_fixtures import permission_snapshot


def fixture(grants=(), denies=(), version="draft:1"):
    actor = ActorContext(
        workspace_id=str(uuid4()), user_id=str(uuid4()), role=RoleCode.SALES, data_scope=DataScope.SELF, team_ids=()
    )
    snapshot = permission_snapshot(actor, {code: "self" for code in grants}, version=version)
    for code in denies:
        snapshot["grants"].append(dict(permission_code=code, effect="deny"))
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=snapshot), execute=AsyncMock())

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection

    return actor, connection, SimpleNamespace(transaction=transaction)


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["customer.create", "customer.create_request"])
async def test_customer_audio_accepts_each_drafting_permission(permission):
    actor, connection, _ = fixture([permission, "visit.transcribe"])
    await audio.require_audio_purpose(connection, actor, "customer_create")


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["customer.create", "customer.create_request"])
async def test_customer_agent_accepts_each_drafting_permission_without_other_writes(permission):
    actor, connection, _ = fixture([permission])
    await require_agent_access(connection, actor, "customer_create", permission_version="draft:1")
    for mode in ("visit_entry", "opportunity_draft", "management_task", "chatbi"):
        with pytest.raises(PermissionError):
            await require_agent_access(connection, actor, mode, str(uuid4()))
    with pytest.raises(PermissionError, match="权限已变化"):
        await require_agent_access(connection, actor, "customer_create", permission_version="old")


@pytest.mark.asyncio
async def test_request_only_draft_does_not_create_customer():
    actor, connection, _ = fixture(["customer.create_request"])
    with pytest.raises(PermissionError):
        await CustomerMutationRepository().create(connection, actor, data={})
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grants,denies,allowed",
    [
        ([], [], False),
        (["customer.create_request"], ["customer.create_request"], False),
        (["customer.create"], ["customer.create"], False),
        (["customer.create", "customer.create_request"], ["customer.create", "customer.create_request"], False),
        (["customer.create", "customer.create_request"], ["customer.create"], True),
        (["customer.create", "customer.create_request"], ["customer.create_request"], True),
    ],
)
async def test_denies_apply_to_their_own_action_without_disabling_another_allowed_draft(grants, denies, allowed):
    actor, connection, _ = fixture(grants, denies)
    if allowed:
        await require_agent_access(connection, actor, "customer_create")
    else:
        with pytest.raises(PermissionError):
            await require_agent_access(connection, actor, "customer_create")


@pytest.mark.asyncio
async def test_transcription_permission_remains_required_and_does_not_grant_visit_structure():
    actor, connection, _ = fixture(["customer.create_request"])
    with pytest.raises(HTTPException) as error:
        await audio.require_audio_purpose(connection, actor, "customer_create")
    assert error.value.status_code == 403
    actor, connection, _ = fixture(["customer.create_request", "visit.transcribe"])
    with pytest.raises(HTTPException):
        await audio.require_audio_purpose(connection, actor, "visit_entry")


@pytest.mark.asyncio
async def test_conversation_dispatch_sets_the_actual_request_permission(monkeypatch):
    actor, connection, database = fixture(["customer.create_request"])
    snapshot = connection.fetchval.return_value
    snapshot["grants"].append(
        dict(
            permission_code="access.mini_program",
            effect="allow",
            scope_code="workspace",
            team_ids=[],
            source_code="fixture",
            source_id="surface",
        )
    )
    identity = SimpleNamespace(actor=actor, client_channel="wechat-mini-program", auth_method="password")
    monkeypatch.setattr(permission_gate, "get_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(permission_gate, "bearer", AsyncMock(return_value=None))

    async def receive():
        return {"type": "http.request", "body": json.dumps({"mode": "customer_create"}).encode(), "more_body": False}

    request = Request(
        dict(
            type="http",
            method="POST",
            path="/api/v1/conversations",
            headers=[],
            endpoint=assistant.create_conversation,
            app=SimpleNamespace(state=SimpleNamespace(database=database, settings=None)),
        ),
        receive,
    )
    gate = permission_gate.enforce_route_permission(request)
    try:
        await anext(gate)
        assert current_feature.get() == "customer.create_request"
    finally:
        await gate.aclose()


@pytest.mark.asyncio
async def test_inference_rechecks_current_draft_permission_and_identity(monkeypatch):
    actor, connection, database = fixture(["customer.create_request"])
    monkeypatch.setattr(
        "sales_backend.services.agent_platform.inference.IdentityRepository.find_actor_by_id",
        AsyncMock(return_value=SimpleNamespace(context=actor)),
    )
    service = InferenceService(database, None)
    with pytest.raises(PermissionError):
        await service.assert_actor_current(actor, "customer.create")
    await service.assert_mode_current(actor, "customer_create", "customer.create")
    connection.fetchval.return_value = permission_snapshot(actor, {}, version="revoked")
    with pytest.raises(PermissionError):
        await service.assert_mode_current(actor, "customer_create", "customer.create")
