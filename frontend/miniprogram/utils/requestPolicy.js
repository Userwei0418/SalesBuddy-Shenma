// Client budgets do not extend the server/database deadline. Aggregate reads
// need room for its bounded queries; writes keep their existing explicit budget.
const errorMessages = require('./errorMessages');
const AGGREGATE_READS = new Set([
  '/customer-assets', '/customer-assets/map', '/customer-assets/quarters',
  '/customers', '/customers/claim-pool', '/customers/portfolio', '/opportunities', '/opportunities/overview',
  '/dashboard', '/dashboard/rankings', '/profile/performance',
  '/workbench', '/fde/dashboard', '/fde/profile',
]);
// Only completed HTTP failures on small directory reads may be replayed once.
// A transport timeout can leave an expensive server request running: never retry it.
const RETRYABLE_DIRECTORIES = new Set([
  '/metadata/business-options', '/directory/teams', '/directory/members',
  '/directory/task-assignees', '/directory/task-positions',
  '/profile/scope-options', '/fde/scope-options', '/dashboard/options',
]);
function methodOf(options) { return String(options.method || 'GET').toUpperCase(); }
function isRead(options) { return ['GET', 'HEAD'].includes(methodOf(options)); }
function pathOf(options) { return String(options.path || '').split('?')[0]; }
function timeoutFor(options) {
  return options.timeout === undefined
    ? (isRead(options) && AGGREGATE_READS.has(pathOf(options)) ? 45000 : 15000)
    : options.timeout;
}
function header(headers, name) {
  const key = Object.keys(headers || {}).find(k => k.toLowerCase() === name);
  return key ? String(headers[key]) : '';
}
function retryAfter(headers, now = Date.now()) {
  const raw = header(headers, 'retry-after');
  if (!raw) return null;
  const ms = /^\d+(\.\d+)?$/.test(raw) ? Number(raw) * 1000 : Date.parse(raw) - now;
  return Number.isFinite(ms) ? Math.max(0, ms) : null;
}
function recovery(options, status, body, headers) {
  const code = errorMessages.codeFor(body), knownRejection = errorMessages.isKnownRejection(code);
  const audioPending=['ASR_RESULT_UNKNOWN','ASR_OPERATION_IN_PROGRESS','ASR_OPERATION_CONFLICT'].includes(code);
  const read = isRead(options), uncertain = !read && !knownRejection && code!=='ASR_UPSTREAM_FAILED' && (status >= 500 || status === 408 || audioPending);
  const requestId = body && body.request_id || header(headers, 'x-request-id');
  const retryable = read && !knownRejection && [408, 429, 500, 502, 503, 504].includes(status) && body?.retryable !== false;
  const recoveryAction = uncertain ? 'check_status' : knownRejection ? 'contact_admin'
    : status === 401 ? 'login' : status === 403 ? 'contact_admin'
      : audioPending || ['IDEMPOTENCY_IN_PROGRESS', 'IDEMPOTENCY_UNRESOLVED', 'IDEMPOTENCY_CONFLICT', 'UPLOAD_STATUS_UNAVAILABLE'].includes(code) ? 'check_status'
        : retryable ? 'retry_read' : status === 409 ? 'refresh' : 'correct_request';
  return {
    retryable, outcomeUnknown: uncertain,
    recoveryAction,
    retryAfterMs: retryAfter(headers),
    ...(typeof requestId === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(requestId) ? {requestId} : {}),
  };
}
function transportError(options, error) {
  const raw = String(error?.errMsg || error?.message || '');
  const timeout = /time\s*out|超时/i.test(raw), cancelled = /abort|取消/i.test(raw);
  const read = isRead(options), outcomeUnknown = !read;
  const message = cancelled ? (read ? '读取已取消' : '提交已中断，结果待确认，请先查看记录')
    : read ? (timeout ? '读取超时，请稍后重试' : '网络连接失败，请恢复连接后重试')
      : (timeout ? '提交超时，结果待确认，请先查看记录，勿重复提交' : '连接中断，提交结果待确认，请先查看记录，勿重复提交');
  return Object.assign(new Error(message), {
    // Keep NETWORK_ERROR compatible with existing draft/upload recovery handlers.
    code: cancelled ? 'REQUEST_CANCELLED' : 'NETWORK_ERROR',
    timeout, cancelled, outcomeUnknown, retryable: read && !cancelled,
    recoveryAction: read ? 'retry_read' : 'check_status',
  });
}
function retryDelay(options, error, attempt) {
  if (attempt > 0 || methodOf(options) !== 'GET' || !RETRYABLE_DIRECTORIES.has(pathOf(options)) ||
      options.retryRead === false || ![429, 503, 504].includes(error.statusCode) || error.retryable === false) return null;
  // Respect longer server cooldowns by returning control to the user instead of
  // clipping Retry-After and issuing an early request. Jitter avoids a herd.
  if (error.retryAfterMs !== null && error.retryAfterMs > 5000) return null;
  return Math.max(750, error.retryAfterMs || 0) + Math.floor(Math.random() * 250);
}
module.exports = {timeoutFor, isRead, recovery, transportError, retryDelay};
