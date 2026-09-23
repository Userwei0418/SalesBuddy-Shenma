import {state,api,setCompany,esc,field,dialog,table,num,badge} from './core.js';
export const companyKind = c => c.kind==='demo' ? '演示公司' : c.kind==='production' ? '正式公司' : '公司';
export function companyStorageKey() { return `sales-console-company:${state.actor?.workspace_id}:${state.actor?.user_id}`; }
export async function loadCompanies() {
  const result=await api('/companies');state.companies=result.items;
  let stored;try{stored=sessionStorage.getItem(companyStorageKey());}catch(_){}
  const selected=state.companies.find(c=>c.id===(state.company?.id||stored)) || state.companies.find(c=>c.id===result.home_company_id);
  if(!selected) throw Error('当前账号没有有效公司');
  if(state.company?.id!==selected.id) setCompany(selected);else state.company=selected;
}
export async function chooseCompany(id) {
  if(id===state.company?.id)return;
  const result=await api('/companies/select',{method:'POST',key:crypto.randomUUID(),body:{company_id:id}});
  setCompany(result.company);
  try{sessionStorage.setItem(companyStorageKey(),id);}catch(_){}
}
export function companyLabel() {
  return `<div class="company-label"><span>当前公司</span><strong>${esc(state.company?.name || '')}</strong></div>`;
}
export function companyCards() {
  return `<div class="card"><div class="card-head"><h2>公司管理</h2><small>部门、成员与业务数据按公司独立管理</small></div>${table(['公司','类型','有效账号','有效部门','状态','操作'],state.companies,c=>`<tr><td class="title">${esc(c.name)}${c.id===state.company.id?'<span class="mini-label">当前公司</span>':''}<small>${esc(c.code)}</small></td><td>${companyKind(c)}</td><td>${num(c.account_count)}</td><td>${num(c.department_count)}</td><td>${badge(c.status)}</td><td>${c.id!==state.company.id?`<button class="link" data-company-select="${esc(c.id)}">进入管理</button>`:state.actor.role==='administrator'?`<button class="link" data-company-edit="${esc(c.id)}">编辑名称</button>`:'当前公司'}</td></tr>`)}</div>`;
}
export function bindCompanies(root) {
  root.querySelectorAll('[data-company-select]').forEach(b=>b.onclick=()=>state.selectCompany(b.dataset.companySelect));
  root.querySelectorAll('[data-company-edit]').forEach(b=>b.onclick=()=>{
    const company=state.companies.find(c=>c.id===b.dataset.companyEdit);
    dialog('编辑公司名称',field('公司名称','name',company.name,{required:true}),{onSubmit:async(form,key)=>{
      await api(`/companies/${company.id}`,{method:'PUT',key,body:{name:form.get('name'),version_no:company.version_no}});
      await loadCompanies();document.dispatchEvent(new Event('company-label-changed'));
    }});
  });
}
