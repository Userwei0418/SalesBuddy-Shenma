import {api, dialog, esc, details, state, date} from './core.js';

const labels = {passed:'通过',failed:'未通过',not_tested:'未执行',running:'测试中',unavailable:'暂不可测试',unknown:'未取得完成回执'};
export function connectivityResult(result) {
  const rows = [['测试对象',result.label],['连接与鉴权',labels[result.connection_status] || '未执行'],
    ['固定样本返回',labels[result.inference_status] || '未执行'],['耗时',`${((result.elapsed_ms || 0)/1000).toFixed(1)} 秒`],
    ['测试时间',date(result.started_at)],['业务验收','本次不执行'],['测试编号',result.id]];
  if(result.kind==='direct') rows.push(['服务商',result.provider || '未设置'],['模型',result.model || '未设置'],
    ['配置版本',result.version ? `第 ${result.version} 版` : '现有基线']);
  if(result.agent_id) rows.push(['智能体编号',result.agent_id],['预期发布快照',result.expected_snapshot_id || '未记录'],
    ['实际执行快照',result.runtime_snapshot_verified ? result.actual_snapshot_id : '中台未提供可核验回执']);
  if(result.execution_process) rows.push(['执行位置',result.execution_process==='worker'?'智能体后台任务进程':'接口服务进程']);
  if(result.http_status) rows.push(['接口状态码',result.http_status]);
  if(result.ids?.task_id) rows.push(['中台任务编号',result.ids.task_id]);
  const tone=result.status==='passed'?'green':result.status==='failed'?'red':'amber';
  return `<p class="badge ${tone}">${esc(labels[result.status] || '未完成')}</p><p>${esc(result.message)}</p>${details(rows)}`;
}

export function bindConnectivityTests(root) {
  root.querySelectorAll('[data-connectivity-kind]').forEach(button=>button.onclick=()=>{
    const company=state.company?.id, kind=button.dataset.connectivityKind, target=button.dataset.connectivityTarget;
    let requestId=crypto.randomUUID(), busy=false, settled=false;
    const d=dialog(`${button.dataset.connectivityLabel} · 测试连通性`,
      '<p class="notice">使用当前生效配置和固定样本，可能产生少量调用费用。不会更改配置、写入客户业务或使用兜底接口。中台连通与业务验收分别记录。</p><div data-connectivity-result role="status">正在测试，请稍候…</div>',
      {wide:true,footer:'<button type="button" data-connectivity-retry disabled>重新测试</button>'});
    const output=d.querySelector('[data-connectivity-result]'), retry=d.querySelector('[data-connectivity-retry]');
    const current=()=>d.open && d.contains(output) && state.company?.id===company;
    async function run() {
      if(busy || !current())return;
      busy=true;retry.disabled=true;output.textContent='正在测试，请稍候…';
      try {
        if(settled)requestId=crypto.randomUUID();
        let result=await api(`/api/v1/admin/ai-connectivity/${kind}/${target}/tests`,{method:'POST',body:{request_id:requestId}});
        for(let attempt=0;result.status==='running' && attempt<30 && current();attempt++){
          output.textContent=result.message;
          await new Promise(resolve=>setTimeout(resolve,2000));
          if(!current())return;
          result=await api(`/api/v1/admin/ai-connectivity/tests/${requestId}`);
        }
        if(!current())return;
        settled=result.status!=='running';
        output.innerHTML=connectivityResult(result);
        retry.textContent=settled?'重新测试':'查询本次结果';
      } catch(e) {
        if(current()){output.textContent=e.message;retry.textContent='查询本次结果';}
      } finally {busy=false;if(current())retry.disabled=false;}
    }
    retry.onclick=run;
    run();
  });
}
