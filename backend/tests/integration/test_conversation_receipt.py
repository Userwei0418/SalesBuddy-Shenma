"""Lost conversation responses replay without duplicating a persisted session."""

from uuid import uuid4

import pytest

from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde, members
from tests.integration.test_operations_api import client_for

pytestmark = pytest.mark.asyncio


async def test_conversation_receipt_replays_conflicts_and_rechecks_fde_access(connection):
    sales, project, people, _ = await fde_fixture(connection)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        body = {"mode": "visit_entry", "customer_id": project["customer_id"], "opportunity_id": project["id"]}
        headers = {"Idempotency-Key": str(uuid4())}
        first = await client.post("/api/v1/conversations", json=body, headers=headers)
        assert first.status_code == 201, first.text
        replay = await client.post("/api/v1/conversations", json=body, headers=headers)
        assert replay.status_code == 201 and replay.json() == first.json()
        assert await connection.fetchval(
            "SELECT count(*) FROM ops.mutation_receipt WHERE request_key=$1::uuid AND operation='conversations.create'",
            headers["Idempotency-Key"],
        ) == 1
        changed = await client.post("/api/v1/conversations", json={"mode": "operating_report"}, headers=headers)
        assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
        await members(connection, sales, project, [people["second"]["id"]])
        denied = await client.post("/api/v1/conversations", json=body, headers=headers)
        assert denied.status_code == 403  # A receipt never revives removed opportunity access.
