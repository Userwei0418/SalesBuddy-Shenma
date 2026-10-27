"""Visit reuse with the complete runtime policy in a private synthetic PG.

No shared database, account credentials or model calls. The fixture stops and
removes only its own cluster, including when any assertion fails.
"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import getpass
import io
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.permission_context import current_feature
from sales_backend.repositories.opportunity_history import native_creation
from sales_backend.repositories.review_overview import opportunity_rows
from sales_backend.services.review_overview import metric_cards
from tests.test_work_report_manager_team_eligibility import postgres  # noqa: F401 - private cluster

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/scripts'), str(ROOT / 'tools/installation')]
from migrate import migrate
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles

ROLE = 'review_projection_api'


def day(n):
    return datetime(2026, 10, n, tzinfo=timezone.utc)


async def person(c, workspace, suffix):
    team, user, customer = [uuid4() for _ in range(3)]
    await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", team, workspace, suffix)
    await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)", user, workspace, suffix)
    await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", workspace, user, team)
    await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", workspace, user, team)
    await c.execute("""INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id)
      VALUES($1,$2,$3,$3,$4,$4,$5)""", customer, workspace, suffix, user, team)
    await c.execute("""INSERT INTO crm.customer_ownership(customer_id,workspace_id,owner_user_ref_id,state)
      VALUES($1,$2,$3,'claimed') ON CONFLICT(customer_id) DO UPDATE SET owner_user_ref_id=excluded.owner_user_ref_id,state='claimed'""", customer, workspace, user)
    return ActorContext(workspace_id=str(workspace), user_id=str(user), role='sales', data_scope='self', team_ids=(str(team),)), customer


async def opportunity(c, actor, customer, name):
    ident = uuid4()
    await set_request_context(c, actor)
    async with native_creation(c, ident):
        await c.execute("""INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,created_by_user_ref_id,
          owner_user_ref_id,owner_team_id,source_code,amount,probability,stage_code)
          VALUES($1,$2::uuid,$3,$4,$5::uuid,$5::uuid,$6::uuid,'manual',100,70,'identified')""",
          ident, actor.workspace_id, customer, name, actor.user_id, actor.team_ids[0])
    return ident


async def visit(c, actor, customer, opportunity_id, when, *, status='archived', deleted=False):
    return await c.fetchval("""INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
      recorder_team_id,form_version_id,status,interaction_at,created_by_user_ref_id,deleted_at)
      VALUES($1::uuid,$2,$3,$4::uuid,$5::uuid,
        (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),$6,$7,$4::uuid,
        CASE WHEN $8 THEN transaction_timestamp() END) RETURNING id""",
      actor.workspace_id, customer, opportunity_id, actor.user_id, actor.team_ids[0], status, when, deleted)


@pytest.mark.asyncio
async def test_visit_batch_keeps_authorized_max_nulls_money_and_denials(postgres):
    cfg = dict(host=str(postgres), port=55439, database='postgres', password='', passfile='/dev/null', ssl=False)
    owner = await asyncpg.connect(user=getpass.getuser(), command_timeout=120, **cfg)
    await owner.execute("CREATE DATABASE review_projection TEMPLATE template0 ENCODING 'UTF8'")
    await owner.close()
    cfg['database'] = 'review_projection'
    owner = await asyncpg.connect(user=getpass.getuser(), command_timeout=120, **cfg)
    api = None
    try:
        for role in ('salegent_api', 'salegent_worker', 'salegent_feishu_worker', 'salegent_platform'):
            await owner.execute(f'CREATE ROLE {role} NOLOGIN')
        with redirect_stdout(io.StringIO()):
            await migrate(owner, ROOT / 'database', adopt_existing=True)
        await owner.execute(f'CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOBYPASSRLS')
        policy = load_policy()
        async with owner.transaction():
            await reconcile_service_roles(owner, {'api': ROLE}, policy)
            workspaces = [uuid4(), uuid4()]
            for w in workspaces:
                await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Projection synthetic')", w, str(w))
                await owner.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', w)
            first, customer = await person(owner, workspaces[0], 'first')
            other, other_customer = await person(owner, workspaces[0], 'other')
            foreign, foreign_customer = await person(owner, workspaces[1], 'foreign')
            a, b, empty = [await opportunity(owner, first, customer, name) for name in ('direct', 'linked', 'empty')]
            hidden = await opportunity(owner, other, other_customer, 'hidden')
            foreign_opp = await opportunity(owner, foreign, foreign_customer, 'foreign')
            await set_request_context(owner, first)
            await owner.execute("""INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,
              collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,2026,4,0,0,'high'),($1,$3,2026,4,NULL,20,'high')""", workspaces[0], a, b)
            direct_linked = await visit(owner, first, customer, a, day(20))
            await owner.executemany('INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
                                    [(workspaces[0], direct_linked, a)])
            linked_only = await visit(owner, first, customer, None, day(20))
            await owner.execute('INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3)', workspaces[0], linked_only, b)
            await visit(owner, first, customer, a, day(21))
            await visit(owner, first, customer, a, day(28), status='recording')
            await visit(owner, first, customer, a, day(29), deleted=True)
            unauthorized = await visit(owner, other, customer, a, day(30))
            await owner.execute('INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING', workspaces[0], unauthorized, a)
            await visit(owner, other, other_customer, hidden, day(30))
            await visit(owner, foreign, foreign_customer, foreign_opp, day(30))
        api = await asyncpg.connect(user=ROLE, command_timeout=30, **cfg)
        await _initialize_connection(api)
        assert not await api.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        own = SimpleNamespace(kind='self', member_ids=[UUID(first.user_id)], team_id=None)
        async with api.transaction(readonly=True, isolation='repeatable_read'):
            current_feature.set('work_report.read')
            await set_request_context(api, first)
            assert not await api.fetchval("SELECT security.authorization_visit('work_report.read',$1)", unauthorized)
            rows = await opportunity_rows(api, first, own, year=2026, quarter=4)
            by_id = {r['id']: r for r in rows}
            assert set(by_id) == {str(a), str(b), str(empty)}
            assert by_id[str(a)]['last_follow_up_at'] == day(21)
            assert by_id[str(b)]['last_follow_up_at'] == day(20)
            assert by_id[str(empty)]['last_follow_up_at'] is None
            assert by_id[str(a)]['collection_amount'] == '0.00'
            assert by_id[str(b)]['collection_amount'] is None and by_id[str(b)]['forecast_id']
            assert by_id[str(empty)]['forecast_id'] is None
            cards = {m['metric_code']: m for m in metric_cards(rows, {}, False, year=2026, quarter=4)}
            assert cards['forecast_collection']['known_subtotal'] == '0.00'
            assert cards['forecast_collection']['missing_amount_count'] == 1
            assert cards['forecast_collection']['unknown_eligibility_count'] == 1
            assert cards['forecast_recognized']['known_subtotal'] == '20.00'
            assert all(len(m['source_refs']) == 3 for m in cards.values())
            for target in (other, foreign):
                scope = SimpleNamespace(kind='team', member_ids=[], team_id=UUID(target.team_ids[0]))
                assert await opportunity_rows(api, first, scope, year=2026, quarter=4) == []
        await owner.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2::uuid)', workspaces[0], first.user_id)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2::uuid,'visit.read','deny')", workspaces[0], first.user_id)
        async with api.transaction(readonly=True, isolation='repeatable_read'):
            await set_request_context(api, first)
            rows = await opportunity_rows(api, first, own, year=2026, quarter=4)
            assert len(rows) == 3 and all(r['last_follow_up_at'] is None for r in rows)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2::uuid,'opportunity.read','deny')", workspaces[0], first.user_id)
        async with api.transaction(readonly=True, isolation='repeatable_read'):
            await set_request_context(api, first)
            assert await opportunity_rows(api, first, own, year=2026, quarter=4) == []
        await verify_service_roles(owner, {'api': ROLE}, policy)
    finally:
        if api:
            await api.close()
        await owner.close()
