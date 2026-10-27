"""Current column/recipient projections cannot resurrect mutable legacy values."""

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.tasks import TaskRepository
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_task_transfer_evidence import make_task, transfer

pytestmark = pytest.mark.asyncio


async def test_clearing_customer_fields_keeps_legacy_evidence_without_displaying_it(connection):
    project = await opportunity(connection, "XS001", 100)
    person = await actor(connection, "XS001")
    evidence = {"next_action": "导入时的旧行动", "cooperation_years": 4, "sourceRow": 12}
    await connection.execute(
        "UPDATE crm.customer SET next_action='当前行动',cooperation_years=5,"
        "attributes=$2::jsonb,import_meta=$3::jsonb WHERE id=$1::uuid",
        project["customer_id"], {"next_action": "旧扩展行动"}, evidence,
    )
    await CustomerMutationRepository().update(
        connection, person, customer_id=project["customer_id"],
        data={"next_action": "", "cooperation_years": None}, management_profile=True,
    )
    persisted = await connection.fetchrow(
        "SELECT next_action,cooperation_years,attributes,import_meta FROM crm.customer WHERE id=$1::uuid",
        project["customer_id"],
    )
    assert persisted["next_action"] == "" and persisted["cooperation_years"] is None
    assert persisted["import_meta"] == evidence
    assert persisted["attributes"] == {"next_action": "旧扩展行动"}
    repo = CustomerRepository()
    detail = await repo.detail(connection, customer_id=project["customer_id"])
    listed = next(row for row in await repo.list(connection, query=None, level=None, unassigned=None, limit=None)
                  if row["id"] == project["customer_id"])
    for result in (detail, listed):
        assert "next_action" not in result["attributes"]
        assert "cooperation_years" not in result["attributes"]
        assert result["attributes"]["sourceRow"] == 12


async def test_transfer_updates_compatibility_owner_without_rewriting_import_evidence(connection):
    creator, task = await make_task(connection, owner_code="XS001")
    evidence = {"assignee_account_code": "XS001", "rejection_comment": "历史退回原因", "sourceRow": 12}
    await connection.execute("UPDATE workflow.task SET import_meta=$2::jsonb,rejection_comment='历史退回原因' "
                             "WHERE id=$1::uuid",
                             task["id"], evidence)
    task = await TaskRepository().detail(connection, task_id=task["id"])
    result = await transfer(connection, creator, task, "XS002")
    owner, = [person for person in result["assignees"] if person["responsibility"] == "owner"]
    assert owner["account_code"] == "XS002"
    assert result["attributes"]["assignee_account_code"] == "XS002"
    assert result["creator_user_ref_id"] == creator.user_id
    await set_request_context(connection, creator)
    assert await connection.fetchval("SELECT import_meta FROM workflow.task WHERE id=$1::uuid", task["id"]) == evidence
    reread = await TaskRepository().detail(connection, task_id=task["id"])
    assert reread["attributes"]["assignee_account_code"] == "XS002"
    assert "rejection_comment" not in reread["attributes"]
    overview = await TaskRepository().overview(connection, task_ids=[task["id"]])
    assert overview["items"][0]["attributes"]["rejection_comment"] is None
    assert reread["events"][-1]["payload"]["previous_owners"][0]["user_id"] == creator.user_id
