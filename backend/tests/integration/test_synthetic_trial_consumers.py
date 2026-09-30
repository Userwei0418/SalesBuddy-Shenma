"""Mixed trial/real records through weekly, competency and management read paths.

All fixture changes roll back in the disposable PostgreSQL test database. No
model or external notification is invoked by these tests.
"""

from datetime import timedelta
from uuid import uuid4

import pytest

from sales_backend.repositories.competency_reviews import CompetencyReviewRepository
from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from sales_backend.services.weekly_source import assert_snapshot_access, build_snapshot
from tests.integration.feishu_fixtures import seed_execute
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_opportunity_lifecycle import setup_customer
from tests.integration.test_synthetic_trial_boundaries import customer as snapshot_customer

pytestmark = pytest.mark.asyncio


async def mark_trial(connection, customer_id, batch):
    await seed_execute(connection, """UPDATE crm.customer SET data_kind='demo',
        import_meta=import_meta||jsonb_build_object('synthetic_trial',jsonb_build_object(
            'batch_id',$2::text,'root_customer_id',id::text,'source','trial_seed')) WHERE id=$1::uuid""",
        customer_id, batch)


async def visit(connection, person, customer_id):
    return await connection.fetchval("""INSERT INTO activity.visit(
        workspace_id,customer_id,recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,
        form_version_id,status,created_at,interaction_at,follow_up_record,next_action)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$3::uuid,
            (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
            'archived',transaction_timestamp()-interval '1 hour',transaction_timestamp()-interval '1 hour',
            '已与采购负责人核对项目边界，明确交付范围。','下周一补充交付清单') RETURNING id::text""",
        person.workspace_id, customer_id, person.user_id, person.team_ids[0])


async def test_mixed_visits_keep_real_weekly_and_competency_facts(connection, sales_actor):
    # Master facts must predate the weekly transaction waterline; the shared
    # fixture inserts them with explicit historical timestamps.
    real, _ = await snapshot_customer(connection, sales_actor, synthetic=False)
    trial, _ = await snapshot_customer(connection, sales_actor)
    real_visit = await visit(connection, sales_actor, str(real))
    trial_visit = await visit(connection, sales_actor, str(trial))

    # There is an ordinary archived record for each customer; only provenance
    # distinguishes the mock record, not its business-looking title or text.
    assert await connection.fetchval('SELECT count(*) FROM activity.visit WHERE id=ANY($1::uuid[])',
                                     [real_visit, trial_visit]) == 2
    source, _, _ = await build_snapshot(connection, sales_actor, str(uuid4()))
    keys = {r['id'] for r in source['records']}
    assert real_visit in keys and trial_visit not in keys
    assert str(real) in {c['id'] for c in source['context']['customers']}
    assert str(trial) not in {c['id'] for c in source['context']['customers']}
    assert source['statistics']['record_count'] == len(keys)
    await assert_snapshot_access(connection, sales_actor, source)

    now = await connection.fetchval('SELECT transaction_timestamp()')
    facts, _ = await CompetencyReviewRepository().facts(
        connection, sales_actor, now.date(), now-timedelta(days=1), now+timedelta(days=1))
    assert real_visit in {r['visit_id'] for r in facts}
    assert trial_visit not in {r['visit_id'] for r in facts}


async def test_preexisting_weekly_snapshot_cannot_reuse_a_newly_marked_trial_customer(connection, sales_actor):
    customer, _ = await snapshot_customer(connection, sales_actor, synthetic=False)
    ident = await visit(connection, sales_actor, str(customer))
    source, _, _ = await build_snapshot(connection, sales_actor, str(uuid4()))
    assert ident in {r['id'] for r in source['records']}
    await assert_snapshot_access(connection, sales_actor, source)
    await mark_trial(connection, str(customer), 'LATE-' + uuid4().hex)
    with pytest.raises(PermissionError, match='WEEKLY_SOURCE_ACCESS_CHANGED'):
        await assert_snapshot_access(connection, sales_actor, source)
    fresh, _, _ = await build_snapshot(connection, sales_actor, str(uuid4()))
    assert ident not in {r['id'] for r in fresh['records']}


async def test_console_lists_filters_and_exports_trial_provenance_without_raw_import_metadata(connection, sales_actor):
    _, real = await setup_customer(connection, sales_actor)
    _, trial = await setup_customer(connection, sales_actor)
    batch = 'SM-TRIAL-' + uuid4().hex
    await mark_trial(connection, trial['id'], batch)
    await seed_execute(connection, """UPDATE crm.customer
        SET import_meta=import_meta||'{"private_import_note":"not a console field"}'::jsonb WHERE id=$1::uuid""",
        trial['id'])
    await actor(connection, 'OPS001')
    repository = OperationsCustomerRepository()
    profile = await repository.profile(connection, trial['id'])
    assert profile['data_kind'] == 'demo' and profile['synthetic_trial_batch_id'] == batch
    assert 'import_meta' not in profile and 'private_import_note' not in profile
    assert (await repository.profile(connection, real['id']))['synthetic_trial_batch_id'] is None

    async with await client_for(connection) as client:
        await sign_in(client)
        response = await client.get('/api/v1/console/customers', params={'data_kind':'demo', 'trial_batch':batch})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 1
        row = response.json()['items'][0]
        assert row['id'] == trial['id'] and row['synthetic_trial_batch_id'] == batch
        assert row['name'] == trial['name'] and 'import_meta' not in row
        empty = await client.get('/api/v1/console/customers', params={'data_kind':'production', 'trial_batch':batch})
        assert empty.json()['items'] == []
        overview = await client.get('/api/v1/console/customers/' + trial['id'] + '/overview')
        assert overview.json()['synthetic_trial_batch_id'] == batch
        exported = await client.get('/api/v1/console/customers/export', params={'trial_batch':batch})
        assert exported.status_code == 200 and batch in exported.text
        assert '试用数据批次' in exported.text and 'not a console field' not in exported.text
        invalid = await client.get('/api/v1/console/customers', params={'data_kind':'unreviewed'})
        assert invalid.status_code == 422
