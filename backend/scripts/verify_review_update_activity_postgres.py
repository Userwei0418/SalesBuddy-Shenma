# ruff: noqa: E501 -- Narrow opt-in extension of the disposable V253 fixture.
"""Run via verify_review_history_postgres.py <private socket> <port> activity."""
from uuid import uuid4

from sales_backend.db import set_request_context


async def run_activity_checks(c, role, actor, spaces, begin):
    checks = []
    ws, team, uid, customer, oid = spaces[0]
    await c.execute("UPDATE ops.review_history_capture SET complete_from=$1::timestamptz-interval '1 day'",begin)

    async def read(start, subjects=None, actor_context=None):
        await c.execute(f'SET ROLE "{role}"')
        try:
            async with c.transaction(readonly=True,isolation='repeatable_read'):
                await set_request_context(c, actor_context or actor)
                return await c.fetchval('SELECT security.review_update_activity($1,$2,$3,transaction_timestamp())',
                                        'team',subjects or [team],start)
        finally:
            await c.execute('RESET ROLE')

    async def mutate(sql,*args):
        async with c.transaction():
            await set_request_context(c,actor)
            return await c.execute(sql,*args)

    start=await c.fetchval('SELECT clock_timestamp()')
    assert (await read(start))['items']==[]
    await mutate('UPDATE crm.opportunity SET updated_at=clock_timestamp() WHERE id=$1',oid)
    no_op=await read(start)
    assert no_op['items']==[] and no_op['history_coverage']['status']=='complete'
    checks.append('V254_complete_zero_and_timestamp_only_noop_remain_zero')
    await mutate("UPDATE crm.opportunity_forecast SET collection_confidence='high' WHERE opportunity_id=$1",oid)
    await mutate("UPDATE crm.opportunity_forecast SET collection_confidence='low' WHERE opportunity_id=$1",oid)
    changed=await read(start,[team,team])
    assert len(changed['items'])==1 and len(changed['items'][0]['events'])==2
    assert changed['history_coverage']['status']=='complete'
    assert [e['changes'][0]['field'] for e in changed['items'][0]['events']]==['collection_confidence']*2
    assert [(e['changes'][0]['before'],e['changes'][0]['after']) for e in changed['items'][0]['events']]==[('low','high'),('high','low')]
    checks.append('V254_nonzero_distinct_opportunity_roundtrip_fields_and_duplicate_subject_dedupe')
    born=uuid4()
    born_start=await c.fetchval('SELECT clock_timestamp()')
    async with c.transaction():
        await set_request_context(c,actor)
        await c.execute("""INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
            amount,currency,probability,stage_code,status,created_by_user_ref_id)
            VALUES($1,$2,$3,$4,$5,$6,100,'CNY',70,'proposal','open',$5)""",born,ws,customer,str(born),uid,team)
        await c.execute("""INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,
            collection_amount,recognized_amount,collection_confidence)
            VALUES($1,$2,2026,4,100,200,'high')""",ws,born)
    assert (await read(born_start))['items']==[]
    await mutate('UPDATE crm.opportunity SET name=$2 WHERE id=$1',born,'Changed after creation')
    creation_result=await read(born_start)
    assert [r['id'] for r in creation_result['items']]==[str(born)]
    assert creation_result['items'][0]['events'][0]['changes'][0]['field']=='name'
    checks.append('V254_pure_creation_initial_children_excluded_but_later_update_counted')
    closed_start=await c.fetchval('SELECT clock_timestamp()')
    closed=[]
    for status in ('won','lost'):
        ident=uuid4()
        closed.append(str(ident))
        await mutate("""INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
            amount,currency,probability,stage_code,status,created_by_user_ref_id)
            VALUES($1,$2,$3,$4,$5,$6,100,'CNY',70,'proposal',$7,$5)""",ident,ws,customer,str(ident),uid,team,status)
        await mutate('UPDATE crm.opportunity SET note=$2 WHERE id=$1',ident,'Changed closed opportunity')
    assert sorted(r['id'] for r in (await read(closed_start))['items'])==sorted(closed)
    checks.append('V254_won_and_lost_updates_are_not_filtered_like_active_pool')
    denied=await read(start,[spaces[1][1]])
    assert denied['items']==[] and denied['history_coverage']['status']!='complete'
    limited=actor.model_copy(update={'data_scope':'self','team_ids':[]})
    # Actor identity has existing workspace grants; the foreign subject check,
    # not a forged presentation scope string, is the authorization boundary.
    assert (await read(start,[spaces[1][1]],limited))['items']==[]
    checks.append('V254_cross_tenant_denial_and_current_subject_permission_guard')
    actorless_start=await c.fetchval('SELECT clock_timestamp()')
    # The audit table permits system/import writes without an actor. Insert a
    # synthetic audit row as the disposable superuser to prove that a missing
    # actor does not erase an otherwise reliable update count.
    await c.execute("""INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,action_code,object_type,object_id,
        changed_fields,before_snapshot,after_snapshot)
        VALUES($1,NULL,'crm.opportunity.update','opportunity',$2,ARRAY['note'],
        jsonb_build_object('note','before'),jsonb_build_object('note','after'))""",ws,oid)
    anonymous=await read(actorless_start)
    assert len(anonymous['items'])==1 and anonymous['history_coverage']['status']=='complete'
    assert anonymous['items'][0]['events'][0]['actor_status']=='not_recorded'
    checks.append('V254_missing_actor_does_not_erase_reliable_update_count')
    await c.execute('UPDATE ops.review_history_capture SET complete_from=clock_timestamp()')
    partial=await read(start)
    assert partial['history_coverage']['status']!='complete'
    assert partial['history_coverage']['reason_codes']==['before_complete_capture']
    checks.append('V254_capture_start_prevents_historical_full_count_claim')
    return checks
