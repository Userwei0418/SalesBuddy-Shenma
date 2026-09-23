import { api, head, field, esc, date, table, dialog, details, options, bindCommon, state, roles} from "./core.js";
import {bindConnectivityTests} from './ai-connectivity.js';

const statuses = {draft:"草稿",active:"已发布",retired:"历史版本"};
const quadrants = {customer_asset:"客户资产",main_attack:"主攻区",customer_resource:"客户资源",order_driven:"见单打单"};
const display = v => typeof v === "boolean" ? (v ? "是" : "否") : v;
const fieldValue = (f, value) => f.type === "capability_roles" || f.type === "capability_users" ?
  (Object.entries(value || {}).map(([key, enabled]) => `${roles[key] || state.org?.accounts?.find(u=>u.id===key)?.display_name || key}：${enabled ? "开启" : "关闭"}`).join("；") || "沿用默认") : f.options ? (f.options.find(x=>x[0]===value)?.[1] || value) : display(value);
function policyDetails(item, definition) {
  return details(item.fields.map(f=>[f.label,f.type === 'textarea' && !definition[f.key] ? '沿用能力基础规则' : fieldValue(f,definition[f.key])]));
}
function draftBody(item, version, form) {
  const definition = {schema_version:1};
  item.fields.forEach(f=> {
    if (f.type === "capability_roles" || f.type === "capability_users") {
      const values = {...(version?.definition || item.current.definition)[f.key]};
      for (const [key, value] of form.entries()) if (key.startsWith(f.key + ":")) {
        const id=key.slice(f.key.length+1);
        if(value === "") delete values[id]; else values[id] = value === "true";
      }
      definition[f.key]=values; return;
    }
    const v=form.get(f.key); definition[f.key]=f.type==="number" ? Number(v) : f.type==="boolean" ? v==="true" : v;});
  return {id:version?.id || null,revision:version?.revision_no || null,
    base_id:item.current.id,definition,reason:form.get("reason") || "预览"};
}
function ruleField(f, d) {
  if (f.type === "capability_roles" || f.type === "capability_users") {
    const entries=f.type === "capability_roles" ? [["fde","FDE"],["fde_lead","FDE主管"]] :
      (state.org?.accounts || []).filter(u=>u.status === "active" && u.roles?.some(r=>r === "fde" || r === "fde_lead"))
        .map(u=>[u.id,`${u.display_name} · ${u.account_code}`]);
    return `<section class="full"><h3>${esc(f.label)}</h3><p class="help">${f.type === "capability_users" ? "个人设置优先于岗位和默认设置。未单独配置的成员沿用岗位设置。" : "未单独配置的岗位沿用默认设置。"}</p><div class="form-grid">` +
      entries.map(([id,label])=>field(label,f.key+":"+id,d[f.key]?.[id],{select:options([["","沿用默认"],["true","开启"],["false","关闭"]],d[f.key]?.[id] === undefined ? "" : String(d[f.key][id]),null)})).join("") + "</div></section>";
  }
  if (f.type === 'textarea') {
    const required=f.required ?? true;
    const limit=Number.isInteger(f.max) && f.max>0 ? f.max : null;
    return '<label class="field full"><b>'+esc(f.label)+(required ? ' <span class="req">*</span>' : '')+'</b><textarea name="'+esc(f.key)+'"'+(required ? ' required' : '')+(limit ? ' maxlength="'+limit+'"' : '')+'>'+esc(d[f.key])+'</textarea>'+
      (!required || limit ? '<small>'+(!required ? '可留空，沿用能力基础规则。' : '')+(limit ? '最多 '+limit+' 字。' : '')+'</small>' : '')+'</label>';
  }
  return field(f.label,f.key,d[f.key],{
    type:f.type === "boolean" ? "text" : f.type, min:f.min ?? "", max:f.max ?? "", step:f.type === "time" ? 60 : 1,
    required:f.required ?? f.type !== "boolean",full:f.type === "textarea",
    select:f.options ? options(f.options,d[f.key],null) : f.type === "boolean" ? options([["true","是"],["false","否"]],String(d[f.key]),null) : null,
  });
}
export function policyFields(item, definition) {
  const guidance = item.fields.filter(f=>f.type === 'textarea');
  const structured = item.fields.filter(f=>f.type !== 'textarea');
  if (!guidance.length || !structured.length) return '<div class="form-grid">'+item.fields.map(f=>ruleField(f,definition)).join('')+'</div>';
  return '<section class="rule-form-section"><h3>业务判断规则</h3><p class="help">用于评分判断与校准，不改变返回格式、数据权限或人工确认流程。</p><div class="form-grid">'+guidance.map(f=>ruleField(f,definition)).join('')+'</div></section>'+
    '<section class="rule-form-section"><h3>结构化标准</h3><p class="help">系统按发布的数值与选项处理，补充业务指引不可覆盖。</p><div class="form-grid">'+structured.map(f=>ruleField(f,definition)).join('')+'</div></section>';
}
function edit(item, version) {
  const d=version?.definition || item.current.definition;
  dialog(item.label+" · "+(version ? "编辑草稿" : "新建草稿"),
    '<p class="notice">保存草稿不会改变现有业务。发布后用于新业务结果；历史结果保留原规则。权限、证据与人工确认由系统继续校验。</p>'+
    (item.code.startsWith('score.') ? '<p class="notice">各项权重之和须为100%；停用某项可设为0。缺少依据的指标不计零分，页面同时展示有效指标及覆盖率。发布后当前总分按新权重计算，历史复盘保持原记录。</p>' : '')+
    (item.code==='visit_admission' ? '<p class="notice">必填正文、归档日期与对接人、首访四项、时间与行动审核、人工确认继续按表单契约执行，不可由评分文字关闭。</p>' : '')+
    (item.code.startsWith('agent_business.') ? '<p class="notice">仅维护当前公司的补充业务指引。固定返回契约与权限优先，其次为公司结构化规则，最后为本页补充指引。发布后每次调用携带当前版本，不覆盖其他公司的规则。</p>' : '')+
    policyFields(item,d)+'<div class="form-grid rule-change-reason">'+field("变更原因","reason",version?.change_reason || "",{required:true,full:true})+'</div>'+
    '<div id="rule-preview"></div>', {
      wide:true,submit:"保存草稿",footer:'<button type="button" id="preview-rule">配置检查</button>',
      onSubmit:async(form,key)=>api('/company-rules/'+item.code+'/drafts',{method:"POST",key,body:draftBody(item,version,form)}),
      afterRender:root=>{root.querySelector('#preview-rule').onclick=async()=>{
        const output=root.querySelector('#rule-preview');
        try {
          const result=await api('/company-rules/'+item.code+'/preview',{method:"POST",body:draftBody(item,version,new FormData(root.querySelector('form')))});
          output.innerHTML='<p class="help">'+esc(result.note || '配置检查完成')+' 配置检查不代表真实 Agent 验收。</p>'+table(result.columns || ["潜力 / 关系","当前规则","草稿规则"],result.samples || [],s=>
            '<tr><td>'+esc(s.label || (s.potential+' / '+s.relationship))+'</td><td>'+esc(quadrants[s.before] || s.before)+'</td><td>'+esc(quadrants[s.after] || s.after)+'</td></tr>');
        } catch(e) {output.innerHTML='<p class="error-box">'+esc(e.message)+'</p>';}
      };},
    });
}
function versions(item, canPublish) {
  const current=item.current.definition;
  dialog(item.label+" · 版本与回退",'<p class="notice">回退先恢复历史内容为新草稿，核对后由管理员发布；历史版本不可覆盖。</p>'+table(
    ["版本 / 状态","修改人 / 发布时间","原因","操作"],item.versions,v=>'<tr><td>'+(v.workspace_id ? '公司 V' : '基线 V')+v.version_no+' · '+esc(statuses[v.status])+
    '</td><td>'+esc(v.updated_by || "未记录")+'<small>'+date(v.published_at)+'</small></td><td>'+esc(v.change_reason)+
    '</td><td><button type="button" class="link" data-version="'+v.id+'">'+(v.status==="draft" ? "核对草稿" : "查看 / 恢复")+'</button></td></tr>'),{
      wide:true,afterRender:root=>root.querySelectorAll('[data-version]').forEach(button=>button.onclick=()=>{
        const v=item.versions.find(x=>x.id===button.dataset.version);
        const diff=table(["配置项","当前生效","此版本"],item.fields,f=>'<tr><td>'+esc(f.label)+'</td><td>'+esc(fieldValue(f,current[f.key]))+
          '</td><td>'+esc(fieldValue(f,v.definition[f.key]))+'</td></tr>');
        if(v.status==="draft") {
          dialog('核对草稿 V'+v.version_no,diff+'<p>发布后新业务采用此版本。已生成结果不重算。</p>'+details([["原因",v.change_reason]]),{
            wide:true,submit:"发布此版本",onSubmit:canPublish ? async(_,key)=>api('/company-rules/versions/'+v.id+'/publish',{
              method:"POST",key,body:{revision:v.revision_no}}) : null,
            footer:item.code.startsWith('agent_execution.') && !canPublish ? '' : '<button type="button" id="edit-draft">继续编辑</button>',
            afterRender:d=>{const b=d.querySelector('#edit-draft'); if(b) b.onclick=()=>edit(item,v);},
          });
        } else {
          dialog('历史内容 V'+v.version_no,diff+field("恢复原因","reason","",{required:true}),{
            wide:true,submit:"恢复为新草稿",onSubmit:(item.code.startsWith("agent_execution.") && !canPublish) ? null : async(form,key)=>api('/company-rules/versions/'+v.id+'/restore',{
              method:"POST",key,body:{base_id:item.current.id,reason:form.get('reason')}}),
          });
        }
      }),
    });
}
// Only the verified middle-platform configuration route can leave this console.
// Agent identifiers are navigation metadata; they never grant platform access.
export function agentConfigurationUrl(agent) {
  if (!agent?.bound || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(agent.agent_id || '')) return '';
  try {
    const url = new URL(agent.configuration_url);
    return url.protocol === 'https:' && url.hostname === '123.207.235.181' && !url.port && !url.username && !url.password &&
      !url.search && !url.hash && url.pathname === '/agents/'+agent.agent_id+'/configure' ? url.href : '';
  } catch { return ''; }
}
export function ruleManagement(item) {
  const management=item.management;
  if (!management) return '<p class="help">当前公司关联信息未核验。</p>';
  const agents=management.related_agents || [];
  const status=management.validation_status || '未核验';
  const tone=status==='绑定能力不匹配' ? 'red' : ['已加载绑定','程序计算'].includes(status) ? 'blue' : 'amber';
  return '<section class="rule-management"><div class="rule-management-heading"><div><span class="rule-kicker">'+esc(management.scope || '当前公司')+'</span><strong>'+esc(management.executor || '执行职责未核验')+'</strong></div><span class="badge '+tone+'">'+(agents.length ? '绑定配置：' : '')+esc(status)+'</span></div>'+
    (management.validation_note ? '<p class="help">'+esc(management.validation_note)+'</p>' : '')+
    agents.map(agent=>{
      const url=agentConfigurationUrl(agent);
      return '<div class="rule-agent"><div class="rule-agent-heading"><div><strong>'+esc(agent.capability_label || '关联能力')+'</strong><small>'+esc(agent.bound ? (agent.agent_name || '智能体名称未核验') : '当前公司未绑定中台')+'</small></div>'+
        '<div class="rule-agent-actions">'+
        (state.actor?.role==='administrator' ? '<button type="button" class="rule-connectivity-button" data-connectivity-kind="agent" data-connectivity-target="'+esc(agent.capability)+'" data-connectivity-label="'+esc(agent.capability_label)+'">测试连通性</button>' : '')+
        (url ? '<a class="rule-platform-link" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">前往中台配置 <span aria-hidden="true">↗</span></a>' : '<span class="rule-link-unavailable">'+(agent.bound ? '中台入口未核验' : '绑定后可前往中台')+'</span>')+'</div></div>'+
        (agent.bound ? '<details class="rule-binding"><summary>查看绑定与预期版本</summary>'+details([['智能体编号',agent.agent_id || '未记录'],['预期发布快照',agent.expected_snapshot_id || '未记录']])+'</details>' : '')+'</div>';
    }).join('')+(agents.length ? '<div class="rule-management-footer"><a class="rule-platform-link" href="#agentRuns">查看运行审计 ›</a></div>' : '')+'</section>';
}
function businessRulePanel(item) {
  const rule=item.business_rule;
  if (!rule) return '';
  return '<section class="rule-business"><div class="rule-business-heading"><div><h3>业务指引</h3><span class="rule-kicker">'+esc(rule.current.source==='baseline' ? '沿用能力基础规则' : '公司 V'+rule.current.version)+'</span></div><div class="actions"><button type="button" data-action="edit-business" data-id="'+esc(item.code)+'">编辑业务指引</button><button type="button" class="link" data-action="business-versions" data-id="'+esc(item.code)+'">版本与回退（'+rule.versions.length+'）</button></div></div>'+
    '<details class="rule-binding"><summary>查看已发布指引</summary>'+policyDetails(rule,rule.current.definition)+'</details></section>';
}
function managementOverview(data, technical) {
  const management=data.management || {};
  return '<section class="card rule-overview"><div><span class="rule-kicker">当前公司</span><strong>'+esc(state.company?.name || state.company?.display_name || '当前选择公司')+'</strong><p>业务规则按公司发布，每次调用携带生效版本。</p></div><div class="rule-overview-counts"><a href="#companyRules"><strong>'+esc(management.rule_count ?? (technical ? '—' : data.items.length))+'</strong><span>公司规则</span></a><a href="#agentExecution"><strong>'+esc(management.capability_count ?? (technical ? data.items.length : '—'))+'</strong><span>业务能力</span></a></div><p class="help">规则与能力按职责关联，数量无需一一对应；中台旧版本与验收副本不计入业务能力数。</p></section>';
}
export const companyRules = () => renderRules(false);
export const agentExecution = () => renderRules(true);
async function renderRules(technical) {
  const data=await api(technical ? '/ai/execution' : '/company-rules');
  if (!technical && data.items.some(i=>i.code === 'fde_capabilities')) state.org=await api('/organization');
  return {html:head(technical ? "Agent 运行配置" : "公司规则配置",technical ? "按能力维护业务指引，核对当前公司绑定与运行配置。" : "统一维护业务标准。草稿经核对发布后生效，历史结果保留当时版本。")+
    managementOverview(data,technical)+
    '<div class="notice">'+(technical ? '运营可编辑业务指引草稿；系统管理员发布并维护运行配置。固定返回契约和权限优先于公司规则与补充指引。中台入口用于技术配置，真实调用结果请核对运行审计。' : '运营可编辑草稿；系统管理员发布。结构化标准由系统执行，业务判断由关联能力执行；固定返回格式与权限在中台和后端共同校验。')+'</div>'+
    '<div class="card rule-search"><label>查找配置 <input type="search" id="rule-search" placeholder="输入规则名称或配置项" value="'+esc(state.filters.ruleSearch||'')+'"></label><span id="rule-count" role="status"></span></div><p id="rule-empty" class="empty" hidden>没有匹配的配置，请调整关键词。</p>'+
    data.items.map(item=>'<section class="card rule-card" data-rule-card><div class="toolbar"><div class="rule-card-title"><h2>'+esc(item.label)+'</h2><span>'+esc(item.current.source==="baseline" ? "现有基线" : '公司 V'+item.current.version)+'</span></div><div class="actions">'+
      (technical && !data.can_publish ? '' : '<button data-action="edit" data-id="'+item.code+'">'+(technical ? '编辑运行配置' : '新建草稿')+'</button>')+'<button class="link" data-action="versions" data-id="'+item.code+'">版本与回退（'+item.versions.length+'）</button></div></div>'+
      '<div class="dialog-body">'+ruleManagement(item)+businessRulePanel(item)+(technical ? '<details class="rule-binding"><summary>查看运行配置</summary>'+policyDetails(item,item.current.definition)+'</details>' : policyDetails(item,item.current.definition))+
      (item.runtime ? '<details class="rule-binding"><summary>查看接口进程配置</summary><p class="help">以下仅为接口进程已加载的配置。后台任务由任务进程执行，实际绑定、预算与调用结果以运行审计为准。</p>'+details([['接口进程中台绑定',item.runtime.configuration_ready ? '接口进程已加载' : '未加载；不能据此判断任务进程'],['智能体编号',item.runtime.agent_id || '未加载'],['预期发布快照',item.runtime.expected_snapshot_id || '未加载'],['接口进程原接口凭据',item.runtime.original_api_configured ? '已配置' : '未配置'],['接口进程中台 / 总预算',item.runtime.platform_seconds+' / '+item.runtime.total_seconds+' 秒']])+'</details>' : '')+'</div></section>').join(''),
    bind(root){
      if(state.actor?.role==='administrator') bindConnectivityTests(root);
      const input=root.querySelector('#rule-search');
      const filterRules=()=>{const q=input.value.trim().toLowerCase();state.filters.ruleSearch=input.value;let visible=0;root.querySelectorAll('[data-rule-card]').forEach(card=>{card.hidden=!!q&&!card.textContent.toLowerCase().includes(q);if(!card.hidden)visible++;});root.querySelector('#rule-count').textContent=`${visible} / ${data.items.length} 项配置`;root.querySelector('#rule-empty').hidden=visible>0;};
      if(input){input.oninput=filterRules;filterRules();}
      bindCommon(root,{edit:code=>edit(data.items.find(i=>i.code===code)),versions:code=>versions(data.items.find(i=>i.code===code),data.can_publish),
        'edit-business':code=>edit(data.items.find(i=>i.code===code).business_rule),
        'business-versions':code=>versions(data.items.find(i=>i.code===code).business_rule,data.can_publish)});
    },
  };
}
