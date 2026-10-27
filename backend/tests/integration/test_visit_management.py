"""Console formal follow-ups through real PostgreSQL RLS and HTTP, rolled back per test."""
from datetime import date
from uuid import uuid4

import pytest

from sales_backend.repositories.visit_management import VisitManagementRepository, selection
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def people(connection):
    admin = await actor(connection, 'ADMIN001')
    first = await actor(connection, 'XS001')
    second = await actor(connection, 'XS002')
    await actor(connection, 'ADMIN001')
    customer = await connection.fetchval('SELECT id::text FROM crm.customer ORDER BY id LIMIT 1')
    return admin, first, second, customer


async def saved(connection, person, customer, *, recorder='self', status='archived', score=85,
                occurred='2026-09-28T10:00:00+08:00', created='2026-09-29T12:00:00+08:00',
                partner=None, project=None, import_id=None, original=None, recorded=None):
    recorder = person.user_id if recorder == 'self' else recorder
    if recorder is None:
        await seed_execute(connection, "SELECT set_config('app.feishu_historical_import','on',true)")
    return await seed_fetchval(connection, """INSERT INTO activity.visit(workspace_id,customer_id,
      recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,confirmed_by_user_ref_id,
      form_version_id,status,interaction_at,created_at,follow_up_record,next_action,follow_up_score,
      quality_review,partner_id,opportunity_id,source_import_id,original_recorder_name,recorded_on,
      partner_name_snapshot,contact_title_snapshot,duration_minutes,expectation_code,import_meta)
      VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$5::uuid,
      (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
      $6,$7::text::timestamptz,$8::text::timestamptz,repeat('历史正文',300),'下次推进唯一下一步',$9,
      '{"follow_up_score":85,"grade":"健康","next_action_passed":true,"company_policy":{"private":"SECRET"}}',
      $10::uuid,$11::uuid,$12::uuid,$13,$14::date,'原始伙伴快照','原职务',30,'neutral',
      CASE WHEN $3::uuid IS NULL THEN '{"import_type":"crm_history"}'::jsonb ELSE '{}'::jsonb END) RETURNING id::text""",
      person.workspace_id, customer, recorder, person.team_ids[0] if person.team_ids else None,
      person.user_id, status, occurred, created, score, partner, project, import_id, original, recorded)


async def test_full_filtered_summary_pagination_quality_and_formal_only(connection):
    admin, first, _, customer = await people(connection)
    ids = [await saved(connection, first, customer, score=0 if i == 0 else None if i == 1 else 85)
           for i in range(23)]
    await saved(connection, first, customer, status='pending_confirm')
    await saved(connection, first, customer, status='withdrawn')
    await actor(connection, 'ADMIN001')
    async with await client_for(connection) as client:
        await sign_in(client, 'ADMIN001')
        result = await client.get('/api/v1/console/visits', params={'page_size': 10})
        assert result.status_code == 200, result.text
        first_page = result.json()
        assert first_page['summary'] == {'total': 23, 'subject_count': 1, 'scored_count': 22}
        assert first_page['has_more'] and first_page['next_offset'] == 10
        pages = [first_page]
        for offset in (10, 20):
            pages.append((await client.get('/api/v1/console/visits', params={'page_size': 10, 'offset': offset})).json())
        seen = [item for page in pages for item in page['items']]
        assert len({item['id'] for item in seen}) == 23 and {item['id'] for item in seen} == set(ids)
        assert all(len(item['follow_up_record']) == 600 and item['is_summary'] for item in seen)
        assert 'SECRET' not in str(seen) and 'company_policy' not in str(seen)
        assert not pages[-1]['has_more'] and pages[-1]['next_offset'] is None
        zero = (await client.get('/api/v1/console/visits', params={'score_min': 0, 'score_max': 0})).json()
        assert zero['summary']['total'] == 1 and zero['items'][0]['follow_up_score'] == 0
        assert (await client.get('/api/v1/console/visits', params={'score_min': 0})).json()['summary']['total'] == 22
        for params in ({'scope': 'workspace'}, {'score_min': 101}, {'score_max': -1}, {'score_min': 'NaN'},
                       {'score_min': 80, 'score_max': 20}, {'page_size': 101}, {'offset': 100001},
                       {'date_from': '2026-10-02', 'date_to': '2026-10-01'}, {'date_basis': 'recorded'}):
            assert (await client.get('/api/v1/console/visits', params=params)).status_code == 422


async def test_beijing_date_edges_and_unknown_interaction_never_use_created_date(connection):
    _, first, _, customer = await people(connection)
    ids = [await saved(connection, first, customer, occurred=at) for at in (
        '2026-09-27T23:59:59+08:00', '2026-09-28T00:00:00+08:00',
        '2026-09-28T23:59:59+08:00', '2026-09-29T00:00:00+08:00')]
    missing = await saved(connection, first, customer, occurred=None, recorded=date(2026, 9, 27))
    await actor(connection, 'ADMIN001')
    repo = VisitManagementRepository()
    rows = await repo.page(connection, date_from=date(2026, 9, 28), date_to=date(2026, 9, 28))
    assert {r['id'] for r in rows['items']} == set(ids[1:3])
    assert (await repo.page(connection, date_basis='created', date_from=date(2026, 9, 29), date_to=date(2026, 9, 29)))['summary']['total'] == 5
    detail = await repo.detail(connection, missing)
    assert detail['visit_date'] is None and detail['created_date'] == '2026-09-27'
    assert detail['interaction_at'] is None


async def test_read_only_visit_scope_keeps_history_and_denies_cross_tenant_and_private_names(connection):
    admin, first, second, customer = await people(connection)
    own = await saved(connection, first, customer)
    other = await saved(connection, second, customer)
    unknown = await saved(connection, first, customer, recorder=None, original='历史无匹配原跟进人')
    await seed_execute(connection, "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", first.user_id)
    ops = await actor(connection, 'OPS001')
    await grant(connection, admin, ops.user_id, [
        dict(permission='visit.read', effect='allow', scope='workspace'),
        *[dict(permission=p, effect='deny') for p in ('customer.read', 'opportunity.read', 'organization.read', 'directory.read', 'visit.supplement')],
    ])
    foreign_ws, foreign_user = uuid4(), uuid4()
    await seed_execute(connection, "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'外公司')", foreign_ws, 'followup-'+uuid4().hex)
    await seed_execute(connection, "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,'外公司跟进人','FOREIGN')",
                       foreign_user, foreign_ws, str(foreign_user))
    from types import SimpleNamespace
    foreign_partner = await seed_fetchval(connection, "INSERT INTO crm.partner(workspace_id,name) VALUES($1,'外公司伙伴') RETURNING id::text", foreign_ws)
    outside = await saved(connection, SimpleNamespace(workspace_id=str(foreign_ws), user_id=str(foreign_user), team_ids=[]), None,
                          partner=foreign_partner, original='外公司跟进人')
    async with await client_for(connection) as client:
        await sign_in(client)
        listing = await client.get('/api/v1/console/visits')
        assert listing.status_code == 200, listing.text
        assert {r['id'] for r in listing.json()['items']} == {own, other, unknown}
        recorders = await client.get('/api/v1/console/visits/recorders')
        assert recorders.status_code == 200, recorders.text
        directory = recorders.json()
        assert directory['unmapped_count'] == 1
        assert next(r for r in directory['items'] if r['id'] == first.user_id)['active'] is False
        filtered = await client.get('/api/v1/console/visits', params={'recorder_id': first.user_id})
        assert [r['id'] for r in filtered.json()['items']] == [own]
        assert (await client.get('/api/v1/console/visits', params={'q': '历史无匹配原跟进人'})).json()['items'][0]['id'] == unknown
        detail = await client.get('/api/v1/console/visits/' + own)
        assert detail.status_code == 200, detail.text
        assert not detail.json()['can_open_customer'] and not detail.json()['can_supplement']
        assert (await client.get('/api/v1/console/visits/' + outside)).status_code == 404
        assert (await client.get('/api/v1/console/visits', params={'recorder_id': str(foreign_user)})).json()['summary']['total'] == 0
        assert (await client.get('/api/v1/console/visits/recorders', params={'recorder_q': 'FOREIGN'})).json()['items'] == []
        await grant(connection, admin, ops.user_id, [dict(permission='visit.read', effect='deny')], version=1)
        assert (await client.get('/api/v1/console/visits')).status_code == 403
        assert (await client.get('/api/v1/console/visits/recorders')).status_code == 403
        assert (await client.get('/api/v1/console/visits/' + own)).status_code == 403


async def test_partner_multi_opportunity_and_principal_only_search_count_without_name_leak(connection):
    admin, first, _, customer = await people(connection)
    await grant(connection, admin, admin.user_id, [dict(permission='visit.supplement', effect='allow', scope='self')])
    partner = await seed_fetchval(connection, "INSERT INTO crm.partner(workspace_id,name) VALUES($1::uuid,'伙伴主体') RETURNING id::text", admin.workspace_id)
    projects = [await seed_fetchval(connection, """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id)
      VALUES($1::uuid,$2::uuid,$3,$4::uuid,$5::uuid) RETURNING id::text""", admin.workspace_id, customer,
      name, first.user_id, first.team_ids[0]) for name in ('主商机独有搜索词', '关联商机独有搜索词')]
    principal = await saved(connection, first, None, project=projects[0])
    # The consistency trigger is deferred; this represents a historical missing bridge
    # inside the always-rolled-back fixture only. No production trigger is disabled.
    await seed_execute(connection, 'DELETE FROM activity.visit_opportunity WHERE visit_id=$1::uuid', principal)
    multiple = await saved(connection, admin, None, partner=partner)
    for project in projects:
        await seed_execute(connection, "INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id) VALUES($1::uuid,$2::uuid,$3::uuid)", multiple, admin.workspace_id, project)
    await actor(connection, 'ADMIN001')
    repo = VisitManagementRepository()
    rows = await repo.page(connection, q='主商机独有搜索词')
    assert {r['id'] for r in rows['items']} == {principal, multiple}
    assert rows['summary']['total'] == 2 and rows['summary']['subject_count'] == 2
    only = await repo.detail(connection, principal)
    assert only['subject_kind'] == 'opportunity' and len(only['linked_opportunities']) == 1
    multi = await repo.detail(connection, multiple)
    assert multi['subject_kind'] == 'partner' and len(multi['linked_opportunities']) == 2
    assert not multi['can_supplement'] and '未关联客户' in multi['supplement_unavailable_reason']
    ops = await actor(connection, 'OPS001')
    await grant(connection, admin, ops.user_id, [dict(permission='visit.read', effect='allow', scope='workspace'),
                                               dict(permission='opportunity.read', effect='deny')])
    await actor(connection, 'OPS001')
    hidden = await repo.page(connection, q='主商机独有搜索词')
    assert hidden['summary']['total'] == 0  # Searching must not become a name oracle.


async def test_safe_detail_original_authorization_and_existing_supplement_contract(connection):
    admin, first, _, customer = await people(connection)
    await grant(connection, admin, admin.user_id, [
        dict(permission='visit.supplement', effect='allow', scope='self'),
        dict(permission='visit.download_original', effect='allow', scope='workspace')])
    import_id = str(uuid4())
    await seed_execute(connection, """INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,
      filename,file_path,file_size,status,storage_key) VALUES($1::uuid,$2::uuid,$3::uuid,'会议原件.txt','/private/should-not-leak',120,'succeeded','/private/should-not-leak')""",
      import_id, admin.workspace_id, admin.user_id)
    # Different creator/confirming person is not an additional restriction on a direct supplement grant.
    visit = await saved(connection, first, customer, recorder=admin.user_id, import_id=import_id)
    draft = await saved(connection, admin, customer, status='pending_confirm')
    async with await client_for(connection) as client:
        await sign_in(client, 'ADMIN001')
        response = await client.get('/api/v1/console/visits/' + visit)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail['can_supplement'] is True and detail['supplement_unavailable_reason'] is None
        assert 'follow_up_record' not in detail['supplement_fields'] and 'collaborator_ids' not in detail['supplement_fields']
        assert detail['partner_name_snapshot'] == '原始伙伴快照' and detail['duration_minutes'] == 30
        assert len(detail['follow_up_record']) == 1200
        assert 'company_policy' not in str(detail) and 'SECRET' not in str(detail) and '/private/' not in str(detail)
        assert detail['original_material_status'] == 'available'
        assert detail['original_materials'][0]['download_url'] == f'/api/v1/visit-imports/{import_id}/original'
        assert (await client.get('/api/v1/console/visits/' + draft)).status_code == 404
        patch = await client.patch('/api/v1/visits/' + visit, json={'contact_title': '新职务', 'version_no': detail['version_no']})
        assert patch.status_code == 200, patch.text
        after = (await client.get('/api/v1/console/visits/' + visit)).json()
        assert after['contact_title'] == '新职务' and after['follow_up_record'] == detail['follow_up_record']
        stale = await client.patch('/api/v1/visits/' + visit, json={'contact_title': '陈旧提交', 'version_no': detail['version_no']})
        assert stale.status_code == 422
        body = await client.patch('/api/v1/visits/' + visit, json={'follow_up_record': '不允许覆盖', 'version_no': after['version_no']})
        assert body.status_code == 422
        await grant(connection, admin, admin.user_id, [dict(permission='visit.download_original', effect='deny')], version=1)
        revoked = (await client.get('/api/v1/console/visits/' + visit)).json()
        assert revoked['original_material_status'] == 'unavailable'
        assert not any(m['can_download'] or m['download_url'] for m in revoked['original_materials'])


async def test_self_scope_directory_and_detail_query_is_selective(connection):
    _, first, second, customer = await people(connection)
    own = await saved(connection, first, customer)
    await saved(connection, second, customer)
    await actor(connection, 'XS001')
    repo = VisitManagementRepository()
    assert (await repo.page(connection, scope='mine'))['summary']['total'] == 1
    assert [r['id'] for r in (await repo.recorders(connection, scope='mine'))['items']] == [first.user_id]
    cte, args, _ = selection(visit_id=own)
    plan = await connection.fetchval('EXPLAIN (ANALYZE,FORMAT JSON) '+cte+' SELECT id FROM filtered', *args)
    # Tiny representative fixture only: verify the materialized metadata is bounded;
    # this is not a claim of a production-sized load/performance test.
    assert plan[0]['Plan']['Actual Rows'] == 1
    assert (await repo.detail(connection, own))['id'] == own


async def test_http_uses_actual_readonly_repeatable_read_under_non_bypass_role(connection, monkeypatch):
    import os
    import asyncpg
    from sales_backend.db import Database, _initialize_connection
    from sales_backend.main import app
    from tests.integration.conftest import _dsn

    role = os.environ['SALES_TEST_ROLE']
    assert role.startswith('salegent_verify_role_')

    async def setup(conn):
        await conn.execute(f'SET ROLE "{role}"')

    observed = []
    for method in ('page', 'recorders', 'detail'):
        original = getattr(VisitManagementRepository, method)

        async def inspect(self, conn, *args, _original=original, **kwargs):
            observed.append(await conn.fetchrow("""SELECT current_setting('transaction_read_only') AS readonly,
                current_setting('transaction_isolation') AS isolation,
                (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user) AS bypass"""))
            return await _original(self, conn, *args, **kwargs)

        monkeypatch.setattr(VisitManagementRepository, method, inspect)
    pool = await asyncpg.create_pool(dsn=_dsn(), min_size=1, max_size=2, init=_initialize_connection, setup=setup)
    try:
        async with await client_for(connection) as client:
            monkeypatch.setattr(app.state, 'database', Database(app.state.settings, pool=pool))
            await sign_in(client, 'ADMIN001')
            for path in ('/api/v1/console/visits', '/api/v1/console/visits/recorders'):
                result = await client.get(path)
                assert result.status_code == 200, result.text
            assert (await client.get('/api/v1/console/visits/' + str(uuid4()))).status_code == 404
        assert len(observed) == 3
        assert all(dict(row) == {'readonly': 'on', 'isolation': 'repeatable read', 'bypass': False} for row in observed)
    finally:
        await pool.close()
