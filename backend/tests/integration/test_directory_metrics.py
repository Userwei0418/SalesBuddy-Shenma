"""Directory counters must match independent RLS-scoped facts without detail fanout."""
import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.directory import DirectoryRepository

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('role', [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
async def test_directory_counters_match_independent_facts(connection, actor_factory, role):
    actor = await actor_factory(role)
    await connection.execute("SET LOCAL statement_timeout='3s'")
    rows = await DirectoryRepository().members(connection, actor)
    assert rows
    assert len({(row['id'], row['role'], row['team_id']) for row in rows}) == len(rows)
    if role == RoleCode.SALES:
        assert {row['id'] for row in rows} == {actor.user_id}
    for row in rows:
        uid = row['id']
        assert row['visits'] == await connection.fetchval(
            'SELECT count(*) FROM activity.visit WHERE recorder_user_ref_id=$1::uuid AND deleted_at IS NULL', uid)
        assert row['opportunities'] == await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity WHERE owner_user_ref_id=$1::uuid AND deleted_at IS NULL "
            "AND status='open'", uid)
        assert row['risks'] == await connection.fetchval(
            "SELECT count(*) FROM insight.risk WHERE owner_user_ref_id=$1::uuid AND deleted_at IS NULL "
            "AND status IN ('new','pending','in_progress','escalated')", uid)
        tasks = await connection.fetch(
            "SELECT status FROM workflow.task t WHERE deleted_at IS NULL AND EXISTS("
            "SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_user_ref_id=$1::uuid "
            "AND a.responsibility='owner')", uid)
        from decimal import ROUND_HALF_UP, Decimal
        expected = (Decimal(100) * sum(t['status'] == 'completed' for t in tasks) / len(tasks)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP) if tasks else 0
        assert row['completion'] == expected
