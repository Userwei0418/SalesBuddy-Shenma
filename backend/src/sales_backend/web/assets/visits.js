import {state, $, api, esc, hasPermission, requirePermission, dialog, dialogIntent, head, stats, details, table, options, field, bindCommon} from './core.js';

const ROOT = '/api/v1/console/visits';
const PAGE_SIZE = 50;
const modes = {offline_meeting:'线下会议',online_meeting:'线上会议',phone_voice:'电话 / 语音',social_meal:'饭局 / 聚会'};
const expectations = {exceeded:'超出100%',met:'达成100%',met_50_100:'达成50–100%',met_30_50:'达成30–50%',met_10_30:'达成10–30%',not_met:'未达成'};
const firstVisitFields = ['customer_main_business','customer_needs','customer_budget','contact_role'];
const editableFields = [
  ['interaction_at','跟进日期','date'], ['created_date','销售填写日期','date'],
  ['contact_name','对接人','text'], ['contact_title','联系人职位','text'],
  ['partner_name','伙伴名称（补充信息）','text'], ['interaction_mode','沟通方式','mode'],
  ['visit_location','拜访地点','text'], ['customer_main_business','客户主营业务','textarea'],
  ['customer_needs','客户需求','textarea'], ['customer_budget','客户预算','text'], ['contact_role','联系人角色','role'],
];
const listOf = value => Array.isArray(value) ? value : [];
const text = value => value === null || value === undefined || value === '' ? '未填写' : String(value);
const query = values => '?' + new URLSearchParams(Object.entries(values).filter(([,v])=>v!==null && v!==undefined && v!==''));
const peopleNames = people => listOf(people).map(person=>person.name || person.display_name || '未记录姓名').join('、') || '未记录';

export function visitDay(value) {
  if (!value) return '';
  const raw = String(value);
  // Date-only and timezone-less legacy fields already mean the Beijing date.
  if (/^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$/.test(raw)) return raw.slice(0,10);
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) ? new Date(timestamp + 8*3600000).toISOString().slice(0,10) : '';
}
export function visitTimestamp(value) {
  if (!value) return '未记录';
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp+8*3600000).toISOString().slice(0,16).replace('T',' ') : '未记录';
}
function validDay(value) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
  const n=Date.parse(value+'T00:00:00Z');return Number.isFinite(n) && new Date(n).toISOString().slice(0,10)===value;
}
export function visitFilters(filters) {
  const values={scope:filters.scope==='mine'?'mine':'all',date_basis:filters.date_basis==='created'?'created':'interaction'};
  const keyword=String(filters.q || '').trim();if(keyword.length>100)throw Error('搜索内容请控制在 100 字以内');if(keyword)values.q=keyword;
  if(filters.recorder_id)values.recorder_id=filters.recorder_id;
  for(const key of ['date_from','date_to'])if(filters[key]){if(!validDay(filters[key]))throw Error('请选择有效的日期');values[key]=filters[key];}
  if(values.date_from && values.date_to && values.date_from>values.date_to)throw Error('开始日期不能晚于结束日期');
  for(const key of ['score_min','score_max'])if(filters[key]!=='' && filters[key]!==null && filters[key]!==undefined){
    const n=Number(filters[key]);if(!Number.isInteger(n) || n<0 || n>100)throw Error('质量分请填写 0–100 的整数');values[key]=n;
  }
  if(values.score_min!==undefined && values.score_max!==undefined && values.score_min>values.score_max)throw Error('最低质量分不能高于最高质量分');
  return values;
}
function validatePage(result, offset) {
  if(!Array.isArray(result.items) || typeof result.has_more!=='boolean' || (result.has_more && (!Number.isInteger(result.next_offset) || result.next_offset<=offset)))throw Error('列表响应不完整，请刷新后重试');
}
function score(value) {
  return value!==null && value!==undefined && Number.isFinite(Number(value)) ? `<span class="vm-score">${esc(value)}<small>分</small></span>` : '<span class="muted">未评分</span>';
}
function subjects(record) {
  const linked=listOf(record.linked_opportunities);
  const names=linked.length?linked.map(row=>row.name || '商机名称未记录').join('、'):record.opportunity_name;
  const subject=record.subject_name || record.customer_name || record.partner_name || record.partner_name_snapshot || (names?'仅关联商机':'历史关联未记录');
  const kind=({customer:'客户',partner:'伙伴',opportunity:'商机',unknown:'历史记录'})[record.subject_kind] || (record.customer_id?'客户':record.partner_id?'伙伴':names?'商机':'历史记录');
  return {name:subject,kind,opportunities:names || '未关联商机'};
}
function recorder(record) {
  return `<strong>${esc(record.original_recorder_name || record.recorder_name || '跟进人未记录')}</strong>${record.original_recorder_name?'<small>原始跟进人</small>':''}${record.manager_name?`<small>当前管理人：${esc(record.manager_name)}</small>`:''}`;
}
function savedQuality(record) {
  const review=record.quality_review && typeof record.quality_review==='object'?record.quality_review:{};
  const next=review.next_action && typeof review.next_action==='object'?review.next_action:{};
  const suggestions=listOf(review.suggestions).filter(v=>typeof v==='string');
  const nextSuggestions=listOf(next.suggestions).filter(v=>typeof v==='string');
  return `<section class="vm-section"><div class="vm-section-title"><h3>已保存的质量审核</h3>${score(record.follow_up_score ?? review.follow_up_score)}</div>${details([
    ['质量等级',review.grade || '未记录'],['下一步审核',(review.next_action_passed ?? next.passed)===true?'通过':(review.next_action_passed ?? next.passed)===false?'未通过':'未记录'],
  ])}${suggestions.length?`<h4>改进建议</h4><ul class="vm-suggestions">${suggestions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul>`:''}${nextSuggestions.length?`<h4>下一步审核说明</h4><ul class="vm-suggestions">${nextSuggestions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul>`:''}<p class="help">展示归档时保存的审核结果，查看详情不会重新分析。</p></section>`;
}
function editable(record) {
  if(!hasPermission('visit.supplement') || record.can_supplement!==true || record.recorder_id!==state.actor?.user_id)return [];
  return editableFields.filter(([key])=>listOf(record.supplement_fields).includes(key) && (!firstVisitFields.includes(key) || record.is_first_visit===true));
}
function originalFiles(record) {
  return hasPermission('visit.download_original') ? listOf(record.original_materials).filter(file=>file.can_download===true && typeof file.id==='string' && file.id) : [];
}
function recordedDay(record) {
  // New responses distinguish unknown human-entered dates from system timestamps.
  return visitDay(Object.hasOwn(record,'recorded_on') ? record.recorded_on : record.created_date);
}
function fieldValue(record, key) {
  if(key==='interaction_at')return visitDay(record.visit_date || record.interaction_at);
  if(key==='created_date')return recordedDay(record);
  if(key==='partner_name')return record.partner_name_snapshot ?? record.partner_name ?? '';
  if(key==='contact_name')return record.contact_name_snapshot ?? record.contact_name ?? '';
  if(key==='contact_title')return record.contact_title_snapshot ?? record.contact_title ?? '';
  if(key==='interaction_mode')return record.interaction_mode_code ?? record.interaction_mode ?? '';
  return record[key] ?? record.first_visit_profile?.[key] ?? '';
}

export function supplementPayload(record, form) {
  requirePermission('visit.supplement');const permitted=editable(record);
  if(!permitted.length)throw Error('此记录当前不可补充，请刷新后重试');
  if(!Number.isInteger(record.version_no))throw Error('记录版本未返回，请刷新后重试');
  const body={version_no:record.version_no};
  for(const [key,,kind] of permitted){
    if(!form.has(key))continue;
    const value=String(form.get(key) ?? '').trim(),before=String(fieldValue(record,key)).trim();
    if(value===before)continue;
    if(kind==='date' && !validDay(value))throw Error('跟进日期和销售填写日期不能清空，请选择有效日期');
    if(firstVisitFields.includes(key) && !value)throw Error('首次拜访的补充信息不能为空');
    if(!firstVisitFields.includes(key) && kind!=='date' && value.length>300)throw Error('辅助信息请控制在 300 字以内');
    body[key]=value;
  }
  if(Object.keys(body).length===1)throw Error('尚未修改可补充的信息');
  return body;
}

export function supplementVisit(record) {
  const fields=editable(record);if(!fields.length)throw Error('此记录当前不可补充，请刷新后重试');
  const html=fields.map(([key,label,kind])=>{
    // Unknown historical values remain unknown when another field is supplemented.
    // Changed dates/profile fields are checked by supplementPayload before submission.
    const value=fieldValue(record,key);
    let choices=null;
    if(kind==='mode')choices=options([...Object.entries(modes), ...(value && !modes[value]?[[value,value]]:[])],value,'未填写');
    if(kind==='role')choices=options([...new Set(['使用者','影响者','决策者',...(value?[value]:[])])],value,'请选择');
    return field(label,key,value,{type:kind==='date'?'date':kind==='textarea'?'textarea':'text',full:kind==='textarea',select:choices});
  }).join('');
  return dialog('补充跟进信息',`<p class="notice">${esc(subjects(record).name)} · ${esc(visitDay(record.visit_date || record.interaction_at) || '日期未记录')}</p><p class="help">仅补充本次记录的辅助信息。沟通正文、下一步计划、关联主体与原跟进人保持归档内容。</p><div class="form-grid">${html}</div>`,{
    wide:true,submit:'保存补充',onSubmit:async(form,key)=>{
      const body=supplementPayload(record,form);
      await api('/api/v1/visits/'+encodeURIComponent(record.id),{method:'PATCH',body,key});
    },
  });
}

export async function downloadVisitOriginal(file, current) {
  requirePermission('visit.download_original');
  if(typeof current!=='function' || !current())return;
  if(file.can_download!==true || !file.id)throw Error('当前无法下载该原始材料');
  const response=await api('/api/v1/visit-imports/'+encodeURIComponent(file.id)+'/original',{raw:true});
  const blob=await response.blob();
  if(!current() || !hasPermission('visit.download_original'))return;
  const link=document.createElement('a'),url=URL.createObjectURL(blob);
  link.href=url;link.download=String(file.filename || '原始材料').replace(/[\\/\u0000-\u001f\u007f]/g,'_');
  try{link.click();}finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
}

export async function showVisit(id) {
  requirePermission('visit.read');const current=dialogIntent();let record;
  try{record=await api(ROOT+'/'+encodeURIComponent(id));}catch(error){if(current())throw error;return;}
  if(!current() || !hasPermission('visit.read'))return;
  const subject=subjects(record),files=originalFiles(record),first=record.is_first_visit===true;
  const narrative=[['拜访目标',record.visit_goal],['沟通内容',record.follow_up_record],['达成结果',expectations[record.expectation_code] || record.expectation_code],['下一步计划',record.next_action]];
  return dialog('跟进详情',`<div class="vm-detail-heading"><span class="vm-subject-tag">${esc(subject.kind)}跟进</span><h3>${esc(subject.name)}</h3><p>${esc(visitDay(record.visit_date || record.interaction_at) || '跟进日期未记录')} · ${esc(modes[record.interaction_mode_code || record.interaction_mode] || record.interaction_mode_code || record.interaction_mode || '方式未记录')}<span class="badge green">${record.status==='confirmed'?'已确认':'已归档'}</span></p></div>
    <section class="vm-section"><h3>关联信息与日期</h3>${details([
      ['客户',record.customer_name || '未关联客户'],['伙伴',record.partner_name || record.partner_name_snapshot || '未关联伙伴'],
      ['关联商机',subject.opportunities],['客户类型',({prospect:'潜在客户',opportunity:'商机客户',won:'已成单客户'})[record.customer_type] || record.customer_type || '未记录'],
      [record.original_recorder_name?'原始跟进人':'跟进人',record.original_recorder_name || record.recorder_name || '未记录'],['当前管理人',record.manager_name || '未单独设置'],
      ['跟进日期',visitDay(record.visit_date || record.interaction_at) || '未记录'],['销售填写日期',recordedDay(record) || '未记录'],
      ['系统录入时间',visitTimestamp(record.created_at)],['对接人',record.contact_name_snapshot || record.contact_name || '未记录'],
      ['联系人职位',record.contact_title_snapshot || record.contact_title || '未记录'],['拜访地点',record.visit_location || '未记录'],
      ['拜访时长',record.duration_minutes===null || record.duration_minutes===undefined?'未记录':`${record.duration_minutes} 分钟`],['是否首次拜访',first?'是':record.is_first_visit===false?'否':'未记录'],
    ])}</section><section class="vm-section vm-narrative"><h3>沟通与行动</h3>${narrative.map(([label,value])=>`<article class="${label==='下一步计划'?'vm-next-action':''}"><h4>${label}</h4><p>${esc(text(value))}</p></article>`).join('')}</section>
    ${first?`<section class="vm-section"><h3>首次拜访信息</h3>${details(editableFields.filter(([key])=>firstVisitFields.includes(key)).map(([key,label])=>[label,text(fieldValue(record,key))]))}</section>`:''}
    <section class="vm-section"><h3>协作记录</h3>${details([['协同人',peopleNames(record.collaborators)],['本次实际协助 FDE',peopleNames(record.fde_participants)]])}</section>${savedQuality(record)}
    <section class="vm-section"><h3>原始材料</h3>${files.length?`<div class="vm-materials">${files.map((file,i)=>`<div><span>${esc(file.filename || '原始材料')}</span><button type="button" class="link" data-visit-original="${i}">下载原始材料</button></div>`).join('')}</div>`:`<p class="help">${record.original_material_status==='none'?'此记录未关联原始材料。':'原始材料当前不可下载，可联系管理员核实访问权限或材料状态。'}</p>`}</section>
    ${!editable(record).length && record.supplement_unavailable_reason?`<p class="help vm-readonly-note">${esc(record.supplement_unavailable_reason)}</p>`:''}`,{
      wide:true,footer:editable(record).length?'<button type="button" id="visit-supplement" class="primary">补充信息</button>':'',afterRender:root=>{
        const live=dialogIntent({liveForm:true});
        const supplement=$('#visit-supplement',root);
        if(supplement)supplement.onclick=()=>{if(!live())return;try{supplementVisit(record);}catch(error){$('.form-error',root).textContent=error.message;}};
        root.querySelectorAll('[data-visit-original]').forEach(button=>button.onclick=async()=>{
          if(!live() || button.disabled)return;button.disabled=true;
          try{await downloadVisitOriginal(files[Number(button.dataset.visitOriginal)],()=>live() && hasPermission('visit.read'));}
          catch(error){if(live())$('.form-error',root).textContent=error.message;}
          finally{button.disabled=false;}
        });
      },
    });
}

export function chooseVisitRecorder(filters,onSelect) {
  requirePermission('visit.read');
  const root=dialog('筛选跟进人','<div class="vm-picker-search"><input type="search" id="visit-recorder-search" aria-label="搜索跟进人" placeholder="姓名或账号"><button type="button" id="visit-recorder-find">搜索</button></div><div id="visit-recorder-results" aria-live="polite"></div><div class="vm-picker-error" role="alert"></div><div class="vm-picker-pager"><button type="button" id="visit-recorder-prev">上一页</button><span id="visit-recorder-page"></span><button type="button" id="visit-recorder-next">下一页</button></div>',{wide:true});
  const current=dialogIntent({liveForm:true});let serial=0,offset=0,next=null,search='';
  const live=()=>current() && hasPermission('visit.read');
  async function load(start=0){
    if(!live())return;const request=++serial;
    $('#visit-recorder-prev',root).disabled=true;$('#visit-recorder-next',root).disabled=true;
    $('#visit-recorder-results',root).innerHTML='<p class="muted">正在加载…</p>';$('.vm-picker-error',root).textContent='';
    try{
      const values={...filters,recorder_q:search,page_size:100,offset:start};delete values.recorder_id;
      const result=await api(ROOT+'/recorders'+query(values));if(!live() || request!==serial)return;
      validatePage(result,start);offset=start;next=result.has_more?result.next_offset:null;
      const choices=[{id:'',name:'全部跟进人'},...result.items];
      $('#visit-recorder-results',root).innerHTML=choices.map((person,i)=>`<button type="button" class="vm-picker-choice" data-visit-recorder="${i}"><strong>${esc(person.name)}</strong>${person.id?`<span>${esc(person.account_code)}${person.active===false?' · 账号停用':''} · ${esc(person.record_count)} 条记录</span>`:''}</button>`).join('')+`${result.unmapped_count?`<p class="help">另有 ${esc(result.unmapped_count)} 条历史记录未匹配账号，选择“全部跟进人”可查看。</p>`:''}${!result.items.length?'<p class="muted">当前筛选暂无匹配人员</p>':''}`;
      root.querySelectorAll('[data-visit-recorder]').forEach(button=>button.onclick=()=>{if(!live())return;const person=choices[Number(button.dataset.visitRecorder)];root.close();onSelect({recorder_id:person.id,recorder_name:person.id?person.name:''});});
      $('#visit-recorder-page',root).textContent=`第 ${Math.floor(offset/100)+1} 页${result.has_more?' · 还有更多跟进人':''}`;
      $('#visit-recorder-prev',root).disabled=!offset;$('#visit-recorder-next',root).disabled=next===null;
    }catch(error){if(live() && request===serial)$('.vm-picker-error',root).textContent=error.message;}
  }
  $('#visit-recorder-prev',root).onclick=()=>load(Math.max(0,offset-100));$('#visit-recorder-next',root).onclick=()=>next===null?undefined:load(next);
  const searchNow=()=>{search=$('#visit-recorder-search',root).value.trim();return load();};
  $('#visit-recorder-find',root).onclick=searchNow;$('#visit-recorder-search',root).onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();searchNow();}};
  return load();
}

export async function visits({isCurrent=()=>true}={}) {
  requirePermission('visit.read');
  const saved={...state.filters},filters=visitFilters(saved),offset=state.offset;
  const result=await api(ROOT+query({...filters,page_size:PAGE_SIZE,offset}));if(!isCurrent() || !hasPermission('visit.read'))return {html:''};validatePage(result,offset);
  const summary=result.summary || {};
  return {html:`<div class="visit-management">${head('跟进管理','集中查看已确认的客户、伙伴及商机跟进，回顾沟通结果与下一步行动。')}
    <div class="vm-stats">${stats([['跟进记录',summary.total ?? '—','当前筛选范围内的正式记录'],['涉及主体',summary.subject_count ?? '—','按关联客户、伙伴去重'],['已有评分',summary.scored_count ?? '—','保存了质量评分的记录']])}</div>
    <section class="card"><div class="vm-viewbar"><div class="tm-segment" aria-label="跟进范围"><button type="button" data-visit-scope="all" aria-pressed="${filters.scope==='all'}">全部可见记录</button><button type="button" data-visit-scope="mine" aria-pressed="${filters.scope==='mine'}">我的跟进</button></div><span>已确认 / 已归档</span></div>
    <form id="filters" class="toolbar vm-filters"><label class="vm-keyword"><span>搜索记录</span><input type="search" name="q" maxlength="100" placeholder="客户、伙伴、商机或沟通内容" value="${esc(filters.q || '')}"></label><label><span>跟进人</span><button type="button" id="visit-recorder-filter">${esc(saved.recorder_name || (filters.recorder_id?'已选跟进人':'全部跟进人'))}</button></label><label><span>日期维度</span><select name="date_basis">${options([['interaction','跟进日期'],['created','系统录入时间']],filters.date_basis,null)}</select></label><label><span>开始日期</span><input type="date" name="date_from" value="${filters.date_from || ''}"></label><label><span>结束日期</span><input type="date" name="date_to" value="${filters.date_to || ''}"></label><label class="vm-score-input"><span>最低质量分</span><input type="number" name="score_min" min="0" max="100" step="1" placeholder="不限" value="${filters.score_min ?? ''}"></label><label class="vm-score-input"><span>最高质量分</span><input type="number" name="score_max" min="0" max="100" step="1" placeholder="不限" value="${filters.score_max ?? ''}"></label><button type="submit">筛选</button><button type="button" class="link" id="visits-reset">重置</button><p class="vm-filter-note">日期按北京时间，起止日期均包含。设置分数范围后，仅显示已有评分的记录。</p><div id="visits-filter-error" role="alert"></div></form>
    <div class="vm-result-note">共 <strong>${esc(summary.total ?? '—')}</strong> 条 · 本页 ${result.items.length} 条<span>统计覆盖全部筛选结果</span></div>
    ${table(['跟进日期 / 录入时间','关联主体 / 商机','跟进人','沟通与下一步','质量分','操作'],result.items,record=>{
      const subject=subjects(record);
      return `<tr><td class="vm-date"><strong>${esc(visitDay(record.visit_date || record.interaction_at) || '日期未记录')}</strong><small>录入 ${esc(visitTimestamp(record.created_at))}</small></td><td class="vm-subject"><span class="vm-subject-tag">${esc(subject.kind)}</span><strong>${esc(subject.name)}</strong><small>${esc(subject.opportunities)}</small></td><td class="vm-recorder">${recorder(record)}</td><td class="vm-excerpt"><p>${esc(record.follow_up_record || '沟通内容未记录')}</p><small><b>下一步</b> ${esc(record.next_action || '未记录')}</small></td><td>${score(record.follow_up_score)}</td><td><button class="link" data-action="visit-detail" data-id="${esc(record.id)}">查看详情</button></td></tr>`;
    },'当前筛选没有跟进记录')}
    <div class="pager"><span>第 ${Math.floor(offset/PAGE_SIZE)+1} 页 · 每页最多 ${PAGE_SIZE} 条</span><div><button id="visits-prev" ${offset?'':'disabled'}>上一页</button><button id="visits-next" ${result.has_more?'':'disabled'}>下一页</button></div></div></section></div>`,bind:root=>{
      const identity=JSON.stringify(state.actor),company=state.company,page=state.page;
      const live=()=>isCurrent() && root===$('#content') && state.page===page && state.company===company && identity===JSON.stringify(state.actor) && hasPermission('visit.read');
      const refresh=(patch,next=0)=>{if(!live())return;state.filters={...saved,...patch};state.offset=next;return state.refresh();};
      bindCommon(root,{'visit-detail':id=>live()?showVisit(id):undefined});
      $('#filters',root).onsubmit=event=>{event.preventDefault();if(!live())return;const patch=Object.fromEntries(new FormData(event.currentTarget));try{visitFilters({...saved,...patch});return refresh(patch);}catch(error){$('#visits-filter-error',root).textContent=error.message;}};
      $('#visits-reset',root).onclick=()=>{if(!live())return;state.filters={};state.offset=0;return state.refresh();};
      root.querySelectorAll('[data-visit-scope]').forEach(button=>button.onclick=()=>refresh({scope:button.dataset.visitScope}));
      $('#visit-recorder-filter',root).onclick=()=>live()?chooseVisitRecorder(filters,patch=>refresh(patch)):undefined;
      $('#visits-prev',root).onclick=()=>offset?refresh({},Math.max(0,offset-PAGE_SIZE)):undefined;
      $('#visits-next',root).onclick=()=>result.has_more?refresh({},result.next_offset):undefined;
    }};
}
