require('./helpers/business-options');
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

async function loadPage(savedDraft = null, draftKey = "customerCreateDraft:w1:u1") {
  let definition;
  const apiCalls = [];
  const toasts = [];
  const storage = new Map(savedDraft ? [[draftKey, savedDraft]] : []);
  const context = {
    require: (name) => name.includes('businessOptions') ? require('../miniprogram/utils/businessOptions') : name.includes('access') ? require('../miniprogram/utils/access') : name.includes('customerLevel') ? require('../miniprogram/utils/customerLevel') : name.includes('draftScope') ? require('../miniprogram/utils/draftScope') : ({
      getBusinessOptions: () => Promise.resolve(require('./helpers/business-options.json')),
      getTeamDirectory: () => Promise.resolve({ teams: [{id:'south',name:'南区'}] }),
      createCustomer: (payload) => { apiCalls.push(payload); return Promise.resolve({ id: "customer-1" }); },
      transcribeAudio: () => Promise.reject(new Error("not used")),
      runAgent: () => Promise.reject(new Error("not used")),
    }),
    Page: (page) => { definition = page; },
    getApp: () => ({
      ensureLogin: () => true,
      globalData: {
        session: { userId: "u1", workspaceId: "w1", role: "operations", capabilities:{"customer.create":true}, userName: "刘志德", team: "南区" },
        roles: { sales: { name: "一线销售" } },
      },
    }),
    wx: {
      getStorageSync: (key) => storage.get(key),
      getRecorderManager: undefined,
      showToast: (toast) => toasts.push(toast.title),
      vibrateShort: () => {},
      showModal: ({ success }) => success({ confirm: true }),
      setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: (key) => storage.delete(key),
      navigateBack: () => {},
    },
    setInterval,
    clearInterval,
    setTimeout: () => {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../miniprogram/pages/customer-create/index.js"), "utf8"), context);
  const page = { ...definition, data: { ...definition.data }, setData(update) { Object.assign(this.data, update); } };
  await page.onLoad();
  await Promise.resolve();
  page.refresh(page.data.fields.map(f=>f.key==='target_team'?{...f,value:'南区',teamId:'south'}:f));
  return { page, apiCalls, toasts, storage };
}

test("customer create includes the shared Tier customer level", async () => {
  const { page } = await loadPage();
  assert.deepEqual([...page.data.fields.map((field) => field.key)], [
    "customer_name", "industry", "customer_type", "level_code", "lead_source", "target_team",
    "partner_name", "contact_name", "contact_title", "contact_role",
  ]);
  assert.equal(page.data.fields.find((field) => field.key === "customer_name").required, true);
  assert.equal(page.data.fields.find((field) => field.key === "industry").required, false);
  assert.equal(page.data.fields.find((field) => field.key === "partner_name").required, false);
  assert.equal(page.data.fields.find((field) => field.key === "contact_role").required, true);

  page.openEditor({ currentTarget: { dataset: { index: 2 } } });
  assert.deepEqual([...page.data.editorOptions.map((option) => option.label)], require("./helpers/business-options.json").customer.customer_type);
  page.openEditor({ currentTarget: { dataset: { index: 3 } } });
  assert.deepEqual([...page.data.editorOptions.map((option) => option.label)], ["Tier-1", "Tier-2", "Tier-3"]);
  page.openEditor({ currentTarget: { dataset: { index: 4 } } });
  assert.deepEqual([...page.data.editorOptions.map((option) => option.label)], require("./helpers/business-options.json").customer.source);
  page.openEditor({ currentTarget: { dataset: { index: 9 } } });
  assert.deepEqual([...page.data.editorOptions.map((option) => option.label)], ["使用者", "影响者", "决策者"]);
});

test("optional fields may stay blank while a missing contact role blocks submission", async () => {
  const { page, apiCalls, toasts, storage } = await loadPage();
  page.applyVoiceDraft({ customer_name: "验收客户", source: "销售自拓", level_code: "Tier-2", contact_name: "联系人", contact_title: "采购经理" });
  assert.equal(page.data.missingCount, 1);
  page.submitCustomer();
  assert.equal(apiCalls.length, 0);
  page.openEditor({ currentTarget: { dataset: { index: 9 } } });
  page.selectOption({ currentTarget: { dataset: { index: 2 } } });
  page.saveEditor();
  assert.equal(page.data.missingCount, 0);
  assert.equal(page.data.progressPercent, 100);
  page.submitCustomer();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(apiCalls.length, 1);
  assert.equal(apiCalls[0].contact_role, "决策者");
  assert.equal(apiCalls[0].level_code, "Tier-2");
  assert.equal(apiCalls[0].industry, "");
  assert.equal(apiCalls[0].partner_name, "");
  assert.ok(toasts.includes("客户创建成功"));
  assert.equal(storage.has("customerCreateDraft:w1:u1"), false);
  for (const removed of ["opportunity_name", "estimated_amount", "demand_summary", "next_action"]) {
    assert.equal(Object.hasOwn(apiCalls[0], removed), false, `${removed} must not be submitted`);
  }
});

test("legacy drafts restore values into the new schema without reviving deleted fields", async () => {
  const { page, storage } = await loadPage({ creator: "刘志德", fields: [
    { key: "customer_name", value: "上次填写的客户" },
    { key: "industry", value: "", required: true },
    { key: "partner_name", value: "", required: true },
    { key: "lead_source", value: "公司分配", label: "客户 / 线索来源" },
    { key: "target_team", value: "旧团队" },
    { key: "opportunity_name", value: "旧商机", required: true },
    { key: "estimated_amount", value: "100", required: true },
    { key: "demand_summary", value: "旧需求", required: true },
    { key: "next_action", value: "旧行动", required: true },
  ] });
  assert.equal(page.data.draftRestored, true);
  assert.equal(page.data.fields.length, 10);
  assert.equal(page.data.customerName, "上次填写的客户");
  assert.equal(page.data.requiredCount, 8);
  assert.equal(page.data.fields.find((field) => field.key === "lead_source").value, "销售线索");
  assert.equal(page.data.fields.find((field) => field.key === "target_team").value, "南区");
  assert.equal(page.data.fields.find((field) => field.key === "contact_role").missing, true);
  page.saveDraft();
  const { page: restored } = await loadPage(storage.get("customerCreateDraft:w1:u1"));
  assert.equal(restored.data.fields.length, 10);
  assert.equal(restored.data.customerName, "上次填写的客户");
});

test("voice suggestions cannot introduce unsupported customer types or contact roles", async () => {
  const { page } = await loadPage();
  page.applyVoiceDraft({ customer_type: "旧类型", source: "公司分配", contact_role: "采购负责人", opportunity_name: "旧商机" });
  assert.equal(page.data.fields.find((field) => field.key === "customer_type").value, "潜在客户");
  assert.equal(page.data.fields.find((field) => field.key === "lead_source").value, "销售线索");
  assert.equal(page.data.fields.find((field) => field.key === "contact_role").missing, true);
  assert.equal(page.data.fields.some((field) => field.key === "opportunity_name"), false);
  page.applyVoiceDraft({ level_code: "A级" });
  assert.equal(page.data.fields.find((field) => field.key === "level_code").value, "");
});


test("customer drafts never restore another account or a legacy name-only key", async () => {
  for (const key of ["customerCreateDraft:w1:u2", "customerCreateDraft:w2:u1", "customerCreateDraft"]) {
    const {page} = await loadPage({creator:"刘志德",fields:[{key:"customer_name",value:"别人的客户"}]}, key);
    assert.equal(page.data.draftRestored,false);
    assert.equal(page.data.customerName,"待创建客户");
  }
});
