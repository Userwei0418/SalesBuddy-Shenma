import pytest

from sales_backend.domain.agent import RoleCode



@pytest.mark.asyncio
async def test_usage_includes_owner_visible_invocations(connection, actor_factory):
    actor = await actor_factory(RoleCode.MANAGER)
    result = {"items": await connection.fetchval("SELECT security.model_usage_summary(90)")}
    expected = await connection.fetchval('''
        SELECT count(*) FROM agent.model_invocation i
        JOIN agent.run r ON r.id=i.run_id AND r.workspace_id=i.workspace_id
        WHERE i.workspace_id=$1::uuid AND i.started_at>=clock_timestamp()-interval '90 days'
    ''', actor.workspace_id)
    # The manager sees workspace aggregates, while raw invocation RLS still
    # exposes only their own runs. The migration regression independently
    # compares this aggregate with the migration owner's workspace count.
    assert sum(row['calls'] for row in result['items']) >= expected
    for row in result['items']:
        assert row['calls'] == sum(row[k] for k in ('succeeded', 'failed', 'running', 'cancelled'))
        assert 'request_snapshot' not in row and 'response_snapshot' not in row
        assert 0 <= row['recorded_retries'] <= row['calls']
