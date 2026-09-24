"""Exercise real route -> repository.page -> SQL builder parameter forwarding."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import business, customer_assets
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories import opportunity_browse, opportunity_overview


def client_context(role):
    actor = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role=role, data_scope="self")
    from tests.authorization_fixtures import permission_snapshot
    connection = SimpleNamespace(
        fetchval=AsyncMock(return_value=permission_snapshot(actor, {"opportunity.read": "workspace", "battle_map.read": "workspace"})),
        fetchrow=AsyncMock(return_value={
            "ids": [], "summary": {"total": 0}, "facets": {"years": []}, "creation_date_facts": [],
        }),
        fetch=AsyncMock(return_value=[]),
    )

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection

    app = FastAPI()
    app.include_router(business.router)
    app.include_router(customer_assets.router)
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(transaction=transaction)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")
    return client, connection, actor


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["sales", "supervisor", "manager", "operations", "administrator", "fde", "fde_lead"])
async def test_default_opportunity_page_accepts_added_member_array_for_every_role(monkeypatch, role):
    client, connection, actor = client_context(role)
    monkeypatch.setattr(opportunity_browse, "scope_members", AsyncMock(return_value=("self", [actor.user_id], [])))
    async with client:
        response = await client.get("/api/v1/opportunities", params={"page_size": 20})
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["summary"]["total"] == 0
    sql = connection.fetchrow.call_args.args[0]
    assert "filtered AS MATERIALIZED" in sql and " LIMIT " in sql and " OFFSET " in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("customer_id", [None, str(uuid4())])
async def test_selected_members_reach_shared_sql_scope_even_with_customer_filter(monkeypatch, customer_id):
    client, connection, _ = client_context("fde_lead")
    members = [str(uuid4()), str(uuid4())]
    resolver = AsyncMock(return_value=("team", members, []))
    monkeypatch.setattr(opportunity_browse, "scope_members", resolver)
    params = [("scope", "team"), *(('member_ids', value) for value in members), ("page_size", "20")]
    if customer_id:
        params.append(("customer_id", customer_id))
    async with client:
        response = await client.get("/api/v1/opportunities", params=params)
    assert response.status_code == 200, response.text
    assert [str(value) for value in resolver.call_args.args[4]] == members
    sql, *args = connection.fetchrow.call_args.args
    assert "security.authorization_opportunity" in sql and "o.owner_user_ref_id=ANY" in sql
    assert members in args


@pytest.mark.asyncio
async def test_invalid_member_scope_is_forbidden_in_page_map_and_overview(monkeypatch):
    client, _, _ = client_context("fde_lead")
    forbidden = AsyncMock(side_effect=PermissionError("所选成员不在当前数据范围内"))
    monkeypatch.setattr(opportunity_browse, "scope_members", forbidden)
    monkeypatch.setattr(customer_assets, "scope_members", forbidden)
    monkeypatch.setattr(opportunity_overview, "scoped_opportunity_ids", forbidden)
    params = {"scope": "team", "member_ids": str(uuid4()), "year": 2026}
    async with client:
        for path in ["/opportunities", "/customer-assets/map", "/opportunities/overview"]:
            response = await client.get("/api/v1" + path, params=params)
            assert response.status_code == 403, (path, response.text)


@pytest.mark.asyncio
async def test_map_and_overview_forward_selected_members_to_their_sql_scopes(monkeypatch):
    client, connection, _ = client_context("fde_lead")
    members, projects = [str(uuid4()), str(uuid4())], [str(uuid4()), str(uuid4())]
    map_scope = AsyncMock(return_value=("team", members, []))
    overview_scope = AsyncMock(return_value=("team", members, [], [{"id": value} for value in projects]))
    monkeypatch.setattr(customer_assets, "scope_members", map_scope)
    monkeypatch.setattr(opportunity_overview, "scoped_opportunity_ids", overview_scope)
    params = [("scope", "team"), *(('member_ids', value) for value in members), ("year", "2026")]
    async with client:
        response = await client.get("/api/v1/customer-assets/map", params=params)
        assert response.status_code == 200, response.text
        assert [str(value) for value in map_scope.call_args.args[4]] == members
        assert any(members in call.args[1:] for call in connection.fetch.call_args_list)
        response = await client.get("/api/v1/opportunities/overview", params=params)
        assert response.status_code == 200, response.text
        assert [str(value) for value in overview_scope.call_args.args[4]] == members
        assert connection.fetchrow.call_args.args[-1] == projects
