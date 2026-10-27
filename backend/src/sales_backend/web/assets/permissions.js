import {state, esc, api, options, table, head, field, dialog,dialogIntent, date, badge} from './core.js';

export const scopeNames = {departments:'当前有效任职部门', inherit:'继承账号授权范围', self:'本人负责', assigned:'本人参与', teams:'指定团队', workspace:'本公司全部'};
const actionButton = (label, action, id='') => `<button type="button" class="link" data-permission-action="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
const expiryInput=(name,value)=>field('到期时间（留空长期有效）',name,value?new Date(new Date(value).getTime()-new Date(value).getTimezoneOffset()*60000).toISOString().slice(0,16):'',{type:'datetime-local'});
const expiryValue=(form,name)=>form.get(name)?new Date(form.get(name)).toISOString():null;
const can = code => state.permissions?.[code] === true;

function teamChoices(teams, ids=[], name='') {
  return `<select multiple name="${esc(name)}" aria-label="指定授权团队" size="3">${teams.map(team=>
    `<option value="${esc(team.id)}" ${ids.includes(team.id)?'selected':''}>${esc(team.name)}</option>`).join('')}</select>`;
}
function scopeChoices(scopes, value='self', name='') {
  return `<select name="${esc(name)}" aria-label="数据范围">${options(scopes.map(s=>[s,scopeNames[s]]),value,null)}</select>`;
}
export function permissionRows(catalog, existing, teams, account=false,explicit=false) {
  const groups = new Map();
  catalog.forEach(item=>{if(!groups.has(item.module))groups.set(item.module,[]);groups.get(item.module).push(item);});
  return `<label class="field">查找功能<input type="search" data-permission-search placeholder="输入功能名称"></label>`+
    [...groups].map(([module, items])=>`<details class="permission-group" open><summary>${esc(module)}</summary>${items.map(item=>{
      const current=existing.find(p=>p.permission===item.code);
      const fixed=item.scopes.length===1;
      const scope=current?.scope && current.scope!=='inherit' ? current.scope : account || explicit || fixed ? item.scopes[0] : 'inherit';
      const legacy=!account && !explicit && fixed && current && (!current.scope || current.scope==='inherit');
      return `<div class="permission-row" data-permission="${esc(item.code)}" data-label="${esc(item.label)}">
        <label>${account ? `<select name="effect:${esc(item.code)}" aria-label="${esc(item.label)}的账号设置">${options([['','随角色'],['allow','额外允许'],['deny','禁用']],current?.effect || '',null)}</select>` :
          `<input type="checkbox" name="enabled:${esc(item.code)}" ${current?'checked':''}>`}<span>${esc(item.label)}${item.sensitive?' · 敏感操作':''}</span></label>
        <div data-scope-controls>${scopeChoices(account ? item.scopes.filter(scope=>scope!=='departments') : explicit || fixed ? item.scopes : ['inherit',...item.scopes],scope,`scope:${item.code}`)}
          <div data-team-controls>${teamChoices(teams,current?.team_ids || [],`teams:${item.code}`)}</div></div>${legacy?'<small class="notice">旧配置为继承范围；请确认此功能的固定范围并保存后生效，当前授权不会自动改变。</small>':''}${account?expiryInput(`expires:${item.code}`,current?.expires_at):''}
        </div>`;
    }).join('')}</details>`).join('');
}
export function bindPermissionEditor(dialogRoot, account=false) {
  // The dialog is reused; bind to its new form so prior role/account listeners
  // cannot run against the next editor's different controls.
  const root=dialogRoot.querySelector('#dialog-form');
  const update=()=>root.querySelectorAll('[data-permission]').forEach(row=>{
    const code=row.dataset.permission;
    const enabled=account ? row.querySelector(`[name="effect:${code}"]`).value==='allow' : row.querySelector('input[type=checkbox]').checked;
    const controls=row.querySelector('[data-scope-controls]');
    controls.hidden=!enabled;
    const teams=controls.querySelector('[data-team-controls]');
    teams.hidden=controls.querySelector('select').value!=='teams';
  });
  root.addEventListener('change',update);update();
  root.querySelector('[data-permission-search]').oninput=event=>{
    const query=event.target.value.trim().toLowerCase();
    root.querySelectorAll('[data-permission]').forEach(row=>row.hidden=!row.dataset.label.toLowerCase().includes(query));
    root.querySelectorAll('.permission-group').forEach(group=>{
      group.hidden=![...group.querySelectorAll('[data-permission]')].some(row=>!row.hidden);
      if(query)group.open=true;
    });
  };
  root.querySelectorAll('[data-role-assignment]').forEach(row=>{
    const updateRole=()=>{
      row.querySelector('[data-role-scope]').hidden=!row.querySelector('input[type=checkbox]').checked;
      row.querySelector('[data-role-teams]').hidden=row.querySelector('select').value!=='teams';
    };
    row.addEventListener('change',updateRole);updateRole();
  });
}
export function collectPermissions(form,catalog,account=false) {
  return catalog.flatMap(item=>{
    const effect=account ? form.get(`effect:${item.code}`) : form.has(`enabled:${item.code}`) ? 'allow' : '';
    if(!effect)return [];
    if(effect==='deny')return [{permission:item.code,effect:'deny',scope:'inherit',team_ids:[],expires_at:expiryValue(form,`expires:${item.code}`)}];
    let scope=form.get(`scope:${item.code}`);
    if(scope==='inherit' && !account && item.scopes.length===1)scope=item.scopes[0];
    if(!item.scopes.includes(scope) && !(scope==='inherit' && !account))throw Error(`“${item.label}”不支持所选的数据范围`);
    const team_ids=scope==='teams' ? form.getAll(`teams:${item.code}`) : [];
    if(scope==='teams' && !team_ids.length)throw Error(`请为“${item.label}”选择授权团队`);
    return [{permission:item.code,scope,team_ids,...(account?{effect:'allow',expires_at:expiryValue(form,`expires:${item.code}`)}:{})}];
  });
}
function editRole(role,catalog,teams) {
  const editable=can('authorization.roles_manage');
  dialog(role.id?'编辑权限角色':'新增权限角色',
    '<p class="help">角色只决定功能和数据范围，不改变部门、业务任职或业绩统计身份。多个角色取并集，账号禁用优先。</p>'+
    field('角色名称','name',role.name || '',{required:true})+field('用途说明','description',role.description || '')+
    field('状态','status','',{select:options([['active','启用'],['inactive','停用']],role.status || 'active',null)})+
    expiryInput('expires_at',role.expires_at)+permissionRows(catalog,role.permissions || [],teams)+field('变更原因','reason','',{required:true,type:'textarea'}),
    {wide:true, onSubmit:editable?async(form,key)=>{
      await api('/permissions/roles'+(role.id?'/'+role.id:''),{method:role.id?'PUT':'POST',key,body:{
        name:form.get('name'),description:form.get('description'),status:form.get('status'),
        permissions:collectPermissions(form,catalog),expires_at:expiryValue(form,'expires_at'),version_no:role.id?role.version_no:null,reason:form.get('reason'),
      }});
    }:null,afterRender:root=>bindPermissionEditor(root)});
}
function grantLabel(grant,roles,teams) {
  const source=grant.source_code==='account_override'?'账号单独设置':roles.find(r=>r.id===grant.source_id)?.name || grant.source_code;
  const range=grant.scope_code==='teams' ? (grant.team_ids || []).map(id=>teams.find(t=>t.id===id)?.name || '已停用团队').join('、') : scopeNames[grant.scope_code] || '';
  return `${source} · ${grant.effect==='deny'?'账号禁用':range}`;
}
export const protectedActions=new Set(['authorization.accounts_manage','authorization.roles_manage','account.create','account.password_policy','organization.manage']);
export function cleanAssignment(row,role=false) {
  return {scope:row.scope,team_ids:row.team_ids || [],expires_at:row.expires_at || null,
    ...(role?{role_id:row.role_id}:{permission:row.permission,effect:row.effect})};
}
export function mergeEditableEntries(original,incoming,authority,role=false) {
  if(authority.company_administrator)return incoming;
  const key=role?'role_id':'permission';
  const immutable=original.filter(row=>row.delegation_user_ref_id!==authority.user_id);
  return [...immutable.map(row=>cleanAssignment(row,role)),...incoming.filter(row=>!immutable.some(old=>old[key]===row[key]))];
}
function editDelegation(user,authority,catalog,teams,users) {
 const current=authority.items.find(d=>d.user_ref_id===user.id) || {};
 const allowed=catalog.filter(p=>!protectedActions.has(p.code));
 dialog('委派授权上限 · '+user.name,
  '<p class="help">先配置上限，再在账号权限中授予相应管理功能。这里只允许管理选定的现有普通成员；开户、角色模板、公司安全设置和组织任职由公司管理员维护。变更上限会使此前委派出的授权失效，须重新确认。</p>'+
  field('启用','enabled','',{select:options([['true','启用'],['false','停用']],String(current.enabled!==false),null)})+
  expiryInput('expires_at',current.expires_at)+
  '<label class="field">可管理的成员<select name="recipient_ids" multiple size="8">'+users.filter(u=>u.id!==user.id&&u.id!==authority.user_id).map(u=>
    `<option value="${esc(u.id)}" ${(current.recipient_ids||[]).includes(u.id)?'selected':''}>${esc(u.name)} · ${esc(u.account_code)}</option>`).join('')+'</select></label>'+
  '<h3>可授予的功能及最大数据范围</h3>'+permissionRows(allowed,current.permissions||[],teams,false,true)+
  field('变更原因','reason','',{required:true,type:'textarea'}),
  {wide:true,afterRender:root=>bindPermissionEditor(root),onSubmit:async(form,key)=>{
    await api('/permissions/delegations/'+user.id,{method:'PUT',key,body:{
      version_no:current.version_no||0,enabled:form.get('enabled')==='true',expires_at:expiryValue(form,'expires_at'),
      recipient_ids:form.getAll('recipient_ids'),permissions:collectPermissions(form,allowed),reason:form.get('reason'),
    }});
  }});
}
async function editAccount(user,catalog,roles,teams,authority,isCurrent) {
  const current=await api('/permissions/accounts/'+user.id);
  if(!isCurrent())return;
  const assigned=current.roles || [];
  const delegated=!authority.company_administrator;
  const editable=can('authorization.accounts_manage') && (!delegated || authority.mine?.recipient_ids.includes(user.id));
  const lockedRoles=delegated?assigned.filter(r=>r.delegation_user_ref_id!==authority.user_id):[];
  const lockedOverrides=delegated?(current.overrides||[]).filter(r=>r.delegation_user_ref_id!==authority.user_id):[];
  const defaultExpiry=delegated?authority.mine?.expires_at:null;
  const content=`<p><strong>${esc(user.name)}</strong> · ${esc(user.account_code)}</p><p class="help">任职自动带入的角色继续生效。这里可以额外绑定权限角色，或单独增加、禁用功能。</p>
    ${delegated?'<p class="notice">只能修改本人委派的授权；其他管理者的设置保持原样。授权不能超过委派范围和期限。模板版本变化后须重新确认。</p>':'<p class="help">公司管理员保存时，将所列授权确认为公司直接授权；变更留审计记录。</p>'}
    <h3>额外绑定角色</h3>${roles.filter(role=>role.status==='active' || assigned.some(a=>a.role_id===role.id)).map(role=>{
      const selection=assigned.find(a=>a.role_id===role.id);
      return `<div class="permission-row" data-role-assignment="${esc(role.id)}"><label><input type="checkbox" name="role:${esc(role.id)}" ${selection?'checked':''}>${esc(role.name)}</label>
        <div data-role-scope>${scopeChoices(['self','assigned','teams','workspace'],selection?.scope || 'self',`role-scope:${role.id}`)}
        <div data-role-teams>${teamChoices(teams,selection?.team_ids || [],`role-teams:${role.id}`)}</div></div>${expiryInput(`role-expires:${role.id}`,selection?.expires_at || defaultExpiry)}</div>`;
    }).join('')}<h3>单账号功能调整</h3><p class="help">“随角色”保留全部角色的授权结果；“禁用”覆盖所有角色和单独授权。</p>
    ${permissionRows(catalog,current.overrides || [],teams,true)}
    <details><summary>查看保存前的实际权限与来源</summary>${table(['功能','当前结果','授权来源'],catalog.filter(p=>(current.grants || []).some(g=>g.permission_code===p.code)),p=>
      `<tr><td>${esc(p.label)}</td><td>${current.permissions[p.code]?'已允许':'已禁用'}</td><td>${(current.grants || []).filter(g=>g.permission_code===p.code).map(g=>`<small>${esc(grantLabel(g,roles,teams))}</small>`).join('')}</td></tr>`)}</details>
    ${field('变更原因','reason','',{required:true,type:'textarea'})}`;
  dialog('账号权限 · '+user.name,content,{wide:true,afterRender:root=>{
      bindPermissionEditor(root,true);
      if(delegated){
        root.querySelectorAll('[data-role-assignment]').forEach(row=>{if(lockedRoles.some(r=>r.role_id===row.dataset.roleAssignment))row.querySelectorAll('input,select').forEach(n=>n.disabled=true);});
        root.querySelectorAll('[data-permission]').forEach(row=>{
          const code=row.dataset.permission;
          if(lockedOverrides.some(r=>r.permission===code) || (!authority.mine?.permissions.some(p=>p.permission===code) && !(current.overrides||[]).some(r=>r.permission===code&&r.delegation_user_ref_id===authority.user_id)))row.querySelectorAll('input,select').forEach(n=>n.disabled=true);
          const expiry=row.querySelector(`[name="expires:${code}"]`);
          if(expiry&&!expiry.value&&defaultExpiry)expiry.value=new Date(new Date(defaultExpiry).getTime()-new Date(defaultExpiry).getTimezoneOffset()*60000).toISOString().slice(0,16);
        });
      }
    },
    onSubmit:editable?async(form,key)=>{
      const assignments=roles.filter(role=>form.has(`role:${role.id}`)).map(role=>{
        const scope=form.get(`role-scope:${role.id}`),team_ids=scope==='teams'?form.getAll(`role-teams:${role.id}`):[];
        if(scope==='teams'&&!team_ids.length)throw Error(`请为角色“${role.name}”选择授权团队`);
        return {role_id:role.id,scope,team_ids,expires_at:expiryValue(form,`role-expires:${role.id}`)};
      });
      await api('/permissions/accounts/'+user.id,{method:'PUT',key,body:{roles:mergeEditableEntries(assigned,assignments,authority,true),
        overrides:mergeEditableEntries(current.overrides||[],collectPermissions(form,catalog,true),authority),version_no:current.version_no,reason:form.get('reason')}});
    }:null});
}
export async function permissionsPage() {
  const [catalogResult,roleResult,directory,mine,moduleResult,authority]=await Promise.all([
    api('/permissions/catalog'),api('/permissions/roles'),api('/permissions/options'),api('/permissions/me'),api('/permissions/modules'),api('/permissions/delegations'),
  ]);
  state.permissions=mine.permissions;
  const catalog=catalogResult.permissions,roles=roleResult.roles,teams=directory.teams,users=directory.accounts;
  const audit=can('authorization.audit')?(await api('/permissions/audit')).entries:[];
  return {html:head('权限管理','按功能配置角色和账号权限，数据范围始终限定在本公司。',
    can('authorization.roles_manage')?actionButton('新增权限角色','new-role'):'')+
    `<div class="notice">公司已开通：${moduleResult.modules.filter(m=>m.enabled).map(m=>esc(m.label)).join('、')}。未开通或已到期模块的权限配置不会生效。</div>`+
    (authority.company_administrator?'<div class="notice">公司管理员可维护委派上限。角色模板、开户、部门任职和公司安全设置由公司管理员维护。</div>':`<div class="notice">${authority.mine?`当前委派：${authority.mine.recipient_ids.length} 个指定成员，${authority.mine.permissions.length} 项功能；${authority.mine.expires_at?'到期 '+date(authority.mine.expires_at):'长期有效'}`:'当前没有有效委派，仅可查看已获准的信息。'}</div>`)+
    '<div class="notice">多个有效角色的权限取并集；单账号禁用优先。授权不会改变业务岗位和业绩归属。</div>'+
    `<section class="card"><div class="card-head"><h2>角色模板</h2></div>${table(['角色','说明','状态','已授权功能','操作'],roles,r=>
      `<tr><td>${esc(r.name)}${r.builtin_role_code?'<small>按有效任职自动带入</small>':''}</td><td>${esc(r.description)}</td><td>${badge(r.status)}</td><td>${r.permissions.length}<small>${r.expires_at?'到期 '+date(r.expires_at):'长期有效'}</small></td><td>${actionButton(can('authorization.roles_manage')?'配置':'查看','role',r.id)}</td></tr>`)}</section>
    <section class="card"><div class="card-head"><h2>账号权限</h2><input type="search" data-account-search placeholder="查找姓名或账号名" aria-label="查找账号"></div>
    ${table(['账号','状态','操作'],users,u=>`<tr data-account-label="${esc((u.name+' '+u.account_code).toLowerCase())}"><td>${esc(u.name)}<small>${esc(u.account_code)}</small></td><td>${badge(u.status)}</td><td>${actionButton('查看与配置','account',u.id)}${authority.company_administrator&&can('authorization.accounts_manage')&&u.id!==authority.user_id?actionButton('委派上限','delegation',u.id):''}</td></tr>`)}</section>
    ${can('authorization.audit')?`<section class="card"><div class="card-head"><h2>最近变更记录</h2></div>${table(['时间','操作人','对象','原因'],audit,a=>`<tr><td>${date(a.created_at)}</td><td>${esc(users.find(u=>u.id===a.actor_user_ref_id)?.name || '历史账号')}</td><td>${esc(a.subject_type==='role' ? roles.find(r=>r.id===a.subject_id)?.name || '权限角色' : users.find(u=>u.id===a.subject_id)?.name || '账号')}</td><td>${esc(a.reason)}</td></tr>`)}</section>`:''}`,
    bind(root) {
      root.querySelector('[data-account-search]').oninput=event=>root.querySelectorAll('[data-account-label]').forEach(row=>row.hidden=!row.dataset.accountLabel.includes(event.target.value.trim().toLowerCase()));
      root.querySelectorAll('[data-permission-action]').forEach(button=>button.onclick=async()=>{
        const isCurrent=dialogIntent();
        button.disabled=true;
        try {
          if(button.dataset.permissionAction==='new-role')editRole({},catalog,teams);
          if(button.dataset.permissionAction==='role')editRole(roles.find(r=>r.id===button.dataset.id),catalog,teams);
          if(button.dataset.permissionAction==='account')await editAccount(users.find(u=>u.id===button.dataset.id),catalog,roles,teams,authority,isCurrent);
          if(button.dataset.permissionAction==='delegation')editDelegation(users.find(u=>u.id===button.dataset.id),authority,catalog,teams,users);
        } catch(error) { if(isCurrent())dialog('权限读取失败',`<div class="error-box">${esc(error.message)}</div>`); }
        finally {button.disabled=false;}
      });
    }};
}
