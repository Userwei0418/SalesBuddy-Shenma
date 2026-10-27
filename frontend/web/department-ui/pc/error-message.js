// HTTP 200 can carry a failed asynchronous job. Its diagnostic detail is not UI copy.
export function businessErrorMessage(error, fallback = '处理未完成，请稍后重试') {
  try {
    return globalThis.SalesRuntime.require('utils/errorMessages').businessMessage(error, fallback);
  } catch {
    return fallback;
  }
}
