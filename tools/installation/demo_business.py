"""Reviewed synthetic business rows, separated from account/configuration setup."""
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
import uuid
from zoneinfo import ZoneInfo

from instance_files import InstanceError


def identity(workspace, name, generation=None):
    prefix = 'salesbuddy-demo/sales-cycle-v1/'
    if generation is not None: prefix += 'reset/'+str(uuid.UUID(str(generation)))+'/'
    return uuid.uuid5(uuid.UUID(str(workspace)),prefix+name)


async def seed_business(conn,row,value,as_of,users,teams,form,*,generation=None):
    ws=row['id'];admin=row['administrator_id']
    details={'demo_template':value['id'],'synthetic':True}
    if generation is not None: details['demo_reset_operation']=str(generation)
    marker=json.dumps(details)
    business_id=lambda name:identity(ws,name,generation)
    rule=await conn.fetchval("SELECT id FROM config.rule_set WHERE rule_code='customer_quadrant' AND workspace_id IS NULL AND status='active' ORDER BY version_no DESC LIMIT 1")
    if not rule: raise InstanceError('The standard quadrant rule is missing; no demo data was committed')
    anchor=datetime.combine(as_of,time(12),tzinfo=ZoneInfo(row['default_timezone']))
    for index,customer in enumerate(value['customers']):
        key=customer['key'];cid=business_id('customer:'+key);oid=business_id('opportunity:'+key);vid=business_id('visit:'+key)
        owner=users[customer['owner']];account=next(a for a in value['accounts'] if a['code']==customer['owner']);team=teams[account['team']]
        await conn.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,
            created_by_user_ref_id,level_code,data_kind,demand_summary,attributes)
            VALUES($1,$2,$3,$3,$4,$5,$6,'Tier-2','demo',$7,$8::jsonb)''',cid,ws,customer['name'],owner,team,admin,customer['scenario'],marker)
        await conn.execute('''INSERT INTO crm.customer_ownership(customer_id,workspace_id,owner_user_ref_id,state)
            VALUES($1,$2,$3,'claimed') ON CONFLICT(customer_id) DO UPDATE SET owner_user_ref_id=EXCLUDED.owner_user_ref_id,state='claimed' ''',cid,ws,owner)
        await conn.execute('''INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
            VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING''',ws,cid,owner,admin)
        await conn.execute('''INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,amount,probability,owner_user_ref_id,
            owner_team_id,created_by_user_ref_id,expected_close_year,expected_close_quarter,attributes)
            VALUES($1,$2,$3,$4,$5::numeric,$6,$7,$8,$9,$10,$11,$12::jsonb)''',oid,ws,cid,'合成演示·试点项目 '+str(index+1),
            Decimal(customer['amount']),customer['probability'],owner,team,admin,as_of.year,(as_of.month-1)//3+1,marker)
        await conn.execute('''INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,
            participant_role,source_code,assigned_by_user_ref_id)
            VALUES($1,$2,$3,'fde','manual',$4)''',ws,oid,users['DEMO_FDE'],admin)
        visit_at=anchor-timedelta(days=customer['visit_days_ago'])
        text='【合成演示样例，不是真实客户记录】'+customer['scenario']
        await conn.execute('''INSERT INTO activity.visit(id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
            recorder_team_id,form_version_id,status,interaction_at,follow_up_record,next_action,confirmed_at,
            confirmed_by_user_ref_id,attributes,recording_role_code_snapshot,recording_team_id_snapshot)
            VALUES($1,$2,$3,$4,$5,$6,$7,'confirmed',$8,$9,'确认下一次沟通时间与试点责任人',$8,$5,$10::jsonb,'sales',$6)''',
            vid,ws,cid,oid,owner,team,form,visit_at,text,marker)
        await conn.execute('''INSERT INTO insight.quadrant_score(workspace_id,customer_id,subject_user_ref_id,potential_score,
            relationship_score,quadrant_code,rule_set_id,input_snapshot,calculated_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)''',ws,cid,owner,customer['potential'],customer['relationship'],customer['quadrant'],rule,
            json.dumps({'synthetic':True,'demo_template':value['id'],'source':'固定演示坐标，非实际 AI 推理'}),visit_at)
        task=business_id('task:'+key)
        await conn.execute('''INSERT INTO workflow.task(id,workspace_id,title,description,customer_id,opportunity_id,
            source_visit_id,creator_user_ref_id,creator_team_id,due_at,association_kind,status,attributes,created_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'customer','pending_confirm',$11::jsonb,$12)''',task,ws,
            '合成演示·确认试点事项 '+str(index+1),text,cid,oid,vid,users['DEMO_MANAGER'],teams['east'],anchor+timedelta(days=2),marker,anchor-timedelta(days=1))
        await conn.execute('''INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,assignee_team_id,assignee_role)
            VALUES($1,$2,$3,$4,'sales')''',ws,task,owner,team)
    daily=business_id('task:daily')
    await conn.execute('''INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,
        due_at,association_kind,status,attributes,created_at) VALUES($1,$2,'合成演示·准备内部复盘',
        '合成日常待办，不关联商机。请整理本周沟通要点。',$3,$4,$5,'daily','pending_confirm',$6::jsonb,$7)''',
        daily,ws,users['DEMO_MANAGER'],teams['east'],anchor+timedelta(days=3),marker,anchor-timedelta(days=1))
    await conn.execute("INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,assignee_team_id,assignee_role) VALUES($1,$2,$3,$4,'sales')",ws,daily,users['DEMO_SALES_A'],teams['east'])
    return {'customers':4,'opportunities':4,'confirmed_visits':4,'customer_tasks':4,'daily_tasks':1,
            'map_scores':'fixed synthetic examples; not model inference'}
