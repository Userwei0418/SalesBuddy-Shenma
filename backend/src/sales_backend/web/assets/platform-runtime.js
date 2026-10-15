// Read-only operational counters; never request company business detail APIs.
export function installRuntimeOverview({api,node}){
  const $=s=>document.querySelector(s),dialog=$('#runtime-dialog'),body=$('#runtime-body');
  let generation=0,cursor=null,currentCursor=null,windowMinutes=15;
  const names={active:'启用',provisioning:'准备中',suspended:'暂停',closed:'关闭'};
  function clear(){generation++;cursor=null;currentCursor=null;body.replaceChildren();$('#runtime-sampled').textContent='';$('#runtime-next').hidden=true;}
  function close(){clear();dialog.close();}
  dialog.addEventListener('close',clear);dialog.addEventListener('cancel',clear);
  $('#close-runtime').onclick=close;
  document.addEventListener('visibilitychange',()=>{if(document.hidden)close();});
  function metric(card,label,text){const p=node('p');p.append(node('strong',label+'：'),node('span',text));card.append(p);}
  function render(row){
    const card=node('section','','runtime-tenant'),head=node('div','','tenant-head');
    head.append(node('h3',row.name),node('span',names[row.status]||'状态未知','badge '+row.status));
    card.append(head,node('small','公司码：'+row.company_code));
    const j=row.jobs,s=row.sync,o=row.originals,c=row.calls,issues=[];
    if(j.dead_letter)issues.push('后台任务需人工处理 '+j.dead_letter+' 项');
    if(j.expired_leases)issues.push('后台任务租约过期 '+j.expired_leases+' 项');
    if(j.overdue)issues.push('可执行任务等待超过阈值 '+j.overdue+' 项');
    if(s.dead_letter)issues.push('飞书同步需人工处理 '+s.dead_letter+' 项');
    if(s.expired_leases)issues.push('飞书任务租约过期 '+s.expired_leases+' 项');
    if(s.overdue)issues.push('飞书同步等待超过阈值 '+s.overdue+' 项');
    if(s.unknown_deliveries)issues.push('飞书发送结果待核实 '+s.unknown_deliveries+' 项');
    if(!o.available)issues.push('原件统计未就绪，请核对公司初始化');
    if(o.over_limit)issues.push('原件用量超过当前上限');
    if(o.overdue)issues.push('上传预占超过阈值 '+o.overdue+' 项');
    if(c.unknown)issues.push('模型调用结果待对账 '+c.unknown+' 次');
    if(c.overdue)issues.push('模型请求未结算超过阈值 '+c.overdue+' 次');
    if(c.failed_last_24h)issues.push('近 24 小时模型请求失败 '+c.failed_last_24h+' 次（可能已重试成功）');
    const state=node('p',issues.length?'有待核对事项':'本页已检查的指标暂未发现异常',issues.length?'runtime-attention':'runtime-clear');card.append(state);
    if(issues.length){const list=node('ul');for(const issue of issues)list.append(node('li',issue));card.append(list);}
    metric(card,'后台任务','执行中 '+j.running+' · 等待/重试 '+j.waiting);
    metric(card,'飞书同步',(s.enabled?'已启用':'未启用')+' · 等待/重试 '+s.waiting);
    const mib=value=>(value/1048576).toLocaleString('zh-CN',{maximumFractionDigits:2})+' MiB';
    metric(card,'原件',o.available?'已保存 '+mib(o.stored_bytes)+' · 预占 '+mib(o.reserved_bytes)+' · '+(o.limit_bytes===null?'未设上限':'上限 '+mib(o.limit_bytes)):'统计未就绪');
    metric(card,'模型与语音','待完成请求 '+c.pending+' 次 · 结果未知 '+c.unknown+' 次');
    if(row.status!=='active')card.append(node('small','当前公司未启用，等待中的后台任务与同步不会被当作可执行积压；历史异常仍保留。'));
    return card;
  }
  async function load(after=null){
    const ticket=++generation;
    if(after===null){windowMinutes=Number($('#runtime-wait').value);currentCursor=null;}
    else currentCursor=after;
    cursor=null;$('#runtime-next').hidden=true;$('#runtime-sampled').textContent='';body.replaceChildren();
    if(!Number.isInteger(windowMinutes)||windowMinutes<1||windowMinutes>1440){body.append(node('p','等待阈值请输入 1–1440 的整数分钟。'));return;}
    body.append(node('p','正在读取运行快照…'));
    try{
      const data=await api('/runtime?wait_minutes='+windowMinutes+(after?'&after_id='+encodeURIComponent(after):''));
      if(ticket!==generation||!dialog.open)return;
      body.replaceChildren(...data.items.map(render));
      if(!data.items.length)body.append(node('p','当前页没有公司。'));
      $('#runtime-sampled').textContent='采样时间：'+new Date(data.sampled_at).toLocaleString()+' · 等待阈值 '+data.wait_minutes+' 分钟 · 本页 '+data.items.length+' 家';
      cursor=data.next_cursor;$('#runtime-next').hidden=!cursor;
    }catch(e){if(ticket===generation&&dialog.open)body.replaceChildren(node('p',e.message));}
  }
  $('#open-runtime').onclick=()=>{clear();$('#runtime-wait').value=15;dialog.showModal();return load();};
  $('#runtime-refresh').onclick=()=>load(currentCursor);
  $('#runtime-first').onclick=()=>load();
  $('#runtime-wait').onchange=()=>load();
  $('#runtime-next').onclick=()=>{if(cursor)return load(cursor);};
  return {close};
}
