import {
  state,
  $,
  esc,
  roles,
  api,
  refreshToken,
  loginAccount,
  clearSession,
  logoutAccount,
  toast,
  field,
} from "./core.js";
import {
  customers,
  claims,
  opportunities,
  accounts,
  aiUsage,
  audit,
  systemLogs,
} from "./pages.js";
import {modelApis} from "./model-api.js";
import {businessActivities} from "./activity.js";
import {agentRuns} from "./agent-audit.js";
import {companyRules, agentExecution} from "./company-rules.js";
import {partners} from './partners.js';
import {taskInbox} from './tasks.js';
import {targets, targetRequests} from './targets.js';
import {loadCompanies, chooseCompany, companyLabel} from './companies.js';
import {feishuSync} from "./feishu-sync.js";
export const nav = [
  ["customers", "客户管理", "◈"],
  ["claims", "认领审批", "☷"],
  ["opportunities", "商机管理", "▥"],
  ["partners", "伙伴目录", "◇"],
  ["tasks", "我的待办", "☑"],
  ["targets", "目标管理", "◷"],
  ["target_requests", "目标修改审批", "✓"],
  ["accounts", "账号与组织", "♧"],
  ["ai", "AI 调用管理", "✧"],
  ["modelApis", "模型接口配置", "⚙"],
  ["agentRuns", "智能体运行审计", "◎"],
  ["agentExecution", "Agent 运行配置", "◉"],
  ["feishuSync", "飞书同步", "↗"],
  ["companyRules", "公司规则配置", "⚙"],
  ["activities", "业务操作记录", "≡"],
  ["audit", "技术审计日志", "⌘"],
  ["system", "系统运行日志", "⊞"],
];
const pages = {
  customers,
  claims,
  opportunities,
  partners,
  tasks: taskInbox,
  targets,
  target_requests: targetRequests,
  accounts,
  ai: aiUsage,
  modelApis,
  agentRuns,
  agentExecution,
  companyRules,
  feishuSync,
  audit,
  activities: businessActivities,
  system: systemLogs,
};
const groups=[['业务运营',['customers','claims','opportunities','partners','tasks']],['组织与目标',['accounts','targets','target_requests']],['智能体与规则',['agentRuns','ai','modelApis','agentExecution','companyRules','feishuSync']],['记录与诊断',['activities','audit','system']]];
const iconPaths={customers:'M3 5h18v14H3z M3 10h18 M8 5v14',claims:'M8 5h12v16H4V5h4 M8 3h8v4H8z M8 12l2 2 5-5',opportunities:'M4 20V9h4v11 M10 20V4h4v16 M16 20v-8h4v8',accounts:'M15 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M8 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M17 4a4 4 0 0 1 0 8 M23 21v-2a4 4 0 0 0-4-4',agentRuns:'M12 3a9 9 0 1 0 9 9 M12 7v5l4 2 M17 3h4v4',companyRules:'M4 7h16 M4 17h16 M9 4v6 M15 14v6',tasks:'M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5'};
const navIcon=id=>`<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${iconPaths[id]||'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z'}"/></svg>`;
let renderId = 0;
const pageMemory=new Map();
function navigate(page, {history=true}={}) {
  if(!pages[page]) return;
  pageMemory.set(state.page,{filters:{...state.filters},offset:state.offset});
  state.page=page;
  const saved=pageMemory.get(page);
  state.filters={...(saved?.filters||{})};state.offset=saved?.offset||0;
  if(history) window.location.hash=page;
  document.querySelectorAll('[data-nav]').forEach(button=>{const active=button.dataset.nav===page;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');});
  $('.shell')?.classList.remove('menu-open');
  $('.mobile-menu')?.setAttribute('aria-expanded','false');
  window.scrollTo({top:0});
  return state.refresh({reason:'navigation'});
}
state.refresh = async ({reason = "refresh"} = {}) => {
  const id = ++renderId;
  const page = state.page;
  const root = $("#content");
  if (!root) return;
  root.classList.add("loading");
  root.setAttribute('aria-busy','true');
  const status=$('#page-status');if(status)status.textContent='正在更新…';
  try {
    const view = await pages[page]({reason, isCurrent: () => id === renderId && state.page === page});
    if (id !== renderId) return;
    root.innerHTML = view.html;
    await view.bind?.(root);
    if(id===renderId && status) status.textContent="已更新 · "+new Date().toLocaleTimeString("zh-CN",{hour12:false});
    $(".crumb").textContent =
      "运营管理 / " + nav.find((n) => n[0] === state.page)[1];
  } catch (e) {
    if(id===renderId&&status)status.textContent='加载未完成，请重试';
    if (id === renderId)
      root.innerHTML = `<div class="error-box">${esc(e.message)}<p><button id="retry">重新加载</button></p></div>`;
    $("#retry")?.addEventListener("click", state.refresh);
  } finally {
    if (id === renderId) {root.classList.remove("loading");root.setAttribute('aria-busy','false');}
  }
};
function login(message = "") {
  clearSession();
  pageMemory.clear();
  renderId += 1;
  $("#dialog").close();
  $("#app").innerHTML =
    `<main class="login"><section class="login-art">
      <div class="brand"><img class="brand-logo" src="/admin/assets/brand-white.svg" alt="商汤销售小浣熊 Raccoon SalesBuddy"></div>
      <div class="login-story"><div class="eyebrow">RACCOON SALESBUDDY</div><h1>记录每一次沟通，<br>让 AI 助力销售增长。</h1><div class="story-divider"></div><p>从客户沟通到商机跟进，<br>用 AI 整理拜访要点，让销售行动更有依据。</p></div>
      <div class="login-process" aria-label="工作流程"><span><small>01</small>沟通记录</span><i aria-hidden="true">→</i><span><small>02</small>AI 整理</span><i aria-hidden="true">→</i><span><small>03</small>商机跟进</span></div>
      <footer><span>商汤销售小浣熊</span><span>Raccoon SalesBuddy</span></footer><img class="login-watermark" src="/admin/assets/brand-mark-white.svg" alt="" aria-hidden="true">
    </section><section class="login-form"><form id="login-form"><div class="form-accent" aria-hidden="true"></div><h2>登录管理工作台</h2><p class="muted">使用由管理员开通的账号登录</p>
      <label class="field"><b>账号</b><input name="account" type="text" autocomplete="username" placeholder="请输入账号名或手机号" required></label>
      <label class="field password-field"><b>密码</b><span class="password-input"><input name="password" id="login-password" type="password" autocomplete="current-password" placeholder="请输入密码" required><button type="button" class="password-toggle" id="toggle-password" aria-label="显示密码" aria-pressed="false"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg></button></span></label>
      <div id="login-error" role="alert">${message ? `<div class="error-box">${esc(message)}</div>` : ""}</div><button class="primary login-submit" type="submit"><span>登录工作台</span><span aria-hidden="true">→</span></button><div class="login-footer">账号由管理员统一开通，不开放注册。<br>忘记密码请联系运营或系统管理员。</div>
    </form><div class="login-access-note"><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="4" y="8" width="12" height="10" rx="2"/><path d="M7 8V5a3 3 0 0 1 6 0v3 M10 12v3"/></svg>按账号授权访问工作台</div></section></main>`;
  $('#toggle-password').onclick=()=>{const input=$('#login-password'),show=input.type==='password';input.type=show?'text':'password';$('#toggle-password').setAttribute('aria-pressed',String(show));$('#toggle-password').setAttribute('aria-label',show?'隐藏密码':'显示密码');};
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.currentTarget,
      b = $("button[type=submit]", f);
    b.disabled = true;
    $("#login-error").textContent = "";
    try {
      const data = new FormData(f);
      const session = await loginAccount({
        account_code: data.get("account"),
        password: data.get("password"),
      });
      if (session.must_change_password) return passwordScreen();
      await shell();
    } catch (err) {
      $("#login-error").innerHTML =
        `<div class="error-box">${esc(err.message)}</div>`;
    } finally {
      b.disabled = false;
    }
  };
}
function passwordScreen(required = true) {
  $("#app").innerHTML =
    `<section class="login-form boot"><form id="password-form"><h2>设置你的登录密码</h2><p class="muted">${required ? "首次登录或重置密码后，请先设置新密码。" : "修改密码后，其他设备需要重新登录。"}</p>${field("当前密码", "old", "", { type: "password", required: true })}${field("新密码", "new", "", { type: "password", required: true, help: "8–128 个字符，不限制字符组合。" })}${field("再次输入新密码", "confirm", "", { type: "password", required: true })}<div id="password-error" role="alert"></div><button type="submit" class="primary">保存新密码</button>${required ? "" : '<button id="cancel-password" type="button">返回工作台</button>'}</form></section>`;
  $("#cancel-password")?.addEventListener("click", shell);
  $("#password-form").onsubmit = async (e) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget),
      b = $("button", e.currentTarget);
    b.disabled = true;
    try {
      if (d.get("new") !== d.get("confirm"))
        throw Error("两次输入的新密码不一致");
      await api("/auth/password", {
        method: "POST",
        body: { old_password: d.get("old"), new_password: d.get("new") },
      });
      toast("密码已更新");
      await shell();
    } catch (err) {
      $("#password-error").innerHTML =
        `<div class="error-box">${esc(err.message)}</div>`;
    } finally {
      b.disabled = false;
    }
  };
}
async function shell() {
  const me = await api("/auth/me");
  state.actor = me.actor || state.actor;
  if (!["operations", "administrator"].includes(state.actor?.role)) {
    await logoutAccount();
    return login("此页面仅向运营和系统管理员开放，请使用相应账号。");
  }
  await loadCompanies();
  state.org = await api("/organization");
  const initial=window.location.hash.slice(1);if(pages[initial])state.page=initial;
  $('#app').innerHTML=`<a class="skip-link" href="#content">跳到主要内容</a><div class="shell"><aside id="sidebar"><a class="brand" href="#customers" aria-label="商汤销售小浣熊运营管理"><img class="brand-logo" src="/admin/assets/brand-white.svg" alt="商汤销售小浣熊 Raccoon SalesBuddy"></a><div class="workspace-label">运营管理工作台</div><label class="nav-search"><input id="nav-search" type="search" aria-label="查找功能" placeholder="查找功能…"><kbd>/</kbd></label><nav class="nav" aria-label="管理功能">${groups.map(([group,items])=>`<section class="nav-group"><h2>${group}</h2>${items.map(id=>{const item=nav.find(n=>n[0]===id);return `<button type="button" data-nav="${id}" title="${item[1]}" aria-current="${state.page===id?'page':'false'}" class="${state.page===id?'active':''}"><span class="nav-icon">${navIcon(id)}</span><span class="nav-text">${item[1]}</span></button>`;}).join('')}</section>`).join('')}<p id="nav-empty" hidden>没有匹配的功能</p></nav><div class="side-bottom"><span class="connection-dot"></span>业务数据实时读取<button type="button" id="collapse-nav" aria-label="收起导航" title="收起导航">«</button></div></aside><button class="nav-scrim" aria-label="关闭导航" hidden></button><div class="main"><header class="topbar"><button class="mobile-menu" aria-label="展开导航" aria-expanded="false" aria-controls="sidebar">☰</button>${companyLabel()}<div class="user"><span class="avatar">${esc((state.actor.display_name||'管')[0])}</span><span>${esc(state.actor.display_name||state.actor.account_code||'管理账号')}<small>${esc(roles[state.actor.role])}</small></span><button class="link" id="change-password">修改密码</button><button class="link" id="logout">退出</button></div></header><div class="page-tools"><span class="crumb">运营管理</span><span id="page-status" role="status">正在读取数据…</span><button id="refresh-page" class="link" type="button">↻ 刷新当前页</button></div><main class="content" id="content" tabindex="-1"></main><footer class="main-footer">Raccoon SalesBuddy · 运营管理</footer></div></div>`;
  document.querySelectorAll('[data-nav]').forEach(button=>button.onclick=()=>navigate(button.dataset.nav));
  $('#refresh-page').onclick=()=>state.refresh();
  state.selectCompany=async id=>{
    if($('#dialog')?.open){return toast('请先保存或关闭当前表单，再切换公司');}
    const previousCompany=state.company.id,oldRoot=$('#content');
    renderId+=1;if(oldRoot){oldRoot.inert=true;oldRoot.setAttribute('aria-busy','true');}
    try{
      await chooseCompany(id);pageMemory.clear();renderId+=1;
      await shell();toast('已进入'+state.company.name);
    }catch(error){if(state.company.id!==previousCompany&&oldRoot){oldRoot.innerHTML='<div class="error-box">公司已切换，页面加载未完成，请刷新当前页。</div>';}toast(error.message);}
    finally{if(oldRoot?.isConnected){oldRoot.inert=false;oldRoot.removeAttribute('aria-busy');}}
  };
  $('#collapse-nav').onclick=()=>{const closed=$('.shell').classList.toggle('nav-collapsed');$('#collapse-nav').textContent=closed?'»':'«';$('#collapse-nav').setAttribute('aria-label',closed?'展开导航':'收起导航');};
  $('#nav-search').oninput=event=>{const q=event.target.value.trim().toLowerCase();let total=0;document.querySelectorAll('.nav-group').forEach(group=>{let count=0;group.querySelectorAll('[data-nav]').forEach(button=>{const matches=button.textContent.toLowerCase().includes(q);button.hidden=!matches;if(matches)count++;});group.hidden=!count;total+=count;});$('#nav-empty').hidden=!!total;};
  $('.nav-scrim').onclick=()=>{$('.shell').classList.remove('menu-open');$('.mobile-menu').setAttribute('aria-expanded','false');};
  $("#change-password").onclick = () => passwordScreen(false);
  $("#logout").onclick = async () => {
    let message = "";
    try { await logoutAccount(); } catch (error) {
      if (error.code === "SESSION_CHANGED") return;
      message = error.message;
    }
    login(message);
  };
  $(".mobile-menu").onclick = () => {const open=$(".shell").classList.toggle("menu-open");$('.mobile-menu').setAttribute('aria-expanded',String(open));};
  await state.refresh();
}
window.addEventListener('hashchange',()=>{const page=window.location.hash.slice(1);if(page!==state.page&&state.actor&&$('#content'))navigate(page,{history:false});});
document.addEventListener('keydown',event=>{if(event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)&&!$('#dialog')?.open){event.preventDefault();$('.shell')?.classList.remove('nav-collapsed');$('#nav-search')?.focus();}if(event.key==='Escape')$('.shell')?.classList.remove('menu-open');});
document.addEventListener("company-label-changed",()=>{const label=$(".company-label");if(label)label.outerHTML=companyLabel();});
document.addEventListener("session-expired", () =>
  login("登录已过期，请重新登录。"),
);
try {
  const s = await refreshToken();
  if (s.must_change_password) passwordScreen();
  else await shell();
} catch (e) {
  login();
}
