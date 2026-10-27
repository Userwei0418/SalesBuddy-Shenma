"""An asynchronous personal-risk result cannot overwrite changed source facts."""

from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from sales_backend.db import Database
from sales_backend.domain.agent import AgentMode
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.handler import AgentRunHandler
from sales_backend.services.agent_run.persist import AgentRunStore
from tests.integration.connection_pool import FixtureConnectionPool
from tests.integration.feishu_fixtures import assert_restricted, seed_execute, seed_fetchval
from tests.integration.provision import create_owned_customer, create_owned_opportunity
from tests.test_personal_risk_platform import ANSWER, configured

pytestmark = pytest.mark.asyncio


async def setup_analysis(connection, actor):
    await assert_restricted(connection)
    customer = await create_owned_customer(connection, actor, data={
        "name": "个人风险来源时效隔离测试" + uuid4().hex,
        "industry": "软件", "customer_type": "潜在客户", "level_code": "Tier-2",
        "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "合成人员", "contact_title": "经理", "contact_role": "决策者",
    })
    opportunity = await create_owned_opportunity(connection, actor, customer["id"])
    visit = await VisitRepository().create(connection, actor, customer_id=customer["id"], fields={
        "interaction_at": date.today().isoformat(), "created_date": date.today().isoformat(),
        "contact_name": "合成人员", "follow_up_record": "客户预算尚未获批，需要确认。",
        "next_action": "下周一销售与财务确认预算", "_follow_up_quality_score": 85,
        "opportunity_id": opportunity,
    })
    settings = configured(enabled=False)
    database = Database(settings, pool=FixtureConnectionPool(connection))
    assistant = AssistantRepository()

    async def start():
        conversation = await assistant.create_conversation(
            connection, actor, mode=AgentMode.PERSONAL_RISKS, customer_id=None,
        )
        run_id = await assistant.enqueue_message(
            connection, actor, conversation_id=conversation["id"], text="个人风险分析",
            client_message_id=str(uuid4()), input_source="text",
        )
        run = await AgentRunHandler(database, settings)._load_and_start(run_id, actor)
        facts = await AgentFactsLoader(database).load(run)
        result = deepcopy(ANSWER)
        result["risks"][0]["source_visit_id"] = visit["id"]
        return run, facts, result

    return customer, opportunity, visit, AgentRunStore(database, settings), start


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("change", [
    "visit_text", "visit_deleted", "visit_aged", "customer_level", "opportunity_amount", "opportunity_deleted",
])
async def test_changed_sources_reject_before_risk_or_receipt_write(connection, sales_actor, change, existing):
    customer, opportunity, visit, store, start = await setup_analysis(connection, sales_actor)
    if existing:
        prior_run, prior_facts, prior_result = await start()
        await store.persist_result(prior_run, prior_result, prior_facts)
    run, facts, result = await start()
    before = await connection.fetch("SELECT * FROM insight.risk ORDER BY id")
    events = await connection.fetch("SELECT * FROM insight.risk_event ORDER BY id")
    # Mutate after the authorized facts were read, as another request can do
    # while a model is in flight. Owner-only seeds model record deletion.
    changes = {
        "visit_text": ("UPDATE activity.visit SET next_action='客户暂停推进，改日重新确认' WHERE id=$1::uuid",
                       visit["id"]),
        "visit_deleted": ("UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1::uuid", visit["id"]),
        "visit_aged": ("UPDATE activity.visit SET interaction_at=clock_timestamp()-interval '181 days' "
                       "WHERE id=$1::uuid", visit["id"]),
        "customer_level": ("UPDATE crm.customer SET level_code='Tier-1' WHERE id=$1::uuid", customer["id"]),
        "opportunity_amount": ("UPDATE crm.opportunity SET amount=99999 WHERE id=$1::uuid", opportunity),
        "opportunity_deleted": ("UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid",
                                opportunity),
    }
    await seed_execute(connection, *changes[change])
    with pytest.raises((SenseAudioError, PermissionError)) as error:
        await store.persist_result(run, result, facts)
    if isinstance(error.value, SenseAudioError):
        assert error.value.retryable is False and "来源已变更" in str(error.value)
    else:
        assert "权限已变化" in str(error.value)
    assert await connection.fetch("SELECT * FROM insight.risk ORDER BY id") == before
    assert await connection.fetch("SELECT * FROM insight.risk_event ORDER BY id") == events
    assert await seed_fetchval(connection,
        "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run.run_id,
    ) == 0
    assert await seed_fetchval(connection, "SELECT status FROM agent.run WHERE id=$1::uuid", run.run_id) == "running"


async def test_unchanged_source_allows_timezone_normalization_and_unrelated_edits(connection, sales_actor):
    customer, _, visit, store, start = await setup_analysis(connection, sales_actor)
    run, facts, result = await start()
    # The source loader exposes Shanghai time; PG stores the same UTC instant.
    assert str(facts["visits"][0]["interaction_at"].utcoffset()) == "8:00:00"
    await connection.execute(
        "UPDATE crm.customer SET main_business='不在本次推理来源中的辅助字段' WHERE id=$1::uuid", customer["id"],
    )
    await store.persist_result(run, result, facts)
    row = await connection.fetchrow("SELECT * FROM insight.risk WHERE source_visit_id=$1::uuid", visit["id"])
    assert str(row["owner_user_ref_id"]) == sales_actor.user_id and row["status"] == "pending"
    assert await connection.fetchval("SELECT status FROM agent.run WHERE id=$1::uuid", run.run_id) == "succeeded"
