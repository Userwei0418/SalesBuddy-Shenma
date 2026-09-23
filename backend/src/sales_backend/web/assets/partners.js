import {state, $, api, esc, date, badge, options, field, dialog, table, pager, head, query, filters, search, bindCommon} from './core.js';

function editPartner(row = {}) {
  return dialog(row.id ? '编辑伙伴' : '添加伙伴',
    `<p class="help">伙伴停用后不能用于新的关联，已有商机记录保留。</p>${field('伙伴名称','name',row.name || '',{required:true})}${field('状态','status','',{select:options([['active','启用'],['inactive','停用']],row.status || 'active',null)})}`,
    {onSubmit:async (form, key) => api('/partners',{method:'POST',key,body:{id:row.id || null,name:String(form.get('name')).trim(),status:form.get('status'),version_no:row.version_no || null}})});
}

export function partnerFields(row = {}) {
  const channel = row.sales_channel || (row.id ? 'unknown' : 'direct');
  return field('销售渠道','sales_channel','',{required:true,select:options([['direct','直销'],['partner','合作伙伴']],channel==='unknown'?'':channel,'请选择渠道')}) +
    `<div class="field full" id="partner-picker" ${channel==='partner'?'':'hidden'}><label><b>搜索伙伴目录</b><input type="search" id="partner-search" placeholder="输入伙伴名称查询"></label><label><b>所属伙伴</b><select name="partner_id" aria-label="所属伙伴">${options(row.partner_id?[[row.partner_id,row.partner_name]]:[],row.partner_id,'请选择已有伙伴')}</select></label><p class="help" id="partner-hint">找不到伙伴时，可到伙伴目录维护。</p></div>`;
}

export function bindPartnerFields(root, row = {}) {
  let sequence=0,timer;
  const channel=$('[name=sales_channel]',root), select=$('[name=partner_id]',root), panel=$('#partner-picker',root), input=$('#partner-search',root), hint=$('#partner-hint',root);
  async function load() {
    const version=++sequence;
    hint.textContent='正在查询伙伴…';
    try {
      const result=await api('/api/v1/directory/partners?q='+encodeURIComponent(input.value.trim()));
      if (version!==sequence || !root.open) return;
      const saved=select.value;
      const choices=result.items.map(p=>[p.id,p.name]);
      if (row.partner_id && !choices.some(p=>p[0]===row.partner_id)) choices.unshift([row.partner_id,row.partner_name+' · 当前关联']);
      select.innerHTML=options(choices,saved,'请选择已有伙伴');
      hint.textContent=result.total>result.items.length?`找到 ${result.total} 个伙伴，显示前 ${result.items.length} 个；输入完整名称可缩小范围。`:'找不到伙伴时，可到伙伴目录维护。';
    } catch(error) { if(version===sequence && root.open) hint.textContent=error.message+'；重新输入可重试。'; }
  }
  function sync() { panel.hidden=channel.value!=='partner';select.required=!panel.hidden;select.disabled=panel.hidden;if(!panel.hidden)load(); }
  channel.onchange=sync;
  input.oninput=()=>{sequence++;clearTimeout(timer);timer=setTimeout(load,250);};
  root.addEventListener('close',()=>{sequence++;clearTimeout(timer);},{once:true});
  sync();
}

export async function partners() {
  const result = await api('/partners' + query({offset:state.offset}));
  return {
    html:head('伙伴目录','统一维护可选伙伴，销售从目录选择，历史关联持续保留。','<button class="primary" data-action="create">添加伙伴</button>') +
      `<section class="card">${filters(search('搜索伙伴名称')+`<select name="status" aria-label="伙伴状态">${options([['active','启用'],['inactive','停用']],state.filters.status || '')}</select>`)}${table(['伙伴名称','状态','更新时间','操作'],result.items,r=>`<tr><td><strong>${esc(r.name)}</strong></td><td>${badge(r.status)}</td><td>${date(r.updated_at)}</td><td><button class="link" data-action="edit" data-id="${esc(r.id)}">编辑</button></td></tr>`)}${pager(result.total)}</section>`,
    bind:root => bindCommon(root,{create:()=>editPartner(),edit:id=>editPartner(result.items.find(p=>p.id===id))}),
  };
}
