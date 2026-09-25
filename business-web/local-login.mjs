import http from 'node:http';
import https from 'node:https';
import {readFile,stat} from 'node:fs/promises';
import {isAbsolute} from 'node:path';

const loopback = value => value === '127.0.0.1' || value === '::1' || value === '::ffff:127.0.0.1';
const localHost = value => ['127.0.0.1','localhost','[::1]'].includes(value);
const normalize = value => new URL(value).href.replace(/\/$/,'');
function localRequest(req) {
  try {
    const host = typeof req.headers.host === 'string' && /^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$/i.exec(req.headers.host);
    if (!host) return false;
    const port = Number(host[2] || 80);
    if (port < 1 || port > 65535 || port !== req.socket.localPort) return false;
    const url = new URL('http://' + req.headers.host);
    return localHost(url.hostname) && !url.username && !url.password && url.pathname === '/' &&
      loopback(req.socket.remoteAddress) && loopback(req.socket.localAddress);
  } catch (_) {return false;}
}
const result = (status,detail) => ({status,body:{detail}});
const cell = value => value.trim().replace(/^`+|`+$/g,'').replace(/\\\|/g,'|');

async function readCredentials(config,signal) {
  const info = await stat(config.guidePath);
  if (!info.isFile() || info.size > 65536) throw new Error('LOCAL_GUIDE_INVALID');
  const source = await readFile(config.guidePath,{encoding:'utf8',signal});
  const rows = source.split(/\r?\n/).filter(line => line.trim().startsWith('|')).map(line => line.trim().replace(/^\||\|$/g,'').split(/(?<!\\)\|/).map(cell));
  const header = rows.find(row => row.includes('企业账号') && row.includes('密码') && row.includes('已分配角色'));
  if (!header) throw new Error('LOCAL_GUIDE_INVALID');
  const accountIndex = header.indexOf('企业账号'),passwordIndex = header.indexOf('密码'),roleIndex = header.indexOf('已分配角色');
  const matched = rows.filter(row => row[accountIndex] === config.accountCode);
  if (matched.length !== 1 || matched[0].length !== header.length || !matched[0][roleIndex].includes('一线销售')) throw new Error('LOCAL_GUIDE_AMBIGUOUS');
  const password = matched[0][passwordIndex];
  if (!password || password.length > 128) throw new Error('LOCAL_GUIDE_INVALID');
  return {account_code:config.accountCode,password,role:'sales'};
}

function authenticate(target,credentials,timeout,signal) {
  return new Promise((resolve,reject) => {
    if (signal?.aborted) {reject(Object.assign(new Error('ABORTED'),{code:'ABORTED'}));return;}
    const destination = new URL(target);destination.pathname = destination.pathname.replace(/\/$/,'') + '/auth/password/login';
    const payload = JSON.stringify(credentials);
    let settled = false,timer;
    const finish = (error,value) => {
      if (settled) return;settled = true;clearTimeout(timer);signal?.removeEventListener('abort',abort);
      error ? reject(error) : resolve(value);
    };
    const request = (destination.protocol === 'https:' ? https : http).request(destination,{
      method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json','Content-Length':Buffer.byteLength(payload)}
    },response => {
      const chunks = [];let size = 0;
      response.on('data',chunk => {size += chunk.length;if(size > 262144) request.destroy(new Error('RESPONSE_TOO_LARGE'));else chunks.push(chunk);});
      response.on('end',() => {
        let data;try {data = JSON.parse(Buffer.concat(chunks).toString('utf8'));} catch (_) {}
        finish(null,{status:response.statusCode,data});
      });
      response.on('error',error => finish(error));
      response.on('aborted',() => finish(new Error('UPSTREAM_ABORTED')));
    });
    const abort = () => request.destroy(Object.assign(new Error('ABORTED'),{code:'ABORTED'}));
    signal?.addEventListener('abort',abort,{once:true});
    timer = setTimeout(() => request.destroy(Object.assign(new Error('TIMEOUT'),{code:'TIMEOUT'})),timeout);
    request.on('error',error => finish(error));
    request.end(payload);
  });
}

function validSession(auth,config) {
  const actor = auth?.actor;
  return typeof auth?.access_token === 'string' && !!auth.access_token && typeof auth.refresh_token === 'string' && !!auth.refresh_token &&
    auth.auth_method === 'password' && typeof auth.expires_at === 'string' && Date.parse(auth.expires_at) > Date.now() &&
    (auth.must_change_password === undefined || typeof auth.must_change_password === 'boolean') &&
    actor && typeof actor === 'object' && !Array.isArray(actor) && actor.account_code === config.accountCode && actor.role === 'sales' &&
    typeof actor.user_id === 'string' && !!actor.user_id && typeof actor.workspace_id === 'string' && !!actor.workspace_id &&
    ['display_name','role_name','data_scope','scope_name'].every(key => typeof actor[key] === 'string') &&
    ['team_ids','team_names'].every(key => Array.isArray(actor[key]) && actor[key].every(value => typeof value === 'string')) &&
    (actor.permission_version === undefined || typeof actor.permission_version === 'string') &&
    actor.capabilities && typeof actor.capabilities === 'object' && !Array.isArray(actor.capabilities) && Object.values(actor.capabilities).every(value => typeof value === 'boolean');
}

export function createLocalLogin({config,upstream,timeout=12000}={}) {
  let enabled = false,busy = false;
  try {
    const approved = new URL(config.apiTarget);
    enabled = !!upstream && config.role === 'sales' && typeof config.accountCode === 'string' && !!config.accountCode &&
      isAbsolute(config.guidePath) && !approved.username && !approved.password && !approved.search && !approved.hash &&
      (approved.protocol === 'https:' || (approved.protocol === 'http:' && localHost(approved.hostname))) && normalize(approved) === normalize(upstream);
  } catch (_) {}
  return {
    statusFor(req) {return enabled && localRequest(req) ? {available:true,role:'sales',label:'交付账号 · 一线销售'} : {available:false};},
    async handle(req,signal) {
      if (!enabled) return result(404,'本机快捷登录未启用');
      if (req.method !== 'POST') return result(405,'请通过登录页的一键登录按钮进入');
      if (!localRequest(req) || req.headers.origin !== new URL('http://' + req.headers.host).origin ||
        (req.headers['sec-fetch-site'] && req.headers['sec-fetch-site'] !== 'same-origin')) return result(403,'仅允许本机工作空间使用快捷登录');
      if (req.headers['transfer-encoding'] || (req.headers['content-length'] && req.headers['content-length'] !== '0')) {req.resume();return result(400,'快捷登录不接收账号或密码参数');}
      if (busy) return result(409,'登录正在处理，请等待本次结果');
      busy = true;
      try {
        let credentials;
        try {credentials = await readCredentials(config,signal);} catch (_) {return result(503,'原交付包的账号说明不可用，请使用企业账号登录');}
        const response = await authenticate(upstream,credentials,timeout,signal);
        if ([401,403].includes(response.status)) return result(response.status,'交付账号未通过认证，密码或账号状态可能已变化，请联系账号管理员');
        if (response.status === 429) return result(429,'登录请求过于频繁，请稍后重试');
        if (response.status !== 200 || !validSession(response.data,config)) return result(502,'登录服务未返回有效的销售账号会话，请稍后重试');
        const body = Object.fromEntries(['access_token','refresh_token','expires_at','token_type','auth_method','must_change_password','actor'].filter(key => response.data[key] !== undefined).map(key => [key,response.data[key]]));
        return {status:200,body};
      } catch (error) {
        return result(error.code === 'TIMEOUT' ? 504 : error.code === 'ABORTED' ? 499 : 502,error.code === 'TIMEOUT' ? '登录服务处理超时，请稍后重试' : '本次登录连接中断，请重试');
      } finally {busy = false;}
    }
  };
}
