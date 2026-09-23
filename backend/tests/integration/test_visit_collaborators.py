"""Check collaborator eligibility with original accounts and real PostgreSQL RLS."""

import pytest

from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.visits import validate_collaborators
from sales_backend.services.visit_flow import prepare_visit_run
from tests.integration.test_visit_entry_platform import existing_actor


@pytest.mark.asyncio
@pytest.mark.parametrize("account,role", [
    ("XS001", "sales"), ("ZJ001", "supervisor"), ("ZJL001", "manager"), ("ADMIN001", "administrator"),
])
async def test_company_collaborators_keep_other_roles_and_reject_fde(connection, account, role):
    from tests.integration.test_fde_identity_tasks import fde_fixture

    _, _, people, _ = await fde_fixture(connection, extra_members=1)
    actor = await existing_actor(connection, account, role)
    rows = await DirectoryRepository().colleagues(connection, actor)
    codes = {row["account_code"] for row in rows}
    assert {"XS001", "XS002", "ZJ001", "ZJL001", "ADMIN001"} <= codes
    assert not {person["code"] for person in people.values()} & codes
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    validated = await validate_collaborators(connection, actor, ids)
    assert {row["display_name"] for row in validated} == {row["name"] for row in rows}
    fde_ids = list(people.values())
    assert len(fde_ids) == 4
    for person in fde_ids:
        with pytest.raises(ValueError, match="非 FDE"):
            await validate_collaborators(connection, actor, [person["id"]])
    assert await validate_collaborators(connection, actor, []) == []


@pytest.mark.asyncio
async def test_fde_collaborator_is_rejected_before_quality_is_enqueued(connection):
    from tests.integration.test_fde_identity_tasks import fde_fixture
    from tests.integration.test_visit_attendance import review_attendance
    from sales_backend.db import set_request_context

    actor, opportunity, people, _ = await fde_fixture(connection)
    await set_request_context(connection, actor)
    original, _ = await review_attendance(connection, actor, opportunity["customer_id"], opportunity["id"], [])
    body = original.model_copy(update={"collaborator_ids": [people["first"]["id"]]})
    before = await connection.fetchval("SELECT count(*) FROM agent.run")
    with pytest.raises(ValueError, match="非 FDE"):
        await prepare_visit_run(connection, actor, body, "quality")
    assert await connection.fetchval("SELECT count(*) FROM agent.run") == before
