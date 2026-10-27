"""Read actual mixed-history task projections with full RLS in disposable PG.

Only synthetic seed data uses the migration owner. All assertions execute as the
normal non-superuser API role with the unchanged installation policy.
"""
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
import getpass
import io
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.permission_context import current_feature
from sales_backend.repositories.task_browse import TaskBrowseRepository
from sales_backend.repositories.task_management import TaskManagementRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.repositories.work_report_task_activity import read_task_activities
from sales_backend.contracts.work_report_base import ResolvedPeriod
from sales_backend.contracts.work_report_overview import OverviewResolvedScope
from tests.test_work_report_manager_team_eligibility import postgres  # noqa: F401
from tests.test_review_opportunity_projection_postgres import person

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'database/scripts'), str(ROOT/'tools/installation')]
from migrate import migrate
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles


async def seed(c, actor, *, mode='legacy', status='completed', action=None, at=None, event=None):
    ident = uuid4()
    await set_request_context(c, actor)
    await c.execute("""INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,creator_team_id,
      title,description,due_at,association_kind,source_code,status,execution_mode,completed_at,created_at)
      VALUES($1,$2::uuid,$3::uuid,$4::uuid,'Synthetic outcome','Synthetic historical outcome',
        clock_timestamp()+interval '1 day','daily','mini_program',$5,$6,$7,clock_timestamp()-interval '2 days')""",
      ident, actor.workspace_id, actor.user_id, actor.team_ids[0], status, mode, at if status=='completed' else None)
    await c.execute("""INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility)
      VALUES($1,$2::uuid,$3::uuid,$4::uuid,'sales','owner')""",ident,actor.workspace_id,actor.user_id,actor.team_ids[0])
    if mode != 'legacy':
        version = int(mode[-1])
        state = dict(protocol_version=version, items=[{'id':str(uuid4()),'text':'Historical item','state':'completed'}],
          requirements_version=1,responsibility_version=1,completion_result={'action':action,'note':'Synthetic reason','occurred_at':at.isoformat() if at else None})
        await c.execute('INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES($1,$2::uuid,$3::jsonb)',ident,actor.workspace_id,json.dumps(state))
    if event:
        await c.execute("""INSERT INTO workflow.task_event(task_id,workspace_id,actor_user_ref_id,event_type,from_status,to_status,occurred_at)
          VALUES($1,$2::uuid,$3::uuid,$4,'in_progress',$5,$6)""",ident,actor.workspace_id,actor.user_id,event,status,at)
        # A later comment must not move the end date or change the reason.
        await c.execute("""INSERT INTO workflow.task_event(task_id,workspace_id,actor_user_ref_id,event_type,from_status,to_status,occurred_at)
          VALUES($1,$2::uuid,$3::uuid,'comment',$4,$4,clock_timestamp())""",ident,actor.workspace_id,actor.user_id,status)
    return str(ident)


@pytest.mark.asyncio
async def test_full_scope_counts_end_dates_pagination_and_rls(postgres):
    cfg=dict(host=str(postgres),port=55439,database='postgres',password='',passfile='/dev/null',ssl=False)
    owner=await asyncpg.connect(user=getpass.getuser(),command_timeout=120,**cfg)
    await owner.execute("CREATE DATABASE task_outcome TEMPLATE template0 ENCODING 'UTF8'")
    await owner.close()
    cfg['database']='task_outcome'
    owner=await asyncpg.connect(user=getpass.getuser(),command_timeout=120,**cfg)
    api=None
    try:
        for role in ('salegent_api','salegent_worker','salegent_feishu_worker','salegent_platform'):
            await owner.execute(f'CREATE ROLE {role} NOLOGIN')
        with redirect_stdout(io.StringIO()):
            await migrate(owner,ROOT/'database',adopt_existing=True)
        await owner.execute('CREATE ROLE outcome_api LOGIN NOSUPERUSER NOBYPASSRLS')
        policy=load_policy()
        async with owner.transaction():
            await reconcile_service_roles(owner,{'api':'outcome_api'},policy)
            w,foreign_w=uuid4(),uuid4()
            for workspace in (w,foreign_w):
                await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Outcome synthetic')",workspace,str(workspace))
                await owner.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1',workspace)
            actor,_=await person(owner,w,'own'); other,_=await person(owner,w,'other'); foreign,_=await person(owner,foreign_w,'foreign')
            now=await owner.fetchval('SELECT clock_timestamp()')
            normal={await seed(owner,actor,mode=mode,at=now) for mode in ('legacy','execution_v1','execution_v2')}
            normal.add(await seed(owner,actor,mode='execution_v3',action='submit_completion',at=now))
            normal.add(await seed(owner,actor,mode='execution_v3',action='review_completion',at=now))
            stopped=await seed(owner,actor,mode='execution_v3',action='close',at=now)
            unknown=await seed(owner,actor,mode='execution_v3',action='future_action',at=now)
            cancelled=await seed(owner,actor,status='cancelled',event='cancel',at=datetime(2026,7,2,tzinfo=UTC))
            rejected=await seed(owner,actor,status='cancelled',event='reject',at=now)
            pending=await seed(owner,actor,status='in_progress')
            invisible={await seed(owner,a,at=now) for a in (other,foreign)}
        api=await asyncpg.connect(user='outcome_api',command_timeout=60,**cfg)
        await _initialize_connection(api)
        assert not await api.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        async with api.transaction(readonly=True,isolation='repeatable_read'):
            current_feature.set('task.read'); await set_request_context(api,actor)
            repo=TaskBrowseRepository()
            first=await repo.page(api,tab='ended',limit=2,offset=0,fde_view='self')
            s=first['summary']
            assert (s['total'],s['ended_count'],s['completed_count'],s['stopped_count'],s['unknown_outcome_count'],s['rejected_count'])==(10,9,5,1,1,2)
            assert s['filtered_total']==9 and len(first['items'])==2 and first['has_more']
            rows=[]
            for offset in range(0,10,2):
                page=await repo.page(api,tab='ended',limit=2,offset=offset,fde_view='self')
                assert page['summary']==s
                rows.extend(page['items'])
            byid={r['id']:r for r in rows}
            assert len(byid)==9 and not invisible.intersection(byid) and pending not in byid
            assert byid[stopped]['end_reason']=='stopped' and byid[unknown]['end_reason']=='unknown'
            assert byid[cancelled]['end_reason']=='cancelled' and byid[rejected]['end_reason']=='rejected'
            assert byid[cancelled]['ended_at']==datetime(2026,7,2,tzinfo=UTC)
            assert {r['id'] for r in (await repo.page(api,tab='completed',fde_view='self'))['items']}==normal
            assert {r['id'] for r in (await repo.page(api,tab='rejected',fde_view='self'))['items']}=={cancelled,rejected}
            assert len(await TaskRepository().list(api,status='completed',customer_id=None,limit=100))==7  # raw compatibility
            q3=await repo.page(api,tab='ended',fde_view='self',completed_year=2026,completed_quarters=[3])
            assert {r['id'] for r in q3['items']}=={cancelled}
            overview=await TaskRepository().overview(api,fde_view='self')
            assert overview['metrics']['today_completed']==5 and overview['metrics']['ended_count']==9
            management=await TaskManagementRepository().page(api,tab='ended',scope='mine',limit=2)
            assert management['summary']['completed_count']==5 and management['summary']['filtered_total']==9
            current_feature.set('work_report.read')
            end=now+timedelta(seconds=1)
            period=ResolvedPeriod(kind='day',start=now-timedelta(hours=1),cutoff=end,end_exclusive=end+timedelta(hours=1),label='合成时段')
            scope=OverviewResolvedScope(kind='self',label='本人',member_ids=[UUID(actor.user_id)])
            legacy,modern=await read_task_activities(api,actor,period=period,scope=scope,data_as_of=end)
            assert {str(r.id) for r in modern.completed_tasks}==normal
            assert modern.counts.completed_tasks.known_count==5 and modern.counts.completed_tasks.value is None  # unknown V3 result
            assert stopped not in {str(r.id) for r in legacy.completed_tasks}
        await owner.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2::uuid)',w,actor.user_id)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2::uuid,'task.read','deny')",w,actor.user_id)
        async with api.transaction(readonly=True):
            current_feature.set('task.read'); await set_request_context(api,actor)
            assert (await TaskBrowseRepository().page(api,tab='ended',fde_view='self'))['summary']['total']==0
        await verify_service_roles(owner,{'api':'outcome_api'},policy)
    finally:
        if api: await api.close()
        await owner.close()
