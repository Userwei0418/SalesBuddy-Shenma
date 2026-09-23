import {state, esc, api, head, stats, num, date, options, people, filters, search, table, pager, query, bindCommon, dialog, details, download, toast} from "./core.js";

const select = (name, label, choices, value, empty) => `<select name="${name}" aria-label="${label}">${options(choices, value, empty)}</select>`;
const execution = {human: "人工操作", system: "系统处理", import: "历史导入", unknown: "执行方式未记录"};
const result = (r) => `<span class="badge ${r.result === "failed" ? "red" : r.result === "pending" ? "amber" : "green"}">${esc(r.result_label)}</span>`;
const detailButton = (id) => `<button class="link" data-action="activity-detail" data-id="${esc(id)}">查看详情 →</button>`;

async function showDetail(id) {
  const r = await api("/activities/" + encodeURIComponent(id));
  const d = r.details;
  const changes = d.changes.length ? `<section class="activity-section"><h3>修改了什么</h3><div class="activity-diffs">${d.changes.map(c => `<div><b>${esc(c.label)}</b><span class="activity-before">${esc(c.before ?? "未填写")}</span><span class="activity-arrow">→</span><strong>${esc(c.after ?? "未填写")}</strong></div>`).join("")}</div></section>` : "";
  const materials = d.materials.length ? `<section class="activity-section"><h3>关联的原始材料</h3>${d.materials.map(m => `<article class="activity-source"><span class="activity-source-icon">▤</span><div><strong>${esc(m.filename)}</strong><p>${esc(m.uploader || "上传人未记录")} 上传 · ${date(m.uploaded_at)}</p><button type="button" class="link" data-activity-link="upload:${esc(m.id)}">查看上传记录 →</button></div></article>`).join("")}</section>` : "";
  const visits = d.visits.length ? `<section class="activity-section"><h3>形成的拜访记录</h3>${d.visits.map(v => `<article class="activity-source"><span class="activity-source-icon">✓</span><div><strong>${esc(v.customer_name)}</strong><p>${esc(v.confirmer || "确认人未记录")} 确认归档 · ${date(v.archived_at)}</p><button type="button" class="link" data-activity-link="visit:${esc(v.id)}">查看归档记录 →</button></div></article>`).join("")}</section>` : "";
  dialog(r.action_label, `<div class="activity-detail-head"><div><strong>${esc(r.object_name)}</strong><p>${esc(r.actor_name)} · ${date(r.occurred_at)}</p></div>${result(r)}</div>${details([["业务类型",r.category_label],["关联客户",r.customer_name || "未关联客户"],["执行方式",execution[r.execution_kind] || "未记录"],["记录依据",r.evidence_label],...(r.actor_department ? [["当时部门",r.actor_department]] : [])])}${changes}${d.facts.length ? `<section class="activity-section"><h3>操作内容</h3>${details(d.facts.map(f => [f.label,f.value]))}</section>` : ""}${materials}${visits}${d.note ? `<p class="activity-footnote">${esc(d.note)}</p>` : ""}`, {wide:true,afterRender:el => el.querySelectorAll("[data-activity-link]").forEach(b => b.onclick = () => {el.close(); showDetail(b.dataset.activityLink).catch(e => toast(e.message));})});
}

export async function businessActivities() {
  const data = await api("/activities" + query({limit:50,offset:state.offset}));
  const category = state.filters.category;
  const actions = Object.entries(data.actions).filter(([key]) => !category || key.startsWith(category + "."));
  return {
    html: head("业务操作记录", "谁在何时，对哪条业务资料做了什么。", '<button data-action="export">导出操作记录</button>') +
      `<div class="activity-stats">${stats([["业务记录",num(data.total),"当前筛选范围"],["涉及人员",num(data.people),"按操作人去重"],["系统处理",num(data.system_count),"与人工操作分别标注"]])}</div>` +
      `<div class="card activity-card">${filters(
        select("period","时间范围",[["day","今日"],["week","本周"],["month","本月"],["custom","指定日期"]],state.filters.period || "month",null) +
        `<input type="date" name="start" aria-label="起始日期" value="${esc(state.filters.start || "")}"><span class="muted">至</span><input type="date" name="end" aria-label="结束日期" value="${esc(state.filters.end || "")}">` +
        search("搜索人员、客户、商机或文件") +
        `<select name="actor" aria-label="操作人">${people(state.filters.actor,"全部人员")}</select>` +
        select("department","操作人当前部门",(state.org?.departments || []).map(d => [d.id,d.name]),state.filters.department,"全部部门（当前）") +
        select("category","业务类型",Object.entries(data.categories),category,"全部业务类型") +
        select("action","业务动作",actions,state.filters.action,"全部操作") +
        select("outcome","处理结果",[["success","已完成"],["pending","处理中"],["failed","处理失败"]],state.filters.outcome,"全部处理结果"))}` +
      table(["操作时间","操作人","操作及变化","操作对象","结果",""], data.items, r =>
        `<tr><td class="activity-time">${date(r.occurred_at).replace(" ","<br>")}</td><td><strong>${esc(r.actor_name)}</strong><small>${esc(r.actor_department || (r.execution_kind === "system" ? "任务发起人" : ""))}</small></td><td><span class="activity-action">${esc(r.action_label)}</span><div class="activity-summary">${esc(r.summary)}</div><small>${esc(execution[r.execution_kind] || "未记录")}</small></td><td class="activity-object"><strong>${esc(r.object_name)}</strong>${r.customer_name && r.customer_name !== r.object_name ? `<small>${esc(r.customer_name)}</small>` : ""}</td><td>${result(r)}</td><td>${detailButton(r.event_id)}</td></tr>`,"暂无符合条件的业务操作") +
      pager(data.total) + `</div><p class="activity-footnote">历史导入与已保存资料按现有证据展示。接口自动刷新保留在技术审计日志中。</p>`,
    bind(root) {
      bindCommon(root, {export:() => download("/activities/export" + query(),"业务操作记录"), "activity-detail":showDetail});
      const categories = root.querySelector('[name="category"]');
      categories.onchange = () => {
        root.querySelector('[name="action"]').innerHTML = options(Object.entries(data.actions).filter(([key]) => !categories.value || key.startsWith(categories.value + ".")),"","全部操作");
      };
    },
  };
}
