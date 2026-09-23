import {
  state,
  $,
  esc,
  roles,
  roleLabel,
  statuses,
  num,
  money,
  date,
  badge,
  options,
  roleOptions,
  people,
  api,
  toast,
  field,
  dialog,
  table,
  pager,
  stats,
  head,
  query,
  filters,
  search,
  download,
  bindCommon,
  details,
  jsonBlock,
} from "./core.js";
import {partnerFields, bindPartnerFields} from './partners.js';
import {showCustomerDetail} from './customer-detail.js';
import {editCustomerProfile} from './customer-profile.js';
import {capabilityLabel} from './ai-capabilities.js';
import {agentId, modelName, providers, label, errorName} from './ai-audit-labels.js';
const btn = (label, action, id = "", style = "") =>
  `<button class="${style}" data-action="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
const select = (name, opts) =>
  `<select name="${name}" aria-label="${name}">${opts}</select>`;
const section = (title) => `<div class="section-title">${esc(title)}</div>`;
const period = () =>
  select(
    "period",
    options(
      [
        ["day", "今日"],
        ["week", "本周"],
        ["month", "本月"],
        ["custom", "指定日期"],
      ],
      state.filters.period || "month",
      null,
    ),
  ) +
  `<input type="date" name="start" aria-label="起始日期" value="${esc(state.filters.start || "")}"><span class="muted">至</span><input type="date" name="end" aria-label="结束日期" value="${esc(state.filters.end || "")}">`;
const history = (rows) =>
  rows.length
    ? `<div class="history">${rows.map((x) => `<article><strong>${esc(x.title)}</strong><p>${esc(x.text)}</p><small>${esc(x.meta)}</small></article>`).join("")}</div>`
    : '<p class="help">暂无历史记录</p>';
async function ensureOrg() {
  state.org = await api("/organization");
}

// Only the currently mounted customer / AI page can reuse its auxiliary reads
// while paging. Querying, navigation, explicit refresh and the dialog's write-
// completion refresh replace this entry. Nothing is persisted across sessions.
let sectionCache;
function pageSections(page, context, definitions) {
  const actor = state.actor;
  const identity = JSON.stringify(actor);
  const filterKey = JSON.stringify(Object.entries(state.filters).sort());
  if (context.reason !== "page" || sectionCache?.page !== page ||
      sectionCache?.actor !== actor || sectionCache?.identity !== identity ||
      sectionCache?.filterKey !== filterKey) {
    sectionCache = {page, actor, identity, filterKey, resources: {}};
  }
  const cache = sectionCache;
  const resources = {};
  for (const [name, definition] of Object.entries(definitions)) {
    if (!definition.reusable || !cache.resources[name]) {
      cache.resources[name] = {status: "loading", promise: null};
    }
    resources[name] = cache.resources[name];
  }
  let scopeRefreshStarted = false;
  const current = () => {
    if (context.isCurrent && !context.isCurrent()) return false;
    if (!state.actor) return false;
    if (JSON.stringify(state.actor) !== identity) {
      // A token refresh may also update the same actor's permissions. Rebuild
      // all regions in the new scope; never combine old totals with new rows.
      if (!scopeRefreshStarted) {
        scopeRefreshStarted = true;
        state.refresh();
      }
      return false;
    }
    return sectionCache === cache;
  };
  const content = (name) => {
    const resource = resources[name], definition = definitions[name];
    if (resource.status === "ready") return definition.render(resource.data);
    const retry = btn("重新加载", `retry-${name}`, "", "link");
    return resource.status === "error"
      ? `<div class="error-box" role="alert">${esc(definition.label)}加载失败：${esc(resource.error)}<p>${retry}</p></div>`
      : `<div class="notice" role="status">正在加载${esc(definition.label)}… ${retry}</div>`;
  };
  return {
    region: (name) => `<div data-region="${page}-${name}">${content(name)}</div>`,
    bind: (root, actions) => {
      const paint = (name, resource) => {
        if (!current() || resources[name] !== resource) return;
        const node = root.querySelector(`[data-region="${page}-${name}"]`);
        if (node) {
          try {
            node.innerHTML = content(name);
          } catch (error) {
            resource.status = "error";
            resource.error = "响应内容无法显示，请重新加载";
            node.innerHTML = content(name);
          }
          node.setAttribute("aria-busy", resource.status === "loading" ? "true" : "false");
        }
      };
      const load = async (name, retry = false) => {
        if (!current()) return;
        if (retry) resources[name] = cache.resources[name] = {status: "loading", promise: null};
        const resource = resources[name];
        paint(name, resource);
        if (!resource.promise) {
          resource.promise = api(definitions[name].path).then(data => {
            resource.data = data;
            resource.status = "ready";
          }, error => {
            resource.error = error.message;
            resource.status = "error";
          });
        }
        await resource.promise;
        paint(name, resource);
      };
      bindCommon(root, {
        ...actions,
        refresh: () => state.refresh(),
        ...Object.fromEntries(Object.keys(definitions).map(name => [
          `retry-${name}`, () => load(name, true),
        ])),
      });
      const click = root.onclick;
      root.onclick = event => current() && click(event);
      const form = root.querySelector("#filters");
      if (form) {
        const submit = form.onsubmit;
        form.onsubmit = event => current() ? submit(event) : event.preventDefault();
      }
      // Returning settlement is useful to hosts/tests that need it; the console
      // deliberately doesn't await it, so a slow summary cannot hide the list.
      return Promise.all(Object.keys(definitions).map(name => load(name)));
    },
  };
}
function customerForm(row = {}) {
  const existing = !!row.id;
  const body = `<div class="notice">客户资料以公司系统为准。${existing ? "修改后自动保存变更记录。" : "请核实公司已完成建档，再在此录入；创建后进入待认领客户池。"}</div><div class="form-grid">${field("客户名称", "name", row.name, { required: true, full: true })}${!existing ? field("公司客户编号 / 已通过审批单号", "company_reference", "", { required: true, full: true }) : ""}${field("所属行业", "industry", row.industry_code)}${field("客户类型", "customer_type", "", { required: true, select: options(["潜在客户", "商机客户", "已成单客户"], row.customer_type_code || "潜在客户", null) })}${field("客户优先级", "level_code", "", { required: true, select: options(["Tier-1", "Tier-2", "Tier-3"], row.level_code || "Tier-2", null) })}${field("客户来源", "source", "", { required: true, select: options(["销售自拓", "客户转介绍", "市场活动", "销售线索", "合作伙伴", "其他"], row.source_code || "销售线索", null) })}${
    !existing
      ? field("管理部门", "target_team", "", {
          required: true,
          select: options(
            state.org.departments
              .filter((t) => t.status === "active")
              .map((t) => [t.name, t.name]),
            "",
            "请选择部门",
          ),
        })
      : ""
  }${field("所属伙伴", "partner_name", row.primary_partner_name)}${section("首要联系人")}${field("姓名", "contact_name", row.contact_name, { required: true })}${field("职位", "contact_title", row.contact_title, { required: true })}${field("联系人角色", "contact_role", "", { required: true, select: options(["使用者", "影响者", "决策者"], { user: "使用者", influencer: "影响者", decision_maker: "决策者" }[row.contact_role] || row.contact_role || "使用者", null) })}${field("联系电话", "contact_phone", row.contact_phone)}${field("邮箱", "contact_email", row.contact_email, { type: "email" })}${!existing ? '<label class="check field full"><input type="checkbox" required>我已核实此客户在公司系统中创建成功</label>' : ""}</div>`;
  dialog(existing ? "编辑客户资料" : "创建客户档案", body, {
    submit: existing ? "保存修改" : "确认建档",
    onSubmit: async (fd, key) => {
      const data = Object.fromEntries(fd);
      if (existing) {
        data.version_no = row.version_no;
        for (const k of ["industry", "partner_name"])
          if (!data[k]) delete data[k];
      }
      await api("/api/v1/customers" + (existing ? "/" + row.id : ""), {
        method: existing ? "PATCH" : "POST",
        body: data,
        key,
      });
      toast(existing ? "客户资料已保存" : "客户已建档，可发起认领");
    },
  });
}
async function customerDetail(id) {
  return showCustomerDetail(id, customerId => {
    state.page = "opportunities";
    state.filters = {customer: customerId};
    state.offset = 0;
    document.querySelector("[data-nav=opportunities]").click();
    state.filters = {customer: customerId};
    state.refresh();
  }, editCustomerProfile);
}
export async function customers(context = {}) {
  let data = {items: []};
  const offset = state.offset;
  const partial = pageSections("customers", context, {
    summary: {
      label: "客户概览", path: "/summary", reusable: true,
      render: (summary) => stats([
        ["客户总数", num(summary.customers), "公司已建档客户"],
        ["待认领", num(summary.unclaimed), "等待销售发起申请"],
        ["待审批", num(summary.pending_claims), "认领通过后正式归属"],
        ["待核对归属", num(summary.legacy_review), "历史多人认领待运营处理"],
      ]),
    },
    list: {
      label: "客户列表", path: "/customers" + query({limit: 50, offset}),
      render: (result) => {
        data = result;
        return table(
          [
            "客户 / 行业",
            "首要联系人",
            "客户优先级",
            "认领人",
            "认领状态",
            "创建时间",
            "操作",
          ],
          data.items,
          (r) =>
            `<tr><td class="title">${esc(r.name)}<small>${esc(r.industry_code || "行业未填写")}</small></td><td>${esc(r.contact_name || "—")}<small>${esc(r.contact_phone || "")}</small></td><td>${esc(r.level_code || "—")}</td><td>${esc(r.owner_name || "—")}</td><td>${badge(r.ownership_state)}</td><td>${date(r.created_at)}</td><td>${btn("客户档案", "detail", r.id, "link")}${btn("编辑资料", "edit", r.id, "link")}${r.ownership_state === "claimed" ? btn("释放", "release", r.id, "link") : r.ownership_state === "legacy_review" ? btn("核对归属", "resolve", r.id, "link") : ""}</td></tr>`,
        ) +
        pager(data.total, offset);
      },
    },
  });
  return {
    html:
      head(
        "客户管理",
        "核实公司建档结果，维护客户资料与认领归属。",
        btn("刷新", "refresh") + btn("导出客户", "export") + btn("＋ 创建客户", "create", "", "primary"),
      ) +
      partial.region("summary") +
      `<div class="card">` +
      filters(
        search("搜索客户名称 / 公司编号") +
          select(
            "state",
            options(
              ["unclaimed", "claimed", "legacy_review"].map((s) => [
                s,
                statuses[s],
              ]),
              state.filters.state,
              "全部认领状态",
            ),
          ) +
          select(
            "level",
            options(
              ["Tier-1", "Tier-2", "Tier-3"],
              state.filters.level,
              "全部等级",
            ),
          ) +
          select("owner", people(state.filters.owner, "全部认领人")) +
          `<input name="industry" placeholder="行业" aria-label="行业" value="${esc(state.filters.industry || "")}">`,
      ) +
      partial.region("list") +
      `</div>`,
    bind: (root) =>
      partial.bind(root, {
        create: () => customerForm(),
        edit: editCustomerProfile,
        detail: customerDetail,
        export: () => download("/customers/export" + query(), "客户列表"),
        release: (id) => {
          const r = data.items.find((r) => r.id === id);
          dialog(
            "释放客户认领",
            `<div class="notice amber">${esc(r.name)} 将回到待认领客户池。现有商机、任务和历史记录保留，交接由运营另行处理。</div>` +
              field("释放原因", "reason", "", {
                required: true,
                type: "textarea",
                full: true,
              }),
            {
              submit: "确认释放",
              onSubmit: async (fd, key) => {
                await api(`/customers/${id}/release`, {
                  method: "POST",
                  key,
                  body: {
                    version_no: r.ownership_version,
                    reason: fd.get("reason"),
                  },
                });
                toast("客户已释放");
              },
            },
          );
        },
        resolve: async (id) => {
          const c = await api("/customers/" + id);
          dialog(
            "核对历史认领归属",
            '<div class="notice amber">此客户曾由多人认领。请核实后确定唯一认领人，或释放回客户池。</div>' +
              field("认领人", "owner", "", {
                select: options(
                  (c.ownership.historical_members || []).map((u) => [
                    u.id,
                    u.name,
                  ]),
                  "",
                  "释放回待认领池",
                ),
              }) +
              field("核对说明", "reason", "", {
                type: "textarea",
                required: true,
              }),
            {
              submit: "确认处理",
              onSubmit: async (fd, key) => {
                const owner = fd.get("owner");
                await api(
                  `/customers/${id}/${owner ? "resolve-owner" : "release"}`,
                  {
                    method: "POST",
                    key,
                    body: {
                      version_no: c.ownership.version_no,
                      reason: fd.get("reason"),
                      ...(owner ? { owner_user_ref_id: owner } : {}),
                    },
                  },
                );
                toast("历史归属已处理");
              },
            },
          );
        },
      }),
  };
}
export async function claims() {
  if (!Object.keys(state.filters).length) state.filters = { status: "pending" };
  const data = await api(
    "/claims" + query({ limit: 50, offset: state.offset }),
  );
  return {
    html:
      head("认领审批", "核实销售认领申请。一个客户同时只能有一位正式认领人。") +
      `<div class="notice">审批通过后客户加入申请人的作战地图。已认领客户需由运营先释放，其他销售才能重新申请。</div><div class="card">` +
      filters(
        search("搜索客户 / 申请人") +
          select(
            "status",
            options(
              ["pending", "approved", "rejected", "cancelled"].map((s) => [
                s,
                statuses[s],
              ]),
              state.filters.status,
              "全部状态",
            ),
          ),
      ) +
      table(
        ["申请客户", "申请人", "申请时间", "状态", "审核人 / 意见", "操作"],
        data.items,
        (r) =>
          `<tr><td class="title">${esc(r.customer_name)}</td><td>${esc(r.applicant_name)}<small>${esc(r.account_code)}</small></td><td>${date(r.requested_at)}</td><td>${badge(r.status)}</td><td>${esc(r.reviewer_name || "—")}<small>${esc(r.decision_reason || "")}</small></td><td>${btn("客户资料", "detail", r.customer_id, "link")}${r.status === "pending" ? btn("审核", "review", r.id, "link") : ""}</td></tr>`,
      ) +
      pager(data.total) +
      `</div>`,
    bind: (root) =>
      bindCommon(root, {
        detail: customerDetail,
        review: (id) => {
          const r = data.items.find((x) => x.id === id);
          dialog(
            "审核客户认领",
            details([
              ["客户", r.customer_name],
              ["申请人", r.applicant_name],
            ]) +
              field("审核结果", "decision", "", {
                select: options(
                  [
                    ["approved", "通过认领"],
                    ["rejected", "驳回申请"],
                  ],
                  "approved",
                  null,
                ),
              }) +
              field("审核意见", "reason", "", {
                type: "textarea",
                help: "驳回时须填写原因；通过后会向申请人推送结果。",
              }),
            {
              submit: "确认审核",
              onSubmit: async (fd, key) => {
                if (
                  fd.get("decision") === "rejected" &&
                  !fd.get("reason").trim()
                )
                  throw Error("请填写驳回原因");
                await api(`/claims/${id}/decision`, {
                  method: "POST",
                  key,
                  body: Object.fromEntries(fd),
                });
                toast("审批结果已保存并通知申请人");
              },
            },
          );
        },
      }),
  };
}
export {accounts} from "./organization.js";
const stages = [
  [10, "意向沟通 · 10%"],
  [30, "商机确认 · 30%"],
  [50, "方案沟通 · 50%"],
  [70, "商务谈判 · 70%"],
  [90, "客户签约 · 90%"],
  [100, "赢单 Won · 100%"],
  ["lost", "丢单 Lost"],
];
async function opportunityForm(row = {}) {
  const editing = !!row.id;
  const initial = editing ? null : await api("/customers?limit=100");
  const quarterRows = (row.quarterly_forecasts || [])
    .map(
      (r, i) =>
        `<div class="form-grid quarter-row" data-year="${r.year}" data-quarter="${r.quarter}"><div class="section-title">${r.year} 年 Q${r.quarter}</div>${field("预测含税确收（万元）", `recognized_${i}`, r.recognized_amount === null ? "" : Number(r.recognized_amount) / 10000, { type: "number", min: 0, step: "0.0001" })}${field("预测回款（万元）", `collection_${i}`, r.collection_amount === null ? "" : Number(r.collection_amount) / 10000, { type: "number", min: 0, step: "0.0001" })}</div>`,
    )
    .join("");
  dialog(
    editing ? "编辑商机" : "新增商机",
    `<div class="notice">商机阶段、金额及预计关单日期发生变化后，自动通知商机负责人。</div><div class="form-grid">${
      editing
        ? field("关联客户", "customer_label", row.customer_name, {
            disabled: true,
            full: true,
          })
        : `<label class="field full"><b>搜索公司客户</b><input type="search" id="op-customer-search" placeholder="输入客户名称查询"></label>${field(
            "关联客户",
            "customer_id",
            "",
            {
              required: true,
              full: true,
              select: options(
                initial.items.map((c) => [c.id, c.name]),
                "",
                "请选择客户",
              ),
            },
          )}`
    }${field("商机名称", "name", row.name, { required: true, full: true })}${field("商机阶段", "stage", "", { required: true, select: options(stages, row.status === "lost" ? "lost" : row.probability || 10, null) })}${field("ACV（万元）", "amount_wan", editing ? Number(row.amount) / 10000 : "", { type: "number", min: "0.0001", step: "0.0001", required: true })}${field("预计关单日期", "expected_close_date", row.expected_close_date, { type: "date", required: true })}${field(
      "负责人",
      "owner_user_ref_id",
      "",
      {
        required: !editing,
        disabled: editing,
        select: options(
          state.org.accounts
            .filter((u) => u.status === "active" && u.roles.includes("sales"))
            .map((u) => [u.id, `${u.display_name} · ${u.account_code}`]),
          row.owner_user_ref_id,
          "请选择负责人",
        ),
      },
    )}${field("产品线", "product_line", row.product_line)}${partnerFields(row)}${field("跟进计划", "follow_up_plan", row.follow_up_plan, { type: "textarea", full: true })}<label class="check field full"><input type="checkbox" name="closure">若涉及赢单、丢单或重新打开，我已核实并确认此状态</label></div><details open><summary class="section-title">季度回款与确收计划</summary><p class="help">30% 及以上阶段须至少填写一个完整季度；按年份与季度分别保存。留空表示未填写，0 表示明确为零；预测不作为实际收入。</p>${quarterRows}<div class="form-grid">${field("新增预测年份", "forecast_year", new Date().getFullYear(), { type: "number", min: 2000, max: 2100 })}${field(
      "新增预测季度",
      "forecast_quarter",
      "",
      {
        select: options(
          [1, 2, 3, 4].map((q) => [q, "Q" + q]),
          "",
          "暂不新增",
        ),
      },
    )}${field("预测含税确收（万元）", "forecast_recognized", "", { type: "number", min: 0, step: "0.0001" })}${field("预测回款（万元）", "forecast_collection", "", { type: "number", min: 0, step: "0.0001" })}</div></details>`,
    {
      submit: editing ? "保存商机" : "确认创建",
      onSubmit: async (fd, key, form) => {
        const stage = fd.get("stage"),
          closed = fd.has("closure");
        const body = {
          action: editing ? "update" : "create",
          customer_id: row.customer_id || fd.get("customer_id"),
          name: fd.get("name"),
          amount: String(
            Math.round(Number(fd.get("amount_wan")) * 1000000) / 100,
          ),
          probability: stage === "lost" ? row.probability || 10 : Number(stage),
          status: stage === "lost" ? "lost" : stage === "100" ? "won" : "open",
          closure_confirmed: closed,
          reopen_confirmed: closed,
          expected_close_date: fd.get("expected_close_date"),
          sales_channel: fd.get("sales_channel"),
          partner_id: fd.get("sales_channel") === 'partner' ? fd.get("partner_id") : null,
          product_line: fd.get("product_line"),
          follow_up_plan: fd.get("follow_up_plan"),
          owner_user_ref_id:
            row.owner_user_ref_id || fd.get("owner_user_ref_id"),
        };
        if (editing) {
          body.opportunity_id = row.id;
          body.version_no = row.version_no;
        }
        const amt = (v) =>
          v === "" || v === null ? null : Math.round(Number(v) * 1000000) / 100;
        const forecasts = (row.quarterly_forecasts || []).map((r, i) => ({
          year: r.year,
          quarter: r.quarter,
          recognized_amount: amt(fd.get(`recognized_${i}`)),
          collection_amount: amt(fd.get(`collection_${i}`)),
        }));
        if (fd.get("forecast_quarter"))
          forecasts.push({
            year: Number(fd.get("forecast_year")),
            quarter: Number(fd.get("forecast_quarter")),
            recognized_amount: amt(fd.get("forecast_recognized")),
            collection_amount: amt(fd.get("forecast_collection")),
          });
        else if (fd.get("forecast_recognized") || fd.get("forecast_collection"))
          throw Error("请为新增预测选择季度");
        if (forecasts.length) body.quarterly_forecasts = forecasts;
        const result = await api("/opportunities", {
          method: "POST",
          key,
          body,
        });
        toast(result.changed ? "商机已保存，变化通知已生成" : "内容未变化");
      },
      afterRender: (d) => {
        bindPartnerFields(d, row);
        let timer,
          version = 0;
        const input = $("#op-customer-search", d);
        if (input)
          input.oninput = () => {
            clearTimeout(timer);
            const seq = ++version;
            timer = setTimeout(async () => {
              try {
                const result = await api(
                  "/customers?q=" +
                    encodeURIComponent(input.value) +
                    "&limit=100",
                );
                if (seq === version)
                  $("[name=customer_id]", d).innerHTML = options(
                    result.items.map((c) => [c.id, c.name]),
                    "",
                    "请选择客户",
                  );
              } catch (e) {
                toast(e.message);
              }
            }, 250);
          };
      },
    },
  );
}
async function opportunityDetail(id) {
  const r = await api("/opportunities/" + id);
  dialog(
    r.name,
    details([
      ["关联客户", r.customer_name],
      ["负责人", r.owner_name],
      ["ACV", money(r.amount)],
      [
        "当前阶段",
        stages.find(
          (s) =>
            String(s[0]) ===
            String(r.status === "lost" ? "lost" : r.probability),
        )?.[1],
      ],
      ["预计关单日期", r.expected_close_date],
      ["所属伙伴", r.partner_name || "未填写"],
      ["跟进计划", r.follow_up_plan || "未填写"],
    ]) +
      `<h3>历史修改记录</h3>` +
      history(
        r.changes.map((c) => ({
          title: c.operator || "操作人",
          text: (c.changes || [])
            .map((x) => `${x.label}：${x.before} → ${x.after}`)
            .join("；"),
          meta: date(c.created_at),
        })),
      ) +
      `<h3>关联拜访记录</h3>` +
      history(
        r.visits.map((v) => ({
          title: v.recorder || "跟进人",
          text: v.follow_up_record || "",
          meta: date(v.interaction_at),
        })),
      ) +
      `<h3>关联报价</h3>` +
      table(
        ["报价编号", "名称", "金额", "链接"],
        r.quote_references,
        (q) =>
          `<tr><td>${esc(q.reference_no)}</td><td>${esc(q.title)}</td><td>${money(q.amount)}</td><td>${q.url && /^https?:\/\//.test(q.url) ? `<a href="${esc(q.url)}" target="_blank" rel="noopener noreferrer">查看外部报价 ↗</a>` : "—"}</td></tr>`,
        "尚未关联报价",
      ),
    {
      wide: true,
      footer:
        btn("关联报价", "quote", id) + btn("编辑商机", "edit", id, "primary"),
      afterRender: (d) => {
        d.querySelector("[data-action=edit]").onclick = () => {
          d.close();
          opportunityForm(r);
        };
        d.querySelector("[data-action=quote]").onclick = () => {
          d.close();
          dialog(
            "关联外部报价",
            `<div class="notice">仅记录已有报价的编号和链接，报价制作与审批仍按公司流程完成。</div><div class="form-grid">${field("报价编号", "reference_no", "", { required: true })}${field("报价名称", "title", "", { required: true })}${field("链接", "url", "", { type: "url", full: true })}${field("报价金额（元）", "amount", "", { type: "number", min: 0, step: "0.01" })}</div>`,
            {
              onSubmit: async (fd, key) => {
                const data = Object.fromEntries(fd);
                data.amount = data.amount ? Number(data.amount) : null;
                data.url ||= null;
                await api(`/opportunities/${id}/quotes`, {
                  method: "POST",
                  key,
                  body: data,
                });
                toast("报价已关联");
              },
            },
          );
        };
      },
    },
  );
}
export async function opportunities() {
  const data = await api(
    "/opportunities" + query({ limit: 50, offset: state.offset }),
  );
  return {
    html:
      head(
        "商机管理",
        "统一维护商机进展，变化自动通知负责人。",
        btn("导出商机", "export") + btn("＋ 新增商机", "create", "", "primary"),
      ) +
      stats([
        ["商机总数", num(data.total), "当前筛选范围"],
        ["ACV 合计", money(data.total_amount), "按商机记录汇总"],
        [
          "推进中",
          num(
            data.stages
              .filter((x) => x.status === "open")
              .reduce((n, x) => n + x.count, 0),
          ),
          "阶段 10%–90%",
        ],
        [
          "已赢单",
          num(
            data.stages
              .filter((x) => x.status === "won")
              .reduce((n, x) => n + x.count, 0),
          ),
          "已人工确认",
        ],
      ]) +
      `<div class="card">` +
      filters(
        search("搜索商机 / 客户名称") +
          select("owner", people(state.filters.owner, "全部负责人")) +
          select(
            "status",
            options(
              [
                ["open", "推进中"],
                ["won", "赢单"],
                ["lost", "丢单"],
              ],
              state.filters.status,
              "全部状态",
            ),
          ) +
          select(
            "probability",
            options(
              stages.filter((x) => x[0] !== "lost"),
              state.filters.probability,
              "全部阶段",
            ),
          ) +
          `<input type="date" name="close_from" aria-label="预计关单起始日期" value="${esc(state.filters.close_from || "")}"><input type="date" name="close_to" aria-label="预计关单结束日期" value="${esc(state.filters.close_to || "")}">`,
      ) +
      table(
        ["商机 / 客户", "阶段", "ACV", "负责人", "预计关单日期", "操作"],
        data.items,
        (r) =>
          `<tr><td class="title">${esc(r.name)}<small>${esc(r.customer_name)}</small></td><td>${badge(r.status)} ${r.probability ?? "—"}%<div class="progress"><i style="width:${Math.max(0, Math.min(100, Number(r.probability) || 0))}%"></i></div></td><td>${money(r.amount)}</td><td>${esc(r.owner_name || "—")}</td><td>${esc(r.expected_close_date)}</td><td>${btn("详情", "detail", r.id, "link")}${btn("编辑", "edit", r.id, "link")}</td></tr>`,
      ) +
      pager(data.total) +
      `</div><div class="card card-pad"><h2>阶段分布</h2><div class="actions">${data.stages.map((s) => `<span class="pill">${esc(stages.find((x) => String(x[0]) === String(s.status === "lost" ? "lost" : s.probability))?.[1] || s.stage_code)} · ${s.count} 个 · ${money(s.amount)}</span>`).join("") || '<span class="help">当前筛选范围没有商机</span>'}</div></div>`,
    bind: (root) =>
      bindCommon(root, {
        create: () => opportunityForm(),
        edit: async (id) => opportunityForm(await api("/opportunities/" + id)),
        detail: opportunityDetail,
        export: () => download("/opportunities/export" + query(), "商机列表"),
      }),
  };
}
function ruleForm(row = {}) {
  dialog(
    row.id ? "编辑用量提醒" : "新增用量提醒",
    `<div class="notice">达到阈值后提醒运营与系统管理员，不阻断业务调用。未上报的 Token 不计作零。</div><div class="form-grid">${field("规则名称", "name", row.name, { required: true, full: true })}${field("角色范围", "role_code", "", { select: roleOptions(row.role_code) })}${field(
      "统计周期",
      "period",
      "",
      {
        select: options(
          [
            ["day", "每日"],
            ["week", "每周"],
            ["month", "每月"],
          ],
          row.period || "day",
          null,
        ),
      },
    )}${field("调用次数阈值", "calls_limit", row.calls_limit, { type: "number", min: 1 })}${field("Token 阈值", "tokens_limit", row.tokens_limit, { type: "number", min: 1 })}<label class="check"><input type="checkbox" name="enabled" ${row.enabled !== false ? "checked" : ""}>启用提醒</label></div>`,
    {
      onSubmit: async (fd, key) => {
        const data = Object.fromEntries(fd);
        data.role_code ||= null;
        data.calls_limit = data.calls_limit ? Number(data.calls_limit) : null;
        data.tokens_limit = data.tokens_limit
          ? Number(data.tokens_limit)
          : null;
        data.enabled = fd.has("enabled");
        if (row.id) data.version_no = row.version_no;
        await api("/ai/rules" + (row.id ? "/" + row.id : ""), {
          method: row.id ? "PUT" : "POST",
          body: data,
          key,
        });
        toast("用量规则已保存");
      },
    },
  );
}
export async function aiUsage(context = {}) {
  let calls = {items: []};
  let rules = {items: [], alerts: []};
  const offset = state.offset;
  const partial = pageSections("ai", context, {
    overview: {
      label: "调用概览", path: "/ai/overview" + query(), reusable: true,
      render: (report) => {
        const s = report.summary;
        const completed = s.succeeded + s.failed + s.cancelled;
        const trend = report.trend;
        const maximum = Math.max(1, ...trend.map((x) => x.calls));
        return stats([
          ["实际请求次数", num(s.calls), "包含真实发生的重试"],
          [
            "已知用量",
            s.input_tokens === null && s.output_tokens === null
              ? "未上报"
              : num((s.input_tokens || 0) + (s.output_tokens || 0)),
            `${s.unknown_token_calls} 次未完整上报`,
          ],
          [
            "请求完成率",
            completed ? ((100 * s.succeeded) / completed).toFixed(1) + "%" : "—",
            `${s.failed} 次失败 · ${s.running} 次处理中；业务结果见智能体审计`,
          ],
          [
            "平均响应耗时",
            s.average_latency_ms === null
              ? "—"
              : (s.average_latency_ms / 1000).toFixed(2) + " 秒",
            `${num(s.business_operations)} 次业务操作`,
          ],
        ]) +
        `<div class="split"><div class="card card-pad"><h2>调用趋势</h2>${trend.length ? `<div class="chart">${trend.map((t) => `<div class="chart-col" title="${esc(t.day)}：${t.calls} 次"><span>${num(t.calls)}</span><div class="chart-bar" style="height:${Math.max(2, (130 * t.calls) / maximum)}px"></div><span>${esc(t.day.slice(5))}</span></div>`).join("")}</div>` : '<div class="empty">所选周期暂无实际调用</div>'}</div><div class="card card-pad"><h2>角色用量</h2>${report.roles.map((r) => `<div class="barline"><span>${esc(roles[r.role] || "历史未记录角色")}</span><strong>${num(r.calls)} 次 <small>· ${r.users} 人</small></strong></div>`).join("") || '<p class="help">所选周期暂无角色用量</p>'}<p class="help">历史逻辑调用 ${num(report.legacy_logical_calls)} 次，单独保留，不与实际网络请求混算。<br>音频时长：${s.audio_seconds === null ? "未上报" : num(s.audio_seconds) + " 秒"}。</p></div></div>`;
      },
    },
    list: {
      label: "调用明细", path: "/ai/calls" + query({limit: 50, offset}),
      render: (result) => {
        calls = result;
        return table(
          [
            "调用时间",
            "操作人 / 角色",
            "业务能力 / 实际调用",
            "输入 / 输出用量",
            "耗时",
            "结果",
            "详情",
          ],
          calls.items,
          (r) =>
            `<tr><td>${date(r.started_at)}</td><td>${esc(r.actor_name || "未记录")}<small>${esc(roles[r.actor_role_code] || r.actor_role_code || "—")}</small></td><td>${esc(capabilityLabel(r.operation_code || r.endpoint_code))}<small>${esc(modelName(r.model_id))}</small></td><td>${num(r.input_tokens)} / ${num(r.output_tokens)}</td><td>${r.latency_ms === null ? "—" : num(r.latency_ms) + " 毫秒"}</td><td>${badge(r.status)}<small>${esc(statuses[r.record_kind] || "历史记录类型未标注")}</small></td><td>${btn("查看", "call", r.id, "link")}</td></tr>`,
        ) +
        pager(calls.total, offset);
      },
    },
    rules: {
      label: "用量提醒", path: "/ai/rules", reusable: true,
      render: (result) => {
        rules = result;
        return `<div class="card"><div class="card-head"><h2>用量提醒</h2>${btn("新增提醒", "rule")}</div>` + table(
          ["规则", "角色", "周期", "阈值", "状态", "操作"],
          rules.items,
          (r) =>
            `<tr><td>${esc(r.name)}</td><td>${esc(roles[r.role_code] || "全部角色")}</td><td>${{ day: "每日", week: "每周", month: "每月" }[r.period]}</td><td>${r.calls_limit ? num(r.calls_limit) + " 次" : ""} ${r.tokens_limit ? num(r.tokens_limit) + " Token" : ""}</td><td>${badge(r.enabled ? "active" : "inactive")}</td><td>${btn("编辑", "edit-rule", r.id, "link")}</td></tr>`,
        ) +
        `</div><div class="card card-pad"><h2>最近用量告警</h2>` +
        history(
          rules.alerts.map((a) => ({
            title: a.name,
            text: `实际调用 ${num(a.calls_count)} 次 · 已知用量 ${num(a.known_tokens)} · 未完整上报 ${num(a.unknown_usage_calls)} 次`,
            meta: date(a.created_at),
          })),
        ) + `</div>`;
      },
    },
  });
  return {
    html:
      head(
        "AI 调用管理",
        "查看真实调用与角色用量，及时发现异常和用量变化。",
        `<a class="button" href="#modelApis">接口配置</a>` + btn("刷新", "refresh") + btn("导出角色用量", "export-roles") + btn("导出调用明细", "export"),
      ) +
      `<div class="card">` +
      filters(
        period() +
          select("role", roleOptions(state.filters.role)) +
          select("user", people(state.filters.user)),
      ) +
      `</div>` +
      partial.region("overview") +
      `<div class="card"><div class="card-head"><h2>调用明细</h2><small>时间按北京时间展示</small></div>` +
      partial.region("list") + `</div>` + partial.region("rules"),
    bind: (root) =>
      partial.bind(root, {
        export: () => download("/ai/calls/export" + query(), "AI调用明细"),
        "export-roles": () =>
          download("/ai/roles/export" + query(), "AI角色用量"),
        rule: () => ruleForm(),
        "edit-rule": (id) => ruleForm(rules.items.find((r) => r.id === id)),
        call: (id) => {
          const c = calls.items.find((x) => x.id === id);
          dialog(
            "调用详情",
            details([
              ["时间", date(c.started_at)],
              ["操作人", c.actor_name],
              ["当时角色", roles[c.actor_role_code]],
              ["业务能力", capabilityLabel(c.operation_code || c.endpoint_code)],
              ["业务标识", capabilityLabel(c.operation_code)],
              ["接口", c.provider_code === "agent_platform" ? "中台智能体对话接口" : "原模型调用接口"],
              ["实际调用", modelName(c.model_id)],
              ["智能体编号", agentId(c.model_id)],
              ["提供方", c.request_summary?.connection_provider || label(providers,c.provider_code,"未记录提供方")],
              ["接口配置版本", c.request_summary?.connection_version ? "第 " + c.request_summary.connection_version + " 版" : "现有默认接口"],
              ["结果", statuses[c.status]],
              ["尝试序号", c.attempt_no],
              ["网络状态码", c.http_status],
              ["异常原因", errorName(c.error_code)],
              ["请求标识", c.request_id],
              ["音频秒数", c.audio_seconds],
            ]) +
              `<h3>关联信息</h3>${details([["上游链路编号",c.upstream_trace_id]])}<p class="help">保留调用元数据；不展示客户原文、音频内容或模型凭证。</p>`,
          );
        },
      }),
  };
}
export async function audit() {
  const data = await api("/audit" + query({ limit: 50, offset: state.offset }));
  return {
    html:
      head(
        "技术审计日志",
        "查询接口请求和底层数据变更，辅助技术核查与问题追溯。",
        btn("导出审计日志", "export"),
      ) +
      `<div class="notice">业务变更在同一数据库事务内留痕。审计记录只读，应用内不可修改或删除。</div><div class="card">` +
      filters(
        period() +
          search("搜索操作人 / 对象 / 请求标识") +
          select("actor", people(state.filters.actor, "全部操作人")) +
          select("role", roleOptions(state.filters.role)) +
          `<input name="module" placeholder="模块，如 crm / platform" value="${esc(state.filters.module || "")}" aria-label="模块"><input name="action" placeholder="操作，如 update / export" value="${esc(state.filters.action || "")}" aria-label="操作">`,
      ) +
      table(
        [
          "操作时间",
          "操作人 / 当时角色",
          "模块 / 操作",
          "操作对象",
          "IP 地址",
          "结果",
          "详情",
        ],
        data.items,
        (r) =>
          `<tr><td>${date(r.occurred_at)}</td><td>${esc(r.actor_name || "系统")}<small>${esc(roles[r.actor_role_code] || r.actor_role_code || "—")}</small></td><td>${esc(r.object_type || r.module_code)}<small>${esc(r.action_code)}</small></td><td>${esc(r.object_label || r.object_id || "—")}</td><td>${esc(r.client_ip || "—")}</td><td>${esc(r.result_code || "success")}</td><td>${btn("查看变更", "detail", r.id, "link")}</td></tr>`,
      ) +
      pager(data.total) +
      `</div>`,
    bind: (root) =>
      bindCommon(root, {
        export: () => download("/audit/export" + query(), "技术审计日志"),
        detail: (id) => {
          const r = data.items.find((x) => String(x.id) === id);
          dialog(
            "操作详情",
            details([
              ["操作人", r.actor_name || "系统"],
              ["时间", date(r.occurred_at)],
              ["操作", r.action_code],
              ["请求标识", r.request_id],
              ["IP 地址", r.client_ip],
              ["变更字段", (r.changed_fields || []).join("、")],
            ]) +
              `<div class="split"><section><h3>变更前</h3>${jsonBlock(r.before_snapshot)}</section><section><h3>变更后 / 请求结果</h3>${jsonBlock(r.after_snapshot)}</section></div>`,
            { wide: true },
          );
        },
      }),
  };
}
export async function systemLogs() {
  const data = await api(
    "/system-events" + query({ limit: 50, offset: state.offset }),
  );
  return {
    html:
      head(
        "系统运行日志",
        "查询服务运行事件、接口异常和权限校验失败，辅助运维排查。",
        btn("导出系统日志", "export"),
      ) +
      `<div class="notice">系统日志在线保留 90 天，支持导出存档；业务审计独立永久保留。系统管理员可查看公共服务事件。</div><div class="card">` +
      filters(
        period() +
          select(
            "level",
            options(["INFO", "WARN", "ERROR"], state.filters.level, "全部级别"),
          ) +
          `<input name="module" placeholder="服务模块" aria-label="服务模块" value="${esc(state.filters.module || "")}">` +
          search("搜索详情 / 请求标识"),
      ) +
      table(
        ["时间", "级别", "服务模块", "事件", "日志详情", "操作"],
        data.items,
        (r) =>
          `<tr><td>${date(r.occurred_at)}</td><td><span class="badge ${r.level === "ERROR" ? "red" : r.level === "WARN" ? "amber" : "blue"}">${esc(r.level)}</span></td><td>${esc(r.service_module)}</td><td>${esc(r.event_type)}</td><td>${esc(r.detail)}</td><td>${btn("查看", "detail", r.id, "link")}</td></tr>`,
      ) +
      pager(data.total) +
      `</div>`,
    bind: (root) =>
      bindCommon(root, {
        export: () =>
          download("/system-events/export" + query(), "系统状态日志"),
        detail: (id) => {
          const r = data.items.find((x) => x.id === id);
          dialog(
            "系统事件详情",
            details([
              ["时间", date(r.occurred_at)],
              ["级别", r.level],
              ["服务模块", r.service_module],
              ["请求标识", r.request_id],
            ]) +
              `<h3>日志详情</h3><pre class="code">${esc(r.detail)}</pre><h3>错误堆栈</h3><pre class="code">${esc(r.error_stack || "无错误堆栈")}</pre>`,
            { wide: true },
          );
        },
      }),
  };
}
