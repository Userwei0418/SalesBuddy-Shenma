import http from 'node:http';
import https from 'node:https';
import { createReadStream, readFileSync } from 'node:fs';
import { stat } from 'node:fs/promises';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createLocalLogin } from './local-login.mjs';
import { readLocalPreview } from './local-preview.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };
const hopHeaders = new Set(['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']);
function respond(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(data));
}
function parseTarget(raw) {
  if (!raw) return null;
  const target = new URL(raw);
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password || target.search || target.hash) throw new Error('SALES_WEB_API_TARGET 须为不含认证信息或查询参数的 HTTP(S) API 根地址');
  target.pathname = target.pathname.replace(/\/+$/, '') || '/api/v1';
  return target;
}

export function createSalesWebServer({ target = process.env.SALES_WEB_API_TARGET || '', staticDir = join(here, 'dist'), timeout = 150000, environment = '销售智助业务服务', healthTimeout = 5000, localLogin, localLoginTimeout = 12000, localPreviewPath, previewOnly = false } = {}) {
  const upstream = previewOnly ? null : parseTarget(target);
  const quickLogin = createLocalLogin({config:previewOnly ? null : localLogin,upstream,timeout:localLoginTimeout});
  const root = resolve(staticDir);
  let connectionSnapshot = null, connectionFlight = null;
  async function connectionStatus() {
    if (connectionSnapshot && Date.now() - Date.parse(connectionSnapshot.checkedAt) < 15000) return connectionSnapshot;
    if (connectionFlight) return connectionFlight;
    const base = {configured: Boolean(upstream), previewOnly, environment, label: environment, apiPrefix: '/api/v1'};
    if (!upstream) return {...base, reachable: false, checkedAt: new Date().toISOString(), detail: '尚未配置业务服务'};
    connectionFlight = new Promise(resolveStatus => {
      const destination = new URL(upstream); destination.pathname = upstream.pathname.replace(/\/$/, '') + '/health/ready';
      let finished = false;
      const finish = result => {
        if (finished) return; finished = true; clearTimeout(timer);
        connectionSnapshot = {...base, ...result, checkedAt: new Date().toISOString()}; resolveStatus(connectionSnapshot);
      };
      const request = (destination.protocol === 'https:' ? https : http).get(destination, {headers: {Accept: 'application/json'}}, response => {
        let body = ''; response.setEncoding('utf8');
        response.on('data', chunk => {body += chunk; if (body.length > 65536) request.destroy(new Error('RESPONSE_TOO_LARGE'));});
        response.on('end', () => {
          let data; try {data = JSON.parse(body);} catch (_) {}
          const reachable = response.statusCode === 200 && data?.status === 'ok';
          const checks = Object.fromEntries(['database', 'model_gateway_configured', 'auth_configured'].filter(key => typeof data?.checks?.[key] === 'boolean').map(key => [key, data.checks[key]]));
          finish({reachable, status: response.statusCode, checks, detail: reachable ? '业务服务已响应，登录后读取授权数据' : '业务服务暂时不可用'});
        });
        response.on('error', () => finish({reachable: false, detail: '业务服务暂时不可用'}));
      });
      const timer = setTimeout(() => request.destroy(new Error('HEALTH_TIMEOUT')), healthTimeout);
      request.on('error', () => finish({reachable: false, detail: '无法连接业务服务，请稍后重试'}));
    }).finally(() => {connectionFlight = null;});
    return connectionFlight;
  }
  return http.createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'same-origin');
    const base = `http://${req.headers.host || 'localhost'}`;
    let url;
    try { url = new URL(req.url, base); } catch (_) { return respond(res, 400, { detail: '请求地址无效' }); }
    if (url.pathname === '/health' || url.pathname === '/healthz') return respond(res, 200, { configured: Boolean(upstream) });
    // Local boot capabilities must not wait for upstream readiness or expose configuration.
    if (url.pathname === '/web-capabilities') {
      if (req.method !== 'GET') return respond(res, 405, {detail: '仅支持读取服务能力'});
      return respond(res, 200, {previewOnly});
    }
    if (url.pathname === '/local-preview-data') {
      const result = await readLocalPreview(req, localPreviewPath);
      return respond(res, result.status, result.body);
    }
    if (url.pathname === '/connection-status') {
      if (req.method !== 'GET') return respond(res, 405, {detail: '仅支持读取连接状态'});
      return respond(res, 200, {...await connectionStatus(),localQuickLogin:quickLogin.statusFor(req)});
    }
    if (url.pathname === '/local-login') {
      const controller = new AbortController();
      const abort = () => {if (!res.writableEnded) controller.abort();};
      res.once('close',abort);
      try {
        const result = await quickLogin.handle(req,controller.signal);
        if (!res.destroyed) respond(res,result.status,result.body);
      } finally {res.off('close',abort);}
      return;
    }
    if (/^\/api\/v1(?:\/|$)/.test(url.pathname)) {
      if (req.headers.origin && req.headers.origin !== base) return respond(res, 403, { detail: '仅允许本站发起接口请求' });
      if (!upstream) return respond(res, 503, { detail: previewOnly ? '此地址不支持企业账号登录，请使用企业工作区地址。' : '尚未配置后端连接，请由协作开发者设置 SALES_WEB_API_TARGET 后重启 Web 服务。' });
      let decodedPath;
      try { decodedPath = decodeURIComponent(url.pathname); } catch (_) { return respond(res, 400, { detail: '接口路径编码无效' }); }
      if (decodedPath.includes('\\') || decodedPath.split('/').some(part => part === '..' || part === '.')) return respond(res, 400, { detail: '接口路径无效' });
      const destination = new URL(upstream);
      destination.pathname = upstream.pathname.replace(/\/$/, '') + url.pathname.slice('/api/v1'.length);
      destination.search = url.search;
      const headers = Object.fromEntries(Object.entries(req.headers).filter(([key]) => !hopHeaders.has(key) && !['host', 'origin', 'referer', 'cookie'].includes(key)));
      headers.host = destination.host;
      const proxy = (destination.protocol === 'https:' ? https : http).request(destination, { method: req.method, headers }, reply => {
        if (reply.statusCode >= 300 && reply.statusCode < 400) {
          reply.resume(); return respond(res, 502, { detail: '后端返回了重定向，请核对 API 根地址及 HTTPS 配置。' });
        }
        const responseHeaders = Object.fromEntries(Object.entries(reply.headers).filter(([key]) => !hopHeaders.has(key) && !['set-cookie', 'access-control-allow-origin'].includes(key)));
        responseHeaders['cache-control'] = 'no-store';
        res.writeHead(reply.statusCode || 502, responseHeaders);
        reply.on('error', () => res.destroy());
        reply.pipe(res);
      });
      const timer = setTimeout(() => proxy.destroy(new Error('UPSTREAM_TIMEOUT')), timeout);
      proxy.on('error', error => {
        if (!res.headersSent) respond(res, error.message === 'UPSTREAM_TIMEOUT' ? 504 : 502, { detail: error.message === 'UPSTREAM_TIMEOUT' ? '后端处理超时，请稍后重试。' : '无法连接已配置的后端，请检查服务状态。' });
        else res.destroy();
      });
      proxy.on('close', () => clearTimeout(timer));
      req.on('aborted', () => proxy.destroy());
      res.on('close', () => { if (!res.writableEnded) proxy.destroy(); });
      req.pipe(proxy);
      return;
    }
    if (!['GET', 'HEAD'].includes(req.method)) return respond(res, 405, { detail: '不支持此请求方法' });
    try {
      const requested = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
      if (requested.includes('\0') || requested.includes('\\')) return respond(res, 400, { detail: '文件路径无效' });
      let file = resolve(root, '.' + requested);
      if (relative(root, file).startsWith('..')) return respond(res, 403, { detail: '文件不可访问' });
      const info = await stat(file);
      if (!info.isFile()) return respond(res, 404, { detail: '页面不存在' });
      res.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream', 'Content-Length': info.size, 'Cache-Control': 'no-cache' });
      if (req.method === 'HEAD') return res.end();
      createReadStream(file).on('error', () => res.destroy()).pipe(res);
    } catch (_) { respond(res, 404, { detail: '页面尚未生成，请先运行 npm run build。' }); }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 5186);
  const host = process.env.HOST || '127.0.0.1';
  let config = {}; try {config = JSON.parse(readFileSync(join(here, 'connection.config.json'), 'utf8'));} catch (error) {if (error.code !== 'ENOENT') throw error;}
  const target = process.env.SALES_WEB_API_TARGET ?? config.apiTarget ?? '';
  let localLogin;
  try {localLogin = JSON.parse(readFileSync(join(here,'.runtime/local-login.json'),'utf8'));} catch (_) {}
  const server = createSalesWebServer({target, environment: config.environment || '销售智助业务服务',localLogin,
    localPreviewPath: join(here, '.runtime/crm-preview.json')});
  server.listen(port, host, () => console.log(`商汤销售小浣熊 Web：http://${host}:${port}；后端${target ? '已配置' : '待配置'}`));
}
