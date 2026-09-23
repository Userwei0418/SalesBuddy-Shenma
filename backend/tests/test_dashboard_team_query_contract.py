"""Dynamic department selections cross the HTTP boundary without a two-region cap."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import business
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.agent import ActorContext


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/dashboard", "/dashboard/rankings"])
async def test_dynamic_team_queries_accept_more_than_two_teams_and_keep_a_payload_bound(monkeypatch, path):
    actor = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role="manager", data_scope="workspace")

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield object()

    app = FastAPI()
    app.include_router(business.router)
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(transaction=transaction)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
    load = AsyncMock(return_value={"data_source": "database"})
    if path.endswith("rankings"):
        monkeypatch.setattr(business, "dashboard_rankings", load)
    else:
        monkeypatch.setattr(business.DashboardRepository, "load", load)
    teams = [f"team:{uuid4()}" for _ in range(3)]
    params = [("year", "2026"), ("quarters", "3"), *(("team_groups", team) for team in teams)]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.get("/api/v1" + path, params=params)
        assert result.status_code == 200, result.text
        assert load.call_args.kwargs["team_groups"] == teams
        load.reset_mock()
        result = await client.get("/api/v1" + path, params=[
            ("year", "2026"), ("quarters", "3"),
            *(("team_groups", f"team:{uuid4()}") for _ in range(101)),
        ])
        assert result.status_code == 422
        load.assert_not_awaited()
