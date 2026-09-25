import {readFile, stat} from 'node:fs/promises';

const loopback = value => ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(value);
// Fixed runtime file; no user-controlled file paths and no public static copy.
export async function readLocalPreview(req, path) {
  const result = (status, detail) => ({status, body: {detail}});
  const host = /^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$/i.exec(req.headers.host || '');
  if (!host || Number(host[2] || 80) !== req.socket.localPort ||
      !loopback(req.socket.remoteAddress) || !loopback(req.socket.localAddress) ||
      req.headers['sec-fetch-site'] === 'cross-site' ||
      req.headers.origin && req.headers.origin !== 'http://' + req.headers.host) return result(403, '真实样本仅供本机同源预览');
  if (req.method !== 'GET') return result(405, '仅支持读取本地样本');
  if (!path) return result(404, '未配置本地真实样本');
  try {
    const info = await stat(path);
    if (!info.isFile() || info.size > 64000000) return result(500, '本地 CRM 文件无效');
    const body = JSON.parse(await readFile(path, 'utf8'));
    if (body.format !== 'sales-crm-preview-v1') return result(500, '本地样本格式无效');
    return {status: 200, body};
  } catch (error) {
    return error.code === 'ENOENT' ? result(404, '未配置本地真实样本') : result(500, '本地样本无法读取，请重新生成');
  }
}
