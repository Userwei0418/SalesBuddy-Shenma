"""Real PostgreSQL, RLS and business persistence; both provider HTTPs are synthetic."""

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from sales_backend.db import Database, set_request_context
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services import battle_map_reviews as business
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from tests.test_battle_map_platform import ANSWER, map_settings
from tests.test_fde_facts_runtime import Chunks, frame

from .provision import create_owned_customer

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("scenario", ["normal", "blocked", "blocked-malformed", "off"])
async def test_customer_review_uses_existing_facts_and_persists_one_private_scoped_score(
    connection, sales_actor, monkeypatch, scenario
):
    config = map_settings(enabled=scenario != "off", blocked=scenario.startswith("blocked"))
    # Fixture identities come from the isolated DB's real account resolver.
    pilot = next(iter(json.loads(config.agent_fde_pilot_json).values()))
    pilot["capabilities"]["battle_map_review"]["user_ids"] = [sales_actor.user_id]
    binding = next(iter(json.loads(config.agent_platform_bindings_json).values()))
    config = replace(
        config, agent_fde_pilot_json=json.dumps({sales_actor.workspace_id: pilot}),
        agent_platform_bindings_json=json.dumps({sales_actor.workspace_id: binding}),
        senseaudio_api_key="synthetic-original-key", senseaudio_base_url="https://original.invalid",
        agent_inference_platform_seconds=2, agent_inference_total_seconds=5,
        max_retries=1 if scenario == "blocked-malformed" else 0,
    )
    customer = await create_owned_customer(connection, sales_actor, data={
        "name": "作战地图合成回归" + uuid4().hex, "industry": "软件", "customer_type": "潜在客户",
        "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "合成联系人", "contact_title": "经理", "contact_role": "决策者",
    })
    # Company policies are now published only by administrators. The migrated
    # global baseline is sufficient for this routing/business persistence test.
    assert await connection.fetchval("SELECT security.active_company_rule('customer_quadrant')")

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    database = Database(config, pool=Pool())
    calls = []
    answer = {**ANSWER, "evidence": []}

    def platform_wire(request):
        calls.append("agent_platform")
        supplied = json.loads(json.loads(request.content)["query"])
        assert supplied["facts"]["customer"]["id"] == customer["id"]
        assert supplied["mode"] == "battle_map_review"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks([
            frame("message", answer=json.dumps(answer)), frame("message_end"),
        ]))

    def original_wire(request):
        calls.append("senseaudio")
        if scenario == "blocked-malformed" and len(calls) == 1:
            return httpx.Response(200, content=b'{"choices":[')
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        })

    def runtime_factory(db, settings, **kwargs):
        runtime = filtered_facts_runtime(db, settings, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(platform_wire))
        return runtime

    async with httpx.AsyncClient(
        base_url="https://original.invalid", transport=httpx.MockTransport(original_wire),
    ) as http:
        def direct_factory(settings, **kwargs):
            return SenseAudioClient(settings, client=http, **kwargs)

        monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
        monkeypatch.setattr(business, "InferenceService", lambda db, settings, **kw: InferenceService(
            db, settings, direct_factory=direct_factory, **kw,
        ))
        monkeypatch.setattr(business, "SenseAudioClient", direct_factory)
        await business.BattleMapReviewHandler(database, config).handle(customer["id"], sales_actor)

    row = await connection.fetchrow(
        "SELECT * FROM insight.quadrant_score WHERE customer_id=$1::uuid AND valid_to='infinity'", customer["id"],
    )
    assert row["potential_score"] == 75 and row["relationship_score"] == 50
    assert row["quadrant_code"] == "main_attack"
    assert str(row["subject_user_ref_id"]) == sales_actor.user_id
    assert await connection.fetchval(
        "SELECT count(*) FROM insight.quadrant_score WHERE customer_id=$1::uuid", customer["id"],
    ) == 1
    public = await CustomerRepository().detail(connection, customer_id=customer["id"])
    assert public["potential_score"] == 75
    assert "inference_route" not in str(public) and "ControlledPlatformBlock" not in str(public)
    expected = ["agent_platform"] if scenario == "normal" else ["senseaudio"]
    if scenario == "blocked-malformed":
        expected.append("senseaudio")
    assert calls == expected
    if scenario != "off":
        route = row["input_snapshot"]["inference_route"]
        attempts = await connection.fetch(
            "SELECT provider_code,status,http_status,operation_id FROM agent.model_invocation "
            "WHERE operation_id=$1::uuid ORDER BY started_at", route["operation_id"],
        )
        assert route["provider"] == calls[-1]
        assert len(attempts) == {"normal": 1, "blocked": 2, "blocked-malformed": 3}[scenario]
        assert attempts[-1]["http_status"] == 200 and attempts[-1]["status"] == "succeeded"
        if scenario.startswith("blocked"):
            assert attempts[0]["http_status"] is None
            assert route["fallback_reason"] == "ControlledPlatformBlock"
            assert all(str(row["operation_id"]) == route["operation_id"] for row in attempts)
        if scenario == "blocked-malformed":
            assert attempts[1]["http_status"] == 200 and attempts[1]["status"] == "failed"
    other = await IdentityRepository().find_actor_by_account(
        connection, workspace_external_id="demo-sales-workspace", account_code="XS002",
    )
    await set_request_context(connection, other.context)
    assert await connection.fetchval(
        "SELECT count(*) FROM insight.quadrant_score WHERE customer_id=$1::uuid", customer["id"],
    ) == 0


async def test_missing_customer_cannot_create_score_after_inference(connection, sales_actor):
    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    handler = business.BattleMapReviewHandler(Database(map_settings(), pool=Pool()), map_settings())
    with pytest.raises(LookupError, match="persistence"):
        await handler._persist(str(uuid4()), sales_actor, {
            "data_as_of": "2026-09-12T00:00:00Z", "visits": [], "contacts": [], "opportunities": [],
            "previous_score": None,
        }, {**ANSWER, "evaluation_mode": "agent"}, {})
