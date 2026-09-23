"""Verify current form binding, first-visit persistence and archive history in PostgreSQL."""
import asyncio
from datetime import date
import json
import os
from pathlib import Path
import sys
import uuid
import asyncpg

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'backend/src'),str(ROOT/'database/scripts')]
from migrate import migrate
from verify_jobs_postgres import settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext
from sales_backend.contracts.visit_schema import FORM_VERSION_ID, FIELDS, schema_snapshot
from sales_backend.repositories.visits import VisitRepository
from sales_backend.repositories.assistant import AssistantRepository


async def main():
    config={'host':os.environ.get('PGHOST','/tmp'),'user':os.environ.get('PGUSER','postgres')}
    admin=await asyncpg.connect(database='postgres',**config)
    name='salegent_verify_visits_'+uuid.uuid4().hex[:12]
    conn=db=None;checks=[]
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn=await asyncpg.connect(database=name,**config);await migrate(conn)
        workspace,user,customer=[uuid.uuid4() for _ in range(3)]
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'表单测试')",workspace,str(workspace))
        await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'销售')",user,workspace,str(user))
        await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'sales','self')",workspace,user)
        await conn.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,created_by_user_ref_id,customer_type_code) VALUES($1,$2,'新客户','新客户',$3,$3,'潜在客户')",customer,workspace,user)
        actor=ActorContext(workspace_id=str(workspace),user_id=str(user),role='sales',data_scope='self')
        db=Database(settings(name));await db.connect();repo=VisitRepository()
        async with db.transaction(actor) as c:
            assert await repo.form_schema(c)==schema_snapshot()['fields']
            fields=dict(follow_up_record='客户要求先试点检索效果',next_action='9月12日销售安排演示',
                contact_name='陈经理',interaction_at='2026-09-09',created_date='2026-09-10',is_first_visit=True,
                customer_main_business='设备制造',customer_needs='查找售后资料',customer_budget='客户尚未确定预算',
                contact_role='影响者',collaborator_ids=[str(user)],_follow_up_quality_score=61)
            result=await repo.create(c,actor,customer_id=str(customer),fields=fields)
            detail=await repo.detail(c,result['id'])
            assert detail['visit_goal']=='' and detail['customer_needs']=='查找售后资料'
            assert detail['recorded_on']==date(2026,9,10) and detail['form_version_id']==FORM_VERSION_ID
            assert result['opportunity_id'] is None and result['total_count']==len(FIELDS)
            assert await c.fetchval('SELECT count(*) FROM crm.opportunity')==0
            assert await c.fetchval('''SELECT count(*) FROM activity.visit_field_value val
                LEFT JOIN config.form_version_field f ON f.field_definition_id=val.field_definition_id AND f.form_version_id=$2::uuid
                WHERE val.visit_id=$1::uuid AND f.field_definition_id IS NULL''',result['id'],FORM_VERSION_ID)==0
            checks.append('two_sections_first_visit_fields_and_unknown_budget_persist_in_exact_form_version')
            original=detail['archived_fields'];created_at=detail['created_at']
            updated=await repo.supplement(c,actor,result['id'],{'version_no':detail['version_no'],
                'is_first_visit':True,'customer_needs':'补充人工说明','created_date':'2026-09-08','partner_name':'伙伴'})
            assert updated['customer_needs']=='补充人工说明' and updated['created_date']=='2026-09-08'
            assert updated['archived_fields']==original and updated['created_at']==created_at
            await c.execute("UPDATE crm.customer SET customer_type_code='已成单客户' WHERE id=$1",customer)
            assert (await repo.detail(c,result['id']))['customer_type']=='潜在客户'
            checks.append('current_supplement_is_separate_from_immutable_archive_and_system_timestamp')
            receipts=await AssistantRepository().archived_visits(c,actor)
            receipt=next(r for r in receipts if r['id']==result['id'])
            assert receipt['score']==61 and receipt['grade']=='合格'
            assert receipt['total_count']==result['total_count'] and receipt['completed_count']==result['completed_count']
            checks.append('overview_receipt_reads_real_archived_score_and_version_field_counts')
            for sql in ["UPDATE activity.visit SET archived_fields='{}' WHERE id=$1::uuid",
                        "UPDATE activity.visit SET created_at=clock_timestamp()+interval '1 day' WHERE id=$1::uuid"]:
                try:
                    async with c.transaction(): await c.execute(sql,result['id'])
                    raise AssertionError('immutable archive was overwritten')
                except asyncpg.CheckViolationError: pass
            checks.append('database_rejects_archive_and_audit_time_overwrite')
            legacy=await c.fetchval('''INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,archived_at,
                follow_up_record,next_action,follow_up_score) VALUES($1,$2,$3,'31000000-0000-0000-0000-000000000035','archived',clock_timestamp(),
                '历史沟通','历史计划',85) RETURNING id''',workspace,customer,user)
            assert any(r['id']==str(legacy) for r in await AssistantRepository().archived_visits(c,actor))
            checks.append('previous_form_archives_still_appear_in_overview')
        print(json.dumps({'passed':len(checks),'checks':checks}))
    finally:
        if db: await db.close()
        if conn: await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)');await admin.close()

if __name__=='__main__':asyncio.run(main())
