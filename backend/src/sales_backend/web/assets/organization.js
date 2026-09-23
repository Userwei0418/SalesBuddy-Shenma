import {state, $, esc, roleLabel, num, date, badge, options, roleOptions, api, toast, field, dialog, table, pager, stats, head, filters, search, bindCommon, details} from "./core.js";
import {companyCards, bindCompanies} from './companies.js';
const btn = (label,action,id='',style='') => `<button type="button" class="${style}" data-action="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
const select = (name,opts) => `<select name="${name}" aria-label="${name}">${opts}</select>`;
async function ensureOrg() { const [org, policy] = await Promise.all([api('/organization'), api('/password-policy')]); state.org = {...org, password_policy: policy}; }
const passwordRequirement = () => state.org.password_policy.require_initial_change ? '首次登录或重置密码后需要修改初始密码。' : '首次登录改密要求已关闭，可以直接使用初始或重置密码登录。';
function passwordPolicyCard() {
  const enabled=state.org.password_policy.require_initial_change;
  return `<section class="card"><div class="card-head"><h2>登录与密码</h2><span class="badge ${enabled?'amber':'green'}">${enabled?'已开启':'已关闭'}</span></div><div class="card-pad"><p>首次登录需要修改密码</p><p class="help">${passwordRequirement()} 仅对当前公司生效；重新开启后，仍使用初始或重置密码的账号需要改密，已自行修改密码的账号不受影响。</p>${state.actor.role==='administrator'?btn(enabled?'关闭改密要求':'开启改密要求','password-policy','','primary'):'<p class="help">由系统管理员维护此开关。</p>'}</div></section>`;
}
const kinds = [['sales','区域销售'],['product_sales','产品销售'],['fde','FDE'],['general','综合部门']];
const departmentRole = (role, team) => role === 'supervisor' && team?.kind === 'product_sales' ? '产品销售主管' : roleLabel(role);
export const membershipsOf = row => row.memberships?.length ? row.memberships : row.team_id ? [{team_id:row.team_id,team_name:row.team_name,is_primary:true,roles:row.roles || []}] : [];
const canMaintainAccount = (row) =>
  !row.platform_managed && (state.actor.role === "administrator" ||
  !row.roles.some((role) => ["operations", "administrator"].includes(role)));

function accountLoginRestriction(row) {
  if (!row.login_locked) return '<span class="badge green">未受限</span>';
  const retryAt = row.login_retry_at
    ? new Date(row.login_retry_at).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "";
  return `<span class="badge amber">登录受限</span>${retryAt ? `<small>预计 ${esc(retryAt)} 解除（北京时间）</small>` : ""}`;
}

function unlockAccountLogin(row) {
  if (!row || !canMaintainAccount(row))
    throw Error("此账号需由系统管理员维护");
  if (!row.login_locked) throw Error("此账号当前未受限，请刷新后核对");
  dialog(
    "解除账号限制",
    details([
      ["账号名", row.account_code],
      ["姓名", row.display_name],
    ]) +
      '<div class="notice amber">仅解除此账号的登录限制，当前网络的登录保护保持不变。请填写原因，操作将记入审计记录。</div>' +
      field("解除原因", "reason", "", {
        type: "textarea",
        required: true,
        help: "1–500 字，请说明已核实的情况。",
      }),
    {
      submit: "确认解除",
      onSubmit: async (fd, key) => {
        const reason = String(fd.get("reason") || "").trim();
        if (!reason) throw Error("请填写解除原因");
        if (reason.length > 500) throw Error("解除原因请控制在 500 字以内");
        await api(`/accounts/${row.id}/unlock-login`, {
          method: "POST",
          key,
          body: { version_no: row.version_no, reason },
        });
        await ensureOrg();
        toast("账号登录限制已解除");
      },
    },
  );
}

function appointmentRow(member, index, assignable) {
  const team = state.org.departments.find(t=>t.id===member.team_id);
  return `<section class="appointment" data-appointment="${index}"><div class="appointment-head">${field('任职部门',`membership:${index}:team`,'',{required:true,select:options(state.org.departments.filter(t=>t.status==='active'||t.id===member.team_id).map(t=>[t.id,t.name]),member.team_id,'请选择部门')})}<button type="button" class="link danger" data-remove-appointment="${index}">移除任职</button></div><fieldset><legend>在此部门的岗位</legend><div class="checks">${assignable.map(r=>`<label class="check"><input type="checkbox" name="membership:${index}:roles" value="${esc(r.code)}" ${member.roles?.includes(r.code)?'checked':''}><span data-role-label="${r.code}">${esc(departmentRole(r.code,team))}</span></label>`).join('')}</div></fieldset><label class="check acting-check"><input type="checkbox" name="membership:${index}:acting" ${member.acting?'checked':''}>代管此部门</label></section>`;
}
export function accountPayload(form, row={}) {
  const memberships=[];
  for(const [name, value] of form.entries()) {
    if(!/^membership:\d+:team$/.test(name)) continue;
    const index=name.split(':')[1];
    memberships.push({team_id:value,roles:form.getAll(`membership:${index}:roles`),acting:form.has(`membership:${index}:acting`)});
  }
  const company_roles=form.getAll('company_roles');
  if(!memberships.length && !company_roles.length) throw Error('请选择公司管理权限或添加业务部门任职');
  if(memberships.some(m=>!m.team_id||!m.roles.length)) throw Error('每个任职部门都需要选择部门和至少一个岗位');
  if(new Set(memberships.map(m=>m.team_id)).size!==memberships.length) throw Error('同一部门请在一条任职中选择多个岗位');
  const team_id=memberships.length?form.get('team_id'):null;
  if(memberships.length && !memberships.some(m=>m.team_id===team_id)) throw Error('请选择任职部门中的一个作为主部门');
  const data={...(form.has('organization_team_id')?{organization_team_id:form.get('organization_team_id')||null}:{}),...(form.has('email')?{email:String(form.get('email')||'').trim()||null}:{}),...(form.has('phone_number')?{phone_number:String(form.get('phone_number')||'').trim()||null}:{}),display_name:form.get('display_name'),team_id,memberships,company_roles,roles:[...new Set([...memberships.flatMap(m=>m.roles),...company_roles])]};
  return row.id ? {...data,account_code:form.get('account_code') || row.account_code,status:form.get('status'),version_no:row.version_no} : {...data,account_code:form.get('account_code'),temporary_password:form.get('temporary_password')};
}
function accountForm(row = {}) {
  const editing=!!row.id;
  const assignable=(state.org.roles||[]).filter(r=>r.assignable || row.roles?.includes(r.code));
  const current=membershipsOf(row);
  const initial=current;
  const departmentAssignable=assignable.filter(r=>!['operations','administrator'].includes(r.code)||current.some(m=>m.roles.includes(r.code)));
  const companyPermissions=`<fieldset><legend>公司管理权限</legend><p class="help">管理本公司运营端，不需要分配业务部门，也不因此进入销售或 FDE 人员统计。</p><div class="checks">${assignable.filter(r=>['operations','administrator'].includes(r.code)).map(r=>`<label class="check"><input type="checkbox" name="company_roles" value="${esc(r.code)}" ${row.company_roles?.includes(r.code)?'checked':''}>${esc(roleLabel(r.code))}</label>`).join('')}</div></fieldset>`;
  dialog(editing?'编辑成员 · '+row.display_name:'开通成员账号',
    `<p class="notice">${editing?'岗位或资料修改后，此账号需要重新登录。':passwordRequirement()}</p><div class="form-grid">${field('账号名','account_code',row.account_code||'',{required:true,help:'3–64位字母、数字、点、下划线或短横线，不区分大小写，修改后旧账号名失效，原密码不变'})}${field('姓名','display_name',row.display_name,{required:true})}${field('手机号（选填）','phone_number',row.phone_number||'',{help:'11位手机号；填写后可用手机号和原密码登录。留空仅使用账号名，不发送验证码。'})}${editing?field('账号状态','status','',{select:options([['active','启用'],['inactive','停用']],row.status,null)}):field('初始密码','temporary_password','',{type:'password',required:true,help:'8–128 个字符，不限制字符组合'})}</div>${field('组织归属部门','organization_team_id','',{select:options(state.org.departments.filter(t=>t.status==='active'||t.id===row.organization_team_id).map(t=>[t.id,t.name]),row.organization_team_id,'未单独设置'),help:'只记录组织归属，不授予岗位权限，也不计入业务人员统计。'})}${companyPermissions}<div class="section-title">业务部门与任职</div><p class="help">支持兼任多个部门；主管权限仅作用于设为主管的部门。主部门用于默认业务归属，每份业务只计算一次。</p><div id="appointments">${initial.map((m,i)=>appointmentRow(m,i,departmentAssignable)).join('')}</div><button type="button" id="add-appointment">＋ 添加任职部门</button>${field('主部门','team_id','',{required:initial.length>0,select:options(initial.filter(m=>m.team_id).map(m=>[m.team_id,state.org.departments.find(t=>t.id===m.team_id)?.name||m.team_name]),row.team_id,'请选择主部门')})}`,
    {wide:true,submit:editing?'保存成员':'确认开通',onSubmit:async(form,key)=>{
      await api('/accounts'+(editing?'/'+row.id:''),{method:editing?'PUT':'POST',body:accountPayload(form,row),key});
      await ensureOrg(); toast(editing?'成员资料已更新':'成员账号已开通');
    },afterRender:root=>{
      let index=initial.length;
      const refresh=()=>{
        const primary=root.querySelector('[name="team_id"]'),before=primary.value;
        const selected=[...root.querySelectorAll('[name^="membership:"][name$=":team"]')].map(input=>input.value).filter(Boolean);
        primary.innerHTML=options([...new Set(selected)].map(id=>[id,state.org.departments.find(t=>t.id===id)?.name||id]),before,'请选择主部门');
        primary.required=root.querySelectorAll('[data-appointment]').length>0;
        primary.disabled=!primary.required;
        if(selected.length===1) primary.value=selected[0];
        root.querySelectorAll('[data-appointment]').forEach(section=>{
          const id=section.querySelector('select').value,team=state.org.departments.find(t=>t.id===id);
          section.querySelectorAll('[data-role-label]').forEach(label=>{label.textContent=departmentRole(label.dataset.roleLabel,team);});
        });
      };
      root.querySelector('#add-appointment').onclick=()=>{
        if(root.querySelectorAll('[data-appointment]').length>=20) return toast('最多添加 20 个任职部门');
        root.querySelector('#appointments').insertAdjacentHTML('beforeend',appointmentRow({roles:[]},index++,departmentAssignable));refresh();root.querySelector('form').dispatchEvent(new Event('change'));
      };
      refresh();
      root.querySelector('#appointments').addEventListener('change',refresh);
      root.querySelector('#appointments').addEventListener('click',event=>{
        const button=event.target.closest('[data-remove-appointment]');
        if(button) {button.closest('[data-appointment]').remove();refresh();root.querySelector('form').dispatchEvent(new Event('change'));}
      });
    }});
}
function departmentForm(row = {}) {
  dialog(
    row.id ? "编辑部门" : "新增部门",
    `<div class="form-grid">${field("部门编码", "code", row.code, { required: true })}${field("部门名称", "name", row.name, { required: true })}${field("部门类型", "kind", "", {select:options(kinds,row.kind || "general",null)})}${field(
      "上级部门",
      "parent_team_id",
      "",
      {
        select: options(
          state.org.departments
            .filter((t) => t.id !== row.id && t.status === "active")
            .map((t) => [t.id, t.name]),
          row.parent_team_id,
          "无上级部门",
        ),
      },
    )}${field("状态", "status", "", {
      select: options(
        [
          ["active", "启用"],
          ["inactive", "停用"],
        ],
        row.status || "active",
        null,
      ),
    })}</div>`,
    {
      onSubmit: async (fd, key) => {
        const data = Object.fromEntries(fd);
        data.parent_team_id ||= null;
        if (row.id) data.version_no = row.version_no;
        await api("/departments" + (row.id ? "/" + row.id : ""), {
          method: row.id ? "PUT" : "POST",
          key,
          body: data,
        });
        await ensureOrg();
        toast("部门已保存");
      },
    },
  );
}
export async function accounts() {
  await ensureOrg();
  if(state.company) state.companies=(await api("/companies")).items;
  let rows = state.org.accounts;
  const f = state.filters;
  rows = rows.filter(
    (r) =>
      (!f.q ||
        `${r.display_name} ${r.account_code} ${r.phone_number||""}`
          .toLowerCase()
          .includes(f.q.toLowerCase())) &&
      (!f.team || r.organization_team_id===f.team || membershipsOf(r).some(m=>m.team_id===f.team)) &&
      (!f.role || r.roles.includes(f.role)) &&
      (!f.status || r.status === f.status),
  );
  const visible = rows.slice(state.offset, state.offset + 50);
  return {
    html:
      head(
        "账号与组织",
        "按部门维护任职关系，统一管理账号、主管和登录状态。",
        btn("新增部门", "department") +
          btn("＋ 开通账号", "create", "", "primary"),
      ) +
      passwordPolicyCard() +
      stats([
        ["账号总数", num(state.org.accounts.length), "当前公司"],
        [
          "启用账号",
          num(state.org.accounts.filter((u) => u.status === "active").length),
          "登录限制请查看账号列表",
        ],
        [
          "有效部门",
          num(
            state.org.departments.filter((t) => t.status === "active").length,
          ),
          "支持跨部门兼任",
        ],
        ["多部门成员", num(state.org.accounts.filter(u=>membershipsOf(u).length>1).length), "岗位分别授权，业务不重复计数"],
      ]) +
      `<div class="view-tabs" role="tablist" aria-label="组织管理视图"><button role="tab" data-org-tab="members" aria-selected="${!f.view||f.view==='members'}">成员账号</button><button role="tab" data-org-tab="departments" aria-selected="${f.view==='departments'}">部门与负责人</button><button role="tab" data-org-tab="companies" aria-selected="${f.view==='companies'}">公司管理</button></div>${f.view==='companies'?companyCards():''}<div class="card" ${f.view&&f.view!=='members'?'hidden':''}>` +
      filters(
        search("搜索姓名 / 账号名 / 手机号") +
          select(
            "team",
            options(
              state.org.departments.map((t) => [t.id, t.name]),
              f.team,
              "全部部门",
            ),
          ) +
          select("role", roleOptions(f.role)) +
          select(
            "status",
            options(
              [
                ["active", "启用"],
                ["inactive", "停用"],
              ],
              f.status,
              "全部状态",
            ),
          ),
      ) +
      table(
        ["成员", "部门", "角色", "状态", "登录方式", "登录限制", "操作"],
        visible,
        (r) =>
          `<tr><td class="title">${esc(r.display_name)}<small>${esc(r.account_code)}</small>${r.phone_number?`<small>${esc(r.phone_number)}</small>`:""}</td><td>${r.organization_team_id?`<div class="member-team">${esc(r.organization_team_name || state.org.departments.find(t=>t.id===r.organization_team_id)?.name || "未找到部门")}<span class="mini-label">组织归属</span></div>`:""}${membershipsOf(r).map(m=>`<div class="member-team">${esc(m.team_name || state.org.departments.find(t=>t.id===m.team_id)?.name)}${m.acting?'<span class="mini-label">代管</span>':''}${m.is_primary?'<span class="mini-label">主部门</span>':''}</div>`).join('') || (r.organization_team_id?'':r.company_roles?.length?'公司级管理（无业务任职）':'未分配')}</td><td>${(r.company_roles||[]).map(x=>`<span class="pill">${esc(roleLabel(x))} · 公司</span>`).join('')}${membershipsOf(r).map(m=>`<div class="member-team">${m.roles.map(x=>`<span class="pill">${esc(departmentRole(x,m))}</span>`).join('')}</div>`).join('')}</td><td>${badge(r.status)}</td><td>${r.platform_managed ? "平台管理授权" : r.has_password ? (r.phone_number ? "账号名 / 手机号 + 密码" : "账号名 + 密码") : "待设置密码"}</td><td>${accountLoginRestriction(r)}</td><td>${canMaintainAccount(r) ? btn("编辑", "edit", r.id, "link") + btn(r.has_password ? "重置密码" : "设置密码", "password", r.id, "link") + (r.login_locked ? btn("解除账号限制", "unlock-login", r.id, "link") : "") : r.platform_managed ? "平台管理身份" : "由系统管理员维护"}</td></tr>`,
      ) +
      pager(rows.length) +
      `</div><div class="card" ${f.view!=='departments'?'hidden':''}><div class="card-head"><h2>部门管理</h2><small>停用前需迁移有效成员和子部门</small></div>` +
      table(
        ["部门 / 类型", "负责人", "成员 / 代管", "上级部门", "状态", "操作"],
        state.org.departments,
        (t) =>
          `<tr><td><strong>${esc(t.name)}</strong><small>${esc(kinds.find(k=>k[0]===t.kind)?.[1] || '综合部门')} · ${esc(t.code)}</small></td><td>${state.org.accounts.filter(u=>u.status==='active'&&membershipsOf(u).some(m=>m.team_id===t.id&&m.roles.some(r=>['supervisor','fde_lead','manager'].includes(r)))).map(u=>esc(u.display_name)+(membershipsOf(u).find(m=>m.team_id===t.id)?.acting?'（代管）':'')).join('、') || '暂未设置'}</td><td>${state.org.accounts.filter(u=>u.status==='active'&&(u.organization_team_id===t.id||membershipsOf(u).some(m=>m.team_id===t.id&&!m.acting))).length} 人 / ${state.org.accounts.filter(u=>u.status==='active'&&membershipsOf(u).some(m=>m.team_id===t.id&&m.acting)).length} 代管</td><td>${esc(state.org.departments.find((x) => x.id === t.parent_team_id)?.name || "—")}</td><td>${badge(t.status)}</td><td>${btn("编辑", "edit-department", t.id, "link")}</td></tr>`,
      ) +
      `</div>`,
    bind: (root) => {
      bindCompanies(root);
      root.querySelectorAll('[data-org-tab]').forEach(button=>button.onclick=()=>{state.filters.view=button.dataset.orgTab;state.offset=0;state.refresh();});
      bindCommon(root, {
        "password-policy": () => {
          const current=state.org.password_policy, next=!current.require_initial_change;
          dialog(next?'开启首次登录改密':'关闭首次登录改密',
            `<p>${next?'仍使用初始或重置密码的账号将被要求修改密码。':'账号可直接使用初始或重置密码登录。'}</p><p class="help">仅对当前公司生效，可随时调整；账号密码本身不会被修改。</p>`,
            {submit:next?'确认开启':'确认关闭',onSubmit:async(fd,key)=>{
              await api('/password-policy',{method:'PUT',key,body:{version_no:current.version_no,require_initial_change:next}});
              await ensureOrg();toast('登录策略已更新');
            }});
        },
        create: () => accountForm(),
        edit: (id) => accountForm(state.org.accounts.find((r) => r.id === id)),
        "unlock-login": (id) =>
          unlockAccountLogin(state.org.accounts.find((r) => r.id === id)),
        department: () => departmentForm(),
        "edit-department": (id) =>
          departmentForm(state.org.departments.find((t) => t.id === id)),
        password: (id) => {
          const u = state.org.accounts.find((x) => x.id === id);
          dialog(
            "重置账号密码",
            details([
              ["账号", u.account_code],
              ["姓名", u.display_name],
            ]) +
              `<div class="notice amber">保存后该账号现有会话失效。${passwordRequirement()} 请通过安全方式交付初始密码。</div>` +
              field("新的初始密码", "temporary_password", "", {
                type: "password",
                required: true,
                help: "8–128 个字符，不限制字符组合。",
              }),
            {
              submit: "确认重置",
              onSubmit: async (fd, key) => {
                await api(`/accounts/${id}/reset-password`, {
                  method: "POST",
                  key,
                  body: {
                    temporary_password: fd.get("temporary_password"),
                    version_no: u.version_no,
                  },
                });
                await ensureOrg();
                toast("密码已重置");
              },
            },
          );
        },
      });
    },
  };
}
