const AUTH_STORAGE_KEY = "salesApiAuth";
const BASE_URL_STORAGE_KEY = "salesApiBaseUrl";

function getBaseUrl() {
  return String(wx.getStorageSync(BASE_URL_STORAGE_KEY) || "").replace(/\/$/, "");
}

function getAuth() {
  return wx.getStorageSync(AUTH_STORAGE_KEY) || null;
}

function saveAuth(auth) {
  if (auth) wx.setStorageSync(AUTH_STORAGE_KEY, auth);
  else wx.removeStorageSync(AUTH_STORAGE_KEY);
}

function requestRaw({ path, method = "GET", data, token }) {
  const baseUrl = getBaseUrl();
  if (!baseUrl) return Promise.reject(Object.assign(new Error("API 尚未配置"), { code: "API_DISABLED" }));
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl}${path}`,
      method,
      data,
      timeout: 15000,
      header: token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" },
      success: (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
          return;
        }
        const message = response.data && (response.data.message || response.data.detail);
        reject(Object.assign(new Error(message || `API 请求失败（${response.statusCode}）`), { code: `HTTP_${response.statusCode}`, statusCode: response.statusCode }));
      },
      fail: (error) => reject(Object.assign(new Error(error.errMsg || "网络连接失败"), { code: "NETWORK_ERROR" })),
    });
  });
}

let refreshing = null;

function refreshAccessToken() {
  const auth = getAuth();
  if (!auth || !auth.refresh_token) return Promise.reject(Object.assign(new Error("登录已失效"), { code: "AUTH_REQUIRED" }));
  if (!refreshing) {
    refreshing = requestRaw({ path: "/auth/refresh", method: "POST", data: { refresh_token: auth.refresh_token } })
      .then((next) => { saveAuth(next); return next; })
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}

function request(options, retried = false) {
  const auth = getAuth();
  return requestRaw({ ...options, token: auth && auth.access_token }).catch((error) => {
    if (!retried && error.statusCode === 401 && auth && auth.refresh_token) {
      return refreshAccessToken().then(() => request(options, true));
    }
    throw error;
  });
}

function loginWithAccount(accountCode) {
  return requestRaw({
    path: "/auth/session",
    method: "POST",
    data: {
      account_code: accountCode,
      client: { platform: "wechat-mini-program", version: "0.2.0" },
    },
  }).then((auth) => { saveAuth(auth); return auth; });
}

function logout() {
  const auth = getAuth();
  saveAuth(null);
  if (!auth) return Promise.resolve();
  return requestRaw({ path: "/auth/logout", method: "POST", token: auth.access_token }).catch(() => undefined);
}

function createConversation(mode = "chatbi", customerId = null) {
  return request({ path: "/conversations", method: "POST", data: { mode, customer_id: customerId } });
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
    .then((accepted) => waitForRun(accepted.run_id));
}

function queryTodayTasks() {
  return createConversation("today_tasks")
    .then((conversation) => sendMessage(conversation.id, "结合我的历史跟进记录与主管或经理下发的任务，按时间生成今日待办"))
    .then((accepted) => waitForRun(accepted.run_id, { timeout: 60000 }));
}

function queryPersonalRisks() {
  return createConversation("personal_risks")
    .then((conversation) => sendMessage(conversation.id, "结合我的历史跟进记录，以资深销售视角分析个人客户风险"))
    .then((accepted) => waitForRun(accepted.run_id, { timeout: 60000 }));
}

function listTasks(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request({ path: `/tasks${query}` });
}

function getTask(taskId) {
  return request({ path: `/tasks/${encodeURIComponent(taskId)}` });
}

function completeTask(taskId, note) {
  return request({
    path: `/tasks/${encodeURIComponent(taskId)}/events`,
    method: "POST",
    data: { event_type: "complete", note: String(note || "").trim() || null },
  });
}

function createTask({ description, assigneeAccount, dueAt, priority }) {
  const priorityMap = { 普通: "normal", 中: "medium", 高: "high" };
  return request({
    path: "/tasks",
    method: "POST",
    data: {
      description,
      assignee_account_code: assigneeAccount,
      due_at: new Date(dueAt).toISOString(),
      priority_code: priorityMap[priority] || "normal",
    },
  });
}

function listNotifications(unreadOnly = false) {
  return request({ path: `/notifications?unread_only=${unreadOnly ? "true" : "false"}` });
}

function markNotificationRead(notificationId) {
  return request({ path: `/notifications/${encodeURIComponent(notificationId)}/read`, method: "POST" });
}

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

module.exports = {
  BASE_URL_STORAGE_KEY,
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
  listTasks,
  getTask,
  completeTask,
  createTask,
  listNotifications,
  markNotificationRead,
  listRisks,
  getRisk,
  resolveRisk,
};
