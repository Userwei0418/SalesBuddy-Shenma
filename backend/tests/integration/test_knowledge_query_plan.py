"""Bound actual RLS table scans on a cold, small catalogue (no time threshold)."""

from uuid import uuid4

import pytest

from sales_backend.db import json_value
from sales_backend.repositories.knowledge import KnowledgeRepository

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection


class RecordingConnection:
    """Record the directory query while still executing on the real runtime role."""

    def __init__(self, connection):
        self.connection = connection
        self.query = None
        self.args = None

    async def fetch(self, query, *args):
        self.query, self.args = query, args
        return await self.connection.fetch(query, *args)


def relation_scans(plan):
    if plan.get("Relation Name") in {"material", "version", "material_acl", "catalog"}:
        yield plan
    for child in plan.get("Plans", []):
        yield from relation_scans(child)


@pytest.mark.asyncio
async def test_directory_does_not_rescan_rls_tables_per_material(kb):
    await kb.configure()
    owner = kb.actors["owner"]
    for number in range(40):
        await kb.service.save(owner, uuid4(), selection(name=f"Plan fixture {number:03}"))

    async with kb.database.transaction(owner, readonly=True) as connection:
        assert await connection.fetchval(
            "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        recording = RecordingConnection(connection)
        page = await KnowledgeRepository().list_materials(recording, limit=5, offset=35)
        assert page["total"] == 40 and len(page["items"]) == 5
        assert len({row["material_id"] for row in page["items"]}) == 5
        plan = json_value(await connection.fetchval(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + recording.query, *recording.args
        ))
        scans = list(relation_scans(plan[0]["Plan"]))
        assert {scan["Relation Name"] for scan in scans} == {"material", "version", "material_acl", "catalog"}
        # Before this fix, low estimates caused 40x40 permission-bearing joins.
        # One statement must not repeatedly scan the same authorized relation.
        assert all(scan["Actual Loops"] <= 1 for scan in scans), scans
