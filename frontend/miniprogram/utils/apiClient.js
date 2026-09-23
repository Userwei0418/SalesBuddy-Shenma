/**
 * BACKEND-CONTRACT：本文件是当前小程序 HTTP/上传调用边界，不等同于后端已实现的接口保证。
 * 完整 METHOD、PATH、调用方、字段及未接通项见 docs/backend-handoff/接口契约清单.md。
 * 普通成功响应直接返回 JSON 本体；不要额外套 {data: ...}。列表按各接口返回 items 等分页字段。
 * 配置 API_BASE_URL 已包含 /api/v1；本文件 path 均为相对路径，避免重复拼接版本前缀。
 * 演示数据应显式标记 DEMO-DATA，不可把鉴权失败、超时或合法零值转成真实业务金额。
 */
const AUTH_STORAGE_KEY = "salesApiAuth";
const BASE_URL_STORAGE_KEY = "salesApiBaseUrl";
const { API_BASE_URL } = require("../config");
const DEFAULT_BASE_URL = API_BASE_URL;
const requestIdentity = require('./requestIdentity');
const { notificationDisplayResponse, timelineDisplayResponse } = require('./demoDisplay');
const mutationsInFlight = new Map();
const readsInFlight = new Map();
let readGeneration = 0;
let authGeneration = 0;
let refreshing = null;
let optionsFlight = null;
const businessOptions = require('./businessOptions');

// BACKEND-CONTRACT：本地 salesApiBaseUrl 可覆盖配置地址；发布/解压联调需核对实际地址。
function getBaseUrl() {
  return String(wx.getStorageSync(BASE_URL_STORAGE_KEY) || DEFAULT_BASE_URL).replace(/\/$/, "");
}

function getAuth() {
  return wx.getStorageSync(AUTH_STORAGE_KEY) || null;
}

function writeAuth(auth) {
  if (auth) wx.setStorageSync(AUTH_STORAGE_KEY, auth);
  else wx.removeStorageSync(AUTH_STORAGE_KEY);
}

function invalidateReads() {
  readGeneration += 1;
  readsInFlight.clear();
}

function invalidateSession() {
  authGeneration += 1;
  optionsFlight=null;businessOptions.clear();
  invalidateReads();
  refreshing = null;
}

// A login, logout, or explicit session replacement starts a new generation,
// including when the user logs back into the same account. Token rotation does not.
function saveAuth(auth) {
  invalidateSession();
  writeAuth(auth);
}

function currentSession() {
  return { generation: authGeneration, baseUrl: getBaseUrl() };
}

function isCurrentSession(session) {
  return session.generation === authGeneration && session.baseUrl === getBaseUrl();
}

function assertCurrentSession(session) {
  if (!isCurrentSession(session)) {
    throw Object.assign(new Error("登录状态已变更，请在当前账号下重试"), { code: "SESSION_CHANGED" });
  }
}

// BACKEND-CONTRACT：JSON 请求 15 秒超时，Bearer 鉴权；2xx 直接返回响应本体。
// 非 2xx 只保留 HTTP 状态及 message/detail 文案（目前不透传业务错误 code/details）。
// 这里不暴露 RequestTask.abort；页面忽略迟到结果不等于取消后台事务。
function requestRaw({ path, method = "GET", data, token, idempotencyKey, baseUrl = getBaseUrl() }) {
  if (!baseUrl) return Promise.reject(Object.assign(new Error("API 尚未配置"), { code: "API_DISABLED" }));
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl}${path}`,
      method,
      data,
      timeout: 15000,
      header: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}) },
      success: (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
          return;
        }
        const detail = response.data && (response.data.message || response.data.detail);
        const message = Array.isArray(detail) ? detail.map(item=>String(item.msg || "字段格式不正确").replace(/^Value error, /, "")).join("；") : detail;
        reject(Object.assign(new Error(message || `API 请求失败（${response.statusCode}）`), { code: `HTTP_${response.statusCode}`, statusCode: response.statusCode }));
      },
      fail: (error) => reject(Object.assign(new Error(error.errMsg || "网络连接失败"), { code: "NETWORK_ERROR" })),
    });
  });
}

// BACKEND-CONTRACT：POST /auth/refresh 输入 refresh_token，返回完整 auth 对象覆盖本地存储。
// 同一会话并发 401 共享一次刷新；原请求最多再发一次。切换/退出后丢弃旧响应，不能替新账号重试。
// 后端仍须独立校验 token、租户、权限。
async function refreshAccessToken(session, rejectedToken) {
  assertCurrentSession(session);
  const auth = getAuth();
  if (!auth || !auth.refresh_token) throw Object.assign(new Error("登录已失效"), { code: "AUTH_REQUIRED" });
  // A concurrent request may report its old 401 after another refresh finished.
  if (auth.access_token !== rejectedToken) return auth;
  if (!refreshing || refreshing.generation !== session.generation) {
    const flight = { generation: session.generation };
    flight.promise = requestRaw({ path: "/auth/refresh", method: "POST",
      data: { refresh_token: auth.refresh_token }, baseUrl: session.baseUrl })
      .then((next) => { assertCurrentSession(session); writeAuth(next); return next; })
      .catch(error => { assertCurrentSession(session); throw error; })
      .finally(() => { if (refreshing === flight) refreshing = null; });
    refreshing = flight;
  }
  return refreshing.promise;
}

function authenticatedAttempt(send, session, retried = false) {
  try { assertCurrentSession(session); } catch (error) { return Promise.reject(error); }
  const auth = getAuth();
  return send(auth).then(result => { assertCurrentSession(session); return result; }).catch((error) => {
    assertCurrentSession(session);
    if (!retried && error.statusCode === 401 && auth && auth.refresh_token) {
      return refreshAccessToken(session, auth.access_token)
        .then(() => authenticatedAttempt(send, session, true));
    }
    throw error;
  });
}

function requestAttempt(options, session) {
  return authenticatedAttempt(auth => requestRaw({ ...options, token: auth && auth.access_token,
    baseUrl: session.baseUrl }), session);
}

// BACKEND-CONTRACT：仅 requestIdentity.supports 命中的 POST/PATCH 自动加 Idempotency-Key。
// 同一登录会话内，同租户/用户/地址 + 同 method/path/body 共用进行中 Promise。
// 超时/5xx 或切换账号后的迟到响应保留 key；同账号重新登录可安全重试未确定的提交。
// 这不是全局写接口幂等；Agent、导入重试、风险解除等未在白名单，详见契约清单。
function request(options) {
  const session = currentSession();
  const auth = getAuth();
  if ((options.method || 'GET').toUpperCase() === 'GET') {
    const key = JSON.stringify([session.generation, session.baseUrl, readGeneration, options.path, options.data]);
    let shared = readsInFlight.get(key);
    if (!shared) {
      shared = requestAttempt(options, session).finally(() => {
        if (readsInFlight.get(key) === shared) readsInFlight.delete(key);
      });
      readsInFlight.set(key, shared);
    }
    // wx.request returns JSON; isolate consumers that decorate arrays in place.
    return shared.then(value => value === undefined ? value : JSON.parse(JSON.stringify(value)));
  }
  invalidateReads();
  if (!requestIdentity.supports(options) || !auth || !auth.actor) {
    return requestAttempt(options, session).finally(invalidateReads);
  }
  let identity;
  try {
    identity = requestIdentity.begin([getBaseUrl(),auth.actor.workspace_id,auth.actor.user_id],options);
  } catch (error) { return Promise.reject(new Error('暂时无法保存提交标识，请稍后重试')); }
  const flightKey = `${session.generation}:${identity.fingerprint}`;
  if (mutationsInFlight.has(flightKey)) return mutationsInFlight.get(flightKey);
  const promise = requestAttempt({...options,idempotencyKey:identity.key}, session)
    .then(result => { requestIdentity.finish(identity);return result; })
    .catch(error => {
      // A timeout, 5xx or auth refresh failure may follow a committed request.
      // Keep its key across pages and app restarts; deterministic rejections can be corrected.
      if ([400,403,404,409,422].includes(error.statusCode)) requestIdentity.finish(identity);
      throw error;
    }).finally(() => { mutationsInFlight.delete(flightKey); invalidateReads(); });
  mutationsInFlight.set(flightKey,promise);
  return promise;
}

// Native login verifies the password on the server and returns a real bearer session.
function loginWithAccount(accountCode, password, role) {
  invalidateSession();
  const session = currentSession();
  return requestRaw({path: "/auth/password/login", method: "POST",
    data: {account_code: accountCode, password, role}, baseUrl: session.baseUrl,
  }).then((auth) => { assertCurrentSession(session); saveAuth(auth); return auth; });
}
function changePassword(oldPassword, newPassword) {
  const session = currentSession();
  return request({path: "/auth/password", method: "POST", data: {old_password:oldPassword,new_password:newPassword}})
    .then(result => { assertCurrentSession(session); const auth=getAuth();
      if(auth)writeAuth({...auth,must_change_password:false});return result; });
}

// BACKEND-CONTRACT：先清本地 auth，再异步 POST /auth/logout 撤销服务端会话；失败被吞掉。
// 本地退出成功不证明服务端 token 已撤销；网络异常下撤销/过期策略需后端保证。
function logout() {
  const auth = getAuth();
  saveAuth(null);
  if (!auth) return Promise.resolve();
  return requestRaw({ path: "/auth/logout", method: "POST", token: auth.access_token }).catch(() => undefined);
}

// BACKEND-CONTRACT：Agent 为三段调用：POST /conversations -> POST /{id}/messages -> GET /agent/runs/{id}。
// 会话返回 id；消息受理返回 run_id；业务结果在 run.result，不是消息响应中立即返回。
// client_message_id 每次调用生成；不能视作 requestIdentity 持久化重试键。
function createConversation(mode = "chatbi", customerId = null, opportunityId = null) {
  return request({ path: "/conversations", method: "POST", data: { mode, customer_id: customerId, ...(opportunityId ? {opportunity_id:opportunityId} : {}) } });
}

function sendMessage(conversationId, text, inputSource = "text") {
  return request({
    path: `/conversations/${conversationId}/messages`,
    method: "POST",
    data: { text, input_source: inputSource, client_message_id: `wx_${Date.now()}_${Math.random().toString(16).slice(2)}` },
  });
}

function getRun(runId) {
  return request({ path: `/agent/runs/${runId}` });
}

// BACKEND-CONTRACT：默认轮询间隔 700ms、截止 30s；不同功能为 60s/120s。
// succeeded/waiting_human 结束等待；failed/cancelled 拒绝。调用方仍需核对 result 结构。
// 超时只停止后续轮询，不取消 Agent；当前没有 cancel-run 请求。
function waitForRun(runId, options = {}) {
  const interval = options.interval || 700;
  const timeout = options.timeout || 30000;
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      getRun(runId).then((run) => {
        if (["succeeded", "waiting_human"].includes(run.status)) { resolve(run); return; }
        if (["failed", "cancelled"].includes(run.status)) { reject(Object.assign(new Error(run.error_detail || "Agent 处理失败"), { code: run.error_code || "RUN_FAILED" })); return; }
        if (Date.now() - startedAt >= timeout) { reject(Object.assign(new Error("Agent 处理超时，请稍后查看"), { code: "RUN_TIMEOUT" })); return; }
        setTimeout(poll, interval);
      }).catch(reject);
    };
    poll();
  });
}

function queryChatBI(question) {
  return createConversation("chatbi")
    .then((conversation) => sendMessage(conversation.id, question))
    .then((accepted) => waitForRun(accepted.run_id, { timeout:60000 }));
}

function queryTodayTasks() {
  return createConversation("today_tasks")
    .then((conversation) => sendMessage(conversation.id, "结合我的历史跟进记录与销售主管或销售总经理下发的任务，按时间生成今日待办"))
    .then((accepted) => waitForRun(accepted.run_id, { timeout: 60000 }));
}

function queryPersonalRisks() {
  return createConversation("personal_risks")
    .then((conversation) => sendMessage(conversation.id, "结合我的历史跟进记录，以资深销售视角分析个人客户风险"))
    .then((accepted) => waitForRun(accepted.run_id, { timeout: 60000 }));
}

function queryOperatingReport(prompt) {
  return createConversation("operating_report")
    .then((conversation) => sendMessage(conversation.id, prompt))
    .then((accepted) => waitForRun(accepted.run_id, { timeout: 120000 }));
}

function runAgent(mode, text, customerId = null, opportunityId = null) {
  return createConversation(mode, customerId, opportunityId)
    .then((conversation) => sendMessage(conversation.id, text, "audio_transcript"))
    .then((accepted) => waitForRun(accepted.run_id, { timeout: ["visit_entry", "opportunity_draft"].includes(mode) ? 120000 : 60000 }));
}

// BACKEND-CONTRACT：总览只读数据与个人评价；sales-growth/review 的 POST 会触发复盘。
// /profile/evaluation 与 /profile/performance 是不同端点；不能用一个汇总互相替代。
function getAssistantHome() {
  return request({ path: "/assistant/home" });
}

function ensureSalesGrowthReview() {
  return request({ path: "/profile/sales-growth/review", method: "POST" });
}

function getSalesGrowth(days = 30) {
  return request({ path: `/profile/sales-growth?days=${Number(days || 30)}` });
}

function getScopedSalesGrowth(options = {}) {
  const params = Object.entries(options).filter(([,value]) => value !== undefined && value !== null)
    .map(([key,value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join('&');
  return request({path:`/profile/sales-growth/scoped?${params}`});
}

function getMarketingAnalytics() {
  // The backend exposes the database-backed profile summary as /profile/evaluation.
  return request({ path: "/profile/evaluation" });
}

function getMemberSalesGrowth(accountCode, days = 30) {
  return request({ path: `/profile/team-members/${encodeURIComponent(accountCode)}/sales-growth?days=${Number(days || 30)}` });
}

// BACKEND-CONTRACT：客户搜索仅请求单页，默认 page_size=100；此封装没有 offset/cursor 参数。
// scope/unassigned 是查询条件，不授予越权访问；客户认领仍必须由后端检查当前分配状态。
function listCustomers(options = {}) {
  const params = [];
  if (options.q) params.push(`q=${encodeURIComponent(options.q)}`);
  if (options.level) params.push(`level=${encodeURIComponent(options.level)}`);
  if (options.scope) params.push(`scope=${encodeURIComponent(options.scope)}`);
  if (typeof options.unassigned === "boolean") params.push(`unassigned=${options.unassigned ? "true" : "false"}`);
  params.push(`page_size=${Number(options.pageSize || 100)}`);
  return request({ path: `/customers?${params.join("&")}` });
}

// Company reference directory only; total includes every matching claim state.
// Legacy listCustomers callers keep their first-page contract and scope rules.
function listCustomerClaimPool({ q = '', pageSize = 50, offset = 0 } = {}) {
  return request({ path: `/customers/claim-pool?q=${encodeURIComponent(q)}&page_size=${Number(pageSize)}&offset=${Number(offset)}` });
}

// BACKEND-CONTRACT：客户详情直接消费聚合对象（contacts/opportunities/tasks/visits/risks 等）。
// 商机详情也从客户聚合对象筛选 opportunity_id；不存在独立 getOpportunity 封装。
function getCustomer(customerId) {
  return request({ path: `/customers/${encodeURIComponent(customerId)}` });
}

function getDirectoryMembers() {
  return request({ path: "/directory/members" });
}

function getTaskAssignees(options = {}) {
  return request({ path: "/directory/task-assignees"+(Object.keys(options).length?"?"+assetQuery(options):"") });
}

function getWorkbench() {
  return request({ path: "/workbench" });
}

// BACKEND-CONTRACT：创建、人工字段修改、下发与认领分属不同写接口。
// 当前客户 PATCH 不携带 version_no；名称/评分/象限未在人工编辑页面提交，不代表服务端可任意修改。
// 下发是先建客户再 assignments 两次请求，非前端原子事务；后端应保留审计与重试幂等。
function createCustomer(data) {
  return request({ path: "/customers", method: "POST", data });
}

function updateCustomer(customerId, data) {
  return request({
    path: `/customers/${encodeURIComponent(customerId)}`,
    method: "PATCH",
    data,
  });
}

function assignCustomer(customerId, data) {
  return request({
    path: `/customers/${encodeURIComponent(customerId)}/assignments`,
    method: "POST",
    data,
  });
}

function claimCustomer(customerId) {
  return request({path: `/customers/${encodeURIComponent(customerId)}/claims`,method: "POST",data: {}});
}

// BACKEND-CONTRACT：新建商机金额为元；季度输入持久化为 quarterly_forecasts 原始计划额。
// 直销当前以 partner_name="直销" 表达，partner_mode 仅本地状态，专用渠道/伙伴 ID 合同待确认。
// 新增/编辑均可经本 POST 的 action/create/update 与 opportunity_id/version_no；另有拜访内嵌变更入口。
// 保存页要求 changed:boolean、version_no；changed=true 还需 event_id，缺少回执应先读回核对。
function createOpportunity(customerId, data) {
  return request({
    path: `/customers/${encodeURIComponent(customerId)}/opportunities`,
    method: "POST",
    data,
  });
}

function checkOpportunityName(customerId, name, excludeId) {
  const params = [`name=${encodeURIComponent(name)}`];
  if (excludeId) params.push(`exclude_id=${encodeURIComponent(excludeId)}`);
  return request({ path: `/customers/${encodeURIComponent(customerId)}/opportunities/check-name?${params.join('&')}` });
}

// The server owns pagination and returns summaries for the complete filtered set.
function listOpportunities(options = {}) {
  const params = [];
  ['query','team','teamId','grade','productLine','closePeriod','order'].forEach(key => {
    const name = {query:'q',teamId:'team_id',productLine:'product_line',closePeriod:'close_period'}[key] || key;
    if (options[key] && options[key] !== 'all') params.push(`${name}=${encodeURIComponent(options[key])}`);
  });
  (options.stages || []).forEach(stage => params.push(`stages=${encodeURIComponent(stage)}`));
  if (options.year) params.push(`year=${Number(options.year)}`);
  (options.quarters || []).forEach(quarter => params.push(`quarters=${Number(quarter)}`));
  if (options.scope) params.push(`scope=${encodeURIComponent(options.scope)}`);
  (options.memberIds || []).forEach(id=>params.push(`member_ids=${encodeURIComponent(id)}`));
  if (options.memberId) params.push(`member_id=${encodeURIComponent(options.memberId)}`);
  if (options.includeClosed) params.push("include_closed=true");
  if (options.customerId) params.push(`customer_id=${encodeURIComponent(options.customerId)}`);
  if (options.owner && options.owner !== 'all') params.push(`owner=${encodeURIComponent(options.owner)}`);
  if (options.probability) params.push(`probability=${Number(options.probability)}`);
  if (options.stage) params.push(`stage=${encodeURIComponent(options.stage)}`);
  if (options.closeFrom) params.push(`close_from=${encodeURIComponent(options.closeFrom)}`);
  if (options.closeTo) params.push(`close_to=${encodeURIComponent(options.closeTo)}`);
  params.push(`page_size=${Number(options.pageSize || 100)}`);
  params.push(`offset=${Number(options.offset || 0)}`);
  return request({ path: `/opportunities?${params.join("&")}` });
}

function listAllOpportunities(options = {}, cancelled) {
  return require('./pagination').collectPages(offset => listOpportunities({ ...options, offset }), cancelled);
}

function getOpportunityOverview(selection) {
  const params = [`year=${Number(selection.year)}`, ...selection.quarters.map(q => `quarters=${Number(q)}`)];
  if(selection.scope) params.push(`scope=${encodeURIComponent(selection.scope)}`);
  (selection.memberIds || []).forEach(id=>params.push(`member_ids=${encodeURIComponent(id)}`));
  if(selection.memberId) params.push(`member_id=${encodeURIComponent(selection.memberId)}`);
  return request({ path: `/opportunities/overview?${params.join('&')}` });
}

// BACKEND-CONTRACT：保留但当前生产代码无调用。实际表单规则来自 visitFlow/visitFirstVisit 本地常量。
// 不能仅开发此 schema 接口就期待当前页面自动改变字段。
function getVisitFormSchema() {
  return request({ path: "/visits/form-schema" });
}

// BACKEND-CONTRACT：POST /visits body={customer_id,fields}，fields 包含审核 run 引用与可选商机变更。
// 后端须复核审核/版本/归属并处理拜访与商机的一致性；AI 下一步文本不等于已持久化任务。
function createVisit(customerId, fields, fdeParticipantIds) {
  return request({ path: "/visits", method: "POST", data: { customer_id: customerId, fields, ...(fdeParticipantIds ? {fde_participant_ids:fdeParticipantIds} : {}) } });
}

// BACKEND-CONTRACT：multipart POST /audio/transcriptions，file 字段 + purpose + language=zh；90秒超时。
// 上传成功返回含 text 的对象；401 可刷新一次重传，无上传幂等键，也没有对外 abort 句柄。
function uploadAudioOnce(tempFilePath, purpose, token, baseUrl) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${baseUrl}/audio/transcriptions`,
      filePath: tempFilePath,
      name: "file",
      formData: { purpose, language: "zh" },
      // Leave enough time for the real file upload and upstream transcription.
      timeout: 90000,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (response) => {
        let body = {};
        try { body = JSON.parse(response.data || "{}"); } catch (error) { body = {}; }
        if (response.statusCode >= 200 && response.statusCode < 300) { resolve(body); return; }
        const audioErrors = {
          ASR_AUDIO_INVALID: "录音文件无法解析，请重新录音",
          ASR_RATE_LIMITED: "语音服务请求过于频繁，请稍后重试",
          ASR_SERVICE_BUSY: "语音服务暂时繁忙，请稍后重试",
          ASR_UPSTREAM_FAILED: "语音识别暂时不可用，请稍后重试",
          AUDIO_EMPTY: "没有录到有效声音，请重新录音",
          AUDIO_TOO_LARGE: "录音时间过长，请分段录入",
        };
        const detail = body && body.detail;
        reject(Object.assign(new Error(audioErrors[detail] || detail || `语音识别失败（${response.statusCode}）`), { statusCode: response.statusCode }));
      },
      fail: (error) => reject(new Error(error.errMsg || "语音上传失败")),
    });
  });
}

function transcribeAudio(tempFilePath, purpose) {
  const session = currentSession();
  const auth = getAuth();
  if (!auth || !auth.access_token) return Promise.reject(new Error("登录已失效"));
  return authenticatedAttempt(next => uploadAudioOnce(tempFilePath, purpose, next.access_token, session.baseUrl), session);
}

// BACKEND-CONTRACT：任务列表单页 1..100；完成/接受/拒绝共用 events 接口，返回更新后的完整任务。
// 可操作身份与状态必须在服务端重验；前端 canRespond/canComplete 只是按钮控制。
function listTasks(status, pageSize = 100, options = {}) {
  const params = [`page_size=${Math.max(1, Math.min(100, Number(pageSize || 100)))}`];
  if (status) params.push(`status=${encodeURIComponent(status)}`);
  if(options.view)params.push(`view=${encodeURIComponent(options.view)}`);
  if(options.member_id)params.push(`member_id=${encodeURIComponent(options.member_id)}`);
  return require("./pagination").collectPages(offset => request({ path: `/tasks?${params.join("&")}&offset=${offset}` }));
}

function getTask(taskId) {
  return request({ path: `/tasks/${encodeURIComponent(taskId)}` });
}

function completeTask(taskId, note, versionNo) {
  return request({
    path: `/tasks/${encodeURIComponent(taskId)}/events`,
    method: "POST",
    data: { event_type: "complete", note: String(note || "").trim() || null, ...(versionNo?{version_no:versionNo}:{}) },
  });
}

function respondTask(taskId, eventType, note, versionNo) {
  return request({
    path: `/tasks/${encodeURIComponent(taskId)}/events`,
    method: "POST",
    data: { event_type: eventType, note: String(note || "").trim() || null, ...(versionNo?{version_no:versionNo}:{}) },
  });
}

// BACKEND-CONTRACT：due_at 转 ISO 时间；关联 customer_id/opportunity_id 缺失时明确传 null。
// priority 中文普通/中/高 -> normal/medium/high；其他值当前回退 normal。
function createTask({ description, assigneeAccount, targetPosition, dueAt, priority, customerId, opportunityId, associationKind }) {
  const priorityMap = { 普通: "normal", 中: "medium", 高: "high" };
  return request({
    path: "/tasks",
    method: "POST",
    data: {
      description,
      association_kind: associationKind,
      assignee_account_code: targetPosition ? null : assigneeAccount,
      target_position: targetPosition || null,
      due_at: new Date(dueAt).toISOString(),
      priority_code: priorityMap[priority] || "normal",
      customer_id: customerId || null,
      opportunity_id: opportunityId || null,
    },
  });
}

// BACKEND-CONTRACT：通知 GET 与已读 POST 独立；当前自动已读操作失败静默，不保证全部已落库。
function listNotifications(unreadOnly = false) {
  const workspaceId = ((getAuth() || {}).actor || {}).workspace_id;
  return request({ path: `/notifications?unread_only=${unreadOnly ? "true" : "false"}` })
    .then(response => notificationDisplayResponse(response, workspaceId));
}

function markNotificationRead(notificationId) {
  return request({ path: `/notifications/${encodeURIComponent(notificationId)}/read`, method: "POST" });
}

// BACKEND-CONTRACT：风险解除 POST 输入 resolution_note，返回更新对象；当前未接入幂等白名单。
function listRisks(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request({ path: `/risks${query}` });
}

function getRisk(riskId) {
  return request({ path: `/risks/${encodeURIComponent(riskId)}` });
}

function resolveRisk(riskId, note) {
  return request({
    path: `/risks/${encodeURIComponent(riskId)}/resolve`,
    method: "POST",
    data: { resolution_note: String(note || "").trim() },
  });
}

// BACKEND-CONTRACT：multipart POST /visit-imports，file + original_filename；120秒上传超时。
// 返回 id/status 后页面用 GET /visit-imports/{id} 每2秒查询；retry 为另一个 POST。
// 离页停止页面轮询，不取消服务端队列；removeFile 仅移除本地引用，没有删除导入接口。
function uploadVisitFile(filePath, filename, onProgress) {
  const session = currentSession();
  const send = (auth) => new Promise((resolve,reject) => {
    const task=wx.uploadFile({url: `${session.baseUrl}/visit-imports`,filePath,name:'file',
      formData:{original_filename:filename},timeout:120000,
      header:{Authorization:`Bearer ${auth.access_token}`},
      success(response) {
        let data;try {data=JSON.parse(response.data);} catch(e) {reject(new Error('文件上传响应异常'));return;}
        if(response.statusCode>=200&&response.statusCode<300) resolve(data);
        else reject(Object.assign(new Error(data.detail||'文件上传失败'),{statusCode:response.statusCode}));
      },fail:e=>reject(new Error(e.errMsg||'上传失败'))});
    if(onProgress&&task.onProgressUpdate) task.onProgressUpdate(progress => {
      if (isCurrentSession(session)) onProgress(progress);
    });
  });
  const auth=getAuth();if(!auth) return Promise.reject(new Error('请先登录'));
  return authenticatedAttempt(send, session);
}

module.exports = {
  uploadVisitFile,
  BASE_URL_STORAGE_KEY,
  DEFAULT_BASE_URL,
  isEnabled: () => Boolean(getBaseUrl()),
  getBaseUrl,
  getAuth,
  saveAuth,
  request,
  loginWithAccount,
  logout,
  queryChatBI,
  queryTodayTasks,
  queryPersonalRisks,
  queryOperatingReport,
  runAgent,
  getRun,
  getAssistantHome,
  ensureSalesGrowthReview,
  getSalesGrowth,
  getScopedSalesGrowth,
  getMarketingAnalytics,
  getMemberSalesGrowth,
  listCustomers,
  listCustomerClaimPool,
  getCustomer,
  getDirectoryMembers,
  getTaskAssignees,
  getTaskPositions: (options={}) => request({path:'/directory/task-positions'+(Object.keys(options).length?'?'+assetQuery(options):'')}),
  getWorkbench,
  createCustomer,
  updateCustomer,
  assignCustomer,
  claimCustomer,
  createOpportunity,
  checkOpportunityName,
  listOpportunities,
  listAllOpportunities,
  getOpportunityOverview,
  getVisitFormSchema,
  createVisit,
  transcribeAudio,
  listTasks,
  getTaskOverview: (ids=[]) => request({path:"/tasks/overview"+(ids.length?"?"+ids.map(id=>"task_ids="+encodeURIComponent(id)).join("&"):"")}),
  getTask,
  completeTask,
  respondTask,
  createTask,
  listNotifications,
  markNotificationRead,
  listRisks,
  getRisk,
  resolveRisk,
};

// BACKEND-CONTRACT：以下资产/画像查询原样编码非空 options；合法参数白名单由调用方及后端确认。
// GET /customer-assets 无 customer_id 返回 customers 聚合，有 customer_id 返回 entries 明细。
// 消费者依赖 view/items/summary/has_more/as_of/can_manage；空金额、零金额和缺数必须区分。
function assetQuery(options = {}) {
  return Object.keys(options).filter(k=>options[k] !== null && options[k] !== undefined && options[k] !== '').flatMap(k=>(Array.isArray(options[k])?options[k]:[options[k]]).map(value=>`${encodeURIComponent(k)}=${encodeURIComponent(value)}`)).join('&');
}
module.exports.getCustomerMap = (options={}) => request({path:'/customer-assets/map'+(Object.keys(options).length?'?'+assetQuery(options):'')});
module.exports.getCustomerAssets = options => request({path:`/customer-assets?${assetQuery(options)}`});
// BACKEND-CONTRACT：实绩登记 amount 为元字符串，带 request_id、source_ref、confirmed=true；作废保留原记录。
// 实绩 request_id 是业务提交标识；并行存在自动传输 Idempotency-Key，二者不可混同。
module.exports.createCustomerActual = data => request({path:'/customer-assets',method:'POST',data});
module.exports.voidCustomerActual = (id,reason) => request({path:`/customer-assets/${encodeURIComponent(id)}/void`,method:'POST',data:{reason,confirmed:true}});

// BACKEND-CONTRACT：personal=true 要求 scope=self；false 由角色决定可见部门/团队。
// 响应必须含 data_source=database、opportunities、quarter_forecasts、quarter_actuals；排名还需 recent_visits。
// 所选季度在前端过滤，接口本身未发送 year/quarters；需返回足够完整范围并保留事实/计划/预测区别。
module.exports.getDashboard = (personal, options={}) => {
  const params=[`personal=${!!personal}`];
  if(options.member_id)params.push(`member_id=${encodeURIComponent(options.member_id)}`);
  (options.team_groups || []).forEach(code=>params.push(`team_groups=${encodeURIComponent(code)}`));
  return request({path:'/dashboard?'+params.join('&')});
};
module.exports.getDashboardOptions = () => request({path:'/dashboard/options'});

// BACKEND-CONTRACT：scope=self/department/team/person；team/account_code 和 period 由页面传入。
// 消费 actuals/targets/supplementals/retention/editable 等字段；后端权限决定目标是否可写。
module.exports.getProfilePerformance = (options = {}) => {
  const query = Object.keys(options).filter(key => options[key] !== null && options[key] !== undefined && options[key] !== '')
    .map(key => `${encodeURIComponent(key)}=${encodeURIComponent(options[key])}`).join('&');
  return request({path:`/profile/performance?${query}`});
};
module.exports.saveSalesTarget = data => request({path:'/profile/sales-targets', method:'POST', data});

module.exports.changePassword = changePassword;
module.exports.listPartners = (options = {}) => request({path:`/directory/partners?q=${encodeURIComponent(options.q || '')}&offset=${Number(options.offset) || 0}&limit=50`});
module.exports.getCustomerReference = id => request({path:`/customers/${encodeURIComponent(id)}/reference`});

module.exports.getDashboardRankings = ({year,quarters,personal,member_id='',team_groups=[]}) => request({path:`/dashboard/rankings?year=${encodeURIComponent(year)}&personal=${!!personal}&${quarters.map(q=>`quarters=${encodeURIComponent(q)}`).join('&')}${member_id?'&member_id='+encodeURIComponent(member_id):''}${team_groups.map(g=>'&team_groups='+encodeURIComponent(g)).join('')}`});

module.exports.getBusinessAdvice = id => request({path:`/advice/${encodeURIComponent(id)}`});
module.exports.getOpportunityDetail = id => request({path:`/opportunities/${encodeURIComponent(id)}/detail`});
module.exports.getVisit = id => request({path:`/visits/${encodeURIComponent(id)}`});
module.exports.queryBusinessAdvice = async (subjectKind,subjectId,section='overview',retry=false) => {
  const identity=()=>{const a=getAuth();return a&&a.actor?`${a.actor.workspace_id}:${a.actor.user_id}:${a.actor.role}`:'';};
  const owner=identity();
  let result=await request({path:'/advice',method:'POST',data:{subject_kind:subjectKind,subject_id:subjectId,section,retry}});
  const started=Date.now();let poll=0;
  while(['queued','running'].includes(result.status)){
    if(Date.now()-started>65000)throw Error('仍在后台分析，可稍后查看');
    await new Promise(resolve=>setTimeout(resolve,Math.min(1200+poll++*800,5000)));
    if(identity()!==owner)throw Error('登录身份已变化');
    result=await module.exports.getBusinessAdvice(result.id);
  }
  if(result.status==='failed')throw Error(result.error || '分析失败，请重试');
  return result;
};
module.exports.decideSuggestion = (id,data)=>request({path:`/advice/suggestions/${encodeURIComponent(id)}/decision`,method:'POST',data});
module.exports.getAdviceStatistics = ()=>request({path:'/advice/statistics'});

module.exports.getCurrentActor = () => request({path:'/auth/me'});
module.exports.updateActor = actor => {const auth=getAuth();if(auth){if((auth.actor||{}).permission_version!==actor.permission_version)invalidateSession();writeAuth({...auth,actor});}};
module.exports.listFdeMembers = (options={}) => request({path:`/directory/fde-members?${assetQuery({q:options.q||'',limit:options.limit||50,offset:options.offset||0})}`});
module.exports.updateFdeMembers = (id,memberIds,versionNo) => request({path:`/opportunities/${encodeURIComponent(id)}/fde-members`,method:'PUT',data:{member_ids:memberIds,version_no:versionNo}});
module.exports.getFdeDashboard = (options={}) => request({path:`/fde/dashboard?${fdeQuery(options)}`});

module.exports.getFdeActivity=(options={})=>request({path:`/fde/activity?${fdeQuery(options)}`});

function fdeQuery(options={}) {return assetQuery(options);}
module.exports.coordinateTask=(id,data)=>request({path:`/tasks/${encodeURIComponent(id)}/events`,method:'POST',data});

module.exports.listFdeVisitOpportunities = (options={}) => request({path:`/fde/visit-opportunities?${assetQuery(options)}`});
module.exports.getFdeProfile = (options=30) => request({path:`/fde/profile?${assetQuery(typeof options==='number'?{days:options}:options)}`});
module.exports.reviewFdeProfile = (options=30) => request({path:`/fde/profile/review?${assetQuery(typeof options==='number'?{days:options}:options)}`,method:'POST',data:{}});

// PERF-06 detail read models: bounded pages; overview summaries describe all authorized rows.
module.exports.getCustomerHeader = id => request({path:`/customers/${encodeURIComponent(id)}/header`});
module.exports.getOpportunityDetailHeader = id => request({path:`/opportunities/${encodeURIComponent(id)}/header`});
module.exports.getCustomerOpportunityHeader = (customerId,opportunityId) => request({path:`/customers/${encodeURIComponent(customerId)}/opportunities/${encodeURIComponent(opportunityId)}/header`});
module.exports.getCustomerOverview = id => request({path:`/customers/${encodeURIComponent(id)}/overview`});
module.exports.getOpportunityDetailOverview = id => request({path:`/opportunities/${encodeURIComponent(id)}/overview`});
module.exports.listCustomerOpportunities = (id,options={}) => request({path:`/customers/${encodeURIComponent(id)}/opportunities?${assetQuery(options)}`});
module.exports.listCustomerContacts = (id,options={}) => request({path:`/customers/${encodeURIComponent(id)}/contacts?${assetQuery(options)}`});
module.exports.listVisits = async (options={}) => {
  const result=await request({path:`/visits?${assetQuery(options)}`});
  if(options.sort==='created_desc' && (!result || result.sort!=='created_desc')) {
    throw Error('最新跟进列表正在更新，请稍后重试');
  }
  return result;
};
module.exports.listDetailTasks = (options={}) => request({path:`/tasks?${assetQuery(options)}`});
module.exports.getOpportunityTimeline = (id,options={}) => {
  const workspaceId = ((getAuth() || {}).actor || {}).workspace_id;
  return request({path:`/opportunities/${encodeURIComponent(id)}/timeline?${assetQuery(options)}`})
    .then(response => timelineDisplayResponse(response, workspaceId));
};
module.exports.getCustomerAssetQuarters = (options={}) => request({path:`/customer-assets/quarters?${assetQuery(options)}`});

module.exports.getCustomerOpportunityOverview = (customerId,opportunityId) => request({path:`/customers/${encodeURIComponent(customerId)}/opportunities/${encodeURIComponent(opportunityId)}/overview`});

// One task-card page; legacy listTasks remains available for existing bulk consumers.
module.exports.listTaskPage = (options={}) => {
  const {completed_quarters=[],...query}=options;
  const params=assetQuery({...query,tab:query.tab||'pending',page_size:Math.min(100,Math.max(1,Number(query.page_size||20)))});
  return request({path:`/tasks?${params}${completed_quarters.map(q=>`&completed_quarters=${encodeURIComponent(q)}`).join('')}`});
};

// Authoritative records and effective targets are read from the business API.
module.exports.listDemoScenes=(id,options={})=>request({path:`/opportunities/${encodeURIComponent(id)}/demo-scenes?${assetQuery(options)}`});
module.exports.getDemoScene=id=>request({path:`/demo-scenes/${encodeURIComponent(id)}`});
module.exports.createDemoScenes=(id,scenes)=>request({path:`/opportunities/${encodeURIComponent(id)}/demo-scenes`,method:'POST',data:{scenes}});
module.exports.updateDemoScene=(id,data)=>request({path:`/demo-scenes/${encodeURIComponent(id)}`,method:'PATCH',data});
module.exports.deleteDemoScene=(id,versionNo)=>request({path:`/demo-scenes/${encodeURIComponent(id)}`,method:'DELETE',data:{version_no:versionNo}});
module.exports.getTargets=(options={})=>request({path:`/targets?${assetQuery(options)}`});
module.exports.saveTarget=data=>request({path:'/targets',method:'POST',data});
module.exports.listTaskRecipients=(options={})=>request({path:`/tasks/recipients?${assetQuery(options)}`});

// Keep accepted run IDs in the draft before waiting. A timeout resumes the same run.
module.exports.submitVisitStage = (stage,data) => request({path:`/visit-flow/${stage}`,method:'POST',data});
module.exports.waitVisitRun = id => waitForRun(id,{timeout:120000,interval:1000});

// Task-specific minimal references; scope matches POST /tasks validation.
module.exports.listTaskCustomers = ({q='',pageSize=20,offset=0}={}) =>
  request({path:`/tasks/customers?q=${encodeURIComponent(q)}&page_size=${Number(pageSize)}&offset=${Number(offset)}`});
module.exports.listTaskOpportunities = ({customerId,opportunityId,q='',pageSize=20,offset=0}={}) =>
  request({path:`/tasks/opportunities?customer_id=${encodeURIComponent(customerId)}&q=${encodeURIComponent(q)}&page_size=${Number(pageSize)}&offset=${Number(offset)}${opportunityId?'&opportunity_id='+encodeURIComponent(opportunityId):''}`});

module.exports.getProfileScopeOptions=()=>request({path:"/profile/scope-options"});
module.exports.saveTargetBatch=data=>request({path:"/targets/batch",method:"POST",data});
module.exports.getFdeScopeOptions=()=>request({path:"/fde/scope-options"});

// Organization master data; empty active teams are not inferred from members or business rows.
module.exports.getTeamDirectory=(purpose="browse")=>request({path:`/directory/teams?purpose=${encodeURIComponent(purpose)}`});

// One metadata flight per authenticated identity and API address. Failures are
// retryable and never fall back to local business enums or another tenant's data.
function getBusinessOptions() {
  const session=currentSession(),key=`${session.generation}:${session.baseUrl}`;
  if(optionsFlight&&optionsFlight.key===key&&Date.now()-optionsFlight.at<60000)return optionsFlight.promise;
  businessOptions.clear();
  const flight={key,at:Date.now()};
  flight.promise=request({path:'/metadata/business-options'}).then(value=>{
    assertCurrentSession(session);if(optionsFlight!==flight)throw Error('业务目录已刷新，请重试');return businessOptions.install(value);
  }).catch(error=>{if(optionsFlight===flight){optionsFlight=null;businessOptions.clear();}throw error;});
  optionsFlight=flight;return flight.promise;
}
module.exports.getBusinessOptions=getBusinessOptions;
const catalogReads=['getWorkbench','getDashboard','listOpportunities','getOpportunityDetail','getCustomerOverview',
 'getCustomer','getCustomerMap','getCustomerHeader','getOpportunityDetailHeader','getCustomerOpportunityHeader','listAllOpportunities','getOpportunityOverview','getOpportunityDetailOverview','getCustomerOpportunityOverview',
 'getFdeDashboard','getFdeProfile','listCustomerOpportunities','listFdeVisitOpportunities'];
catalogReads.forEach(name=>{const read=module.exports[name];if(read)module.exports[name]=(...args)=>getBusinessOptions().then(()=>read(...args));});
