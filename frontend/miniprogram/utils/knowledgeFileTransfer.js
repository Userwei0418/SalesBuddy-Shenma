// Bounded, current-authority reads. No bytes or grants are persisted between clicks.
const PART_BYTES = 256 * 1024;
const MAX_BYTES = 50 * 1024 * 1024;
const DEADLINE_MS = 10 * 60 * 1000;
const header = (headers, name) => {
  const key = Object.keys(headers || {}).find(value => value.toLowerCase() === name);
  return key ? String(headers[key]) : '';
};
const failure = (message, code = 'FILE_TRANSFER_INVALID') => Object.assign(Error(message), {code});

async function readFileInParts(read, {check = () => {}, onProgress} = {}) {
  const started = Date.now();
  let stopped = false, completed = 0, cancelReason;
  const tasks = new Set();
  const abort = () => {for (const task of tasks) {try {task.abort();} catch (error) { /* already complete */ }} tasks.clear();};
  const guard = () => {
    if (cancelReason) throw cancelReason;
    check();
    if (stopped) throw failure('文件读取已停止', 'FILE_TRANSFER_CANCELLED');
    if (Date.now() - started >= DEADLINE_MS - 1000) throw failure('原文件读取超时，请检查网络后重试', 'FILE_TRANSFER_TIMEOUT');
  };
  const get = async (start, end, etag) => {
    guard();
    let value;
    try {
      value = await read({rangeHeader:`bytes=${start}-${end}`, fileIfMatch:etag,
        onRequestTask:task=>{if(task&&typeof task.abort==='function')tasks.add(task);},
        timeout:Math.min(120000, DEADLINE_MS - (Date.now() - started))});
    } catch (error) {guard();throw error;}
    guard();
    return value;
  };
  const progress = total => { if (typeof onProgress === 'function') onProgress({received:completed,total}); };
  const validate = (value, start, end, total, etag) => {
    if (!(value.data instanceof ArrayBuffer) || value.data.byteLength !== end - start + 1 || value.statusCode !== 206 ||
      header(value.headers, 'content-range') !== `bytes ${start}-${end}/${total}` || header(value.headers, 'etag') !== etag) {
      throw failure('文件片段或版本不一致，请重新打开资料');
    }
  };
  // Stop in-flight reads promptly on close/logout, not only after their timeout.
  const watcher = setInterval(() => {try {guard();} catch (error) {cancelReason=error;abort();}}, 250);
  try {
    const first = await get(0, PART_BYTES - 1);
    // Old compatible backends may ignore Range and return the complete file.
    if (first.statusCode === 200) {
      if (!(first.data instanceof ArrayBuffer) || !first.data.byteLength || first.data.byteLength > MAX_BYTES) throw failure('原文件响应不完整');
      completed = first.data.byteLength; progress(completed); return first;
    }
    const match = /^bytes 0-(\d+)\/(\d+)$/.exec(header(first.headers, 'content-range'));
    const etag = header(first.headers, 'etag');
    if (!match || !/^"[0-9a-f]{64}"$/.test(etag)) throw failure('原文件分段回执不完整');
    const total = Number(match[2]), firstEnd = Math.min(PART_BYTES, total) - 1;
    if (!Number.isSafeInteger(total) || total <= 0 || total > MAX_BYTES) throw failure('原文件大小超出可读取范围');
    validate(first, 0, firstEnd, total, etag);
    const output = new Uint8Array(total);
    output.set(new Uint8Array(first.data)); completed = first.data.byteLength; progress(total);
    let next = firstEnd + 1;
    const worker = async () => {
      while (next < total) {
        guard();
        const start = next, end = Math.min(start + PART_BYTES, total) - 1;
        next = end + 1;
        let value;
        try { value = await get(start, end, etag); }
        catch (error) {
          guard();
          // Retry only this read segment once. Denial, revision mismatch and
          // logout never retry; completed segments are not fetched again.
          if (error.code !== 'NETWORK_ERROR' && ![502,503,504].includes(error.statusCode)) throw error;
          value = await get(start, end, etag);
        }
        validate(value, start, end, total, etag);
        output.set(new Uint8Array(value.data), start); completed += value.data.byteLength; progress(total);
      }
    };
    await Promise.all(Array.from({length:Math.min(3, Math.ceil((total-next)/PART_BYTES))}, worker));
    guard();
    if (completed !== total) throw failure('原文件读取不完整');
    return {...first, data:output.buffer};
  } finally { stopped = true; clearInterval(watcher); abort(); }
}

module.exports = {readFileInParts};
