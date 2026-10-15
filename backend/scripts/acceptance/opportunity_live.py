"""Authorized fictitious demo records through real HTTP and SenseAudio, no credentials in output."""
import argparse
import asyncio
import json
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import httpx

async def main(base_url):
 async with httpx.AsyncClient(base_url=base_url,timeout=120) as client:
  async def req(method,path,expected=200,**kw):
   r=await client.request(method,path,**kw)
   assert r.status_code==expected,(path,r.status_code,r.text[:350])
   return r.json()
  auth=await req('POST','/auth/session',json={'account_code':'XS001'})
  client.headers['Authorization']='Bearer '+auth['access_token']; actor=auth['actor']
  today=datetime.now(timezone(timedelta(hours=8))).date(); nxt=(today+timedelta(days=3)).isoformat(); tag=uuid4().hex[:6]
  c=await req('POST','/customers',201,json=dict(name=f'【演示】商机闭环-{tag}',industry='软件服务',customer_type='潜在客户',source='销售线索',target_team=actor['team_names'][0],partner_name='',contact_name='林悦',contact_title='客服经理',contact_role='决策者'))
  cid=c['id']; print(json.dumps({'phase':'created_demo','customer_id':cid,'customer_name':c['name']},ensure_ascii=False),flush=True); data=dict(name='客服知识助手一期',probability=30,status='open',amount=500000,expected_close_date=nxt,partner_name='【演示】云启伙伴',product_line='知识助手',quarterly_forecasts=[dict(year=2026,quarter=3,recognized_amount=0,collection_amount=None),dict(year=2026,quarter=4,recognized_amount=300000,collection_amount=200000)])
  o=await req('POST',f'/customers/{cid}/opportunities',201,json=data)
  update={**data,'action':'update','opportunity_id':o['id'],'version_no':o['version_no']}
  same=await req('POST',f'/customers/{cid}/opportunities',201,json=update);assert not same['changed']
  o=await req('POST',f'/customers/{cid}/opportunities',201,json={**update,'probability':50})
  notes=(await req('GET','/notifications'))['items']; note=next(n for n in notes if n['payload'].get('event_id')==o['event_id']);assert note['payload']['tone']=='green'
  # A manager edits the sales owner's opportunity. Both can see their receipt; another salesperson cannot.
  manager=await req('POST','/auth/session',json={'account_code':'ZJL001'})
  client.headers['Authorization']='Bearer '+manager['access_token']
  changed=await req('POST',f'/customers/{cid}/opportunities',201,json={**update,'version_no':o['version_no'],'probability':70})
  client.headers['Authorization']='Bearer '+auth['access_token']
  assert any(n['payload'].get('event_id')==changed['event_id'] for n in (await req('GET','/notifications'))['items'])
  other=await req('POST','/auth/session',json={'account_code':'XS002'});client.headers['Authorization']='Bearer '+other['access_token']
  assert not any(n['payload'].get('customer_id')==cid for n in (await req('GET','/notifications'))['items'])
  client.headers['Authorization']='Bearer '+auth['access_token']
  data2={**data,'name':'仓配工单助手'}
  conv=await req('POST','/conversations',201,json={'mode':'visit_entry'})
  text=f'{today}，刘志德拜访{c["name"]}客服经理林悦，目标是确认仓配异常工单分类试点。林悦介绍每日60条异常工单需要两人核对，现有规则分散在三份文档。现场演示5条样本，其中4条分类正确，1条缺少退货规则。双方约定先做小范围试点，尚未签约采购，预算约50万元。刘志德于{nxt}17:00前提交试点计划及验收清单，林悦于{nxt}12:00前提供20条脱敏工单，双方于{nxt}16:00召开样本复核会议。'
  accepted=await req('POST',f'/conversations/{conv["id"]}/messages',202,json={'text':text,'client_message_id':str(uuid4())})
  for _ in range(200):
   run=await req('GET',f'/agent/runs/{accepted["run_id"]}')
   if run['status'] in ('waiting_human','succeeded'):break
   assert run['status'] not in ('failed','cancelled'),run.get('error_detail')
   await asyncio.sleep(1)
  else:raise AssertionError('real model timeout')
  result=run['result'];score=result['quality_review']['follow_up_score'];assert score>=70,result['quality_review']
  fields={**result['fields'],'interaction_at':today.isoformat(),'opportunity_id':None,'_quality_review_run_id':run['id'],'_opportunity_mutation':data2}
  # Valid AI review + invalid collaborator fails after the opportunity write: transaction must roll back both.
  await req('POST','/visits',422,json={'customer_id':cid,'fields':{**fields,'collaborator_ids':[str(uuid4())]}})
  before=await req('GET',f'/customers/{cid}');assert not any(x['name']==data2['name'] for x in before['opportunities'])
  visit=await req('POST','/visits',201,json={'customer_id':cid,'fields':fields})
  replay=await req('POST','/visits',201,json={'customer_id':cid,'fields':fields});assert replay['id']==visit['id'] and replay['replayed']
  detail=await req('GET',f'/customers/{cid}');linked=next(x for x in detail['opportunities'] if x['id']==visit['opportunity_id']);assert linked['name']==data2['name']
  assert len([x for x in detail['opportunities'] if x['name']==data2['name']])==1
  await req('POST',f'/customers/{cid}/opportunities',409,json=data2)
  forecasts=next(x for x in (await req('GET','/workbench'))['quarter_forecasts'] if x['opportunity_id']==linked['id'] and x['quarter']==3)
  assert float(forecasts['recognized_amount'])==0 and forecasts['collection_amount'] is None
  win={**update,'version_no':changed['version_no'],'status':'won','probability':100}
  await req('POST',f'/customers/{cid}/opportunities',422,json=win)
  won=await req('POST',f'/customers/{cid}/opportunities',201,json={**win,'closure_confirmed':True})
  closed=(await req('GET','/opportunities',params={'customer_id':cid,'include_closed':'true'}))['items'];assert any(x['id']==won['id'] and x['status']=='won' for x in closed)
  active=(await req('GET','/opportunities',params={'customer_id':cid}))['items'];assert all(x['id']!=won['id'] for x in active)
  lost=await req('POST',f'/customers/{cid}/opportunities',201,json={**data,'name':'历史丢单示例','status':'lost','probability':None,'closure_confirmed':True})
  assert lost['probability'] is None
  home=await req('GET','/assistant/home');assert visit['id'] in json.dumps(home)
  for _ in range(120):
   notes=(await req('GET','/notifications'))['items'];reviewed=next((n for n in notes if n['payload'].get('event_id')==changed['event_id'] and n['payload'].get('ai_review')),None)
   if reviewed:break
   await asyncio.sleep(1)
  assert reviewed,'Real review enrichment missing'
  print(json.dumps({'customer_id':cid,'customer_name':c['name'],'opportunity_id':linked['id'],'won_id':won['id'],'lost_id':lost['id'],'visit_id':visit['id'],'run_id':run['id'],'score':score,'checks':['real-AI','human-create','no-op','green-card','manager-to-owner','unrelated-recipient-isolation','atomic-rollback','archive-create','replay-once','duplicate-409','null-vs-zero','quarter-readback','close-confirm','won-retained','lost-retained','home-receipt','real-review-enrichment']},ensure_ascii=False),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--base-url',default='https://www.ericepc.com/api/v1');asyncio.run(main(p.parse_args().base_url))
