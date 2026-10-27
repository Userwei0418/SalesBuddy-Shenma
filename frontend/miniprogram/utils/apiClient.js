/**
 * BACKEND-CONTRACT：本文件是当前小程序 HTTP/上传调用边界，不等同于后端已实现的接口保证。
 * 完整 METHOD、PATH、调用方、字段及未接通项见 docs/backend-handoff/接口契约清单.md。
 * 普通成功响应直接返回 JSON 本体；不要额外套 {data: ...}。列表按各接口返回 items 等分页字段。
 * 配置 API_BASE_URL 已包含 /api/v1；本文件 path 均为相对路径，避免重复拼接版本前缀。
 * 演示数据应显式标记 DEMO-DATA，不可把鉴权失败、超时或合法零值转成真实业务金额。
 */
// MINI-ONLY-BEGIN
const restrictedSession = require("./restrictedSession");
// MINI-ONLY-END
const AUTH_STORAGE_KEY = "salesApiAuth";
const BASE_URL_STORAGE_KEY = "salesApiBaseUrl";
const { API_BASE_URL, LOCK_API_BASE_URL } = require("../config");
const DEFAULT_BASE_URL = API_BASE_URL;
const requestIdentity = require('./requestIdentity');
const requestPolicy = require('./requestPolicy');
const errorMessages = require('./errorMessages');
const agentRecovery = require('./agentRecovery');
const agentFlights = new Map();
const audioFlights = new Map();
const visitUploadIntent = require('./visitUploadIntent');
const access = require('./access');
const { notificationDisplayResponse, timelineDisplayResponse } = require('./demoDisplay');
const mutationsInFlight = new Map();
const opportunityRetryPayloads = new Map(); // Memory only; never persist draft fields.
const readsInFlight = new Map();
// MINI-ONLY-BEGIN
const nativeRequests = new Set();
let restrictedLoginReceipt=null;
function trackedNative(send, options) {
  let task;
  const complete=options.complete;
  task=send({...options,complete:result=>{if(task)nativeRequests.delete(task);if(complete)complete(result);}});
  if(task)nativeRequests.add(task);
  return task;
}
// MINI-ONLY-END
let readGeneration = 0;
let authGeneration = 0;
let refreshing = null;
let optionsFlight = null;
const businessOptions = require('./businessOptions');
const encryptedCredentials = require('./encryptedCredentials');

// 神码发行包固定接口域名，避免历史本机地址将凭据或业务请求发往其他环境。
function getBaseUrl() {
  return String(LOCK_API_BASE_URL ? DEFAULT_BASE_URL : wx.getStorageSync(BASE_URL_STORAGE_KEY) || DEFAULT_BASE_URL).replace(/\/$/, "");
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
  // MINI-ONLY-BEGIN
  restrictedLoginReceipt=null;
  const pending=[...nativeRequests];nativeRequests.clear();
  // Advance auth generation before abort callbacks can attempt refresh.
  // Native abort cannot undo a request already committed on the server.
  pending.forEach(task=>{try{task.abort();}catch(_){}});
  // MINI-ONLY-END
  encryptedCredentials.invalidate();
  optionsFlight=null;businessOptions.clear();
  invalidateReads();
  refreshing = null;
  agentFlights.clear();
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

// BACKEND-CONTRACT：普通JSON默认15秒，地图/客户列表/业绩等聚合读取45秒。
// requestPolicy维护明确的路由名单；调用方可设置1–120秒，写入不自动延长或重放。
// Bearer 鉴权；2xx 直接返回响应本体。
// 422 字段校验仅保留安全的 loc/type，不将输入值、ctx 或原始校验消息带入页面。
// 普通调用不暴露 RequestTask.abort；仅文件分段读取注册取消句柄。取消读取不回滚后台事务。
function responseError(response, method, path = '') {
  const body = response.data;
  const serverCode = errorMessages.codeFor(body);
  // Older servers label rejected password logins AUTH_REQUIRED. This public
  // endpoint has no session to renew; keep all protected-request 401s unchanged.
  const invalidCredentials = method === 'POST' && path.split('?')[0] === '/auth/password/login' &&
    response.statusCode === 401 && ['', 'AUTH_REQUIRED', 'INVALID_CREDENTIALS'].includes(serverCode);
  const recovery = requestPolicy.recovery({method}, response.statusCode, body, response.header);
  const message = invalidCredentials ? errorMessages.MESSAGES.INVALID_CREDENTIALS
    : errorMessages.messageFor(body, response.statusCode, recovery);
  return Object.assign(new Error(message), {
    code: `HTTP_${response.statusCode}`, statusCode: response.statusCode, ...recovery,
    ...(invalidCredentials ? {recoveryAction:'correct_request'} : {}),
    serverCode: invalidCredentials ? 'INVALID_CREDENTIALS' : serverCode,
    ...(response.statusCode === 422 && Array.isArray(body && body.detail)
      ? {validationErrors: errorMessages.validationLocations(body.detail)} : {}),
  });
}

function requestRaw({ path, method = "GET", data, token, idempotencyKey, responseType, includeHeaders = false, baseUrl = getBaseUrl(), timeout = requestPolicy.timeoutFor({path,method}), rangeHeader, fileIfMatch, onRequestTask }) {
  // MINI-ONLY-BEGIN
  if(restrictedSession.active() && !/^\/auth\/(password\/login|company-entry(?:\?|$))/.test(path)) return Promise.reject(Object.assign(new Error('当前账号无法访问此内容'),{code:'RESTRICTED_ENDPOINT_DENIED'}));
  // MINI-ONLY-END
  if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 120000) return Promise.reject(Object.assign(new Error("请求超时配置无效"), { code: "INVALID_REQUEST_TIMEOUT" }));
  if (!baseUrl) return Promise.reject(Object.assign(new Error("API 尚未配置"), { code: "API_DISABLED" }));
  return new Promise((resolve, reject) => {
    let send=options=>wx.request(options);
    // MINI-ONLY-BEGIN
    send=options=>trackedNative(options=>wx.request(options),options);
    // MINI-ONLY-END
    const task = send({
      url: `${baseUrl}${path}`,
      method,
      data,
      ...(responseType ? {responseType} : {}),
      timeout,
      header: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
        ...(rangeHeader ? {Range:rangeHeader} : {}), ...(fileIfMatch ? {'If-Match':fileIfMatch} : {}) },
      success: (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(includeHeaders ? {data:response.data,headers:response.header||{},statusCode:response.statusCode} : response.data);
          return;
        }
        reject(responseError(response, method, path));
      },
      fail: (error) => reject(requestPolicy.transportError({method}, error)),
    });
    if (typeof onRequestTask === 'function') onRequestTask(task);
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
  // MINI-ONLY-BEGIN
  if(restrictedSession.active()) return Promise.reject(Object.assign(new Error('当前账号无法访问此内容'),{code:'RESTRICTED_ENDPOINT_DENIED'}));
  // MINI-ONLY-END
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

async function readAttempt(options, session, attempt = 0) {
  try { return await requestAttempt(options, session); }
  catch (error) {
    assertCurrentSession(session);
    const delay = requestPolicy.retryDelay(options, error, attempt);
    if (delay === null) throw error;
    await new Promise(resolve => setTimeout(resolve, delay));
    assertCurrentSession(session);
    return readAttempt(options, session, attempt + 1);
  }
}

// BACKEND-CONTRACT：仅 requestIdentity.supports 明确命中的写方法自动加 Idempotency-Key。
// 同一登录会话内，同租户/用户/地址 + 同 method/path/body 共用进行中 Promise。
// 超时/5xx 或切换账号后的迟到响应保留 key；同账号重新登录可安全重试未确定的提交。
// 这不是全局写接口幂等；Agent、导入重试、风险解除等未在白名单，详见契约清单。
function request(options) {
  const session = currentSession();
  const auth = getAuth();
  const opportunitySave = options.method === 'POST' && /^\/customers\/([^/?]+)\/opportunities$/.exec(options.path);
  if ((options.method || 'GET').toUpperCase() === 'GET') {
    if (options.fresh === true) return readAttempt(options, session);
    const key = JSON.stringify([session.generation, session.baseUrl, readGeneration, options.path, options.data,
      requestPolicy.timeoutFor(options), options.retryRead, options.responseType, options.includeHeaders]);
    let shared = readsInFlight.get(key);
    if (!shared) {
      shared = readAttempt(options, session).finally(() => {
        if (readsInFlight.get(key) === shared) readsInFlight.delete(key);
      });
      readsInFlight.set(key, shared);
    }
    // wx.request returns JSON; isolate consumers that decorate arrays in place.
    return shared.then(value => value === undefined ? value : JSON.parse(JSON.stringify(value)));
  }
  invalidateReads();
  if (opportunitySave && (!auth || !auth.actor)) return Promise.reject(Object.assign(new Error('登录已失效'), {code:'AUTH_REQUIRED',opportunitySaveUnknown:false}));
  if (!requestIdentity.supports(options) || !auth || !auth.actor) {
    return requestAttempt(options, session).finally(invalidateReads);
  }
  let identity, flightKey, opportunityIntent, recovering = false;
  try {
    const scope = [getBaseUrl(),auth.actor.workspace_id,auth.actor.user_id];
    flightKey = `${session.generation}:${requestIdentity.fingerprint(scope,options)}`;
    // Check before persisting a fresh intent, so double clicks cannot replace an in-flight receipt.
    if (mutationsInFlight.has(flightKey)) return mutationsInFlight.get(flightKey);
    if (opportunitySave) {
      const previous = requestIdentity.pendingOpportunity(scope, options.data.action === 'update' ? options.data.opportunity_id : '');
      if (previous) {
        recovering = options.opportunityRetryKey === previous.key && requestIdentity.fingerprint(scope, options) === previous.fingerprint;
        if (!recovering) throw Object.assign(new Error('原保存结果待确认，请先查询保存结果，勿重复创建'), {code:'OPPORTUNITY_SAVE_UNRESOLVED',opportunitySaveUnknown:true});
      }
    }
    identity = requestIdentity.begin(scope,options);
    if (opportunitySave) {
      opportunityIntent = requestIdentity.rememberOpportunity(scope, decodeURIComponent(opportunitySave[1]), options.data, identity);
      opportunityRetryPayloads.set(identity.key, {path:options.path, method:options.method, data:JSON.parse(JSON.stringify(options.data))});
    }
  } catch (error) {
    if (error.code) return Promise.reject(error);
    return Promise.reject(Object.assign(new Error('暂时无法保存提交标识，请稍后重试'), {opportunitySaveUnknown:false}));
  }
  const payload = options.method === 'POST' && /^\/risks\/[^/?]+\/decision$/.test(options.path)
    ? {...options.data, request_id:identity.key} : options.data;
  const promise = requestAttempt({...options,data:payload,idempotencyKey:identity.key}, session)
    .then(result => {
      if (opportunityIntent) {
        assertCurrentSession(session);
        if (!requestIdentity.validOpportunityReceipt(result, opportunityIntent)) throw Object.assign(new Error('未收到完整保存回执，请查询原保存结果，勿重复创建'), {code:'OPPORTUNITY_RECEIPT_INVALID'});
        requestIdentity.finishOpportunity(opportunityIntent);
        opportunityRetryPayloads.delete(identity.key);
      } else requestIdentity.finish(identity);
      return result;
    })
    .catch(error => {
      // A timeout, 5xx or auth refresh failure may follow a committed request.
      // Keep its key across pages and app restarts; deterministic rejections can be corrected.
      const rejected = !recovering && [400,403,404,409,422].includes(error.statusCode) &&
        !(opportunityIntent && ['IDEMPOTENCY_CONFLICT','IDEMPOTENCY_IN_PROGRESS','IDEMPOTENCY_UNRESOLVED'].includes(error.serverCode));
      if (opportunityIntent) {
        error.opportunitySaveUnknown = !rejected;
        if (rejected) { requestIdentity.finishOpportunity(opportunityIntent); opportunityRetryPayloads.delete(identity.key); }
      } else if (rejected) requestIdentity.finish(identity);
      throw error;
    }).finally(() => { mutationsInFlight.delete(flightKey); invalidateReads(); });
  mutationsInFlight.set(flightKey,promise);
  return promise;
}

// Native login verifies the password on the server and returns a real bearer session.
function loginWithAccount(accountCode, password, role, workspace, loginType='auto') {
  invalidateSession();
  const session = currentSession();
  const data={account_code:accountCode,password,role,login_type:loginType,...(workspace?{workspace}:{})};
  // MINI-ONLY-BEGIN
  data.restricted_session_version=1;
  // MINI-ONLY-END
  return requestRaw({path: "/auth/password/login", method: "POST",data,baseUrl:session.baseUrl}).then(auth=>{
    assertCurrentSession(session);
    // MINI-ONLY-BEGIN
    if(auth.session_kind==='demo_readonly'){saveAuth(null);restrictedLoginReceipt={auth,...currentSession()};return auth;}
    // MINI-ONLY-END
    saveAuth(auth);return auth;
  });
}
function changePassword(oldPassword, newPassword) {
  const session = currentSession();
  const actor=getAuth()?.actor;
  return encryptedCredentials.clear({baseUrl:session.baseUrl,workspace:actor?.company_code,account:actor?.account_code})
    .then(()=>{assertCurrentSession(session);return request({path: "/auth/password", method: "POST", data: {old_password:oldPassword,new_password:newPassword}});})
    .then(result => { assertCurrentSession(session); const auth=getAuth();
      if(auth)writeAuth({...auth,must_change_password:false});return result; });
}
function getCompanyEntry(companyCode) {
  const session = currentSession();
  // Public exact lookup, never a company directory or authenticated request.
  return requestRaw({path:'/auth/company-entry?company_code='+encodeURIComponent(companyCode),baseUrl:session.baseUrl})
    .then(result => { assertCurrentSession(session); return result; })
    .catch(error => { assertCurrentSession(session); throw error; });
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
  const session = currentSession();
  const interval = options.interval || 700;
  const timeout = options.timeout || 30000;
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      try { assertCurrentSession(session); } catch (error) { reject(error); return; }
      getRun(runId).then((run) => {
        if (["succeeded", "waiting_human"].includes(run.status)) { resolve(run); return; }
        if (["failed", "cancelled"].includes(run.status)) {
          const code = errorMessages.codeFor({code:run.error_code}) || (run.status === 'cancelled' ? 'RUN_CANCELLED' : 'RUN_FAILED');
          reject(Object.assign(new Error(errorMessages.businessMessage({code})), {code, serverCode:code, runStatus:run.status})); return;
        }
        if (Date.now() - startedAt >= timeout) { reject(Object.assign(new Error("等待超时，任务仍可继续处理，请稍后查看原任务结果"), { code: "RUN_TIMEOUT", runId, recoveryAction:'resume_run' })); return; }
        setTimeout(poll, interval);
      }).catch(error => {
        if (error.code !== 'SESSION_CHANGED' && ![401,403,404].includes(error.statusCode)) {
          error.runId = runId; error.recoveryAction = 'resume_run';
        }
        reject(error);
      });
    };
    poll();
  });
}

function agentOwner() { return agentRecovery.owner(getBaseUrl(), (getAuth() || {}).actor); }
function pendingAgentRuns(kind) { return agentRecovery.pending(agentOwner(),kind); }
function trackedAgent(mode, input, create, options = {}) {
  const session=currentSession(), owner=agentOwner();
  let prepared;
  try { prepared=options.recoveryKey
    ? {record:agentRecovery.find(owner,options.recoveryKey),existing:true}
    : agentRecovery.prepare(owner,mode,input,options); }
  catch(error){return Promise.reject(error);}
  const {record,existing}=prepared;
  if(!record)return Promise.reject(Object.assign(Error('原 AI 任务不属于当前身份或已结束'),{code:'AGENT_RECOVERY_MISSING'}));
  const flightKey=`${session.generation}:${record.key}`;
  if(agentFlights.has(flightKey))return agentFlights.get(flightKey);
  if(existing && !record.runId){
    if(record.phase==='conversation'){
      agentRecovery.finish(record);
      return options.recoveryKey ? Promise.reject(Object.assign(Error('AI 任务尚未发送，请从原入口重新提交'),{code:'AGENT_NOT_SUBMITTED'})) : trackedAgent(mode,input,create,options);
    }
    return Promise.reject(agentRecovery.unknown(record));
  }
  const flight=(async()=>{
    try {
      let runId=record.runId;
      if(!runId){
        const accepted=await create(()=>agentRecovery.submitting(record));assertCurrentSession(session);
        runId=accepted.run_id;agentRecovery.accepted(record,runId);
      }
      const run=await waitForRun(runId,{timeout:options.timeout || 120000});assertCurrentSession(session);
      // The page may have disappeared before it can consume the result. Keep
      // the receipt (never the result) until the visible consumer acknowledges it.
      agentRecovery.completed(record);return {...run,recoveryKey:record.key};
    } catch(error) {
      if(!record.runId && record.phase==='conversation'){
        agentRecovery.finish(record);
        if(error.code==='SESSION_CHANGED' || [400,401,403,404,422,429].includes(error.statusCode))throw error;
        throw Object.assign(Error('AI 任务尚未发送，可从原入口重试；请保留当前输入'),{code:'AGENT_NOT_SUBMITTED',outcomeUnknown:false,recoveryAction:'retry_start'});
      }
      if(error.code==='SESSION_CHANGED')throw error;
      if(['failed','cancelled'].includes(error.runStatus) || (!record.runId && [400,401,403,404,422,429].includes(error.statusCode))){
        agentRecovery.finish(record);throw error;
      }
      if(!record.runId)throw agentRecovery.unknown(record);
      error.runId=record.runId;error.recoveryKey=record.key;error.recoveryAction='resume_run';
      error.message='AI 任务已受理，结果尚未读到；请点击“继续查看结果”，不会重复生成。';throw error;
    }
  })().finally(()=>{if(agentFlights.get(flightKey)===flight)agentFlights.delete(flightKey);});
  agentFlights.set(flightKey,flight);return flight;
}
function resumePendingAgent(key) { return trackedAgent('',null,null,{recoveryKey:key}); }
function acknowledgeAgentResult(key) {
  const record=agentRecovery.find(agentOwner(),key);
  if(record && record.runId && record.phase==='completed')agentRecovery.finish(record);
}
function confirmAgentNotAccepted(key) {
  const record=agentRecovery.find(agentOwner(),key);
  if(record && !record.runId)agentRecovery.finish(record);
}
function startAgent(mode,text,customerId=null,opportunityId=null,inputSource='text',beforeSend=()=>{}) {
  const session=currentSession();
  return createConversation(mode,customerId,opportunityId).then(c=>{assertCurrentSession(session);beforeSend();return sendMessage(c.id,text,inputSource);});
}
function queryChatBI(question, options = {}) {
  return trackedAgent('chatbi',question,beforeSend=>startAgent('chatbi',question,null,null,'text',beforeSend),{timeout:60000,...options});
}
function queryTodayTasks(options = {}) {
  return trackedAgent('today_tasks','today',beforeSend=>startAgent('today_tasks','结合我的历史跟进记录与销售主管或销售总经理下发的任务，按时间生成今日待办',null,null,'text',beforeSend),{timeout:60000,...options});
}
function queryPersonalRisks(options = {}) {
  return trackedAgent('personal_risks','risks',beforeSend=>startAgent('personal_risks','结合我的历史跟进记录，以资深销售视角分析个人客户风险',null,null,'text',beforeSend),{timeout:60000,...options});
}
function queryOperatingReport(prompt, options = {}) {
  return trackedAgent('operating_report',prompt,beforeSend=>startAgent('operating_report',prompt,null,null,'text',beforeSend),{timeout:120000,...options});
}
function runAgent(mode, text, customerId = null, opportunityId = null, options = {}) {
  return trackedAgent(mode,[text,customerId,opportunityId],beforeSend=>startAgent(mode,text,customerId,opportunityId,'audio_transcript',beforeSend),{timeout:['visit_entry','opportunity_draft'].includes(mode)?120000:60000,...options});
}

// BACKEND-CONTRACT：总览只读数据与个人评价；sales-growth/review 的 POST 会触发复盘。
// /profile/evaluation 与 /profile/performance 是不同端点；不能用一个汇总互相替代。
function getAssistantHome() {
  return request({ path: "/assistant/home" });
}

function ensureSalesGrowthReview() {
  // Explicit user action only. requestIdentity persists one key for uncertain retries.
  return request({ path: "/profile/sales-growth/review", method: "POST", data:{} });
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
  if (options.businessIdentity) params.push(`business_identity=${encodeURIComponent(options.businessIdentity)}`);
  if (options.level) params.push(`level=${encodeURIComponent(options.level)}`);
  if (options.scope) params.push(`scope=${encodeURIComponent(options.scope)}`);
  if (options.order) params.push(`order=${encodeURIComponent(options.order)}`);
  if (typeof options.unassigned === "boolean") params.push(`unassigned=${options.unassigned ? "true" : "false"}`);
  params.push(`page_size=${Number(options.pageSize || 100)}`);
  return request({ path: `/customers?${params.join("&")}` });
}

// Company reference directory only; total includes every matching claim state.
// Legacy listCustomers callers keep their first-page contract and scope rules.
function listCustomerClaimPool({ q = '', pageSize = 50, offset = 0, industry = '', claimStatus = '', businessIdentity = '', order = '' } = {}) {
  const filters = (industry ? `&industry=${encodeURIComponent(industry)}` : '')
    + (claimStatus ? `&claim_status=${encodeURIComponent(claimStatus)}` : '')
    + (businessIdentity ? `&business_identity=${encodeURIComponent(businessIdentity)}` : '')
    + (order ? `&order=${encodeURIComponent(order)}` : '');
  return request({ path: `/customers/claim-pool?q=${encodeURIComponent(q)}&page_size=${Number(pageSize)}&offset=${Number(offset)}${filters}` });
}

// Opportunity selection uses the authorized company directory, not customer.read.
// Directory denial stays explicit; selecting a row still requires customer.reference.
function listOpportunityCustomerReferences({ q = '', pageSize = 20 } = {}) {
  return listCustomerClaimPool({ q, pageSize, offset: 0, businessIdentity: 'customer' });
}

// Creation scope is a separate capability; never reuse it for follow-up/reference lists.
function listOpportunityCreateCustomers({ q = '', pageSize = 20, offset = 0 } = {}) {
  return request({path:`/opportunities/create-customers?q=${encodeURIComponent(q)}&limit=${Number(pageSize)}&offset=${Number(offset)}`,fresh:true});
}
function getOpportunityCreateAccess(customerId) {
  return request({path:`/opportunities/create-access?customer_id=${encodeURIComponent(customerId)}`,fresh:true}).then(result=>{
    const ref=result && result.customer_reference;
    if(!result || typeof result.can_create_opportunity!=='boolean' ||
      (result.can_create_opportunity && (!ref || String(ref.id)!==String(customerId) || typeof ref.name!=='string')))
      throw Error('客户创建资格响应不完整，请重新核对');
    return result;
  });
}

function listCustomerClaimOptions() {
  return request({ path: '/customers/claim-pool/options' });
}

// BACKEND-CONTRACT：客户详情直接消费聚合对象（contacts/opportunities/tasks/visits/risks 等）。
// 商机详情也从客户聚合对象筛选 opportunity_id；不存在独立 getOpportunity 封装。
function getCustomer(customerId) {
  return request({ path: `/customers/${encodeURIComponent(customerId)}` });
}

function getDirectoryMembers(options = {}) {
  const params=assetQuery({permission:options.permission,include_stats:options.include_stats});
  return request({ path: "/directory/members"+(params?'?'+params:'') });
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

function createCustomerCreationRequest(data) {
  return request({ path: "/customers/creation-requests", method: "POST", data });
}

// 客户新建申请只读列表；服务端按当前登录人和工作空间隔离结果。
function listCustomerCreationRequests({ status = '', limit = 20, offset = 0 } = {}) {
  const params = [];
  if (status) params.push(`status=${encodeURIComponent(status)}`);
  params.push(`limit=${Math.min(100, Math.max(1, Number(limit || 20)))}`);
  params.push(`offset=${Math.max(0, Number(offset || 0))}`);
  return request({ path: `/customers/creation-requests/mine?${params.join('&')}` });
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
// 销售渠道使用 sales_channel/partner_id；交易关系与物料行独立保存，均沿用商机版本和幂等写入。
// 新增/编辑均可经本 POST 的 action/create/update 与 opportunity_id/version_no；另有拜访内嵌变更入口。
// 保存页要求 changed:boolean、version_no；changed=true 还需 event_id，缺少回执应先读回核对。
function createOpportunity(customerId, data) {
  return request({
    path: `/customers/${encodeURIComponent(customerId)}/opportunities`,
    method: "POST",
    data,
  });
}

function getPendingOpportunitySave(customerId, opportunityId = '') {
  const auth = getAuth();
  if (!auth || !auth.actor) return null;
  return requestIdentity.pendingOpportunity([getBaseUrl(),auth.actor.workspace_id,auth.actor.user_id], opportunityId);
}
function canRetryOpportunitySave(customerId, opportunityId = '') {
  try {
    const intent = getPendingOpportunitySave(customerId, opportunityId);
    return !!intent && opportunityRetryPayloads.has(intent.key);
  } catch (_) { return false; }
}
function retryOpportunitySave(customerId, opportunityId = '') {
  const intent = getPendingOpportunitySave(customerId, opportunityId), original = intent && opportunityRetryPayloads.get(intent.key);
  if (!original) return Promise.reject(Object.assign(new Error('原提交内容已不在本机内存中，请继续查询结果或联系管理员核对'), {code:'OPPORTUNITY_RETRY_UNAVAILABLE',opportunitySaveUnknown:true}));
  return request({...original, data:JSON.parse(JSON.stringify(original.data)), idempotencyKey:intent.key, opportunityRetryKey:intent.key});
}
async function checkOpportunitySaveResult(customerId, opportunityId = '') {
  const session = currentSession();
  const intent = getPendingOpportunitySave(customerId, opportunityId);
  if (!intent) throw new Error('未找到原保存标识，请联系管理员核对，勿重复创建');
  const response = await request({path:`/customers/${encodeURIComponent(intent.customerId)}/opportunities/save-requests/${encodeURIComponent(intent.key)}`,fresh:true});
  assertCurrentSession(session);
  return resolveOpportunitySaveResult(intent,response);
}
async function cancelOpportunitySave(customerId, opportunityId = '') {
  const session = currentSession();
  const intent = getPendingOpportunitySave(customerId, opportunityId);
  if (!intent) throw new Error('未找到原保存标识，请联系管理员核对，勿重复创建');
  const response = await request({path:`/customers/${encodeURIComponent(intent.customerId)}/opportunities/save-requests/${encodeURIComponent(intent.key)}/cancel`,method:'POST'});
  assertCurrentSession(session);
  return resolveOpportunitySaveResult(intent,response);
}
function resolveOpportunitySaveResult(intent, response) {
  if (!response || response.request_key !== intent.key || !['committed','unresolved','cancelled'].includes(response.status)) throw new Error('保存结果查询回执不完整，请稍后再查');
  if (response.status === 'committed') {
    if (!requestIdentity.validOpportunityReceipt(response.result, {...intent,recovered:true})) throw new Error('保存结果查询回执不完整，请稍后再查');
    requestIdentity.finishOpportunity(intent);
    opportunityRetryPayloads.delete(intent.key);
  } else {
    if (response.result !== null) throw new Error('保存结果查询回执不完整，请稍后再查');
    if (response.status === 'cancelled') { requestIdentity.finishOpportunity(intent); opportunityRetryPayloads.delete(intent.key); }
  }
  return response;
}

function checkOpportunityName(customerId, name, excludeId) {
  const params = [`name=${encodeURIComponent(name)}`];
  if (excludeId) params.push(`exclude_id=${encodeURIComponent(excludeId)}`);
  return request({ path: `/customers/${encodeURIComponent(customerId)}/opportunities/check-name?${params.join('&')}` });
}

// The server owns pagination and returns summaries for the complete filtered set.
function listOpportunities(options = {}) {
  const params = [];
  let periodFilters;
  try {
    periodFilters = require('./opportunityPeriodFilters').params({
      createdEnabled: options.createdYear != null || options.createdQuarter != null,
      createdYear: options.createdYear, createdQuarter: options.createdQuarter,
      activeQuarter: options.activeQuarter === true,
      financialYear: options.financialYear, financialQuarter: options.financialQuarter,
    });
  } catch (error) { return Promise.reject(error); }
  const periodNames={createdYear:'created_year',createdQuarter:'created_quarter',activeQuarter:'active_quarter',financialYear:'financial_year',financialQuarter:'financial_quarter'};
  Object.keys(periodFilters).forEach(key=>params.push(`${periodNames[key]}=${encodeURIComponent(periodFilters[key])}`));
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
  if (options.ownerId) params.push(`owner_id=${encodeURIComponent(options.ownerId)}`);
  if (options.owner && options.owner !== 'all') params.push(`owner=${encodeURIComponent(options.owner)}`);
  if (options.probability) params.push(`probability=${Number(options.probability)}`);
  if (options.stage) params.push(`stage=${encodeURIComponent(options.stage)}`);
  // Activity is a server-side read filter.  Accept the camelCase spelling used
  // by pages as well as the wire spelling so callers cannot accidentally show
  // an unfiltered list while only updating their local picker.
  const activityWindow = options.activityWindow || options.activity_window;
  if (activityWindow && activityWindow !== 'all') params.push(`activity_window=${encodeURIComponent(activityWindow)}`);
  if (options.includeOverview) params.push('include_overview=true');
  if (options.overviewYear) params.push(`overview_year=${Number(options.overviewYear)}`);
  (options.overviewQuarters || []).forEach(quarter => params.push(`overview_quarters=${Number(quarter)}`));
  if (options.closeFrom) params.push(`close_from=${encodeURIComponent(options.closeFrom)}`);
  if (options.closeTo) params.push(`close_to=${encodeURIComponent(options.closeTo)}`);
  params.push(`page_size=${Number(options.pageSize || 100)}`);
  params.push(`offset=${Number(options.offset || 0)}`);
  return request({ path: `/opportunities?${params.join("&")}`, timeout: 45000 });
}

function listAllOpportunities(options = {}, cancelled) {
  return require('./pagination').collectPages(offset => listOpportunities({ ...options, offset }), cancelled);
}

function getOpportunityOverview(selection) {
  // The existing API requires a numeric year, but ignores it for an empty quarter list.
  const allYears = selection.isAllYears || selection.year === 'all';
  const year = allYears ? require('./opportunityQuarter').beijingDateParts(new Date()).year : Number(selection.year);
  const quarters = allYears ? [] : selection.quarters;
  const params = [`year=${year}`, ...quarters.map(q => `quarters=${Number(q)}`)];
  if(selection.scope) params.push(`scope=${encodeURIComponent(selection.scope)}`);
  (selection.memberIds || []).forEach(id=>params.push(`member_ids=${encodeURIComponent(id)}`));
  if(selection.memberId) params.push(`member_id=${encodeURIComponent(selection.memberId)}`);
  const activityWindow = selection.activityWindow || selection.activity_window;
  if (activityWindow && activityWindow !== 'all') params.push(`activity_window=${encodeURIComponent(activityWindow)}`);
  if (selection.teamId && selection.teamId !== 'all') params.push(`team_id=${encodeURIComponent(selection.teamId)}`);
  if (selection.ownerId) params.push(`owner_id=${encodeURIComponent(selection.ownerId)}`);
  if (selection.owner && selection.owner !== 'all') params.push(`owner=${encodeURIComponent(selection.owner)}`);
  return request({ path: `/opportunities/overview?${params.join('&')}`, timeout: 45000 });
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

// Multipart retains the legacy JSON shape. New clients bind a stable operation
// to the local file and query its receipt before any user-confirmed resubmission.
function uploadAudioOnce(tempFilePath, purpose, token, baseUrl, options = {}) {
  return new Promise((resolve, reject) => {
    let send=options=>wx.uploadFile(options);
    // MINI-ONLY-BEGIN
    send=options=>trackedNative(options=>wx.uploadFile(options),options);
    // MINI-ONLY-END
    send({
      url: `${baseUrl}/audio/transcriptions`,
      filePath: tempFilePath,
      name: "file",
      formData: { purpose, language: "zh", session_id:options.sessionId, operation_id:options.operationId, stage:options.stage || 'final' },
      // Leave enough time for the real file upload and upstream transcription.
      timeout: 120000,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (response) => {
        let body = {};
        try { body = JSON.parse(response.data || "{}"); } catch (error) { body = {}; }
        if (response.statusCode >= 200 && response.statusCode < 300) { resolve(body); return; }
        reject(responseError({...response, data:body}, 'POST'));
      },
      fail: (error) => {
        const failure = requestPolicy.transportError({method:'POST'}, error);
        failure.message = '语音处理结果尚未确认，录音已保留，再次点击时将先查询结果';
        failure.recoveryAction = 'check_status';
        reject(failure);
      },
    });
  });
}

function transcribeAudio(tempFilePath, purpose, options = {}) {
  const session = currentSession();
  const auth = getAuth();
  if (!auth || !auth.access_token) return Promise.reject(new Error("登录已失效"));
  const stage=options.stage||'final',key='asrOperations:'+JSON.stringify([session.baseUrl,auth.actor?.workspace_id,auth.actor?.user_id]);
  const fileKey=JSON.stringify([tempFilePath,purpose,stage,options.operationId||'']);
  const flightKey=`${session.generation}:${key}:${fileKey}`;
  if(audioFlights.has(flightKey))return audioFlights.get(flightKey);
  const run=async()=>{
    assertCurrentSession(session);
    const records=wx.getStorageSync(key)||{},previous=records[fileKey];
    let context=previous||{sessionId:options.sessionId||visitUploadIntent.newKey(),operationId:options.operationId||visitUploadIntent.newKey(),stage,createdAt:Date.now()};
    const save=state=>{assertCurrentSession(session);const current=wx.getStorageSync(key)||{};current[fileKey]={...context,state};
      const terminal=Object.keys(current).filter(k=>['completed','failed'].includes(current[k].state)).sort((a,b)=>current[b].createdAt-current[a].createdAt);
      terminal.slice(50).forEach(k=>delete current[k]);wx.setStorageSync(key,current);};
    const pending=(code='ASR_RESULT_UNKNOWN')=>Object.assign(Error(errorMessages.MESSAGES[code]),{code,serverCode:code,
      operationId:context.operationId,sessionId:context.sessionId,receiptPath:`/audio/operations/${context.operationId}?purpose=${encodeURIComponent(purpose)}`,
      outcomeUnknown:true,retryable:false,recoveryAction:'check_status'});
    const read=async()=>{
      const receipt=await request({path:`/audio/operations/${context.operationId}?purpose=${encodeURIComponent(purpose)}`,fresh:true,retryRead:false});
      if(receipt.operation_id!==context.operationId || receipt.purpose!==purpose || receipt.session_id!==context.sessionId)throw pending();
      if(receipt.status==='completed'&&typeof receipt.text==='string'){save('completed');return receipt;}
      if(receipt.status==='failed'){save('failed');throw Object.assign(Error(errorMessages.businessMessage({code:receipt.error_code},'语音处理未完成，录音已保留，请核对后重新识别')),{code:receipt.error_code||'ASR_UPSTREAM_FAILED',audioFailed:true});}
      throw pending(receipt.status==='processing'?'ASR_OPERATION_IN_PROGRESS':'ASR_RESULT_UNKNOWN');
    };
    if(previous){
      try{return await read();}catch(error){
        assertCurrentSession(session);
        if(!error.audioFailed&&error.serverCode!=='ASR_OPERATION_NOT_FOUND')throw error;
        // Only a human retry of a definitely failed/unregistered operation may
        // start a new attempt. An unknown receipt never opens this confirmation.
        if(stage==='preview')throw error;
        const confirmed=await new Promise(resolve=>wx.showModal({title:'重新识别录音',content:'已确认原识别失败或未登记。是否使用保留的录音和当前配置重新发起一次处理？',confirmText:'重新识别',success:r=>resolve(r.confirm),fail:()=>resolve(false)}));
        assertCurrentSession(session);if(!confirmed)throw error;
        if(error.audioFailed)context={sessionId:visitUploadIntent.newKey(),operationId:visitUploadIntent.newKey(),stage,createdAt:Date.now()};
      }
    }
    save('pending');
    try{const result=await authenticatedAttempt(next=>uploadAudioOnce(tempFilePath,purpose,next.access_token,session.baseUrl,context),session);if(typeof result?.text!=='string')throw pending();save('completed');return result;}
    catch(error){
      assertCurrentSession(session);
      if(error.outcomeUnknown||['ASR_RESULT_UNKNOWN','ASR_OPERATION_IN_PROGRESS','ASR_OPERATION_CONFLICT'].includes(error.serverCode)){
        save('unknown');
        if(stage!=='preview')try{return await read();}catch(receiptError){if(receiptError.audioFailed)throw receiptError;}
        throw pending();
      }
      save('failed');throw error;
    }
  };
  const flight=run().finally(()=>audioFlights.delete(flightKey));audioFlights.set(flightKey,flight);return flight;
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
function taskCreateBody({ description, assigneeAccount, targetPosition, dueAt, priority, customerId, opportunityId, associationKind, checklist, executionMode, deliverable, acceptanceCriteria }) {
  return {
    description, association_kind: associationKind,
    ...require('./taskExecutionMode').creation({executionMode,checklist}),
    ...(executionMode==='execution_v2'?{deliverable:String(deliverable||'').trim(),acceptance_criteria:String(acceptanceCriteria||'').trim()}:{}),
    assignee_account_code: targetPosition ? null : assigneeAccount,
    target_position: targetPosition || null,
    due_at: new Date(dueAt).toISOString(),
    priority_code: ({ 普通: "normal", 中: "medium", 高: "high" })[priority] || "normal",
    customer_id: customerId || null, opportunity_id: opportunityId || null,
  };
}
function createTask(input) {
  return request({path: "/tasks", method: "POST", data: taskCreateBody(input)});
}
function createTasks(inputs) {
  return request({path: "/tasks/batch", method: "POST", data: {tasks: inputs.map(taskCreateBody)}});
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

// RISK001: server UUID scope, full summary and pagination; decision and closure are separate writes.
function listRisks(options = {}) {
  const params = typeof options === 'string' ? {status:options} : options;
  const encoded = assetQuery(params), query = encoded ? `?${encoded}` : '';
  return request({ path: `/risks${query}` });
}

function getRisk(riskId, options = {}) {
  return request({ path: `/risks/${encodeURIComponent(riskId)}`, fresh:options.fresh === true });
}

function resolveRisk(riskId, note, version) {
  if (!Number.isInteger(version) || version < 1) return Promise.reject(new Error('风险版本缺失，请刷新后重试'));
  return request({
    path: `/risks/${encodeURIComponent(riskId)}/resolve`,
    method: "POST",
    data: { resolution_note: String(note || "").trim(), version_no:version },
  });
}

function decideRisk(riskId, data, options = {}) {
  const auth = getAuth();
  if (!auth || !auth.actor) return Promise.reject(new Error('请重新登录后提交'));
  const note = String(data.note || '').trim();
  if (!['adopt','defer'].includes(data.decision) || !note || note.length > 2000 || !Number.isInteger(data.version_no) || data.version_no < 1) {
    return Promise.reject(new Error('请确认建议选项、说明与风险版本'));
  }
  return request({path:`/risks/${encodeURIComponent(riskId)}/decision`, method:'POST', freshIntent:options.freshIntent === true,
    data:{decision:data.decision,note,version_no:data.version_no}});
}

// BACKEND-CONTRACT：multipart POST /visit-imports，file + original_filename；120秒上传超时。
// 返回 id/status 后页面用 GET /visit-imports/{id} 每2秒查询；retry 为另一个 POST。
// 离页停止页面轮询，不取消服务端队列；removeFile 仅移除本地引用，没有删除导入接口。
function uploadVisitFile(filePath, filename, onProgress, intentKey, options = {}) {
  if(intentKey&&!visitUploadIntent.UUID.test(intentKey))return Promise.reject(Error('上传标识无效，请保留文件并重新打开草稿'));
  const session = currentSession();
  const send = (auth) => new Promise((resolve,reject) => {
    let upload=options=>wx.uploadFile(options);
    // MINI-ONLY-BEGIN
    upload=options=>trackedNative(options=>wx.uploadFile(options),options);
    // MINI-ONLY-END
    const task=upload({url: `${session.baseUrl}/visit-imports`,filePath,name:'file',
      formData:{original_filename:filename,...(['recording','file'].includes(options.sourceKind)?{source_kind:options.sourceKind}:{}),...(visitUploadIntent.UUID.test(options.asrSessionId || '')?{asr_session_id:options.asrSessionId}:{})},timeout:120000,
      header:{Authorization:`Bearer ${auth.access_token}`,...(intentKey?{'Idempotency-Key':intentKey}:{})},
      success(response) {
        let data;try {data=JSON.parse(response.data);} catch(e) {data=null;}
        if(response.statusCode>=200&&response.statusCode<300) {
          if(intentKey){try{resolve(visitUploadIntent.validate(data,intentKey));}catch(error){reject(error);}return;}
          if(!data || typeof data.id!=='string' || !data.id.trim() || !['queued','processing','succeeded','failed'].includes(data.status)) {
            reject(Object.assign(new Error('未收到完整上传回执，本地文件已保留，请稍后重试'),{code:'UPLOAD_RECEIPT_INVALID',statusCode:response.statusCode}));return;
          }
          resolve(data);return;
        }
        const failure = responseError({...response, data}, 'POST');
        // Upload intent recovery historically consumes its business code directly.
        failure.code = failure.serverCode || failure.code;
        failure.message += '，本地文件已保留';
        reject(failure);
      },fail:e=>reject(requestPolicy.transportError({method:'POST'},e))});
    if(onProgress&&task.onProgressUpdate) task.onProgressUpdate(progress => {
      if (isCurrentSession(session)) onProgress(progress);
    });
  });
  const auth=getAuth();if(!auth) return Promise.reject(new Error('请先登录'));
  return authenticatedAttempt(send, session);
}

module.exports = {
  uploadVisitFile,
  getVisitUploadIntent:key=>request({path:`/visit-imports/requests/${encodeURIComponent(key)}`,fresh:true}).then(receipt=>visitUploadIntent.validate(receipt,key)),
  retryVisitImport:(id,key)=>request({path:`/visit-imports/${encodeURIComponent(id)}/retry`,method:'POST',idempotencyKey:key}),
  BASE_URL_STORAGE_KEY,
  DEFAULT_BASE_URL,
  isEnabled: () => Boolean(getBaseUrl()),
  getBaseUrl,
  getAuth,
  saveAuth,
  request,
  loginWithAccount,
  cancelPendingLogin: invalidateSession,
  getCompanyEntry,
  logout,
  queryChatBI,
  queryTodayTasks,
  queryPersonalRisks,
  queryOperatingReport,
  runAgent,
  getRun,
  resumeRun: waitForRun,
  pendingAgentRuns,
  resumePendingAgent,
  acknowledgeAgentResult,
  confirmAgentNotAccepted,
  getAssistantHome,
  ensureSalesGrowthReview,
  getSalesGrowth,
  getScopedSalesGrowth,
  getMarketingAnalytics,
  getMemberSalesGrowth,
  listCustomers,
  listCustomerClaimPool,
  listOpportunityCustomerReferences,
  listOpportunityCreateCustomers,
  getOpportunityCreateAccess,
  listCustomerClaimOptions,
  getCustomer,
  getDirectoryMembers,
  getTaskAssignees,
  getTaskPositions: (options={}) => request({path:'/directory/task-positions'+(Object.keys(options).length?'?'+assetQuery(options):'')}),
  getWorkbench,
  createCustomer,
  createCustomerCreationRequest,
  listCustomerCreationRequests,
  updateCustomer,
  assignCustomer,
  claimCustomer,
  createOpportunity,
  getPendingOpportunitySave,
  canRetryOpportunitySave,
  retryOpportunitySave,
  checkOpportunitySaveResult,
  cancelOpportunitySave,
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
  createTasks,
  taskCreateBody,
  listNotifications,
  markNotificationRead,
  listRisks,
  getRisk,
  resolveRisk,
  decideRisk,
};

// BACKEND-CONTRACT：以下资产/画像查询原样编码非空 options；合法参数白名单由调用方及后端确认。
// GET /customer-assets 无 customer_id 返回 customers 聚合，有 customer_id 返回 entries 明细。
// 消费者依赖 view/items/summary/has_more/as_of/can_manage；空金额、零金额和缺数必须区分。
function assetQuery(options = {}) {
  return Object.keys(options).filter(k=>options[k] !== null && options[k] !== undefined && options[k] !== '').flatMap(k=>(Array.isArray(options[k])?options[k]:[options[k]]).map(value=>`${encodeURIComponent(k)}=${encodeURIComponent(value)}`)).join('&');
}
// 作战地图 scope 仅支持 self/person/team；department 是画像/目标语义，后端地图授权不接受。
module.exports.getCustomerMap = (options={}) => request({path:'/customer-assets/map'+(Object.keys(options).length?'?'+assetQuery(options):'')});
module.exports.refreshCustomerMap = (options={}) => request({path:'/customer-assets/map/refresh'+(Object.keys(options).length?'?'+assetQuery(options):''),method:'POST'});
module.exports.getCustomerAssets = options => request({path:`/customer-assets?${assetQuery({...options,basis:'effective'})}`});
// FINANCE001: sales clients only read the adopted actual projection. Operations writes live in console.
module.exports.getOpportunityFinancialOverview = (id,options={}) => request({path:`/opportunities/${encodeURIComponent(id)}/financial-overview${Object.keys(options).length?'?'+assetQuery(options):''}`});
module.exports.getOpportunityMaterials = id => request({path:`/opportunities/${encodeURIComponent(id)}/materials`});
module.exports.listMaterials = (options={}) => request({path:'/materials?'+assetQuery(options)});
module.exports.getMaterialProductLines = () => request({path:'/material-product-lines'});
module.exports.previewOpportunityMaterials = (id,data) => request({path:`/opportunities/${encodeURIComponent(id)}/material-forecast-preview`,method:'POST',data});
module.exports.previewMaterialDraft = data => request({path:'/material-forecast-preview',method:'POST',data});
module.exports.listUpstreamOpportunities = (options={}) => request({path:'/opportunities?'+assetQuery({purpose:'upstream',q:options.q,trade_relation:'distributor',include_closed:true,page_size:options.limit||30,offset:options.offset||0,exclude_customer_id:options.customer_id,exclude_opportunity_id:options.opportunity_id})}).then(result=>({...result,total:result.summary&&result.summary.total}));

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
module.exports.getDashboardMetrics = options => request({path:'/dashboard/metrics?'+assetQuery(options),fresh:true});
module.exports.getDashboardMetricOpportunities = options => request({path:'/dashboard/metrics/opportunities?'+assetQuery(options),fresh:true});
module.exports.getDashboardMetricActuals = options => request({path:'/dashboard/metrics/actuals?'+assetQuery(options),fresh:true});

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

module.exports.getBusinessAdvice = id => request({path:`/advice/${encodeURIComponent(id)}`,fresh:true});
module.exports.getLatestBusinessAdvice = async (subjectKind,subjectId,section='overview') => {
  const result=await request({path:`/advice/latest?${assetQuery({subject_kind:subjectKind,subject_id:subjectId,section})}`,fresh:true});
  if(result && result.state==='none' && result.analysis===null)return null;
  const analysis=result && result.analysis;
  const state=analysis && (analysis.stale || analysis.status==='superseded' ? 'stale' : analysis.status);
  if(!result || !['queued','running','succeeded','failed','stale'].includes(result.state) || result.state!==state ||
    !analysis.id || analysis.subject_kind!==subjectKind || String(analysis.subject_id)!==String(subjectId) || analysis.section!==section){
    throw Object.assign(Error('建议读取响应不完整，请重试'),{code:'ADVICE_CONTEXT_MISMATCH'});
  }
  return analysis;
};
// Preserve the returned queued/running ID. Reading status never creates a new job.
module.exports.requestBusinessAdvice = (subjectKind,subjectId,section='overview',retry=false) =>
  request({path:'/advice',method:'POST',data:{subject_kind:subjectKind,subject_id:subjectId,section,retry}});
module.exports.getOpportunityDetail = id => request({path:`/opportunities/${encodeURIComponent(id)}/detail`});
module.exports.getOpportunityViewOptions = () => request({path:'/opportunity-views/options',fresh:true});
module.exports.listOpportunityViews = (options={}) => request({path:`/opportunity-views?limit=${Number(options.limit)||100}&offset=${Number(options.offset)||0}`,fresh:true});
module.exports.getOpportunityView = id => request({path:`/opportunity-views/${encodeURIComponent(id)}`,fresh:true});
module.exports.createOpportunityView = data => request({path:'/opportunity-views',method:'POST',data});
module.exports.updateOpportunityView = (id,data) => request({path:`/opportunity-views/${encodeURIComponent(id)}`,method:'PUT',data});
module.exports.deleteOpportunityView = (id,versionNo) => request({path:`/opportunity-views/${encodeURIComponent(id)}`,method:'DELETE',data:{version_no:versionNo}});
module.exports.getVisit = id => request({path:`/visits/${encodeURIComponent(id)}`});
module.exports.readVisitAudio = function(id,{onProgress,isCancelled}={}) {
  if(!visitUploadIntent.UUID.test(id||''))return Promise.reject(Error('跟进记录标识无效'));
  const session=currentSession();
  const check=()=>{assertCurrentSession(session);if(isCancelled?.())throw Object.assign(Error('录音读取已取消'),{code:'FILE_TRANSFER_CANCELLED'});};
  const path=`/visits/${encodeURIComponent(id)}/audio`;
  let task,timer,tempFilePath='';
  const remove=()=>{if(tempFilePath){try{wx.getFileSystemManager().unlink({filePath:tempFilePath});}catch(_){}tempFilePath='';}};
  // Playback is a single authenticated transfer. Each backend Range currently
  // verifies the complete stored file, so small client ranges multiply I/O.
  const send=auth=>new Promise((resolve,reject)=>{
    check();
    const success=response=>{
      tempFilePath=response.tempFilePath||'';
      try{check();if(response.statusCode<200||response.statusCode>=300)throw responseError(response,'GET');}
      catch(error){remove();reject(error);return;}
      if(!tempFilePath){reject(Error('录音读取失败，请重新打开记录'));return;}
      resolve({tempFilePath});
    };
    task=wx.downloadFile({url:`${session.baseUrl}${path}`,timeout:120000,
      header:auth?.access_token?{Authorization:`Bearer ${auth.access_token}`}:{},
      success,fail:error=>reject(requestPolicy.transportError({method:'GET'},error))});
    if(onProgress&&task?.onProgressUpdate)task.onProgressUpdate(result=>{
      try{check();onProgress({received:result.totalBytesWritten,total:result.totalBytesExpectedToWrite});}catch(_){}
    });
  });
  timer=setInterval(()=>{try{check();}catch(_){task?.abort?.();}},250);
  const pending=typeof wx.downloadFile==='function'?authenticatedAttempt(send,session):request({path,
    responseType:'arraybuffer',fresh:true,timeout:120000,retryRead:false,onRequestTask:value=>{task=value;}}).then(data=>({data}));
  return pending.then(result=>{check();return result;}).catch(error=>{remove();throw error;}).finally(()=>clearInterval(timer));
};
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
  if(result.status==='failed') {
    const code = errorMessages.codeFor({code:result.error_code, detail:result.error}) || 'RUN_FAILED';
    throw Object.assign(Error(errorMessages.businessMessage({code}, '分析未完成，请检查输入后重试')), {code, serverCode:code});
  }
  return result;
};
module.exports.decideSuggestion = (id,data)=>request({path:`/advice/suggestions/${encodeURIComponent(id)}/decision`,method:'POST',data});
module.exports.getAdviceStatistics = ()=>request({path:'/advice/statistics'});

module.exports.getCurrentActor = () => request({path:'/auth/me'});
function actorIdentity(actor={}) {
  return access.identity({workspaceId:actor.workspace_id,userId:actor.user_id,role:actor.role,
    permissions:actor.permissions,permissionGrants:actor.permission_grants,permissionVersion:actor.permission_version,
    capabilities:actor.capabilities,clientExportPermissions:actor.client_export_permissions,teamIds:actor.team_ids});
}
module.exports.updateActor = actor => {const auth=getAuth();if(auth){if(actorIdentity(auth.actor)!==actorIdentity(actor))invalidateSession();writeAuth({...auth,actor});}};
module.exports.listFdeMembers = (options={}) => request({path:`/directory/fde-members?${assetQuery({q:options.q||'',limit:options.limit||50,offset:options.offset||0})}`});
module.exports.updateFdeMembers = (id,memberIds,versionNo) => request({path:`/opportunities/${encodeURIComponent(id)}/fde-members`,method:'PUT',data:{member_ids:memberIds,version_no:versionNo}});
module.exports.getFdeDashboard = (options={}) => request({path:`/fde/dashboard?${fdeQuery(options)}`});

module.exports.getFdeActivity=(options={})=>request({path:`/fde/activity?${fdeQuery(options)}`});

function fdeQuery(options={}) {return assetQuery(options);}
module.exports.coordinateTask=(id,data)=>request({path:`/tasks/${encodeURIComponent(id)}/events`,method:'POST',data});

module.exports.listFdeVisitOpportunities = (options={}) => request({path:`/fde/visit-opportunities?${assetQuery(options)}`});
module.exports.getFdeProfile = (options=30) => request({path:`/fde/profile?${assetQuery(typeof options==='number'?{days:options}:options)}`});
module.exports.getSalesGrowthReview = id => request({path:`/profile/sales-growth/reviews/${encodeURIComponent(id)}`,fresh:true});
module.exports.getFdeProfileReview = id => request({path:`/fde/profile/reviews/${encodeURIComponent(id)}`,fresh:true});
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
// PC Web 跟进记录范围读取；与逐客户的 GET /visits 并行保留，供新 Web 页面一次读取日期/人员/团队范围。
module.exports.listVisitRange = (options={}) => request({path:`/visits/range?${assetQuery(options)}`});
module.exports.listDetailTasks = (options={}) => request({path:`/tasks?${assetQuery({tab:'all',order:'created_desc',...options})}`,fresh:true});
module.exports.getOpportunityTimeline = (id,options={}) => {
  const workspaceId = ((getAuth() || {}).actor || {}).workspace_id;
  return request({path:`/opportunities/${encodeURIComponent(id)}/timeline?${assetQuery(options)}`})
    .then(response => timelineDisplayResponse(response, workspaceId));
};
module.exports.getOpportunityIntentHistory = (id,options={}) => request({
  path:`/opportunities/${encodeURIComponent(id)}/intent-product-line-history?${assetQuery({limit:options.limit||10,offset:options.offset||0,...(options.expected_history_revision?{expected_history_revision:options.expected_history_revision}:{})})}`,
}).then(response=>{
  if(!response||response.schema_version!==1||response.field!=='intent_product_line_ids'||!response.changes)throw Error('历史记录响应不完整，请重试');
  return {...response.changes,opportunity_id:response.opportunity_id,history_revision:response.history_revision};
});
module.exports.getOpportunityChangeHistory = (id,options={}) => {
  const limit=options.limit===undefined?10:options.limit,offset=options.offset===undefined?0:options.offset;
  const revision=options.expected_history_revision;
  if(!Number.isInteger(limit)||limit<1||limit>100||!Number.isInteger(offset)||offset<0||offset>100000||
    (offset>0&&!/^[a-f0-9]{64}$/.test(revision||''))){
    return Promise.reject(Error('历史记录分页参数无效，请重新读取'));
  }
  return request({path:`/opportunities/${encodeURIComponent(id)}/change-history?${assetQuery({
    limit,offset,...(revision?{expected_history_revision:revision}:{})})}`,fresh:true}).then(response=>{
    const page=response&&response.changes;
    if(!response||response.schema_version!==1||response.opportunity_id!==id||
      !/^[a-f0-9]{64}$/.test(response.history_revision||'')||!page||
      page.order!=='created_at_desc_id_desc'||page.limit!==limit||page.offset!==offset||
      !Array.isArray(page.items)||page.items.length>limit||typeof page.has_more!=='boolean'||
      typeof page.coverage_note!=='string'||
      (page.has_more?(!page.items.length||page.next_offset!==offset+page.items.length):page.next_offset!==null)){
      throw Error('历史记录响应不完整，请重试');
    }
    return {...page,opportunity_id:response.opportunity_id,history_revision:response.history_revision};
  });
};
module.exports.getCustomerAssetQuarters = (options={}) => request({path:`/customer-assets/quarters?${assetQuery(options)}`});

module.exports.getCustomerOpportunityOverview = (customerId,opportunityId) => request({path:`/customers/${encodeURIComponent(customerId)}/opportunities/${encodeURIComponent(opportunityId)}/overview`});

// One task-card page; legacy listTasks remains available for existing bulk consumers.
module.exports.listTaskPage = (options={}) => {
  const {completed_quarters=[],...query}=options;
  const params=assetQuery({...query,tab:query.tab||'pending',page_size:Math.min(100,Math.max(1,Number(query.page_size||20)))});
  return request({path:`/tasks?${params}${completed_quarters.map(q=>`&completed_quarters=${encodeURIComponent(q)}`).join('')}`});
};

// Web 1.2.0 task management read model. Its summary covers the complete
// filtered scope, including overdue and undated counts; it is kept separate
// from the legacy task-card endpoint used by the mini-program.
module.exports.listTaskManagement = (options={}) => {
  const params = assetQuery({
    scope: options.scope || 'mine',
    tab: options.tab,
    status: options.status,
    order: ['due_asc','today_first','due_desc','created_desc'].includes(options.order) ? options.order : undefined,
    overdue: options.overdue === true ? true : undefined,
    member_id: options.member_id,
    team_id: options.team_id,
    unassigned: options.unassigned === true ? true : undefined,
    date_from: options.date_from,
    date_to: options.date_to,
    undated: options.undated || 'include',
    page_size: Math.min(100, Math.max(1, Number(options.page_size || 20))),
    offset: Number(options.offset || 0),
  });
  return request({path:`/tasks/management?${params}`});
};
module.exports.listTaskManagementOwners = (options={}) => {
  const params = assetQuery({
    scope: options.scope || 'mine', tab: options.tab, status: options.status, member_id: options.member_id, team_id: options.team_id,
    date_from: options.date_from, date_to: options.date_to, undated: options.undated || 'include',
    page_size: Math.min(100, Math.max(1, Number(options.page_size || 100))), offset: Number(options.offset || 0),
  });
  return request({path:`/tasks/management/owners?${params}`});
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
module.exports.getTeamDirectory=(purpose="browse",options={})=>request({path:`/directory/teams?${assetQuery({purpose,permission:options.permission})}`});

// One metadata flight per authenticated identity and API address. Failures are
// retryable and never fall back to local business enums or another tenant's data.
function getBusinessOptions({fresh=false}={}) {
  const session=currentSession(),key=`${session.generation}:${session.baseUrl}`;
  if(!fresh&&optionsFlight&&optionsFlight.key===key&&Date.now()-optionsFlight.at<60000)return optionsFlight.promise;
  businessOptions.clear();
  const flight={key,at:Date.now()};
  flight.promise=request({path:'/metadata/business-options',fresh}).then(value=>{
    assertCurrentSession(session);if(optionsFlight!==flight)throw Error('业务目录已刷新，请重试');return businessOptions.install(value);
  }).catch(error=>{if(optionsFlight===flight){optionsFlight=null;businessOptions.clear();}throw error;});
  optionsFlight=flight;return flight.promise;
}
module.exports.getBusinessOptions=getBusinessOptions;
const catalogReads=['getWorkbench','getDashboard','listOpportunities','getOpportunityDetail','getCustomerOverview',
 'getCustomer','getCustomerMap','getCustomerHeader','getOpportunityDetailHeader','getCustomerOpportunityHeader','listAllOpportunities','getOpportunityOverview','getOpportunityDetailOverview','getCustomerOpportunityOverview',
 'getFdeDashboard','getFdeProfile','listCustomerOpportunities','listFdeVisitOpportunities'];
catalogReads.forEach(name=>{const read=module.exports[name];if(read)module.exports[name]=(...args)=>{
  const session=currentSession();
  return getBusinessOptions().then(()=>{assertCurrentSession(session);return read(...args);});
};});

module.exports.getOpportunityCreateOptions=()=>request({path:'/opportunities/create-options'});
// Saving a new/changed amount must use a fresh policy for the current company.
module.exports.getCompanyPresentation=()=>request({path:'/company-rules/presentation',fresh:true});
// Knowledge overview reads all currently authorized internal materials with no
// directory filters. Preserve source/status: this is not a JingSocial count,
// and an unknown or failed response must never become zero.
module.exports.getKnowledgeStats=()=>request({path:'/knowledge/stats',fresh:true});

module.exports.getTaskReassignmentOptions=(taskId,options={})=>request({path:`/tasks/${encodeURIComponent(taskId)}/reassignment-options?${assetQuery(options)}`});

// Web history uses a single authorized read model; no client-side per-customer merging.
module.exports.listCustomerRequestHistory = (options = {}) => request({
  path: `/customers/requests/history?${assetQuery({
    scope: options.scope || 'self',
    team_id: options.scope === 'team' ? options.team_id : undefined,
    member_id: options.scope === 'team' ? options.member_id : undefined,
    kind: options.kind || undefined, status: options.status || undefined,
    q: options.q || undefined, limit: options.limit || 20, offset: options.offset || 0,
  })}`,
});

// V1.2.2 G1 task execution. Server checks each action and all three versions.
module.exports.getTaskExecution=id=>request({path:`/tasks/${encodeURIComponent(id)}/execution`,fresh:true});
module.exports.taskExecutionCommand=(id,action,data)=>request({path:`/tasks/${encodeURIComponent(id)}/execution/${encodeURIComponent(action)}`,method:'POST',data});

module.exports.getTaskExecutionCandidates=(id,options)=>request({path:`/tasks/${encodeURIComponent(id)}/execution/candidates?${assetQuery(options)}`,fresh:true});

// Browser File/Blob is passed through the Web wx.uploadFile bridge; native uses a local path.
module.exports.uploadKnowledgeFile=function(file,{idempotencyKey}={}){
  if(typeof idempotencyKey!=='string'||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i).test(idempotencyKey))return Promise.reject(Object.assign(new Error('上传标识无效，请保留文件并重新核对上传意图'),{code:'INVALID_IDEMPOTENCY_KEY'}));
  const session=currentSession();
  if(!getAuth())return Promise.reject(Object.assign(new Error('请先登录'),{code:'AUTH_REQUIRED'}));
  if(!file)return Promise.reject(new Error('请选择文件'));
  invalidateReads();
  return authenticatedAttempt(auth=>new Promise((resolve,reject)=>{
    let upload=options=>wx.uploadFile(options);
    // MINI-ONLY-BEGIN
    upload=options=>trackedNative(options=>wx.uploadFile(options),options);
    // MINI-ONLY-END
    upload({url:`${session.baseUrl}/knowledge/files`,filePath:file,name:'file',timeout:120000,
      header:{Authorization:`Bearer ${auth.access_token}`,'Idempotency-Key':idempotencyKey},
      success(response){
        let data;try{data=typeof response.data==='string'?JSON.parse(response.data):response.data;}catch(error){data=null;}
        if(response.statusCode>=200&&response.statusCode<300){
          if(!data||typeof data!=='object'||Array.isArray(data)){reject(Object.assign(new Error('文件上传回执不完整，请核对文件列表'),{code:'UPLOAD_RECEIPT_INVALID'}));return;}
          resolve(data);return;
        }
        reject(responseError({...response, data}, 'POST'));
      },fail:error=>reject(requestPolicy.transportError({method:'POST'},error))});
  }),session).finally(invalidateReads);
};
module.exports.readKnowledgeFile=function(fileId,{versionId,workId,preview=false,onProgress,isCancelled}={}){
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if(!uuid.test(fileId)||versionId!=null&&!uuid.test(versionId)||workId!=null&&!uuid.test(workId))return Promise.reject(new Error('文件、版本或作品标识无效'));
  if(workId&&(versionId||preview))return Promise.reject(new Error('作品文件仅支持按作品下载'));
  const path=workId?`/knowledge/works/${encodeURIComponent(workId)}/files/${encodeURIComponent(fileId)}/download`:`/knowledge/files/${encodeURIComponent(fileId)}/${preview?'preview':'download'}${versionId?`?version_id=${encodeURIComponent(versionId)}`:''}`;
  const session=currentSession();
  const check=()=>{assertCurrentSession(session);if(typeof isCancelled==='function'&&isCancelled())throw Object.assign(Error('文件读取已取消'),{code:'FILE_TRANSFER_CANCELLED'});};
  // Every part goes through normal current-session authentication and live server ACL.
  return require('./knowledgeFileTransfer').readFileInParts(part=>{
    check();return request({path,responseType:'arraybuffer',includeHeaders:true,fresh:true,...part});
  },{check,onProgress}).then(({data,headers})=>{
    check();
    const header=name=>{const key=Object.keys(headers).find(k=>k.toLowerCase()===name);return key?String(headers[key]):'';};
    const disposition=header('content-disposition'),encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition),plain=/filename="([^"]*)"|filename=([^;]+)/i.exec(disposition);
    let filename=plain?(plain[1]||plain[2]||'').trim():'';if(encoded){try{filename=decodeURIComponent(encoded[1]);}catch(error){/* retain plain filename */}}
    filename=filename.replace(/[\\/\x00-\x1f\x7f]/g,'_');
    return {data,contentType:header('content-type')||'application/octet-stream',filename};
  });
};

module.exports.listOpportunityFollowUpOptions=options=>request({path:'/opportunities/follow-up-options?'+assetQuery(options),fresh:true});
module.exports.getOpportunityAccess=id=>request({path:`/opportunities/${encodeURIComponent(id)}/access`,fresh:true});
// Quarter cumulative collection actuals are saved atomically by createOpportunity.
module.exports.getOpportunityCollectionActuals=(id,{year,quarter})=>request({path:`/opportunities/${encodeURIComponent(id)}/collection-actuals?${assetQuery({year,quarter})}`,fresh:true});

module.exports.getOpportunityCollaborationCandidates=(id,options={})=>{
  if(!require('./opportunityCollaboration').uuid(id))return Promise.reject(Error('商机标识无效'));
  const {q='',limit=20,offset=0}=options;
  return request({path:`/opportunities/${encodeURIComponent(id)}/access-candidates?${assetQuery({q,limit,offset,purpose:'collaborate'})}`,fresh:true});
};
module.exports.saveOpportunityCollaborators=(id,data,idempotencyKey)=>{
  const contract=require('./opportunityCollaboration');
  if(!contract.uuid(id)||!contract.uuid(idempotencyKey))return Promise.reject(Error('协作请求标识无效'));
  let body;try{body=contract.body(data);}catch(error){return Promise.reject(error);}
  return request({path:`/opportunities/${encodeURIComponent(id)}/collaborators`,method:'PUT',data:body,idempotencyKey});
};

module.exports.getOpportunityTransferCandidates=(id,options)=>request({path:`/opportunities/${encodeURIComponent(id)}/access-candidates?${assetQuery({...options,purpose:'transfer'})}`,fresh:true});
module.exports.transferOpportunity=(id,data,idempotencyKey)=>{if(typeof idempotencyKey!=='string'||idempotencyKey.length!==36||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i).test(idempotencyKey))return Promise.reject(Error('转交请求标识无效'));return request({path:`/opportunities/${encodeURIComponent(id)}/transfer`,method:'POST',data,idempotencyKey});};

module.exports.getTaskExecutionHistory=(id,options)=>request({path:`/tasks/${encodeURIComponent(id)}/execution/history?${assetQuery(options)}`,fresh:true});

// MINI-ONLY-BEGIN
module.exports.isCurrentRestrictedLogin=auth=>!!restrictedLoginReceipt && restrictedLoginReceipt.auth===auth && isCurrentSession(restrictedLoginReceipt);
// MINI-ONLY-END

// Narrow recording references do not grant access to opportunity/customer details.
module.exports.listCompanyCustomerFollowUpOptions=options=>request({path:'/customers/company-follow-up-options?'+assetQuery(options),fresh:true});
module.exports.getCustomerFollowUpAccess=id=>request({path:`/customers/${encodeURIComponent(id)}/follow-up-access`,fresh:true});
module.exports.listCompanyFollowUpCustomers=options=>request({path:'/opportunities/company-follow-up-customers?'+assetQuery(options),fresh:true});
module.exports.listCompanyOpportunityFollowUpOptions=options=>request({path:'/opportunities/company-follow-up-options?'+assetQuery(options),fresh:true});
module.exports.getOpportunityFollowUpAccess=id=>request({path:`/opportunities/${encodeURIComponent(id)}/follow-up-access`,fresh:true});

// Four-state task commands retain the shared idempotency and identity protections.
module.exports.taskLightweightCommand=(id,action,data)=>request({path:`/tasks/${encodeURIComponent(id)}/execution-v3/${encodeURIComponent(action)}`,method:'POST',data});

// Net recognition material-quarter facts share the existing atomic opportunity save.
module.exports.getOpportunityRecognizedActuals=(id,{year,quarter})=>request({path:`/opportunities/${encodeURIComponent(id)}/recognized-actuals?${assetQuery({year,quarter})}`,fresh:true});
