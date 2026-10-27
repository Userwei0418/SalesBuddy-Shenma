/* Presentation of existing event receipts. No health evaluation or backend writes. */
(function(global){
  'use strict';
  const groups=[['all','全部'],['attention','需关注'],['customer','客户'],['opportunity','商机'],['task','任务'],['visit','拜访与跟进'],['other','其他']];
  const labels=Object.fromEntries(groups),icons={customer:'客',opportunity:'商',task:'任',visit:'访',other:'动'};
  const tones={red:'red',danger:'red',yellow:'yellow',amber:'yellow',orange:'yellow',green:'green',success:'green'};
  const lights={red:'红灯',yellow:'黄灯',green:'绿灯',gray:'灰灯'};
  const plain=value=>value==null?'':String(value);
  function category(message){
    const c=message.card||{},a=c.action||{},eyebrow=plain(c.eyebrow);
    // Visit receipts may navigate to their customer; classify by receipt, not destination.
    if(a.visitId||/VISIT|拜访|跟进记录/i.test(eyebrow)||/visit|archived_customer/.test(a.code||''))return 'visit';
    if(a.taskId||/TASK|任务|岗位待办/i.test(eyebrow))return 'task';
    if(a.opportunityId)return 'opportunity';
    if(a.customerId||/customer_claim/.test(a.code||'')||/客户认领|客户建档|NEW CUSTOMER/i.test(eyebrow))return 'customer';
    return 'other';
  }
  function timestamp(message){
    const time=plain(message.time),stamp=Number(message.sortAt);
    if(Number.isFinite(stamp)&&stamp>0){
      const date=new Date(stamp+8*3600000),day=`${date.getUTCFullYear()}-${date.getUTCMonth()+1}-${date.getUTCDate()}`;
      return {day,label:`${date.getUTCFullYear()}年${date.getUTCMonth()+1}月${date.getUTCDate()}日`,clock:time.match(/\d{1,2}:\d{2}$/)?.[0]||time,full:time};
    }
    const parts=time.match(/^(.*?\d+月\d+日)\s+(\d{1,2}:\d{2})$/);
    return {day:parts?.[1]||'',label:parts?.[1]||'',clock:parts?.[2]||time,full:time};
  }
  function describe(message){
    const c=message.card||{},a=c.action||{},kind=category(message),metrics=Array.isArray(c.metrics)?c.metrics:[];
    const claim=c.eyebrow==='客户认领',task=kind==='task'&&(c.rows||[]).find(row=>row.taskId&&row.taskId===a.taskId)||null;
    // Card subtitles contain the original business object/context; never infer names.
    // Task rows sometimes contain a whole instruction rather than a short task name.
    const longTask=kind==='task'&&plain(task?.title).length>48;
    const subject=claim||kind==='opportunity'||kind==='visit'?c.subtitle:longTask?c.title:task?.title;
    const title=plain(subject||c.title||message.text||'业务动态');
    const preview=longTask?plain(task.title):claim?'客户认领':subject&&title!==c.title?plain(c.title):plain(c.subtitle);
    const metric=metrics.find(m=>['认领结果','任务状态','风险状态','客户状态'].includes(m.label));
    const health=!!c.statusLabel,tone=tones[c.tone]||'gray';
    const status=plain(c.statusLabel||metric?.value);
    const reason=(c.rows||[]).find(row=>['变化判断','商机变化评估'].includes(row.title))?.meta||'';
    const attention=health&&['red','yellow'].includes(tone);
    const change=health?(c.rows||[]).filter(row=>!['变化判断','商机变化评估','客户关系 · AI评估建议'].includes(row.title)).slice(0,2).map(row=>[row.title,row.meta].filter(Boolean).join('：')).join('；'):'';
    return {kind,label:labels[kind],icon:icons[kind],title,preview:preview===title?'':preview,tone,status,health,attention,change,
      completed:kind==='task'&&status==='已完成',
      statusHint:health?`${lights[tone]} · ${status}${reason?'：'+reason:''}`:metric?`${metric.label}：${status}`:'',
      metrics:metrics.filter(m=>m!==metric&&m.label!=='数据范围').slice(0,2),time:timestamp(message)};
  }
  function searchText(message){
    const c=message.card||{};
    return [message.text,c.title,c.subtitle,c.statusLabel,c.footer,...(c.metrics||[]).flatMap(m=>[m.label,m.value]),...(c.rows||[]).flatMap(r=>[r.title,r.meta,r.tag]),...(c.sections||[]).flatMap(s=>[s.title,s.subtitle,...(s.rows||[]).flatMap(r=>[r.title,r.meta,r.tag])])].map(plain).join(' ').toLocaleLowerCase();
  }
  function presentation(data,filter='all',query=''){
    const messages=(data.messages||[]).filter(m=>m.kind!=='greeting'),counts={all:messages.length,attention:0};
    const described=messages.map(message=>{const webActivity=describe(message);counts[webActivity.kind]=(counts[webActivity.kind]||0)+1;if(webActivity.attention)counts.attention++;return {...message,webActivity};});
    const needle=plain(query).trim().toLocaleLowerCase();let previous='';
    const visible=described.filter(m=>(filter==='all'||filter==='attention'&&m.webActivity.attention||m.webActivity.kind===filter)&&(!needle||searchText(m).includes(needle))).map(m=>{
      const day=m.webActivity.time.day,webActivityDay=day&&day!==previous?m.webActivity.time.label:'';previous=day;
      return {...m,webActivityDay};
    });
    return {webHomeMessages:visible,webActivityFilter:filter,webActivityQuery:query,webActivityTotal:messages.length,webActivityCount:visible.length,
      webActivityFiltered:filter!=='all'||!!needle,
      webActivityGroups:groups.filter(([key])=>key!=='other'||counts.other||filter==='other').map(([key,label])=>({key,label,count:counts[key]||0,selected:key===filter,hint:key==='attention'?'当前已加载动态中的红灯与黄灯业务变化，不代表待办数量':''}))};
  }
  function configure(page){
    if(page._webActivityConfigured)return;page._webActivityConfigured=true;
    page._webActivityFilter='all';page._webActivityQuery='';
    page.webFilterActivity=event=>{const key=event.currentTarget.dataset.key;if(!labels[key])return;page._webActivityFilter=key;page.setData({});};
    page.webSearchActivity=event=>{page._webActivityQuery=plain(event.detail.value);page.setData({});};
    page.webClearActivity=()=>{page._webActivityFilter='all';page._webActivityQuery='';page.setData({});};
  }
  global.SalesHomeActivity=Object.freeze({presentation,configure,describe});
})(globalThis);
