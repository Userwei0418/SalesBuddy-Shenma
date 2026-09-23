"""Upgrade customer-wide analysis without exposing old colleague facts or deleting history."""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

import asyncpg

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'backend/src'),str(ROOT/'database/scripts')]
import migrate as migrations
from verify_jobs_postgres import settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext


async def main():
    config={'host':os.environ.get('PGHOST','/tmp'),'user':os.environ.get('PGUSER','postgres')}
    admin=await asyncpg.connect(database='postgres',**config)
    suffix=uuid.uuid4().hex[:12];name='salegent_verify_legacy_'+suffix;role='salegent_verify_role_'+suffix
    c=db=None;made_role=False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c=await asyncpg.connect(database=name,**config)
        discover=migrations.discover
        migrations.discover=lambda root=migrations.ROOT:[m for m in discover(root) if m.key not in ('V045','V046','V047')]
        await migrations.migrate(c)
        migrations.discover=discover
        w,t,a,b,cid=[uuid.uuid4() for _ in range(5)]
        await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'旧缓存验收')",w,str(w))
        await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'south','南区')",t,w)
        for u in (a,b):
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'销售')",u,w,str(u))
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)",w,u,t)
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')",w,u,t)
        await c.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,created_by_user_ref_id) VALUES($1,$2,'共享客户','共享客户',$3,$4,$3)",cid,w,a,t)
        await c.execute("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount,probability,stage_code,status) VALUES($1,$2,'其他销售机密商机',$3,$4,90000,30,'s30','open')",w,cid,b,t)
        rule=await c.fetchval("SELECT id FROM config.rule_set LIMIT 1")
        old=await c.fetchval("INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,relationship_score,quadrant_code,rule_set_id,input_snapshot,subject_user_ref_id,calculated_at) VALUES($1,$2,70,80,'main_attack',$3,'{}',$4,clock_timestamp()-interval '1 day') RETURNING id",w,cid,rule,a)
        fresh=await c.fetchval("INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,relationship_score,quadrant_code,rule_set_id,input_snapshot,subject_user_ref_id) VALUES($1,$2,70,80,'main_attack',$3,'{}',$4) RETURNING id",w,cid,rule,b)
        note=await c.fetchval("INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,object_type,object_id,payload,created_at) VALUES($1,$2,'in_app','business_changed','变化','字段变化','customer',$3::uuid,jsonb_build_object('customer_id',$3::uuid::text,'ai_review','旧范围评估','changes','人工字段保持'),clock_timestamp()-interval '1 day') RETURNING id",w,a,cid)
        await migrations.migrate(c)
        assert await c.fetchval("SELECT fact_scope_version=1 AND valid_to<clock_timestamp() FROM insight.quadrant_score WHERE id=$1",old)
        assert await c.fetchval("SELECT fact_scope_version=2 AND valid_to='infinity' FROM insight.quadrant_score WHERE id=$1",fresh)
        payload=await c.fetchval("SELECT payload::text FROM workflow.notification WHERE id=$1",note)
        assert json.loads(payload)=={'customer_id':str(cid),'changes':'人工字段保持'}
        assert await c.fetchval("SELECT count(*) FROM ops.job WHERE job_type='battle_map.review' AND payload->>'user_id'=$1",str(a))==1
        await migrations.migrate(c)
        assert await c.fetchval("SELECT count(*) FROM ops.job")==1
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS');made_role=True
        for s in ('platform','crm','insight','common','security','config'):
            await c.execute(f'GRANT USAGE ON SCHEMA {s} TO "{role}"')
            await c.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO "{role}"')
            await c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {s} TO "{role}"')
        db=Database(settings(name));await db.connect()
        actor=ActorContext(workspace_id=str(w),user_id=str(a),role='sales',data_scope='self',team_ids=(str(t),))
        async with db.transaction(actor,readonly=True) as tx:
            await tx.execute(f'SET LOCAL ROLE "{role}"')
            assert not await tx.fetchval('SELECT id FROM insight.quadrant_score WHERE id=$1',old)
        print(json.dumps({'passed':5,'checks':['expire_legacy_keep_history','retain_fresh_personal_score','preserve_formal_changes','enqueue_once','rls_hide_legacy_analysis']}))
    finally:
        if db:await db.close()
        if c:await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if made_role:await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


if __name__=='__main__':asyncio.run(main())
