import {state, api, esc, head, filters, search, table, pager, bindCommon, requirePermission,
  readForDialog, dialog, field, toast} from './core.js';

const endpoint='/api/v1/operations/opportunity-fields';

export function displayValue(key,value,item,catalog) {
  const people=new Map((item.people||[]).map(p=>[p.id,p.name]));
  const label=value=>catalog.fields.find(f=>f.key===key)?.options?.find(o=>o.value===value)?.label||String(value);
  if(typeof value==='boolean')return value?'是':'否';
  if(key==='primary_fde_id')return people.get(value)||'人员待核对';
  if(key==='fde_member_ids')return value.map(id=>people.get(id)||'人员待核对').join('、');
  if(key==='participants')return value.map(p=>`${people.get(p.user_id)||'人员待核对'}（${catalog.participant_roles.find(r=>r.value===p.role)?.label||p.role}）`).join('、');
  if(Array.isArray(value))return value.map(label).join('、');
  return label(value);
}

export function confirmedPayload(form,items) {
  const reason=String(form.get('reason')||'').trim();
  if(!reason)throw Error('请填写本次初始化说明');
  const selected=items.flatMap((item,index)=>{
    const values=Object.fromEntries(Object.entries(item.values).filter(([key])=>form.get(`select:${index}:${key}`)==='on'));
    if(values.primary_fde_id && item.values.fde_member_ids && !values.fde_member_ids)
      throw Error('所选主责需要同时确认长期FDE名单，请一并勾选或取消主责');
    return Object.keys(values).length?[{opportunity_id:item.opportunity_id,version_no:item.version_no,values}]:[];
  });
  if(!selected.length)throw Error('请至少勾选一个已核对的建议值');
  return {reason,items:selected};
}

export async function openInitialization(rows,catalog) {
  requirePermission('opportunity.update');
  if(!rows.length)throw Error('请先选择要核对的商机');
  const result=await readForDialog(()=>api(endpoint+'/preview',{method:'POST',body:{opportunity_ids:rows.map(r=>r.id)}}));
  if(!result)return;
  const resultItems=result.items;
  const names=new Map(rows.map(r=>[r.id,r.name]));
  const label=key=>catalog.fields.find(f=>f.key===key)?.label||key;
  const source=path=>`${path.startsWith('source_fields.')?'导入来源':'原始来源'} · ${label(path.slice(path.indexOf('.')+1))}`;
  const html=result.items.map((item,index)=>'<section class="initialization-record"><h3>'+esc(names.get(item.opportunity_id)||'商机')+'</h3>'+
    table(['确认补入','字段','建议值 / 来源'],Object.entries(item.values),([key,value])=>
      `<tr><td><input type="checkbox" name="select:${index}:${esc(key)}" aria-label="${esc(label(key)+'：确认补入')}"></td><td>${esc(label(key))}</td><td><span class="init-value">${esc(displayValue(key,value,item,catalog))}</span><small>${esc((item.sources[key]||[]).map(source).join('；'))}</small></td></tr>`,'没有可直接确认的旧值')+
    (item.conflicts.length?'<div class="notice"><strong>以下字段需人工核对</strong><ul>'+item.conflicts.map(c=>`<li>${esc(label(c.field))}：${esc(c.message)}</li>`).join('')+'</ul></div>':'')+
    (item.missing.length?'<p class="help">无旧值：'+esc(item.missing.map(label).join('、'))+'。可通过商机字段编辑人工补充。</p>':'')+'</section>').join('');
  return dialog('核对旧字段并确认补入','<p class="notice">请逐项核对后勾选；只补当前空字段。已有值不会被覆盖，任一记录冲突时整批不保存。</p>'+html+
    field('本次初始化说明','reason','',{required:true,type:'textarea'}),{
    wide:true,submit:'确认补入已勾选字段',onSubmit:async(form,key)=>{
      requirePermission('opportunity.update');
      const result=await api(endpoint+'/apply',{method:'POST',key,body:confirmedPayload(form,resultItems)});
      toast(`已补入 ${result.applied_count} 条商机；未勾选字段保持原状`);
    }});
}

export async function opportunityInitialization() {
  requirePermission('opportunity.update');
  // Check actual company-admin authority before requesting business records.
  const catalog=await api(endpoint+'/options');
  const params=new URLSearchParams({q:state.filters.q||'',limit:'50',offset:String(state.offset)});
  const data=await api('/opportunities?'+params);
  return {html:head('旧字段初始化','公司管理员先查看旧字段建议，逐项确认后补入当前空字段。',
    '<button class="primary" data-action="preview">预览所选商机</button>')+
    '<section class="card">'+filters(search('搜索商机 / 客户名称'))+
    table(['选择','商机 / 客户','负责人','核对'],data.items,row=>`<tr><td><input type="checkbox" data-init-id="${esc(row.id)}" aria-label="${esc('选择'+row.name)}" ${row.can_edit?'':'disabled'}></td><td><strong>${esc(row.name)}</strong><small>${esc(row.customer_name)}</small></td><td>${esc(row.owner_name||'—')}</td><td>${row.can_edit?`<button class="link" data-action="single" data-id="${esc(row.id)}">预览旧字段</button>`:'无维护权限'}</td></tr>`)+
    pager(data.total)+'</section><p class="help">每次选择限当前页。预览不修改数据；需要纠正已有正式值时，请使用商机字段编辑。</p>',
    bind:root=>bindCommon(root,{
      single:id=>openInitialization(data.items.filter(r=>r.id===id&&r.can_edit),catalog),
      preview:()=>{const ids=new Set([...root.querySelectorAll('[data-init-id]:checked')].map(e=>e.dataset.initId));return openInitialization(data.items.filter(r=>ids.has(r.id)&&r.can_edit),catalog);}
    })};
}
