// User-facing errors are selected from this catalogue, never server exception text.
// Machine codes and safe request ids remain available for recovery and support.
const MESSAGES = Object.freeze({
  KNOWLEDGE_TEMPORARILY_UNAVAILABLE: '知识库暂不能使用',
  ASR_HOTWORD_INVALID: '热词内容不符合要求，请检查标准词、场景和备注。',
  ASR_HOTWORD_DUPLICATE: '本公司已存在同名热词，请编辑已有词条。',
  ASR_HOTWORD_LIMIT_EXCEEDED: '热词数量或长度超出限制，请减少后再保存。',
  ASR_HOTWORD_VERSION_CONFLICT: '词表已被其他人更新，输入已保留，请核对最新词表后再保存。',
  ASR_OPERATION_CONFLICT: '本次操作内容已变化，请先核对原操作结果。',
  ASR_OPERATION_IN_PROGRESS: '语音正在识别，请稍后核对结果，不需要重复上传。',
  ASR_AUDIO_INVALID: '录音内容无法识别，请检查文件是否完整。',
  ASR_UPSTREAM_FAILED: '语音服务暂时未完成处理，请保留录音并核对结果。',
  ASR_SCOPE_DENIED: '当前账号无法处理这条语音，请核对公司和账号。',
  ASR_OPERATION_NOT_FOUND: '暂未查到本次操作结果，请保留输入并稍后核对。',
  ASR_SNAPSHOT_CHANGED: '识别配置已变化，原结果已保留，请确认后重新处理。',
  ASR_RESULT_UNKNOWN: '识别结果尚未确认，请先查看已有任务，避免重复识别。',
  ASR_BUDGET_EXCEEDED: '本次语音调用次数或额度已用完，请核对已有结果。',
  ASR_CAPACITY_BUSY: '语音处理较繁忙，请保留录音并稍后查看任务。',
  AUTH_REQUIRED: '请先登录后再操作。', TOKEN_INVALID: '登录已失效，请重新登录。',
  SESSION_EXPIRED: '登录已过期，请重新登录。', ACTOR_INACTIVE: '当前账号已停用，请联系管理员。',
  ACTOR_SCOPE_CHANGED: '账号权限已变化，请重新登录。', TENANT_INACTIVE: '当前公司暂不可用，请联系管理员。',
  PASSWORD_CHANGE_REQUIRED: '请先修改初始密码。', PERMISSION_DENIED: '当前账号没有此操作权限，请联系管理员。',
  FORBIDDEN: '当前账号没有此操作权限，请联系管理员。', NOT_FOUND: '记录不存在或当前账号无法访问。',
  CUSTOMER_NOT_FOUND: '客户不存在或当前账号无法访问。', OPPORTUNITY_NOT_FOUND: '商机不存在或当前账号无法访问。',
  VERSION_CONFLICT: '记录已被更新，请刷新后核对最新内容。',
  IDEMPOTENCY_CONFLICT: '本次提交标识对应的内容已变化，请先核对原提交结果。',
  IDEMPOTENCY_IN_PROGRESS: '原提交仍在处理中，请稍后查看处理结果，勿重复提交。',
  IDEMPOTENCY_UNRESOLVED: '原提交结果尚未确认，请先查看处理结果，勿重复提交。',
  UPLOAD_STATUS_UNAVAILABLE: '上传结果暂不可核实，请保留原文件并稍后查看结果。',
  TARGET_INPUT_INVALID: '请核对目标指标、金额精度和版本后重新提交。',
  TARGET_METRIC_UNAVAILABLE: '目标指标已停用或不可配置，请重新选择指标。',
  TARGET_VALUE_INVALID: '请核对目标金额及小数精度；数量指标须填写整数。',
  TARGET_PENDING_CONFLICT: '已有相关目标申请待审批，请等待处理后再调整。',
  VALIDATION_ERROR: '请检查填写内容是否完整、格式是否正确。',
  OPPORTUNITY_TRADE_RELATION_REQUIRED: '请先在交易物料中选择交易关系，再保存实际回款',
  OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN: '下游商机不能登记非零我方回款，请核对交易关系与收款归属',
  OPPORTUNITY_COLLECTION_VERSION_CONFLICT: '实际回款或来源已变化，请刷新回款状态并核对后保存',
  OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED: '请确认替换本季度现有回款来源后再保存',
  OPPORTUNITY_COLLECTION_FORBIDDEN: '当前账号无权修改此商机的实际回款',
  OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED: '本次保存标识缺失，请重新打开商机后保存',
  OPPORTUNITY_COLLECTION_INPUT_INVALID: '请检查季度实际回款金额、回款依据和备注',
  OPPORTUNITY_COLLECTION_PERIOD_INVALID: '请选择有效的回款年份和季度',
  INVALID_REQUEST: '请求内容不符合要求，请检查填写内容。',
  TENANT_RATE_LIMITED: '操作较频繁，请稍后重试。',
  TENANT_CALL_QUOTA_EXCEEDED: '本公司调用额度已用完，请联系管理员。',
  AI_SERVICE_NOT_CONFIGURED: '智能服务尚未配置完成，请联系管理员。',
  KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED: '知识内容生成服务尚未配置，请联系管理员。',
  RUNTIME_CREDENTIAL_UNAVAILABLE: '服务配置暂不可用，请联系管理员。',
  MODEL_CREDENTIAL_UNAVAILABLE: '智能服务配置暂不可用，请联系管理员。',
  FINANCE_RECOVERY_READ_ONLY: '系统暂处于只读恢复状态，请联系管理员。',
  DATABASE_BUSY: '服务繁忙，读取暂未完成，请稍后重试。',
  REQUEST_TIMEOUT: '读取超时，请稍后重试。',
  INTERNAL_ERROR: '服务暂时异常，请稍后重试；持续失败时请联系管理员。',
  TASK_EXECUTION_FORBIDDEN: '当前账号没有此项任务的执行权限。',
  TASK_EXECUTION_UNKNOWN_ACTION: '任务操作已不可用，请刷新后重新选择。',
});
const STATUS_MESSAGES = Object.freeze({
  400: '请求内容不符合要求，请检查填写内容。', 401: '登录验证失败或会话已过期，请重新登录。',
  403: '当前账号没有此操作权限，请联系管理员。', 404: '记录不存在或当前账号无法访问。',
  405: '当前操作不可用，请刷新页面后再试。', 408: '读取超时，请稍后重试。',
  409: '记录状态已变化，请刷新并核对最新内容。', 410: '该功能或记录已不可用，请刷新后重新选择。',
  413: '提交内容过大，请减少内容或文件大小。', 415: '文件格式不受支持，请更换文件。',
  422: '请检查填写内容是否完整、格式是否正确。', 429: '操作较频繁，请稍后重试。',
  500: MESSAGES.INTERNAL_ERROR, 502: '服务暂时无法连接，请稍后重试。',
  503: '服务暂不可用，请稍后重试。', 504: MESSAGES.REQUEST_TIMEOUT,
});
const FIELD_LABELS = Object.freeze({
  company_code: '公司编码', workspace: '公司编码', account_code: '账号', password: '密码',
  old_password: '原密码', new_password: '新密码', otp: '动态验证码', name: '名称',
  customer_id: '客户', customer_type: '客户类型', source: '客户来源', industry: '所属行业',
  contact_name: '联系人姓名', contact_title: '联系人职务', contact_role: '联系人角色',
  opportunity_id: '商机', amount: '金额', expected_close_date: '预计关单日期',
  follow_up_record: '跟进内容', next_action: '下一步计划', interaction_at: '跟进时间',
  collaborator_ids: '协同人', fde_participant_ids: '协助人员', reason: '变更原因',
  collection_actuals: '季度实际回款',
});
const COLLECTION_FIELD_LABELS = Object.freeze({
  year: '年份', quarter: '季度', amount: '金额', source_ref: '回款依据', note: '备注',
  expected_actual_revision: '回款状态', replace_source: '来源替换确认',
});
const REJECTED_WITHOUT_WRITE = new Set([
  'KNOWLEDGE_TEMPORARILY_UNAVAILABLE',
  'AI_SERVICE_NOT_CONFIGURED', 'RUNTIME_CREDENTIAL_UNAVAILABLE', 'FINANCE_RECOVERY_READ_ONLY', 'TENANT_CALL_QUOTA_EXCEEDED',
  'KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED',
]);
const CODE = /^[A-Z][A-Z0-9_]{0,99}$/;
const REQUEST_ID = /^[A-Za-z0-9_-]{1,100}$/;
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
function header(headers, name) {
  if (typeof headers?.get === 'function') return headers.get(name) || '';
  const key = Object.keys(headers || {}).find(value => value.toLowerCase() === name);
  return key ? headers[key] : '';
}
export function errorCode(body) {
  const detail = body?.detail;
  return [body?.code, detail && typeof detail === 'object' && !Array.isArray(detail) ? detail.code : '', detail]
    .find(value => typeof value === 'string' && CODE.test(value)) || '';
}
function collectionField(loc) {
  return Array.isArray(loc) && loc.length === 4 && loc[0] === 'body' && loc[1] === 'collection_actuals' &&
    Number.isSafeInteger(loc[2]) && loc[2] >= 0 && loc[2] < 40 &&
    typeof loc[3] === 'string' && own(COLLECTION_FIELD_LABELS, loc[3]) ? loc[3] : '';
}
function fieldMessage(body) {
  if (!Array.isArray(body?.detail)) return '';
  const details = body.detail.slice(0, 50);
  if (details.some(item => collectionField(item?.loc) === 'expected_actual_revision')) {
    return MESSAGES.OPPORTUNITY_COLLECTION_VERSION_CONFLICT;
  }
  const labels = [...new Set(details.map(item => {
    const loc = item?.loc;
    if (!Array.isArray(loc)) return '';
    if (loc.length === 2 && ['body', 'query', 'path'].includes(loc[0]) &&
        typeof loc[1] === 'string' && own(FIELD_LABELS, loc[1])) return FIELD_LABELS[loc[1]];
    if (collectionField(loc)) {
      return `第 ${loc[2] + 1} 条季度实际回款的${COLLECTION_FIELD_LABELS[loc[3]]}`;
    }
    return '';
  }).filter(Boolean))].slice(0, 3);
  return labels.length ? `请检查${labels.join('、')}是否填写完整、格式是否正确。` : STATUS_MESSAGES[422];
}
export function apiError({status = 0, body, method = 'GET', headers, messages = {}, statusMessages = {}} = {}) {
  const code = errorCode(body);
  const read = ['GET', 'HEAD'].includes(String(method).toUpperCase());
  const explicitlyRejected = REJECTED_WITHOUT_WRITE.has(code);
  const outcomeUnknown = !read && (status === 0 || status >= 500 || status === 408) && !explicitlyRejected;
  const retryable = read && (status === 0 || [408, 429, 500, 502, 503, 504].includes(status)) &&
    body?.retryable !== false && !explicitlyRejected;
  const requestId = [body?.request_id, header(headers, 'x-request-id')]
    .find(value => typeof value === 'string' && REQUEST_ID.test(value)) || '';
  const message = outcomeUnknown ? '提交结果尚未确认，请先查看处理结果，勿重复提交。'
    : status === 422 && Array.isArray(body?.detail) ? fieldMessage(body)
    : own(messages, code) ? messages[code]
    : own(MESSAGES, code) ? MESSAGES[code]
    : own(statusMessages, status) ? statusMessages[status]
    : status === 0 ? '网络连接失败，请恢复连接后重试。'
    : STATUS_MESSAGES[status] || '操作暂未完成，请刷新核对；持续失败时请联系管理员。';
  const recoveryAction = outcomeUnknown || ['IDEMPOTENCY_IN_PROGRESS', 'IDEMPOTENCY_UNRESOLVED', 'IDEMPOTENCY_CONFLICT', 'UPLOAD_STATUS_UNAVAILABLE'].includes(code)
    ? 'check_status' : status === 401 ? 'login' : status === 403 || explicitlyRejected ? 'contact_admin'
    : retryable ? 'retry_read' : 'correct_request';
  return Object.assign(new Error(message), {code: code || (status ? `HTTP_${status}` : 'NETWORK_ERROR'),
    serverCode: code, statusCode: status, requestId, retryable, outcomeUnknown, uncertain: outcomeUnknown,
    recoveryAction});
}
