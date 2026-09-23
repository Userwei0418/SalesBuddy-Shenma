"""Trusted visit preview fields use actual actor-scoped PostgreSQL references."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput

from .provision import create_owned_customer


@pytest.mark.asyncio
async def test_bound_visit_uses_allowed_customer_reference_without_broader_customer_details(connection, sales_actor):
    other = (await IdentityRepository().find_actor_by_account(
        connection, workspace_external_id="demo-sales-workspace", account_code="XS002",
    )).context
    customer = await create_owned_customer(connection, other, data={
        "name": "候选主档边界" + uuid4().hex, "industry": "软件", "customer_type": "客户",
        "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "不得传入模型的历史联系人", "contact_title": "经理", "contact_role": "决策者",
    })
    await set_request_context(connection, sales_actor)
    # Sales may select a company customer for recording without full profile access.
    assert not await connection.fetchval("SELECT id FROM crm.customer WHERE id=$1::uuid", customer["id"])

    @asynccontextmanager
    async def transaction(actor, readonly):
        assert actor == sales_actor and readonly is True
        yield connection

    run = RunInput(str(uuid4()), str(uuid4()), "本次原文", "visit_entry", customer["id"], sales_actor)
    loader = AgentFactsLoader(SimpleNamespace(transaction=transaction))
    facts = await loader.load(run)
    assert facts["server_fields"] == {
        "customer_name": customer["name"], "customer_type": "客户", "recorder_user_id": "XS001",
        "created_date": await connection.fetchval("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date::text"),
    }
    assert "不得传入模型的历史联系人" not in str(facts)
    assert "owner_team_id" not in str(facts)
    with pytest.raises(LookupError, match="客户不存在"):
        await loader.load(replace(run, customer_id=str(uuid4())))


@pytest.mark.asyncio
async def test_unbound_visit_uses_actor_and_database_date_without_guessing_customer_type(connection, sales_actor):
    run = RunInput(str(uuid4()), str(uuid4()), "模型不能决定客户类型", "visit_entry", None, sales_actor)
    fields = await AgentFactsLoader(None)._load_visit_server_fields(connection, run)
    assert fields == {"customer_type": "", "recorder_user_id": "XS001",
                      "created_date": await connection.fetchval(
                          "SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date::text")}
    # Workspace is part of the actor lookup; a supplied foreign scope cannot supply identity defaults.
    foreign_actor = sales_actor.model_copy(update={"workspace_id": str(uuid4())})
    with pytest.raises(PermissionError, match="记录人不存在"):
        await AgentFactsLoader(None)._load_visit_server_fields(connection, replace(run, actor=foreign_actor))
