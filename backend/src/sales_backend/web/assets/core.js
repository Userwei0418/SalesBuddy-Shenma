export const state = {
  token: null,
  permissions: null,
  actor: null,
  org: null,
  company: null,
  companies: [],
  selectCompany: async () => {},
  page: "customers",
  offset: 0,
  filters: {},
  refresh: async () => {},
};
export const $ = (s, root = document) => root.querySelector(s);
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const roles = {
  sales: "一线销售",
  supervisor: "销售主管",
  manager: "总经理",
  fde: "FDE",
  fde_lead: "FDE主管",
  operations: "运营",
  administrator: "系统管理员",
};
// Use directory labels where available; approved FDE titles stay consistent in
// account badges, edit choices and filters. An unknown role never renders blank.
export function roleLabel(code, directory = state.org?.roles || []) {
  if (code === "fde" || code === "fde_lead" || code === "supervisor") return roles[code];
  const name = directory.find((role) => role.code === code)?.name;
  return String(name || "").trim() || roles[code] || code || "未分配角色";
}
export const statuses = {
  unclaimed: "待认领",
  claimed: "已认领",
  legacy_review: "待核对归属",
  pending: "待审批",
  approved: "已通过",
  rejected: "已驳回",
  cancelled: "已取消",
  active: "启用",
  inactive: "停用",
  open: "推进中",
  won: "赢单",
  lost: "丢单",
  succeeded: "成功",
  failed: "失败",
  running: "处理中",
  provider_attempt: "请求尝试",
  legacy_logical: "历史记录",
};
export const num = (v) =>
  v === null || v === undefined ? "—" : Number(v).toLocaleString("zh-CN");
export const money = (v) =>
  v === null || v === undefined ? "—" : `${num(Number(v) / 10000)} 万`;
export const date = (v) =>
  v
    ? new Date(v).toLocaleString("zh-CN", { hour12: false }).replace(/\//g, "-")
    : "—";
export const badge = (s) =>
  `<span class="badge ${["approved", "succeeded", "claimed", "active", "won"].includes(s) ? "green" : ["pending", "legacy_review", "running"].includes(s) ? "amber" : ["rejected", "failed", "inactive", "lost"].includes(s) ? "red" : "blue"}">${esc(statuses[s] || s || "未填写")}</span>`;
export const options = (items, value = "", empty = "全部") =>
  `${empty !== null ? `<option value="">${esc(empty)}</option>` : ""}${items
    .map((i) => {
      const [v, l] = Array.isArray(i) ? i : [i, i];
      return `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`;
    })
    .join("")}`;
export const roleOptions = (value = "", empty = "全部角色") =>
  options([...new Set([...Object.keys(roles), ...(state.org?.roles || []).map((role) => role.code)])]
    .map((code) => [code, roleLabel(code)]), value, empty);
export const people = (value = "", empty = "全部人员") =>
  options(
    (state.org?.accounts || []).map((u) => [
      u.id,
      `${u.display_name} · ${u.account_code}`,
    ]),
    value,
    empty,
  );
let sessionGeneration = 0;
let companyGeneration = 0;
export function setCompany(company) {
  companyGeneration += 1;
  state.company = company;
  state.org = null; state.filters = {}; state.offset = 0;
}
function assertCompany(generation) {
  if (generation !== companyGeneration) throw Object.assign(Error("公司已切换，请在当前公司重新操作"), {code:"COMPANY_CHANGED"});
}
let refreshFlight;
let logoutFlight;
const LOGOUT_TIMEOUT_MS = 10000;
function actorIdentity(actor) {
  if (!actor) return null;
  const fields = [actor.workspace_id, actor.user_id, actor.role];
  if (fields.some(value => typeof value !== "string" || !value.trim())) {
    throw Object.assign(Error("登录身份信息不完整，请重新登录"), {code: "SESSION_IDENTITY_INVALID"});
  }
  return JSON.stringify(fields);
}
function assertActorIdentity(actor, expectedIdentity) {
  const identity = actorIdentity(actor);
  if (!identity || (expectedIdentity && identity !== expectedIdentity)) {
    throw Object.assign(Error("登录身份与当前账号不一致，请重新登录"), {code: "SESSION_IDENTITY_MISMATCH"});
  }
}
function assertSession(generation) {
  if (generation !== sessionGeneration) throw Object.assign(Error("登录状态已变更，请在当前账号下重试"), {code: "SESSION_CHANGED"});
}
export function clearSession() {
  state.permissions = null;
  sessionGeneration += 1;
  refreshFlight = null;
  state.token = null;
  state.actor = null;
  setCompany(null); state.companies = [];
  state.org = null;
  state.filters = {};
  state.offset = 0;
}
function accept(session, generation, expectedIdentity = null) {
  assertSession(generation);
  assertActorIdentity(session.actor, expectedIdentity);
  if (typeof session.access_token !== "string" || !session.access_token) {
    throw Object.assign(Error("登录凭据无效，请重新登录"), {code: "SESSION_IDENTITY_INVALID"});
  }
  state.token = session.access_token;
  state.actor = session.actor;
  return session;
}
export async function refreshToken(generation = sessionGeneration, rejectedToken = state.token, expectedIdentity) {
  assertSession(generation);
  if (expectedIdentity === undefined) expectedIdentity = actorIdentity(state.actor);
  if (state.token && state.token !== rejectedToken) {
    assertActorIdentity(state.actor, expectedIdentity);
    return {access_token: state.token, actor: state.actor};
  }
  if (refreshFlight && refreshFlight.identity !== expectedIdentity) {
    throw Object.assign(Error("登录身份已变更，请重新登录"), {code: "SESSION_IDENTITY_MISMATCH"});
  }
  if (!refreshFlight) {
    const flight = {generation, identity: expectedIdentity};
    flight.promise = fetch("/api/v1/console/auth/refresh", {
      method: "POST", credentials: "same-origin",
    }).then(async r => {
      assertSession(generation);
      if (!r.ok) throw Error("请登录");
      // A late HttpOnly Set-Cookie is applied by the browser before JS sees it.
      // The server binds refresh to the password-login session; also reject an
      // unexpected actor here before accepting a token or replaying any intent.
      return accept(await r.json(), generation, expectedIdentity);
    }).catch(error => { assertSession(generation); throw error; })
      .finally(() => { if (refreshFlight === flight) refreshFlight = null; });
    refreshFlight = flight;
  }
  return refreshFlight.promise;
}
export async function api(
  path,
  { method = "GET", body, key, raw = false, retry = true, generation = sessionGeneration, companyEpoch = companyGeneration, companyId = state.company?.id } = {},
) {
  assertSession(generation); assertCompany(companyEpoch);
  const rejectedToken = state.token;
  const expectedIdentity = actorIdentity(state.actor);
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  if (companyId && !path.includes("/auth/") && !["/companies","/companies/select"].includes(path)) headers["X-Company-ID"] = companyId;
  let r;
  try {
    r = await fetch(
      path.startsWith("/api/") ? path : "/api/v1/console" + path,
      {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin",
        cache: "no-store",
      },
    );
  } catch (e) {
    assertSession(generation); assertCompany(companyEpoch);
    throw Error("连接中断，请检查网络后重试；本次提交标识已保留。");
  }
  assertSession(generation); assertCompany(companyEpoch);
  if (r.status === 401 && retry && !path.includes("/auth/")) {
    try {
      // Only the explicit page bootstrap may recover an unknown cookie actor.
      // An unauthenticated business request must never be replayed as that user.
      if (!expectedIdentity) throw Error("请重新登录后提交");
      await refreshToken(generation, rejectedToken, expectedIdentity);
      return api(path, { method, body, key, raw, retry: false, generation, companyEpoch, companyId });
    } catch (e) {
      assertSession(generation); assertCompany(companyEpoch);
      clearSession();
      document.dispatchEvent(new Event("session-expired"));
      throw Object.assign(Error("登录已过期，请重新登录"), {code: e.code || "SESSION_EXPIRED"});
    }
  }
  if (!r.ok) {
    let data;
    try {
      data = await r.json();
    } catch (e) {
      data = {};
    }
    assertSession(generation); assertCompany(companyEpoch);
    const d = data.detail;
    const message = Array.isArray(d)
      ? d.map((x) => `${x.loc?.slice(1).join(".") || ""} ${x.msg}`).join("；")
      : typeof d === "string"
        ? d
        : `请求失败（${r.status}）`;
    throw Error(
      message === "PASSWORD_CHANGE_REQUIRED" ? "请先修改初始密码" : message,
    );
  }
  const result = raw ? r : r.status === 204 ? {} : await r.json();
  assertSession(generation); assertCompany(companyEpoch);
  return result;
}
export async function loginAccount(body) {
  clearSession();
  const generation = sessionGeneration;
  // A completed logout response applies cookie deletion before a new login can
  // set its cookies. Do not wait for old refresh calls: server session binding
  // and the actor checks above handle those independently.
  if (logoutFlight) await logoutFlight.promise.catch(() => undefined);
  assertSession(generation);
  return accept(await api("/auth/login", {method: "POST", body, retry: false, generation}), generation);
}
export async function logoutAccount() {
  const token = state.token;
  clearSession();
  const generation = sessionGeneration;
  if (!logoutFlight) {
    const flight = {};
    const controller = new AbortController();
    let timeout;
    flight.promise = new Promise((resolve, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(Error("服务端退出超时"));
      }, LOGOUT_TIMEOUT_MS);
      fetch("/api/v1/console/auth/logout", {
        method: "POST", credentials: "same-origin", headers: token ? {Authorization: `Bearer ${token}`} : {},
        signal: controller.signal,
      }).then(resolve, reject);
    }).finally(() => {
      clearTimeout(timeout);
      if (logoutFlight === flight) logoutFlight = null;
    });
    logoutFlight = flight;
  }
  let response;
  try {
    response = await logoutFlight.promise;
  } catch (error) {
    assertSession(generation);
    throw Error("已退出本机；服务端退出尚未确认，请检查网络后重试");
  }
  assertSession(generation);
  if (!response.ok) throw Error("已退出本机；服务端退出尚未确认，请重新尝试");
}
export function toast(message) {
  const t = $("#toast");
  t.textContent = message;
  t.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    t.style.display = "none";
  }, 4500);
}
export const field = (
  label,
  name,
  value = "",
  {
    type = "text",
    required = false,
    full = false,
    select = null,
    help = "",
    disabled = false,
    min = "",
    max = "",
    step = "",
    placeholder = "",
  } = {},
) =>
  `<label class="field ${full ? "full" : ""}"><b>${esc(label)}${required ? ' <span class="req">*</span>' : ""}</b>${select !== null ? `<select name="${esc(name)}" ${required ? "required" : ""} ${disabled ? "disabled" : ""}>${select}</select>` : type === "textarea" ? `<textarea name="${esc(name)}" ${required ? "required" : ""} placeholder="${esc(placeholder)}">${esc(value)}</textarea>` : `<input name="${esc(name)}" type="${esc(type)}" value="${esc(value)}" ${required ? "required" : ""} ${disabled ? "disabled" : ""} ${min !== "" ? `min="${esc(min)}"` : ""} ${max !== "" ? `max="${esc(max)}"` : ""} ${step !== "" ? `step="${esc(step)}"` : ""} placeholder="${esc(placeholder)}" ${type === "password" ? 'autocomplete="new-password"' : ""}>`}${help ? `<small>${esc(help)}</small>` : ""}</label>`;
export function dialog(
  title,
  body,
  {
    submit = "保存",
    wide = false,
    onSubmit = null,
    footer = "",
    afterRender = null,
  } = {},
) {
  const d = $("#dialog");
  d.removeAttribute?.("aria-busy");
  d.className = wide ? "wide" : "";
  d.innerHTML = `<form id="dialog-form"><div class="dialog-title"><h2 id="dialog-title">${esc(title)}</h2><button type="button" data-close aria-label="关闭">×</button></div><div class="dialog-body">${body}<div class="form-error" role="alert"></div></div><div class="dialog-footer">${footer}<button type="button" data-close>${onSubmit ? "取消" : "关闭"}</button>${onSubmit ? `<button class="primary" type="submit">${esc(submit)}</button>` : ""}</div></form>`;
  let dirty=false,busy=false;
  const requestClose=()=>{
    if(busy) return;
    if(!onSubmit || !dirty) {d.close();return;}
    if(d.querySelector('.discard-confirm'))return;
    const notice=document.createElement('div');notice.className='discard-confirm';
    notice.innerHTML='<span>有尚未保存的修改。</span><button type="button" data-keep>继续编辑</button><button type="button" data-discard>放弃修改</button>';
    d.querySelector('.dialog-footer').before(notice);
    notice.querySelector('[data-keep]').onclick=()=>notice.remove();
    notice.querySelector('[data-discard]').onclick=()=>d.close();
  };
  d.querySelectorAll("[data-close]").forEach(b=>b.onclick=requestClose);
  d.oncancel=event=>{event.preventDefault();requestClose();};
  const markDirty=()=>{dirty=true;};
  const currentForm=$('#dialog-form');
  if(onSubmit){currentForm?.addEventListener?.('input',markDirty);currentForm?.addEventListener?.('change',markDirty);}
  const key = crypto.randomUUID();
  if (onSubmit)
    $("#dialog-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const btn = $("button[type=submit]", f);
      if(busy) return;
      busy=true;btn.disabled = true;
      d.setAttribute?.('aria-busy','true');
      const label=btn.textContent;btn.textContent='正在保存…';
      $(".form-error", f).innerHTML = "";
      try {
        await onSubmit(new FormData(f), key, f);
        if($('#dialog-form')!==f)return;
        dirty=false;d.close();
        await state.refresh();
      } catch (error) {
        if($('#dialog-form')===f){
          $(".form-error", f).innerHTML = `<div class="error-box">${esc(error.message)}</div>`;
          $('.form-error',f).scrollIntoView?.({block:'nearest'});
        }
      } finally {
        busy=false;btn.disabled = false;btn.textContent=label;
        if($('#dialog-form')===f)d.removeAttribute?.('aria-busy');
      }
    };
  d.showModal();
  afterRender?.(d);
  return d;
}
export function table(head, rows, render, empty = "暂无符合条件的记录") {
  return `<div class="table-wrap"><table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(render).join("")}</tbody></table>${!rows.length ? `<div class="empty"><strong>${esc(empty)}</strong>调整筛选条件，或稍后刷新查看。</div>` : ""}</div>`;
}
export const pager = (total, offset = state.offset) =>
  `<div class="pager"><span>共 ${num(total)} 条 · 当前 ${total ? offset + 1 : 0}–${Math.min(offset + 50, total)}</span><div><button data-page="prev" ${offset <= 0 ? "disabled" : ""}>上一页</button><button data-page="next" ${offset + 50 >= total ? "disabled" : ""}>下一页</button></div></div>`;
export const stats = (items) =>
  `<div class="stats">${items.map(([label, value, note]) => `<div class="stat"><div class="label">${esc(label)}</div><strong>${esc(value)}</strong><small>${esc(note || "")}</small></div>`).join("")}</div>`;
export const head = (title, desc, buttons = "", eyebrow = "SALES OPERATIONS") =>
  `<div class="pagehead"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div><div class="actions">${buttons}</div></div>`;
export const query = (extra = {}) => {
  const p = new URLSearchParams();
  Object.entries({ ...state.filters, ...extra }).forEach(([k, v]) => {
    if (v !== "" && v !== null && v !== undefined) p.set(k, v);
  });
  return "?" + p.toString();
};
export const filters = (inner) =>
  `<form class="toolbar" id="filters">${inner}<button type="submit">查询</button><button type="button" data-action="reset" class="link">重置</button></form>`;
export const search = (ph = "搜索名称或关键词") =>
  `<input type="search" name="q" placeholder="${esc(ph)}" aria-label="${esc(ph)}" value="${esc(state.filters.q || "")}">`;
export async function download(path, name) {
  const companyEpoch = companyGeneration;
  try {
    const r = await api(path, { raw: true });
    const blob = await r.blob();
    assertCompany(companyEpoch);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name + ".csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast("导出完成");
  } catch (e) {
    toast(e.message);
  }
}
export function bindCommon(root, actions) {
  const f = $("#filters", root);
  if (f)
    f.onsubmit = (e) => {
      e.preventDefault();
      state.filters = Object.fromEntries(new FormData(f));
      state.offset = 0;
      state.refresh();
    };
  root.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.page) {
      state.offset = Math.max(
        0,
        state.offset + (b.dataset.page === "next" ? 50 : -50),
      );
      return state.refresh({reason: "page"});
    }
    if (b.dataset.action === "reset") {
      state.filters = {};
      state.offset = 0;
      return state.refresh();
    }
    if (b.dataset.action && actions[b.dataset.action]) {
      b.disabled = true;
      try {
        await actions[b.dataset.action](b.dataset.id, b);
      } catch (err) {
        toast(err.message);
      } finally {
        b.disabled = false;
      }
    }
  };
}
export const details = (items) =>
  `<dl class="details">${items.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v ?? "未填写")}</dd></div>`).join("")}</dl>`;
export const jsonBlock = (obj) =>
  `<pre class="code">${esc(JSON.stringify(obj, null, 2))}</pre>`;
