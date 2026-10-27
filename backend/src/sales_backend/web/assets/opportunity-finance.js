import {api, readForDialog, dialog, esc, date, table, hasPermission, requirePermission, toast} from './core.js';
import {showFinanceWorkspace} from './finance-workspace.js';

const kinds = {recognized:'实际确收',collection:'实际回款'};
const states = {missing:'未登记',single_source:'唯一旧来源',confirmed:'已人工确认',partial:'部分登记',withdrawn:'已撤回',needs_confirmation:'待核对',stale_confirmation:'旧来源变化，需重新核对'};
export function yuan(value) {
  if(value==null)return '未登记 / 待核对';
  const text=String(value);
  if(!/^\d+(?:\.\d+)?$/.test(text))return '金额待核实';
  const [whole, fraction='']=text.split('.');
  // Display cents without losing precision for large Decimal strings.
  let cents=BigInt(whole)*100n+BigInt(fraction.slice(0,2).padEnd(2,'0'));
  if(Number(fraction[2]||0)>=5)cents+=1n;
  const digits=String(cents).padStart(3,'0'), tail=digits.slice(-2).replace(/0+$/,'');
  return digits.slice(0,-2).replace(/\B(?=(\d{3})+(?!\d))/g,',')+(tail?'.'+tail:'')+' 元';
}

export function financeBody(result) {
  return `<p class="notice">${esc(result.coverage_note)}</p><p><strong>${esc(result.quality)}</strong> · 待核对 ${result.conflict_period_count} 个期间</p>` +
    table(['指标','累计金额','可计入小计'], Object.entries(result.totals), ([kind,total]) =>
      `<tr><td>${esc(kinds[kind])}</td><td>${esc(yuan(total.amount))}</td><td>${esc(yuan(total.confirmed_subtotal))}</td></tr>`) +
    '<p class="help">正式总额须构成完整且口径明确；部分登记只展示右列小计。确收为不含税金额，回款独立登记；季度记录不代表具体到账日。</p>' +
    result.periods.map((p,index) => `<section class="import-row"><h3>${p.year} 年 Q${p.quarter} · ${esc(kinds[p.kind])}</h3><p>${esc(states[p.status])} · ${esc(yuan(p.amount))}${p.tax_unknown?' · 税口径待核实':''}</p>` +
      (p.confirmed_subtotal!=null&&!p.complete?`<p>已登记小计：${esc(yuan(p.confirmed_subtotal))} · 尚缺 ${Number(p.missing_material_count||0)} 项</p>`:'') +
      (p.empty_confirmation?'<p>运营已明确确认本期无应登记自用物料，正式确收为 0。</p>':'') +
      (p.reference_changed?'<p class="notice">原始参考已变化，当前采用金额保留，请运营复核。</p>':'') +
      table(['物料/回款','确认金额','状态','核对依据','版本'],p.confirmations||[],r=>`<tr><td>${esc(r.material_snapshot?.material_line?.catalog_snapshot?.name||(r.material_line_id?'物料行':'商机回款'))}</td><td>${esc(yuan(r.amount))}${r.status==='withdrawn'?'（撤回证据，不计入）':''}</td><td>${r.status==='withdrawn'?'已撤回':'已确认'}</td><td>${esc(r.source_ref)}</td><td>${Number(r.version_no)}</td></tr>`,'尚无逐项运营记录') +
      (result.can_confirm?`<button type="button" data-operational-period="${index}">登记 / 更正本期实际</button>`:'') +
      `<details><summary>查看原始财务证据（逐笔 ${p.entries.length} 条 / 历史汇总 ${p.history.length} 条）</summary>` +
      table(['逐笔发生日','金额','凭据','状态'],p.entries,r=>`<tr><td>${esc(r.occurred_on)}</td><td>${esc(yuan(r.amount))}</td><td>${esc(r.source_ref||'—')}</td><td>${r.voided_at?'已作废 · '+esc(r.void_reason):'有效'}</td></tr>`,'无逐笔记录') +
      table(['历史原始字段','原值/单位','折合元','税口径'],p.history,r=>`<tr><td>${esc(r.source_field)}<small>${esc(r.source_record_id)}</small></td><td>${esc(r.raw_amount)} ${r.source_unit==='wan_cny'?'万元':'元'}</td><td>${esc(yuan(r.amount_yuan))}</td><td>${esc(r.tax_basis)}</td></tr>`,'无历史汇总') + '</details>' +
      (p.decision?`<p class="help">上次确认：${esc(p.decision.decided_by_name||'管理员/运营')} · ${date(p.decision.updated_at)} · ${esc(p.decision.reason)}</p>`:'') +
      (result.can_confirm && (p.entry_amount!==null||p.history.length) ? `<button type="button" data-finance-period="${index}">${p.decision?'重新核对来源':'核对来源'}</button>`:'') + '</section>').join('');
}

function renderFinance(result) {
  return dialog('财务核对 · '+result.name, financeBody(result), {wide:true,
    footer:'<button type="button" data-copy-finance>复制核对链接</button>'+(result.can_confirm?'<button type="button" data-new-finance-period>登记其他期间</button>':''),
    afterRender(d) {
      d.querySelector('[data-copy-finance]').onclick=async()=>{
        try { await navigator.clipboard.writeText(new URL(result.review_path,location.origin).href);toast('核对链接已复制'); }
        catch { toast('浏览器暂不支持复制，请复制地址栏或从飞书商机入口进入'); }
      };
      d.querySelectorAll('[data-finance-period]').forEach(button=>button.onclick=()=>selectSource(result,result.periods[Number(button.dataset.financePeriod)]));
      d.querySelectorAll('[data-operational-period]').forEach(button=>button.onclick=()=>editActualPeriod(result,result.periods[Number(button.dataset.operationalPeriod)]).catch(error=>toast(error.message)));
      const add=d.querySelector('[data-new-finance-period]');
      if(add)add.onclick=()=>dialog('选择实际登记期间','<label class="field"><b>年份</b><input name="year" type="number" min="2000" max="2100" required></label><label class="field"><b>季度</b><select name="quarter"><option value="1">Q1</option><option value="2">Q2</option><option value="3">Q3</option><option value="4">Q4</option></select></label>',{
        submit:'打开期间',onSubmit:async form=>{const updated=await api(`/opportunities/${encodeURIComponent(result.opportunity_id)}/financial-overview?year=${encodeURIComponent(form.get('year'))}&quarter=${encodeURIComponent(form.get('quarter'))}`);renderFinance(updated);}});
    }});
}

export function selectSource(result, period, onSaved=renderFinance) {
  requirePermission('actual.create');
  const choices = (period.entry_amount===null?'':`<option value="entries">逐笔明细合计 · ${esc(yuan(period.entry_amount))}</option>`) +
    period.history.map(r=>`<option value="history:${esc(r.id)}">历史：${esc(r.source_field)} · ${esc(yuan(r.amount_yuan))} · 证据 ${esc(r.id.slice(0,8))}</option>`).join('');
  dialog(`${period.year} 年 Q${period.quarter} · ${kinds[period.kind]}来源确认`,
    `<p class="notice">此处仅用于旧证据核对。明确采用一个来源后保存金额快照，后续参考变化提示复核；新实际请用运营登记。切回旧来源会替换当前采用结果，渠道关联仍须通过防重复校验。</p><label class="field"><b>使用的来源</b><select name="selected" required><option value="">请选择来源</option>${choices}</select></label><label class="field"><b>核对理由</b><textarea name="reason" required maxlength="1000" rows="3" placeholder="说明已核实的凭据及选择依据"></textarea></label>`,
    {submit:'确认来源', onSubmit:async(form,key)=>{
      const selection=String(form.get('selected')||''), history=selection.startsWith('history:');
      if(!selection)throw Error('请选择来源');
      const response=await api(`/opportunities/${encodeURIComponent(result.opportunity_id)}/financial-sources/confirm`,{
        method:'POST',key,body:{year:period.year,quarter:period.quarter,kind:period.kind,
          source:history?'historical':'entries',selected_history_id:history?selection.slice(8):null,
          reason:String(form.get('reason')||'').trim(),expected_fingerprint:period.source_fingerprint,
          expected_version:period.decision?.version_no||0}});
      await onSaved(response);toast('已保存采用来源；同步状态以同步队列为准');
    }});
}

export function operationalPayload(form,result,period,rows) {
  const existing=new Map((period.confirmations||[]).map(r=>[r.material_line_id||'collection',r]));
  const population=period.kind==='recognized'?(form.getAll('expected_material')||[]).map(String):null;
  const entries=[];
  for(const row of period.kind==='recognized'?rows:[{id:'collection'}]) {
    const action=String(form.get('action:'+row.id)||'keep');
    if(action==='keep')continue;
    if(!['confirm','withdraw'].includes(action))throw Error('请选择有效操作');
    const old=existing.get(row.id),entry={material_line_id:period.kind==='recognized'?row.id:null,expected_version:old?.version_no||0,action};
    if(action==='confirm') {
      const amount=String(form.get('amount:'+row.id)||'').trim(),source_ref=String(form.get('source:'+row.id)||'').trim();
      if(!/^\d{1,16}(?:\.\d{1,2})?$/.test(amount)||!source_ref)throw Error('确认实际须填写合法金额与核对依据，金额为零请填 0');
      Object.assign(entry,{amount,source_ref,note:String(form.get('note:'+row.id)||'').trim()});
    }
    entries.push(entry);
  }
  const empty_action=String(form.get('empty_action')||'')||null;
  if(empty_action&&(entries.length||population?.length))throw Error('空集合确认须取消应登记物料，并先单独撤回仍有效的物料记录');
  if(!empty_action&&!entries.length)throw Error('请选择至少一项确认或撤回操作');
  return {year:period.year,quarter:period.quarter,kind:period.kind,
    expected_version:period.version_no??period.decision?.version_no??0,expected_opportunity_version:result.opportunity_version,
    expected_fingerprint:period.source_fingerprint,reason:String(form.get('reason')||'').trim(),
    replace_source:form.get('replace_source')==='on',replace_expected_materials:form.get('replace_expected_materials')==='on',
    expected_material_line_ids:population,empty_action,entries};
}

async function editActualPeriod(result,period) {
  requirePermission('actual.create');
  const material=await api(`/api/v1/opportunities/${encodeURIComponent(result.opportunity_id)}/materials`);
  if(material.version_no!==result.opportunity_version)throw Error('商机物料已变化，请关闭并重新打开财务核对');
  const existing=new Map((period.confirmations||[]).map(r=>[r.material_line_id||'collection',r]));
  const rows=[...material.items,...material.retired_items].filter(r=>material.trade_relation!=='distributor'||r.usage==='self_use');
  const chosen=new Set(period.source==='operations'?(period.expected_material_line_ids||[]):rows.filter(r=>r.is_active).map(r=>r.id));
  const lines=(period.kind==='recognized'?rows:[{id:'collection',catalog_snapshot:{name:'本商机本期回款'}}]).map(row=>{
    const old=existing.get(row.id),canVoid=hasPermission('actual.void')&&old?.status==='confirmed';
    return `<section class="import-row"><h3>${esc(row.catalog_snapshot?.name||row.id)}${row.is_active===false?' · 预测行已停用':''}</h3>`+
      (period.kind==='recognized'?`<label><input type="checkbox" name="expected_material" value="${esc(row.id)}" ${chosen.has(row.id)?'checked':''}>属于本期间应登记集合</label>`:'')+
      `<label class="field"><b>本次操作</b><select name="action:${esc(row.id)}"><option value="keep">保留当前状态</option><option value="confirm">${old?.status==='withdrawn'?'重新确认':'登记 / 更正'}</option>${canVoid?'<option value="withdraw">撤回</option>':''}</select></label>`+
      `<label class="field"><b>${period.kind==='recognized'?'不含税确收':'实际回款'}（元）</b><input name="amount:${esc(row.id)}" inputmode="decimal" value="${esc(old?.amount??'')}" placeholder="未登记；为零请填0"></label>`+
      `<label class="field"><b>核对依据</b><input name="source:${esc(row.id)}" maxlength="160" value="${esc(old?.source_ref||'')}"></label>`+
      `<label class="field"><b>备注</b><input name="note:${esc(row.id)}" maxlength="1000" value="${esc(old?.note||'')}"></label>`+
      `<p class="help">${old?`${old.status==='withdrawn'?'已撤回':'已确认'} · 当前版本 ${old.version_no}`:'尚未登记'}</p></section>`;
  }).join('');
  return dialog(`${period.year} 年 Q${period.quarter} · ${kinds[period.kind]}`,
    `<p class="notice">实际由运营独立核对。预测、物料参考与本次金额不要求相等；未操作行保持原状态。</p>${lines||'<p>没有可登记的自用物料行。</p>'}`+
    (period.kind==='recognized'?`<label class="field"><b>本期空集合决议（可选）</b><select name="empty_action"><option value="">不改变空集合决议</option><option value="confirm">明确确认本期无应登记自用物料，确收为0</option>${period.empty_confirmation&&hasPermission('actual.void')?'<option value="withdraw">撤回已有空集合确认</option>':''}</select></label><label><input type="checkbox" name="replace_expected_materials">明确调整本期应登记集合</label>`:'')+
    '<label><input type="checkbox" name="replace_source">明确替换已有旧实际来源（保留原证据）</label><label class="field"><b>本次核对说明</b><textarea name="reason" required maxlength="1000"></textarea></label>',
    {wide:true,submit:'保存运营实际',onSubmit:async(form,key)=>{
      requirePermission('actual.create');const body=operationalPayload(form,result,period,rows);
      const response=await api(`/opportunities/${encodeURIComponent(result.opportunity_id)}/actual-confirmations`,{method:'POST',key,body});
      renderFinance(response);toast('运营实际已保存');
    }});
}

export async function showOpportunityFinance(id) {
  requirePermission('actual.read');
  return showFinanceWorkspace(id);
}

export async function openFinanceDeepLink() {
  const id=new URL(location.href).searchParams.get('finance');
  if(!id||!hasPermission('actual.read'))return;
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))return;
  await showOpportunityFinance(id);
}
