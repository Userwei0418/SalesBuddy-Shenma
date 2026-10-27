"""Full V298 RLS, real restricted API login, isolated synthetic data only."""
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import getpass
import io
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.contracts.work_report import ShareAudience, SharePreviewRequest, ShareRequest, PeriodSelection
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError, resolve_period
from sales_backend.permission_context import current_feature
from sales_backend.repositories.work_report import WorkReportRepository
from sales_backend.services.work_report_sharing import preview_visibility, recipient_access
from sales_backend.services.work_report_provenance import document_issue
from tests.test_work_report_manager_team_eligibility import postgres  # noqa: F401
from tests.test_review_opportunity_projection_postgres import person

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'database/scripts'), str(ROOT/'tools/installation')]
from migrate import migrate
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles


@pytest.mark.asyncio
async def test_share_visibility_real_actor_rls_and_diagnostic_lineage(postgres):
    cfg = dict(host=str(postgres), port=55439, database='postgres', password='', passfile='/dev/null', ssl=False)
    owner = await asyncpg.connect(user=getpass.getuser(), command_timeout=120, **cfg)
    await owner.execute("CREATE DATABASE report_share TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
    await owner.close()
    cfg['database'] = 'report_share'
    owner = await asyncpg.connect(user=getpass.getuser(), command_timeout=120, **cfg)
    api = None
    token = current_feature.set('work_report.share')
    try:
        for role in ('salegent_api','salegent_worker','salegent_feishu_worker','salegent_platform'):
            await owner.execute(f'CREATE ROLE {role} NOLOGIN')
        with redirect_stdout(io.StringIO()):
            await migrate(owner, ROOT/'database', adopt_existing=True)
        await owner.execute('CREATE ROLE report_share_api LOGIN NOSUPERUSER NOBYPASSRLS')
        policy = load_policy()
        async with owner.transaction():
            await reconcile_service_roles(owner, {'api':'report_share_api'}, policy)
            workspace, foreign_workspace = uuid4(), uuid4()
            for w in (workspace, foreign_workspace):
                await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Share synthetic')", w, str(w))
                await owner.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', w)
            author, _ = await person(owner, workspace, 'AUTHOR')
            readers = [(await person(owner, workspace, f'READER{i}'))[0] for i in range(12)]
            foreign, _ = await person(owner, foreign_workspace, 'FOREIGN')
            await owner.execute("UPDATE platform.role_binding SET role_code='administrator',data_scope_code='workspace' WHERE user_ref_id=$1::uuid", author.user_id)
            author = ActorContext.model_validate({**author.model_dump(), 'role':'administrator', 'data_scope':'workspace'})
            await owner.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2::uuid)", workspace, author.user_id)
            for permission in ('work_report.read','work_report.write','work_report.share','task.read'):
                await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2::uuid,$3,'allow','workspace')", workspace, author.user_id, permission)
            # Readers can read reports but only their own task objects.
            await owner.execute("""INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
              SELECT $1,id,p,'self' FROM config.permission_role CROSS JOIN unnest(ARRAY['work_report.read','task.read']) p
              WHERE workspace_id=$1 AND builtin_role_code='sales'
              ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code='self'""", workspace)
            task = uuid4()
            await owner.execute("""INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,title,description,
              due_at,association_kind,status,source_code) VALUES($1,$2,$3::uuid,'Synthetic source','Synthetic source',clock_timestamp()+interval '1 day','daily','in_progress','mini_program')""", task, workspace, readers[0].user_id)
            await owner.execute("""INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_role,responsibility)
              VALUES($1,$2,$3::uuid,'sales','owner')""", task, workspace, readers[0].user_id)
        manifest = [dict(source_type='task', record_id=str(task))]
        doc = dict(id=str(uuid4()), workspace_id=str(workspace), owner_user_ref_id=author.user_id,
                   latest_revision_id=str(uuid4()), version_no=1, source_manifest=manifest,
                   selected_sources=['task'], context=dict(scope=dict(kind='self')))
        repo = WorkReportRepository()
        api = await asyncpg.connect(user='report_share_api', command_timeout=45, **cfg)
        await _initialize_connection(api)
        assert not await api.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        async with api.transaction(readonly=True, isolation='repeatable_read'):
            await set_request_context(api, author)
            before = await api.fetchrow("SELECT current_setting('app.user_ref_id') u,current_setting('app.role_code') r,current_setting('app.authorization_context') a,current_setting('app.authorized_feature') f")
            started = perf_counter()
            preview = await preview_visibility(api, author, doc, ShareAudience(kind='company'), repo._document_sources)
            elapsed = perf_counter() - started
            assert (preview.recipient_count, preview.readable_count, preview.unreadable_count) == (12,1,11)
            assert preview.confirmation_required and preview.can_share
            after = await api.fetchrow("SELECT current_setting('app.user_ref_id') u,current_setting('app.role_code') r,current_setting('app.authorization_context') a,current_setting('app.authorized_feature') f")
            assert before == after and current_feature.get() == 'work_report.share'
            assert not (await recipient_access(api, foreign, doc, repo._document_sources))[0]
            async def denied(*args):
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', 'Synthetic', status=403)
            assert not (await recipient_access(api, readers[0], doc, denied))[0]
            async def broken(c, *_):
                await c.execute('SELECT 1/0')
            with pytest.raises(asyncpg.DivisionByZeroError):
                await recipient_access(api, readers[0], doc, broken)
            assert before == await api.fetchrow("SELECT current_setting('app.user_ref_id') u,current_setting('app.role_code') r,current_setting('app.authorization_context') a,current_setting('app.authorized_feature') f")
            assert current_feature.get() == 'work_report.share'
            async def cancelled(*args):
                raise asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await recipient_access(api, readers[0], doc, cancelled)
            assert current_feature.get() == 'work_report.share'
            assert before == await api.fetchrow("SELECT current_setting('app.user_ref_id') u,current_setting('app.role_code') r,current_setting('app.authorization_context') a,current_setting('app.authorized_feature') f")
            # Full v2 scope checks are stricter than its one readable source.
            subject = author.user_id
            expanded = {**doc, 'context': {'scope': dict(contract_version='review.scope.v2', kind='member', label='Synthetic',
                subject_ids=[subject], subjects=[dict(scope_type='person',id=subject,label='Synthetic')],
                resolved_member_ids=[subject], scope_key='review-scope:v2:'+'a'*64, scope_revision='b'*64)}}
            assert not (await recipient_access(api, readers[0], expanded, repo._document_sources))[0]
        # Live revocation is read again, not taken from the preview or old GUC.
        await owner.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2::uuid)", workspace, readers[0].user_id)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2::uuid,'task.read','deny')", workspace, readers[0].user_id)
        async with api.transaction(readonly=True):
            await set_request_context(api, author)
            revoked = await preview_visibility(api, author, doc, ShareAudience(kind='company'), repo._document_sources)
            assert not revoked.can_share and revoked.readable_count == 0 and revoked.preview_hash != preview.preview_hash
        await owner.execute("DELETE FROM config.account_permission_override WHERE user_ref_id=$1::uuid", readers[0].user_id)
        # Seed genuine immutable report lineage; a newer unshared generation must
        # not replace the diagnostic for the older published manual revision.
        snapshot, document, initial, manual, later, run1, run2 = [uuid4() for _ in range(7)]
        async with owner.transaction():
            await set_request_context(owner, author)
            await owner.execute("""INSERT INTO agent.work_report_snapshot(id,workspace_id,owner_user_ref_id,selection,period,scope,status,payload,snapshot_hash,data_as_of)
              VALUES($1,$2,$3::uuid,$4::jsonb,$8::jsonb,$5::jsonb,'ready',$6::jsonb,$7,clock_timestamp())""",
              snapshot, workspace, author.user_id, json.dumps({'sources':['task']}), json.dumps({'kind':'self','label':'Synthetic','member_ids':[author.user_id]}), json.dumps({'coverage':[],'metrics':[],'sources':manifest}), 'a'*64, resolve_period(PeriodSelection(), now=datetime.now(timezone.utc)).model_dump_json())
            await owner.execute("""INSERT INTO agent.work_report_document(id,workspace_id,owner_user_ref_id,snapshot_id,snapshot_hash,context,source_manifest,selected_sources,latest_revision_id)
              VALUES($1,$2,$3::uuid,$4,$5,'{}','[]','[]',$6)""", document, workspace, author.user_id, snapshot,'a'*64,later)
            for run, rev, status in ((run1, initial, 'basic_summary'), (run2,later,'generated')):
                await owner.execute("""INSERT INTO agent.work_report_run(id,workspace_id,owner_user_ref_id,snapshot_id,request,generation_input,status,document_id,revision_id,result)
                  VALUES($1,$2,$3::uuid,$4,'{}','{}','succeeded',$5,$6,$7::jsonb)""", run,workspace,author.user_id,snapshot,document,rev,json.dumps(dict(status=status, trace={'generation_issue':{'code':'AI_OUTPUT_INVALID','message':'UNTRUSTED'}})))
            scheme=json.dumps(dict(scheme_id=str(uuid4()),version_no=1,name='Synthetic',config_hash='b'*64))
            for index,(rev,origin,run) in enumerate(((initial,'basic_summary',run1),(manual,'manual',None),(later,'generated',run2)),1):
                await owner.execute("""INSERT INTO agent.work_report_revision(id,workspace_id,document_id,revision_no,origin,content,scheme,created_by_user_ref_id,run_id)
                  VALUES($1,$2,$3,$4,$5,$9::jsonb,$6::jsonb,$7::uuid,$8)""",rev,workspace,document,index,origin,scheme,author.user_id,run,json.dumps(dict(title='Synthetic report',sections=[dict(id='summary',title='Summary',items=[])],missing_data=[])))
            await owner.execute("UPDATE agent.work_report_document SET shared_revision_id=$2,audience=$3::jsonb WHERE id=$1", document,manual,json.dumps(dict(kind='members',member_ids=[readers[0].user_id])))
        async with api.transaction(readonly=True):
            await set_request_context(api, readers[0])
            visible = await repo._document(api, readers[0], str(document))
            assert visible['visible_revision_no'] == 2
            issue = await document_issue(api, readers[0], visible)
            assert issue['code']=='AI_OUTPUT_INVALID' and 'UNTRUSTED' not in issue['message']
            assert await api.fetchval('SELECT common.current_user_ref_id()::text') == readers[0].user_id
            assert await document_issue(api, readers[0], {**visible,'context':{'meeting_facts':{}}}) is None
        async with api.transaction(readonly=True):
            await set_request_context(api, author)
            visible = await repo._document(api, author, str(document))
            assert visible['visible_revision_no']==3 and await document_issue(api, author, visible) is None
        async with api.transaction():
            await set_request_context(api, author)
            saved = await repo.get_document(api, author, str(document))
            body = dict(version_no=saved.version_no, audience=dict(kind='company'))
            confirmation = await repo.share_preview(api, author, str(document), SharePreviewRequest(**body))
            with pytest.raises(WorkReportError) as error:
                await repo.share(api, author, str(document), ShareRequest(**body, confirm=True))
            assert error.value.code == 'WORK_REPORT_SHARE_CONFIRMATION_REQUIRED'
            shared = await repo.share(api, author, str(document), ShareRequest(**body, confirm=True,
                visibility_confirmation=confirmation.preview_hash))
            assert shared.audience.kind == 'company' and shared.share_visibility.readable_count == 1
            assert shared.shared_revision_id == later
        async with api.transaction(readonly=True):
            await set_request_context(api, readers[0])
            assert (await repo.get_document(api, readers[0], str(document))).generation_issue is None
        async with api.transaction(readonly=True):
            await set_request_context(api, readers[1])
            with pytest.raises(WorkReportError) as error:
                await repo.get_document(api, readers[1], str(document))
            assert error.value.status == 404
        await verify_service_roles(owner, {'api':'report_share_api'}, policy)
        print(json.dumps(dict(synthetic=True, restricted_api=True, recipients=12, readable=1,
            preview_elapsed_ms=round(elapsed*1000), checks=['mixed_visibility','cross_company','source_denial','scope_v2',
            'revocation','context_restore_success_denial_sql_error','shared_revision_lineage','manual_provenance','meeting_not_ai','real_share_confirmation_and_recipient_read','exact_grants']),ensure_ascii=False))
    finally:
        current_feature.reset(token)
        if api: await api.close()
        await owner.close()
