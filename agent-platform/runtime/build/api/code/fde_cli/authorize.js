const requestCode = new URLSearchParams(location.search).get('request');
const message = document.getElementById('message');
const button = document.getElementById('approve');
const login = document.getElementById('login');
function showLogin() {
  message.textContent = '请先用你自己的平台账号登录，再返回这里确认授权。';
  login.href = '/signin?redirect_url=' + encodeURIComponent(location.pathname + location.search);
  login.hidden = false;
}
function csrf() {
  const entry = document.cookie.split('; ').find(x => /^(?:__Host-|__Secure-)?csrf_token=/.test(x));
  return entry ? decodeURIComponent(entry.slice(entry.indexOf('=') + 1)) : '';
}
async function load() {
  const r = await fetch('/fde-cli/v1/auth/context?request=' + encodeURIComponent(requestCode), {credentials:'same-origin'});
  const v = await r.json();
  if (r.status === 401) return showLogin();
  if (!v.ok) throw new Error(v.error);
  document.getElementById('account').textContent = v.data.account.email;
  document.getElementById('device').textContent = v.data.label;
  document.getElementById('code').textContent = v.data.user_code;
  document.getElementById('details').hidden = false;
  if (v.data.status !== 'pending') { message.textContent = '此请求已经处理，请返回终端。'; return; }
  message.textContent = '授权范围仅限 授权工作空间。';
  button.hidden = false;
}
button.onclick = async () => {
  button.disabled = true;
  try {
    const r = await fetch('/fde-cli/v1/auth/approve', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify({request:requestCode})});
    const v = await r.json();
    if (r.status === 401) { showLogin(); button.hidden=true; return; }
    if (!v.ok) throw new Error(v.error);
    message.textContent = '授权成功。返回终端即可使用，当前页面可以关闭。';
    button.hidden = true;
  } catch(e) { message.textContent=e.message; button.disabled=false; }
};
load().catch(e => {message.textContent=e.message;});
