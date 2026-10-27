"""Opt-in API boundary and constant-round-trip recommendation reads."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import opportunity_access
from sales_backend.api.dependencies import get_database, get_identity


@pytest.mark.asyncio
async def test_recent_follow_up_page_preserves_capabilities_without_per_row_reads():
    customer_id, opportunity_id = str(uuid4()), str(uuid4())
    item = {
        "contract_version": 1, "opportunity_id": opportunity_id, "name": "已有商机",
        "stage_code": "solution", "status": "open", "version_no": 1,
        "owner": {"user_id": None, "display_name": None, "team_id": None, "team_name": None},
        "customer_reference": {"id": customer_id, "name": "协作客户"},
        "capabilities": {"can_follow_up": True, "can_transfer": False,
                         "can_edit": False, "can_manage_collaborators": False},
        "capability_reasons": {"transfer": "OPPORTUNITY_ACCESS_DENIED"},
    }
    page = {"contract_version": 1, "items": [item], "total": 21, "limit": 20, "offset": 20}
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=page))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert kwargs == {"readonly": True, "isolation": "repeatable_read"}
        yield connection

    app = FastAPI()
    app.include_router(opportunity_access.router)
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(transaction=transaction)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.get("/api/v1/opportunities/follow-up-options", params={
            "order": "recent_follow_up", "customer_id": customer_id, "q": " 协作 ",
            "limit": 20, "offset": 20,
        })
        assert result.status_code == 200, result.text
        assert result.json() == page
        assert connection.fetchval.await_count == 1
        _, query, target, limit, offset = connection.fetchval.call_args.args
        assert (query, str(target), limit, offset) == ("协作", customer_id, 20, 20)
        result = await client.get("/api/v1/opportunities/follow-up-options", params={"order": "updated_at"})
        assert result.status_code == 422
        assert connection.fetchval.await_count == 1
