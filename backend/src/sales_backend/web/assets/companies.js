import {state,api,readForDialog,setCompany,esc,field,dialog,table,num,badge,download,toast} from './core.js';
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
  const exportButton=state.permissions?.['company.export'] ? '<button type="button" data-company-export>导出本公司数据</button>' : '';
  const terminologyButton=state.permissions?.['company.update']?'<button type="button" data-company-role-labels>岗位显示名称</button>':'';
  return `<div class="card"><div class="card-head"><h2>公司管理</h2><small>部门、成员与业务数据按公司独立管理</small>${exportButton}${terminologyButton}</div>${table(['公司','类型','有效账号','有效部门','状态','操作'],state.companies,c=>`<tr><td class="title">${esc(c.name)}${c.id===state.company.id?'<span class="mini-label">当前公司</span>':''}<small>${esc(c.code)}</small></td><td>${companyKind(c)}</td><td>${num(c.account_count)}</td><td>${num(c.department_count)}</td><td>${badge(c.status)}</td><td>${c.id!==state.company.id?`<button class="link" data-company-select="${esc(c.id)}">进入管理</button>`:state.permissions?.['company.update']?`<button class="link" data-company-edit="${esc(c.id)}">编辑名称</button>`:'当前公司'}</td></tr>`)}</div>`;
}
export function bindCompanies(root) {
  const terminology=root.querySelector('[data-company-role-labels]');
  if(terminology)terminology.onclick=async()=>{
    terminology.disabled=true;
    try {
      const current=await readForDialog('/companies/role-labels');
      if(!current)return;
      dialog('本公司岗位显示名称',`<p class="notice">仅调整本公司界面上的岗位称呼，不改变权限、数据范围或统计身份。产品销售部门继续显示“产品销售”和“产品销售主管”。</p>${field('销售岗位称呼','sales','',{select:['一线销售','客户经理'].map(name=>`<option value="${name}" ${current.labels.sales===name?'selected':''}>${name}</option>`).join('')})}`,{onSubmit:async(form,key)=>{
        await api('/companies/role-labels',{method:'PUT',key,body:{labels:{sales:form.get('sales')},version_no:current.version_no}});
        const org=await api('/organization');state.org={...state.org,...org};
        await loadCompanies();toast('本公司岗位称呼已更新，已登录成员刷新或重新进入页面后生效');
      }});
    }catch(e){toast(e.message);}finally{terminology.disabled=false;}
  };
  const exportButton=root.querySelector('[data-company-export]');
  if(exportButton)exportButton.onclick=async()=>{
    exportButton.disabled=true;
    try {
      const result=await readForDialog('/company-data/options');
      if(!result)return;
      const choices=result.sections.map(s=>`<label class="field"><span><input type="checkbox" name="section" value="${esc(s.code)}" ${s.available?'checked':'disabled'}> ${esc(s.label)}${s.available?'':'（无公司级权限）'}</span></label>`).join('');
      dialog('导出本公司业务数据',`<p>${esc(result.note)}</p><p>单包最多 ${Math.floor(result.max_bytes/1024/1024)} MiB、${num(result.max_records)} 条记录；超限或原件损坏时整包失败。</p>${choices}<label class="field"><span><input type="checkbox" name="originals" ${result.originals_available?'':'disabled'}> 包含已确认跟进的录音/文件原件</span></label>${field('导出用途','reason','',{required:true})}`,{submit:'生成并下载',onSubmit:async form=>{
        const sections=form.getAll('section');
        if(!sections.length)throw Error('请选择至少一个分类');
        if(form.has('originals')&&!sections.includes('visits'))throw Error('包含原件时请同时选择跟进记录');
        await download('/company-data/export','SalesBuddy-本公司业务数据',{method:'POST',throwOnError:true,body:{sections,include_originals:form.has('originals'),reason:form.get('reason')}},'zip');
      }});
    }catch(e){toast(e.message);}finally{exportButton.disabled=false;}
  };
  root.querySelectorAll('[data-company-select]').forEach(b=>b.onclick=()=>state.selectCompany(b.dataset.companySelect));
  root.querySelectorAll('[data-company-edit]').forEach(b=>b.onclick=()=>{
    const company=state.companies.find(c=>c.id===b.dataset.companyEdit);
    dialog('编辑公司名称',field('公司名称','name',company.name,{required:true}),{onSubmit:async(form,key)=>{
      await api(`/companies/${company.id}`,{method:'PUT',key,body:{name:form.get('name'),version_no:company.version_no}});
      await loadCompanies();document.dispatchEvent(new Event('company-label-changed'));
    }});
  });
}
