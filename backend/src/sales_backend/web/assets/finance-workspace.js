import {api,readForDialog,dialog,dialogIntent,esc,date,table,hasPermission,requirePermission,toast} from './core.js';
import {yuan,operationalPayload,selectSource} from './opportunity-finance.js';
import {actualHistoryBody, savedHistoryDetails} from './actual-history.js';

const names={recognized:'实际确收',collection:'实际回款'};
const statuses={missing:'未登记',single_source:'旧来源待核对',confirmed:'已确认',partial:'部分登记',withdrawn:'已撤回',needs_confirmation:'待核对',stale_confirmation:'参考已变化，待复核'};
const missingNames={material_lines:'交易物料',quantity:'数量',unit_price:'成交单价',usage:'用途',manual_sales_input:'销售人工参考',expected_delivery_on:'预计交付日',expected_start_on:'预计开始日',service_months:'服务月数',expected_oa_on:'预计 OA 日',expected_consumption_amount:'预计消耗金额',expected_consumption_year:'消耗年份',expected_consumption_quarter:'消耗季度','rule_parameters.monthly_allocation':'目录月度分摊配置','rule_parameters.oa_cutoff_day':'目录 OA 截止日','rule_parameters.oa_deferral_policy':'目录 OA 延期配置'};
const ruleNames={manual:'人工填写参考',delivery:'按交付季度',monthly:'按自然月分摊',consumption:'按消耗季度'};
const valueText=value=>value==null?'未填写':typeof value==='object'?JSON.stringify(value):String(value);
const selectedPeriod=(workspace,kind)=>workspace.actual.periods.find(p=>p.year===workspace.period.year&&p.quarter===workspace.period.quarter&&p.kind===kind);
const sourceConflict=p=>Boolean((p.entries?.length||p.history?.length||p.decision)&&p.decision?.source!=='operations');
function historyValue(field,value,displaySnapshot,side) {
  if(field==='intent_product_line_ids'&&Array.isArray(value)) {
    if(!value.length)return '未选择意向产品线';
    const snapshot=displaySnapshot?.version===1&&displaySnapshot.basis==='directory_at_change'&&Array.isArray(displaySnapshot[side])?displaySnapshot[side]:[];
    return value.map(id=>{
      const item=snapshot.find(row=>row.id===id);
      return item?.status==='recorded'&&typeof item.name==='string'
        ? `<p>${esc(item.name)}${item.code?' · '+esc(item.code):''}${item.is_active===false?'（当时已停用）':''}</p><small>产品线 ID：${esc(id)}</small>`
        : `<p>名称未记录</p><small>产品线 ID：${esc(id)}</small>`;
    }).join('');
  }
  if(field==='material_lines'&&Array.isArray(value))return value.length?value.map(row=>
    `<p><strong>${esc(row.catalog_snapshot?.name||'物料（当时未记录名称）')}</strong>${row.is_active===false?' · 已移除':''}；数量 ${esc(row.quantity??'未填')}，成交单价 ${esc(yuan(row.unit_price))}，用途 ${row.usage==='self_use'?'自用':row.usage==='resale'?'转售':'未填'}${row.expected_delivery_on?'；交付 '+esc(row.expected_delivery_on):''}${row.expected_start_on?'；开始 '+esc(row.expected_start_on):''}${row.service_months?'；服务 '+esc(row.service_months)+' 个月':''}</p>`).join('')+
      `<details><summary>查看当时保存的完整输入</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`:'无物料';
  return esc(value !== null && typeof value === 'object' ? '结构化记录（展开完整记录查看）' : valueText(value));
}

export function financeChangesBody(changes) {
  return `<p class="help">${esc(changes.coverage_note||'按实际保存记录展示，不补造历史过程。')}</p>`+(changes.items||[]).map(change=>
    `<article class="import-row"><strong>${esc(change.actor?.name||'操作人未记录')}</strong> · ${esc(date(change.created_at))} · 商机版本 ${esc(change.version_no??'未记录')}`+
    (change.reason?`<p>${esc(change.reason)}</p>`:'')+table(['变化字段','之前','之后'],change.fields||[],field=>
      field.field==='operational_actual' ? `<tr><td colspan="3">${actualHistoryBody(field)}</td></tr>` :
      `<tr><td>${esc(field.label||field.field)}${field.reason?'<small>'+esc(field.reason)+'</small>':''}</td><td>${historyValue(field.field,field.before,field.display_snapshot,'before')}</td><td>${historyValue(field.field,field.after,field.display_snapshot,'after')}</td></tr>`,'该记录没有可展开的字段变化')+savedHistoryDetails(change)+'</article>').join('')+
    (!(changes.items||[]).length?'<p class="help">没有已记录的商机变化。</p>':'')+
    `<div class="pager"><span>第 ${Number(changes.offset||0)+1} 条起</span><button type="button" data-finance-history="prev" ${!changes.offset?'disabled':''}>上一页</button><button type="button" data-finance-history="next" ${!changes.has_more?'disabled':''}>下一页</button></div>`;
}

export function calculationBody(reference={}) {
  return `<p>本季度物料参考含税确收：<strong>${esc(yuan(reference.recognized_inclusive))}</strong>；不含税确收：<strong>${esc(yuan(reference.recognized_exclusive))}</strong></p>`+
    '<p class="help">参考根据保存的交易输入和当时目录规则计算，供核对使用。未齐备时不凑出总额；销售计划与实际均由对应人员明确填写。</p>'+
    (reference.lines||[]).map(line=>{
      const trace=line.calculation_trace||{},snapshot=trace.catalog_snapshot||{},definition=trace.business_definition?.value,allocation=trace.monthly_allocation;
      return `<details class="import-row" ${line.status==='incomplete'?'open':''}><summary>${esc(snapshot.name||'物料')} · ${esc(line.status==='excluded'?'不计入本商机确收':line.status==='complete'?yuan(line.amount_exclusive):'待补充输入')}</summary>`+
        `<p>${esc(ruleNames[snapshot.recognition_rule]||'规则待核实')} · 目录 v${esc(trace.identity?.catalog_version??'未提供')} · 交易行 v${esc(trace.identity?.line_version??'未提供')}</p>`+
        `<p>${esc(definition?.description||'历史未维护业务说明')}</p>`+(definition?.applicability?`<p>适用场景：${esc(definition.applicability)}</p>`:'')+
        (line.missing_fields?.length?`<p class="notice">还缺：${line.missing_fields.map(key=>esc(missingNames[key]||key)).join('、')}</p>`:'')+
        `<p>数量 ${esc(trace.inputs?.quantity??'未填')} × 成交单价 ${esc(yuan(trace.inputs?.unit_price))}；本季含税 ${esc(yuan(line.amount_inclusive))}，不含税 ${esc(yuan(line.amount_exclusive))}。</p>`+
        (snapshot.recognition_rule==='delivery'?`<p>预计交付日：${esc(trace.inputs?.expected_delivery_on||'未填写')}</p>`:'')+
        (snapshot.recognition_rule==='consumption'?`<p>预计消耗：${esc(yuan(trace.inputs?.expected_consumption_amount))}（含税）；消耗期间 ${esc(trace.inputs?.expected_consumption_year??'未填年份')} 年 Q${esc(trace.inputs?.expected_consumption_quarter??'未填季度')}。</p>`:'')+
        (trace.pricing?.explanation?`<p>${esc(trace.pricing.explanation)}</p>`:'')+
        (trace.steps||[]).filter(step=>step.explanation).map(step=>`<p>${esc(step.explanation)}</p>`).join('')+
        (allocation?`<p>${esc(allocation.explanation)}</p><p>有效服务月份：${esc(allocation.effective_start_month)} 至 ${esc(allocation.effective_end_month_exclusive)}（不含结束月）</p>`+
          (allocation.oa?.required?`<p>OA 日期 ${esc(allocation.oa.expected_oa_on)}；本月截止 ${esc(allocation.oa.cutoff_on)}；${allocation.oa.after_cutoff?'超过截止日':'未超过截止日'}。</p>`:'')+
          table(['月份','含税分摊','本季度计入'],allocation.months||[],month=>`<tr><td>${esc(month.month)}</td><td>${esc(yuan(month.allocated_inclusive))}</td><td>${month.included?'计入':month.excluded_by_oa?'OA 延后未计入':'不在本季度'}</td></tr>`):'')+
        (trace.tax?.explanation?`<p>税率 ${esc(trace.tax.rate)}；${esc(trace.tax.explanation)}</p>`:'')+
        (definition?.exceptions?`<p>例外说明：${esc(definition.exceptions)}</p>`:'')+'</details>';
    }).join('')+(!(reference.lines||[]).length?'<p class="help">尚无交易物料；可先保留人工销售预测。</p>':'');
}

export function actualRows(workspace,period) {
  const materials=workspace.materials;
  const confirmations=new Set((period.confirmations||[]).map(row=>row.material_line_id));
  return [...materials.items,...materials.retired_items].filter(row=>
    confirmations.has(row.id)||(period.expected_material_line_ids||[]).includes(row.id)||
      (row.is_active!==false&&(materials.trade_relation!=='distributor'||row.usage==='self_use')));
}

export function actualEditor(workspace,kind) {
  const period=selectedPeriod(workspace,kind),rows=actualRows(workspace,period),old=new Map((period.confirmations||[]).map(row=>[row.material_line_id||'collection',row]));
  const selected=new Set((period.source==='operations'||period.decision?.source==='operations')?(period.expected_material_line_ids||[]):rows.filter(row=>row.is_active).map(row=>row.id));
  const editable=kind==='recognized'?rows:[{id:'collection',catalog_snapshot:{name:'本商机本季度回款'}}];
  return `<h3>登记${names[kind]}${kind==='recognized'?'（不含税）':''}</h3><p class="help">只对选择“登记 / 更正”或“撤回”的项提交；留空不会变成 0，金额为零须明确填写 0。回款只登记一次，不分摊或重复复制到物料。</p>`+
    editable.map(row=>{
      const existing=old.get(row.id);
      return `<section class="import-row"><h4>${esc(row.catalog_snapshot?.name||'物料')}${row.is_active===false?' · 已从预测移除，原实际仍保留':''}</h4>`+
        `<p>${existing?`${existing.status==='withdrawn'?'已撤回（证据保留）':'已确认'}：${esc(yuan(existing.amount))}`:'尚未登记'}</p>`+
        `<div class="form-grid"><label class="field"><b>本次操作</b><select name="action:${esc(row.id)}"><option value="keep">${existing?'保留当前记录':'暂不登记'}</option><option value="confirm">登记 / 更正</option>${existing?.status==='confirmed'&&hasPermission('actual.void')?'<option value="withdraw">撤回本项实际</option>':''}</select></label>`+
        `<label class="field"><b>${kind==='recognized'?'实际不含税确收':'实际回款'}（元）</b><input name="amount:${esc(row.id)}" inputmode="decimal" value="${esc(existing?.status==='confirmed'?existing.amount:'')}" placeholder="未登记；明确为零请填 0"></label>`+
        `<label class="field"><b>核对依据</b><input name="source:${esc(row.id)}" maxlength="160" value="${esc(existing?.status==='confirmed'?existing.source_ref:'')}" placeholder="例如验收单或银行回单编号"></label>`+
        `<label class="field"><b>备注</b><input name="note:${esc(row.id)}" maxlength="1000" value="${esc(existing?.status==='confirmed'?existing.note:'')}"></label></div></section>`;
    }).join('')+
    (kind==='recognized'?`<details><summary>调整本季度需登记的物料${period.empty_confirmation?' · 已确认本期无确收物料':''}</summary><p class="help">通常沿用当前物料清单。只有本季度适用范围发生变化时才调整；取消勾选不会删除旧实际，请先处理相关记录。</p>`+
      rows.map(row=>`<label class="field"><span><input type="checkbox" name="expected_material" value="${esc(row.id)}" ${selected.has(row.id)?'checked':''}>本季度需登记：${esc(row.catalog_snapshot?.name||'物料')}${row.is_active===false?'（预测已移除）':''}</span></label>`).join('')+
      '<label><input type="checkbox" name="replace_expected_materials">我已核实本季度需登记物料的调整</label>'+
      `<label class="field"><b>本季度无确收物料时</b><select name="empty_action"><option value="">保留当前状态</option><option value="confirm">明确确认本季度无确收物料，实际确收为 0</option>${period.empty_confirmation&&hasPermission('actual.void')?'<option value="withdraw">撤回“本期无确收物料”的确认</option>':''}</select></label></details>`:'')+
    (sourceConflict(period)?'<p class="notice">本期已有旧财务记录。保存本次运营登记将改变采用结果，旧证据继续保留；请先在下方查看证据，再明确确认。</p><label><input type="checkbox" name="replace_source">我已核对旧记录，确认采用本次运营登记</label>':'')+
    '<label class="field"><b>本次核对说明</b><textarea name="reason" required maxlength="1000" placeholder="说明凭据、业务事实或更正原因"></textarea></label>';
}

export function financeWorkspaceBody(workspace,kind='recognized') {
  const forecast=workspace.sales_forecast;
  return `<div class="form-grid"><label class="field"><b>年份</b><input name="workspace_year" type="number" min="2000" max="2100" value="${workspace.period.year}"></label><label class="field"><b>季度</b><select name="workspace_quarter">${[1,2,3,4].map(q=>`<option value="${q}" ${q===workspace.period.quarter?'selected':''}>Q${q}</option>`).join('')}</select></label><button type="button" data-workspace-period>查看期间</button></div>`+
    `<p>产品线：${esc(workspace.opportunity.product_line||'未填写')}${workspace.opportunity.product_line_source==='legacy'?'（历史原文，尚未映射）':''}</p>`+
    `<section class="card"><h3>销售当前预测 · ${workspace.period.year} 年 Q${workspace.period.quarter}</h3><p>预测含税确收：<strong>${esc(yuan(forecast.recognized_amount))}</strong>；预测回款：<strong>${esc(yuan(forecast.collection_amount))}</strong></p><p class="help">${forecast.status==='missing'?'本季度尚未保存销售预测。':'销售明确填写的本季度计划。'} 物料参考和运营实际各自保留，不相互覆盖。</p>${calculationBody(workspace.calculation_reference)}</section>`+
    '<details><summary>商机实际变化过程</summary><p class="notice" data-finance-history-status aria-live="polite"></p><div data-finance-changes>'+financeChangesBody(workspace.changes)+'</div></details>'+
    table(['本季度实际','金额','状态','已登记小计'],['recognized','collection'].map(k=>selectedPeriod(workspace,k)),p=>`<tr><td>${esc(names[p.kind])}</td><td>${esc(yuan(p.amount))}</td><td>${esc(statuses[p.status]||p.status)}${p.tax_unknown?' · 税口径待核实':''}</td><td>${esc(yuan(p.confirmed_subtotal))}${p.missing_material_count?` · 待补 ${p.missing_material_count} 项`:''}</td></tr>`)+
    (workspace.actual.can_confirm?`<div class="actions"><button type="button" data-workspace-kind="recognized" ${kind==='recognized'?'disabled':''}>登记实际确收</button><button type="button" data-workspace-kind="collection" ${kind==='collection'?'disabled':''}>登记商机回款</button></div>${actualEditor(workspace,kind)}`:'<p class="help">当前账号仅可查看，无实际登记权限。</p>')+
    '<details><summary>旧财务证据与异常核对</summary><p class="help">只有需要改变旧来源的采用结果时才在此核对；普通登记使用上方表单。</p>'+
    ['recognized','collection'].map(k=>{
      const p=selectedPeriod(workspace,k);
      return `<h4>${esc(names[k])}</h4>`+table(['逐笔发生日','金额','凭据','状态'],p.entries||[],entry=>`<tr><td>${esc(entry.occurred_on)}</td><td>${esc(yuan(entry.amount))}</td><td>${esc(entry.source_ref||'未记录')}</td><td>${entry.voided_at?'已作废':'有效'}</td></tr>`,'无逐笔记录')+
        table(['历史字段','原值','折合元','税口径'],p.history||[],entry=>`<tr><td>${esc(entry.source_field)}</td><td>${esc(entry.raw_amount)} ${entry.source_unit==='wan_cny'?'万元':'元'}</td><td>${esc(yuan(entry.amount_yuan))}</td><td>${esc(entry.tax_basis)}</td></tr>`,'无历史汇总')+
        (workspace.actual.can_confirm&&(p.entry_amount!=null||p.history?.length)?`<button type="button" data-workspace-source="${k}">核对旧${names[k]}来源</button>`:'');
    }).join('')+'</details>';
}

function workspacePath(id,year,quarter,offset=0,revision=null) {
  const query=new URLSearchParams({year,quarter,history_limit:10,history_offset:offset});
  if(revision)query.set('expected_history_revision',revision);
  return `/opportunities/${encodeURIComponent(id)}/finance-workspace?`+query;
}

export function renderFinanceWorkspace(workspace,kind='recognized') {
  const id=workspace.opportunity.id;
  return dialog('财务核对 · '+workspace.opportunity.name,financeWorkspaceBody(workspace,kind),{wide:true,submit:'确认本次'+names[kind],footer:'<button type="button" data-copy-finance>复制核对链接</button>',
    onSubmit:workspace.actual.can_confirm?async(form,key)=>{
      requirePermission('actual.create');
      const current=dialogIntent({liveForm:true});
      const period=selectedPeriod(workspace,kind),rows=actualRows(workspace,period);
      const payload=operationalPayload(form,workspace.actual,period,rows);
      if(sourceConflict(period)&&!payload.replace_source)throw Error('请先查看旧财务证据，并勾选确认采用本次运营登记。');
      try{await api(`/opportunities/${encodeURIComponent(id)}/actual-confirmations`,{method:'POST',key,body:payload});}
      catch(error){throw Error(error.message+' 本页输入已保留。'+(/已变化|版本|刷新|重新确认/.test(error.message)?'请先复制未保存输入，再关闭并重新打开财务核对，核对最新资料后重新确认。':'请核对后再提交。'));}
      if(!current())return;
      let updated;
      try{updated=await readForDialog(workspacePath(id,workspace.period.year,workspace.period.quarter));}
      catch(error){throw Error(names[kind]+'已保存，但刷新失败：'+error.message+'。重新打开本商机可查看已保存结果。');}
      if(updated)renderFinanceWorkspace(updated,kind);
      toast(names[kind]+'已保存');
    }:null,
    afterRender(d){
      let dirty=false,history=workspace.changes;
      d.querySelector('[data-copy-finance]').onclick=async()=>{
        try{await navigator.clipboard.writeText(new URL(workspace.actual.review_path,location.origin).href);toast('核对链接已复制');}
        catch{toast('浏览器暂不支持复制，请从商机入口进入财务核对');}
      };
      d.querySelector('#dialog-form')?.addEventListener('input',event=>{if(!event.target.name?.startsWith('workspace_'))dirty=true;});
      d.querySelector('#dialog-form')?.addEventListener('change',event=>{if(!event.target.name?.startsWith('workspace_'))dirty=true;});
      const mayLeave=()=>!dirty||window.confirm('本页实际登记尚未保存。继续切换会放弃输入，是否继续？');
      d.querySelector('[data-workspace-period]').onclick=async()=>{
        if(!mayLeave())return;
        const year=Number(d.querySelector('[name=workspace_year]').value),quarter=Number(d.querySelector('[name=workspace_quarter]').value);
        if(!Number.isInteger(year)||year<2000||year>2100||![1,2,3,4].includes(quarter)){toast('请选择有效年份和季度');return;}
        try{await showFinanceWorkspace(id,year,quarter,kind);}catch(error){toast(error.message);}
      };
      d.querySelectorAll('[data-workspace-kind]').forEach(button=>button.onclick=()=>{if(mayLeave())renderFinanceWorkspace(workspace,button.dataset.workspaceKind);});
      d.querySelectorAll('[data-workspace-source]').forEach(button=>button.onclick=()=>{
        if(mayLeave())selectSource(workspace.actual,selectedPeriod(workspace,button.dataset.workspaceSource),()=>showFinanceWorkspace(id,workspace.period.year,workspace.period.quarter,kind));
      });
      const bindHistory=()=>d.querySelectorAll('[data-finance-history]').forEach(button=>button.onclick=async()=>{
        const current=dialogIntent({liveForm:true});
        const offset=button.dataset.financeHistory==='next'?history.next_offset:Math.max(0,history.offset-history.limit);
        button.disabled=true;
        try{
          if(!/^[0-9a-f]{64}$/.test(workspace.history_revision||''))throw Error('缺少首屏变化版本，请重新打开财务核对。');
          const updated=await api(workspacePath(id,workspace.period.year,workspace.period.quarter,offset,workspace.history_revision));
          if(!current())return;
          if(updated.history_revision!==workspace.history_revision||updated.opportunity?.version_no!==workspace.opportunity.version_no)
            throw Error('商机或变化记录已更新，请重新打开财务核对后再翻页。');
          history=updated.changes;d.querySelector('[data-finance-changes]').innerHTML=financeChangesBody(history);bindHistory();
          d.querySelector('[data-finance-history-status]').textContent='';
        }catch(error){if(current()){
          const message=error.message+' 当前变化页及未提交的实际输入已保留；重新打开前请先复制未保存内容。';
          d.querySelector('[data-finance-history-status]').textContent=message;toast(message);button.disabled=false;
        }}
      });
      bindHistory();
    }});
}

export async function showFinanceWorkspace(id,year,quarter,kind='recognized') {
  requirePermission('actual.read');
  const today=new Date();year??=today.getFullYear();quarter??=Math.floor(today.getMonth()/3)+1;
  const workspace=await readForDialog(workspacePath(id,year,quarter));
  if(workspace)renderFinanceWorkspace(workspace,kind);
}
