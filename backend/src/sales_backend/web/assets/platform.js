import {apiError} from './error-presentation.js';
import {installConfigurationStatus} from './platform-configuration.js';
import {installSupport} from './platform-support.js';
import {installRuntimeOverview} from './platform-runtime.js';
const $=s=>document.querySelector(s);
const labels={provisioning:'准备中',active:'启用',suspended:'暂停',closed:'关闭'};
const purposes={internal:'内部自用',demo:'演示',customer:'客户 SaaS',existing:'现有公司'};
const errors={AI_CONFIGURATION_REQUIRED:'AI 文字模型缺少必要配置。请先检查公司模型配置；若公司暂停中，可暂关 AI 后恢复基础业务。',FEISHU_CONFIGURATION_REQUIRED:'飞书缺少必要连接、凭据或映射。请先保存公司配置；若公司暂停中，可暂关飞书后恢复基础业务。',INVALID_CALL_QUOTA:'调用额度设置无效，请检查次数、周期和变更原因。',INVALID_HTTP_LIMIT:'请求频率需同时留空，或填写有效速率、突发容量和变更原因。',INVALID_STORAGE_QUOTA:'原件容量需为空或非负数，并填写变更原因。',INVALID_ACCOUNT_QUOTA:'账号额度需为空或非负整数，并填写变更原因。',INVALID_JOB_SETTINGS:'并发上限应为 1–16，且需填写变更原因。',PLATFORM_MFA_INVALID:'密码或动态验证码无效；已用过的验证码需等待下一次刷新。',PLATFORM_MFA_REQUIRED:'请先绑定并验证动态验证码。',PLATFORM_REAUTH_REQUIRED:'敏感操作验证已过期，请完成验证后重新提交。',PLATFORM_MFA_ALREADY_ENROLLED:'已绑定验证器，如需恢复请联系安装维护人员。',PLATFORM_MFA_NOT_CONFIGURED:'平台验证密钥未就绪，请联系安装维护人员。',INVALID_MODULE_SETTINGS:'模块设置无效，请刷新后重试。',CORE_MODULE_REQUIRED:'账号与基础管理模块必须保留。',INVALID_MODULE_EXPIRY:'到期时间无效。',PLATFORM_AUTH_REQUIRED:'平台会话已失效，请重新登录。',PLATFORM_LOGIN_FAILED:'平台账号或密码不正确。',PLATFORM_NOT_CONFIGURED:'此环境尚未启用平台管理。',PLATFORM_TRY_LATER:'尝试过于频繁，请稍后重试。',COMPANY_CODE_ALREADY_EXISTS:'公司码已存在，请使用其他公司码。',IDEMPOTENCY_CONFLICT:'这次开户请求已用于另一份内容，请刷新后重试。',VERSION_CONFLICT:'公司状态已变化，已刷新列表，请重新选择操作。',TENANT_NOT_READY:'尚未完成管理员激活或配置就绪检查。',ACTIVATION_INVALID_OR_EXPIRED:'激活码无效或已过期，请联系平台重新生成。',INVALID_TENANT_TRANSITION:'当前状态不允许这项操作。'};
let httpSelection=null,httpRequest=0,callSelection=null,callRequest=0;
let tenants=[],entryRequest=0,expiryTimer=null,authExpiry=null;
let generation=0,tenantRequest=null,pending=null,auditCursor=null,moduleSelection=null,moduleRequest=0,jobSelection=null,jobRequest=0,quotaSelection=null,quotaRequest=0,storageSelection=null,storageRequest=0;
function notice(message,error=false){$('#notice').textContent=message;$('#notice').classList.toggle('error',error);}
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
async function api(path,body,headers={}) {
 const g=generation,method=body===undefined?'GET':'POST';let response,data;
 try {
  response=await fetch('/api/v1/platform'+path,{method,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...headers},body:body===undefined?undefined:JSON.stringify(body)});
  data=await response.json();
 } catch (_) {
  if(g!==generation)throw Error('平台身份已变化，请重新操作。');
  throw apiError({method});
 }
 if(g!==generation)throw Error('平台身份已变化，请重新操作。');
 if(!response.ok) {
  const failure=apiError({status:response.status,body:data,method,headers:response.headers,messages:errors});
  if(response.status===401)loggedOut();
  if(failure.serverCode==='PLATFORM_REAUTH_REQUIRED')requestVerification();
  throw failure;
 }
 return data;
}
function loggedOut(){generation++;clearTimeout(expiryTimer);authExpiry=null;entryRequest++;tenants=[];$('#platform-nav').hidden=true;$('#auth-intro').hidden=false;$('#otp-panel').hidden=true;$('#otp-form').reset();$('#mfa-form').reset();$('#login-form').reset();$('#mfa-qr').hidden=true;$('#mfa-qr').removeAttribute('src');for(const id of ['create-dialog','tenant-dialog','activation-result'])$('#'+id).close();supportUI.close();runtimeUI.close();configurationUI.close();$('#calls-dialog').close();callSelection=null;callRequest++;$('#http-dialog').close();httpSelection=null;httpRequest++;$('#mfa-panel').hidden=true;$('#mfa-secret').textContent='';$('#mfa-enrollment').hidden=true;$('#reauth-dialog').close();$('#reauth-form').reset();$('#reauth').hidden=true;$('#workspace').hidden=true;$('#login-panel').hidden=false;$('#operator').textContent='';$('#logout').hidden=true;$('#tenants').replaceChildren();$('#audit').replaceChildren();$('#activation-value').textContent='';$('#state-dialog').close();$('#modules-dialog').close();$('#jobs-dialog').close();$('#quota-dialog').close();quotaSelection=null;quotaRequest++;$('#storage-dialog').close();storageSelection=null;storageRequest++;jobSelection=null;moduleSelection=null;pending=null;tenantRequest=null;}
function loggedIn(operator){
 generation++;supportUI.close();runtimeUI.close();configurationUI.close();
 const verified=operator.mfa_enrolled&&operator.mfa_verified;
 $('#workspace').hidden=!verified;$('#platform-nav').hidden=!verified;$('#auth-intro').hidden=verified;
 $('#mfa-panel').hidden=verified||operator.mfa_enrolled;$('#otp-panel').hidden=verified||!operator.mfa_enrolled;
 $('#reauth').hidden=!verified;$('#login-panel').hidden=true;$('#logout').hidden=false;$('#operator').textContent=operator.display_name;
 $('#mfa-secret').textContent='';$('#mfa-qr').removeAttribute('src');$('#mfa-qr').hidden=true;$('#mfa-enrollment').hidden=true;
 clearTimeout(expiryTimer);authExpiry=operator.expires_at?new Date(operator.expires_at).getTime():null;
 if(authExpiry){const remaining=Math.max(0,authExpiry-Date.now());document.querySelectorAll('.auth-expiry').forEach(e=>e.textContent='请在 '+Math.ceil(remaining/60000)+' 分钟内完成；过期后重新验证账号。');expiryTimer=setTimeout(()=>{loggedOut();notice('平台会话已到期，请重新登录。');},remaining);}
 return verified;
}
async function submit(form,work){const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await work(form.tagName==='FORM'?new FormData(form):new FormData());}catch(e){notice(e.message,true);}finally{buttons.forEach(b=>b.disabled=false);}}
function showActivation(data){$('#activation-value').textContent=(data.company_code?'公司码：'+data.company_code+'\n':'')+data.activation_token;$('#activation-page-link').href='/company-activate?company='+encodeURIComponent(data.company_code||'');$('#activation-result').showModal();}
function requestState(tenant,status){pending={tenant,status};$('#state-title').textContent=status==='activation'?'重新生成激活码':(labels[status]+'公司：'+tenant.name);$('#state-impact').textContent=status==='active'?'就绪检查通过后，成员可以登录并按权限使用业务功能。':status==='activation'?'旧激活码将立即失效，仅可为尚未激活的管理员重新生成。':'将立即撤销公司内的登录会话，并暂停业务访问和后台任务。关闭会保留数据，但当前不能重新启用。';$('#state-form').reset();$('#state-dialog').showModal();}
function tenantReady(t){return Object.values(t.readiness||{}).every(Boolean);}
function showTenant(t){
 const ready=tenantReady(t);
 $('#tenant-title').textContent=t.name;$('#tenant-meta').textContent=t.company_code+' · '+purposes[t.purpose]+' · '+labels[t.status];
 const actions=node('div','','actions');const options=t.status==='provisioning'?(ready?['active','closed']:['activation','closed']):t.status==='active'?['suspended','closed']:t.status==='suspended'?['active','closed']:[];for(const s of options){const b=node('button',s==='activation'?'重新生成激活码':labels[s]);b.onclick=()=>requestState(t,s);actions.append(b);}const modules=node('button','功能模块');modules.onclick=()=>showModules(t).catch(e=>notice(e.message,true));actions.append(modules);const jobs=node('button','任务运行');jobs.onclick=()=>showJobs(t).catch(e=>notice(e.message,true));actions.append(jobs);const quota=node('button','账号额度');quota.onclick=()=>showQuota(t).catch(e=>notice(e.message,true));actions.append(quota);const storage=node('button','原件存储');storage.onclick=()=>showStorage(t).catch(e=>notice(e.message,true));actions.append(storage);const http=node('button','请求频率');http.onclick=()=>showHTTPLimits(t).catch(e=>notice(e.message,true));actions.append(http);const calls=node('button','AI 与语音额度');calls.onclick=()=>showCalls(t).catch(e=>notice(e.message,true));actions.append(calls);const config=node('button','配置检查');config.onclick=()=>configurationUI.open(t);actions.append(config);
 $('#tenant-actions').replaceChildren(...actions.children);$('#tenant-entry').replaceChildren(node('p','正在读取公司入口…'));$('#tenant-dialog').showModal();
 const request=++entryRequest;api('/tenants/'+t.id+'/entry').then(data=>{if(request!==entryRequest||!$('#tenant-dialog').open)return;renderEntry(data);}).catch(e=>{if(request===entryRequest&&$('#tenant-dialog').open)$('#tenant-entry').replaceChildren(node('p',e.message));});
}
function renderEntry(data){
 const body=$('#tenant-entry');body.replaceChildren();
 const block=node('div','','entry-block'),line=node('div','','entry-link');
 const path='/admin?company='+encodeURIComponent(data.company_code),url=new URL(path,window.location.origin).href;
 const link=node('a','打开公司后台 →');link.href=path;link.target='_blank';link.rel='noopener';
 const copy=node('button','复制公司链接');copy.onclick=async()=>{try{await navigator.clipboard.writeText(url);copy.textContent='已复制';}catch(_){notice('自动复制不可用，请复制下方地址。');}};
 line.append(link,copy);block.append(line,node('p',url));
 if(data.web_qr_svg){const img=node('img');img.className='qr-image';img.alt='公司后台网页二维码';img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(data.web_qr_svg);block.append(img,node('small','扫码进入公司后台网页'));}
 else block.append(node('small','网页二维码待配置公开访问地址；上方本域链接可直接使用。'));
 body.append(block);
 const mini=node('div','','entry-block');mini.append(node('h3','小程序入口'),node('p','公司专属入口可自动带入公司。微信官方小程序码需要配置对应小程序的生成通道。'),node('small','页面：'+(data.mini_program_path||'pages/login/index')),node('small','参数：company='+data.company_code));
 if(data.mini_program_scene)mini.append(node('small','场景参数：'+data.mini_program_scene));
 body.append(mini);
}
function renderTenant(t){
 const row=node('tr'),company=node('td');company.append(node('strong',t.name),node('small',t.company_code));
 const status=node('td');status.append(node('span',labels[t.status],'badge '+t.status));
 const action=node('td'),button=node('button','管理');button.onclick=()=>showTenant(t);action.append(button);
 row.append(company,node('td',purposes[t.purpose]||'—'),status,node('td',tenantReady(t)?'管理员与权限已就绪':!t.readiness?.activation_complete?'等待管理员激活':'待补充配置'),action);return row;
}
function renderTenants(){
 const query=$('#tenant-search').value.trim().toLowerCase(),status=$('#tenant-status').value;
 const filtered=tenants.filter(t=>(!status||t.status===status)&&(!query||(t.name+' '+t.company_code).toLowerCase().includes(query)));
 $('#tenants').replaceChildren(...filtered.map(renderTenant));$('#tenant-count').textContent='显示 '+filtered.length+' / '+tenants.length+' 家';
 if(!filtered.length){const row=node('tr'),cell=node('td','没有匹配的公司。','empty-state');cell.colSpan=5;row.append(cell);$('#tenants').append(row);}
 $('#tenant-stats').replaceChildren(...[['全部公司',tenants.length],['启用',tenants.filter(t=>t.status==='active').length],['准备中',tenants.filter(t=>t.status==='provisioning').length],['暂停 / 关闭',tenants.filter(t=>['suspended','closed'].includes(t.status)).length]].map(([label,count])=>{const tile=node('div','','stat');tile.append(node('small',label),node('strong',String(count)));return tile;}));
}
function navigatePlatform(view){
 for(const key of ['tenants','runtime','support','audit']){$('#view-'+key).hidden=key!==view;$('#nav-'+key).setAttribute('aria-current',key===view?'page':'false');}
}
for(const key of ['tenants','runtime','support','audit'])$('#nav-'+key).onclick=()=>navigatePlatform(key);
$('#tenant-search').oninput=renderTenants;$('#tenant-status').onchange=renderTenants;
$('#close-tenant').onclick=()=>$('#tenant-dialog').close();$('#tenant-dialog').addEventListener('close',()=>{entryRequest++;$('#tenant-entry').replaceChildren();});
function suggestCode(){const bytes=new Uint8Array(5);crypto.getRandomValues(bytes);$('#suggested-code').value='c-'+Array.from(bytes,v=>v.toString(16).padStart(2,'0')).join('');}
$('#regenerate-code').onclick=suggestCode;$('#open-create').onclick=()=>{if(!$('#suggested-code').value)suggestCode();$('#create-dialog').showModal();};$('#close-create').onclick=()=>$('#create-dialog').close();
const auditLabels={'platform.login':'平台登录','platform.logout':'平台退出','platform.password_verified':'平台密码验证','platform.mfa_enroll':'绑定验证器','platform.mfa_login':'验证码登录','platform.mfa_verify':'敏感操作验证','tenant.provision':'开通公司','tenant.transition':'变更公司状态','tenant.activation_reissued':'重新生成激活码','tenant.active':'启用公司','tenant.suspended':'暂停公司','tenant.closed':'关闭公司','tenant.administrator_activated':'激活公司管理员','tenant.call_quota':'调整调用额度','tenant.storage_quota':'调整存储额度','tenant.http_limit':'调整请求频率','tenant.ai_service':'调整模型服务','tenant.account_quota':'调整账号额度'};
function auditLabel(action){return auditLabels[action]||action;}
async function audit(older=false){const g=generation,data=await api('/audit'+(older&&auditCursor?'?before_id='+auditCursor:''));if(g!==generation)return;if(!older)$('#audit').replaceChildren();for(const r of data.items){const row=node('div','','audit-row');row.append(node('strong',auditLabel(r.action)),node('small',new Date(r.created_at).toLocaleString()+' · '+(tenants.find(t=>t.id===r.workspace_id)?.name||r.workspace_id||'平台')+' · '+(r.reason||'—')));$('#audit').append(row);}auditCursor=data.items.at(-1)?.id;$('#more-audit').hidden=data.items.length<100;}
async function refresh(){const g=generation,data=await api('/tenants');if(g!==generation)return;tenants=data.items;renderTenants();await audit();notice('公司目录已更新。');}
async function authenticate(path,body,form){try{const data=await api(path,body);form.reset();if(loggedIn(data.operator))await refresh();}catch(error){if(!error.uncertain)throw error;try{const current=await api('/auth/me');form.reset();if(loggedIn(current.operator)){await refresh();return;}notice('已确认当前验证步骤，请使用验证器的新验证码继续。');}catch(_){throw error;}}}
$('#login-form').onsubmit=e=>{e.preventDefault();submit(e.target,async f=>{await authenticate('/auth/start',Object.fromEntries(f),e.target);});};
$('#otp-form').onsubmit=e=>{e.preventDefault();return submit(e.target,async f=>{await authenticate('/auth/mfa/login',Object.fromEntries(f),e.target);});};
$('#otp-back').onclick=()=>submit(document.body,async()=>{await api('/auth/logout',{});loggedOut();notice('请重新输入平台账号和密码。');});
$('#logout').onclick=()=>submit(document.body,async()=>{await api('/auth/logout',{});loggedOut();notice('已退出平台管理。');});
$('#refresh').onclick=()=>refresh().catch(e=>notice(e.message,true));
$('#create-form').onsubmit=e=>{e.preventDefault();submit(e.target,async f=>{const body=Object.fromEntries(f),serialized=JSON.stringify(body);if(!tenantRequest||tenantRequest.body!==serialized)tenantRequest={body:serialized,key:crypto.randomUUID()};const data=await api('/tenants',body,{'Idempotency-Key':tenantRequest.key});tenantRequest=null;e.target.reset();$('#create-dialog').close();await refresh();if(data.activation_token)showActivation(data);else notice('公司已创建。本次为重复请求；需要时可重新生成激活码。');});};
$('#state-form').onsubmit=e=>{e.preventDefault();const action=pending;if(!action)return;submit(e.target,async f=>{try{const body={version_no:action.tenant.version_no,reason:f.get('reason')};if(action.status!=='activation')body.status=action.status;const data=await api('/tenants/'+action.tenant.id+'/'+(action.status==='activation'?'activation':'state'),body);$('#state-dialog').close();$('#modules-dialog').close();$('#tenant-dialog').close();moduleSelection=null;pending=null;await refresh();if(data.activation_token)showActivation({...data,company_code:action.tenant.company_code});}catch(error){await refresh();throw error;}});};
$('#cancel-state').onclick=()=>$('#state-dialog').close();
$('#clear-activation').onclick=()=>$('#activation-result').close();
$('#activation-result').addEventListener('close',()=>{$('#activation-value').textContent='';$('#activation-page-link').href='/company-activate';});
$('#more-audit').onclick=()=>audit(true).catch(e=>notice(e.message,true));
const supportUI=installSupport({api,node,notice});
const runtimeUI=installRuntimeOverview({api,node});
const configurationUI=installConfigurationStatus({api,node,onSaved:refresh});
api('/auth/me').then(async data=>{if(loggedIn(data.operator))await refresh();}).catch(e=>{loggedOut();notice(e.message===errors.PLATFORM_AUTH_REQUIRED?'请使用独立的平台账号登录。':e.message,e.message!==errors.PLATFORM_AUTH_REQUIRED);});

async function showModules(tenant){
 const request=++moduleRequest,g=generation,data=await api('/tenants/'+tenant.id+'/modules');
 if(g!==generation||request!==moduleRequest)return;
 moduleSelection=data;$('#modules-form').reset();$('#modules-title').textContent=tenant.name+' · 功能模块';
 $('#module-fields').replaceChildren(...data.modules.map(m=>{
  const row=node('div','','module-row'),label=node('label'),check=document.createElement('input');check.type='checkbox';check.name='enabled:'+m.code;check.checked=m.enabled;check.disabled=m.required;label.append(check,node('span',m.label+(m.required?'（基础必需）':'')));
  const expiry=document.createElement('input');expiry.type='datetime-local';expiry.name='expires:'+m.code;expiry.disabled=m.required;expiry.setAttribute('aria-label',m.label+'到期时间');if(m.expires_at){const date=new Date(m.expires_at);expiry.value=new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}row.append(label,expiry);return row;
 }));if(!$('#modules-dialog').open)$('#modules-dialog').showModal();
}
$('#cancel-modules').onclick=()=>{moduleRequest++;$('#modules-dialog').close();moduleSelection=null;};
$('#modules-dialog').oncancel=()=>{moduleRequest++;moduleSelection=null;};
$('#modules-form').onsubmit=e=>{e.preventDefault();const selection=moduleSelection;if(!selection)return;submit(e.target,async f=>{
 const modules=selection.modules.map(m=>({code:m.code,enabled:m.required||f.has('enabled:'+m.code),expires_at:!m.required&&f.get('expires:'+m.code)?new Date(f.get('expires:'+m.code)).toISOString():null}));
 await api('/tenants/'+selection.workspace_id+'/modules',{version_no:selection.version_no,modules,reason:f.get('reason')});$('#modules-dialog').close();moduleSelection=null;await refresh();notice('模块设置已生效。');
});};

function requestVerification(){$('#reauth-form').reset();$('#reauth-dialog').showModal();}
$('#reauth').onclick=requestVerification;
$('#cancel-reauth').onclick=()=>$('#reauth-dialog').close();
$('#reauth-form').onsubmit=e=>{e.preventDefault();submit(e.target,async f=>{await api('/auth/verify',Object.fromEntries(f));e.target.reset();$('#reauth-dialog').close();notice('验证完成，请重新提交刚才的操作。');});};
$('#prepare-mfa').onclick=()=>submit($('#mfa-panel'),async()=>{const data=await api('/auth/mfa/enroll',{});$('#mfa-secret').textContent=data.secret;$('#mfa-qr').hidden=!data.qr_svg;if(data.qr_svg)$('#mfa-qr').src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(data.qr_svg);$('#mfa-enrollment').hidden=false;});
$('#mfa-form').onsubmit=e=>{e.preventDefault();submit(e.target,async f=>{await authenticate('/auth/mfa/confirm',Object.fromEntries(f),e.target);});};

async function showJobs(tenant){
 const request=++jobRequest,g=generation,data=await api('/tenants/'+tenant.id+'/jobs');
 if(g!==generation||request!==jobRequest)return;
 jobSelection=data;$('#jobs-form').reset();$('#jobs-title').textContent=tenant.name+' · 任务运行';
 $('#job-limit').value=data.concurrency_limit;$('#job-counts').textContent='执行中 '+data.running+' · 等待/可重试 '+data.waiting+' · 需人工处理 '+data.dead_letter;
 if(!$('#jobs-dialog').open)$('#jobs-dialog').showModal();
}
$('#cancel-jobs').onclick=()=>{jobRequest++;$('#jobs-dialog').close();jobSelection=null;};
$('#jobs-dialog').oncancel=()=>{jobRequest++;jobSelection=null;};
$('#jobs-form').onsubmit=e=>{e.preventDefault();const selection=jobSelection;if(!selection)return;submit(e.target,async f=>{
 await api('/tenants/'+selection.workspace_id+'/jobs',{version_no:selection.version_no,concurrency_limit:Number(f.get('concurrency_limit')),reason:f.get('reason')});
 $('#jobs-dialog').close();jobSelection=null;await refresh();notice('任务并发设置已保存。正在执行的任务保留，新领取按新上限执行。');
});};


async function showQuota(tenant){
 const request=++quotaRequest,g=generation,data=await api('/tenants/'+tenant.id+'/account-quota');
 if(g!==generation||request!==quotaRequest)return;
 quotaSelection={...data,name:tenant.name};$('#quota-form').reset();$('#quota-title').textContent=tenant.name+' · 账号额度';
 $('#quota-limit').value=data.account_limit===null?'':data.account_limit;
 $('#quota-counts').textContent='启用账号 '+data.active_accounts+' · '+(data.remaining===null?'未设上限':'剩余额度 '+data.remaining)+(data.over_limit?'（当前用量超过新上限）':'');
 if(!$('#quota-dialog').open)$('#quota-dialog').showModal();
}
$('#cancel-quota').onclick=()=>{quotaRequest++;quotaSelection=null;$('#quota-dialog').close();};
$('#quota-dialog').oncancel=()=>{quotaRequest++;quotaSelection=null;};
$('#quota-form').onsubmit=e=>{e.preventDefault();const selection=quotaSelection;if(!selection)return;submit(e.target,async f=>{
 const value=f.get('account_limit').trim(),limit=value===''?null:Number(value);
 if(limit!==null&&(!Number.isSafeInteger(limit)||limit<0))throw Error('请输入非负整数，或留空表示不限。');
 try{
  await api('/tenants/'+selection.workspace_id+'/account-quota',{version_no:selection.version_no,account_limit:limit,reason:f.get('reason')});
 }catch(error){
  if(error.code==='VERSION_CONFLICT')await showQuota({id:selection.workspace_id,name:selection.name});
  throw error;
 }
 $('#quota-dialog').close();quotaSelection=null;await refresh();notice('账号额度已保存，现有成员状态保持不变。');
});};

function storageMiB(bytes){return (bytes/1048576).toLocaleString('zh-CN',{maximumFractionDigits:3})+' MiB';}
async function showStorage(tenant){
 const request=++storageRequest,g=generation,data=await api('/tenants/'+tenant.id+'/storage-quota');
 if(g!==generation||request!==storageRequest)return;
 storageSelection={...data,name:tenant.name};$('#storage-form').reset();$('#storage-title').textContent=tenant.name+' · 原件存储额度';
 $('#storage-limit').value=data.limit_bytes===null?'':data.limit_bytes/1048576;
 $('#storage-counts').textContent='已保存 '+storageMiB(data.stored_bytes)+' · 预占 '+storageMiB(data.reserved_bytes)+' · '+(data.remaining_bytes===null?'未设上限':'剩余 '+storageMiB(data.remaining_bytes));
 $('#storage-pending').textContent=data.pending_count?'预占中 '+data.pending_count+' 项（含正在上传和待对账）；最早开始于 '+new Date(data.oldest_pending_at).toLocaleString()+'。上传异常请由维护人员核对原件，不能按时间自动释放。':'没有未结算的上传。';
 if(!$('#storage-dialog').open)$('#storage-dialog').showModal();
}
$('#cancel-storage').onclick=()=>{storageRequest++;storageSelection=null;$('#storage-dialog').close();};
$('#storage-dialog').oncancel=()=>{storageRequest++;storageSelection=null;};
$('#storage-form').onsubmit=e=>{e.preventDefault();const selection=storageSelection;if(!selection)return;submit(e.target,async f=>{
 const value=f.get('limit_mib').trim(),limit=value===''?null:Number(value)*1048576;
 if(limit!==null&&(!Number.isSafeInteger(limit)||limit<0))throw Error('请输入有效容量，精确到完整字节；留空表示不限。');
 try{await api('/tenants/'+selection.workspace_id+'/storage-quota',{version_no:selection.version_no,limit_bytes:limit,reason:f.get('reason')});}
 catch(error){if(error.code==='VERSION_CONFLICT')await showStorage({id:selection.workspace_id,name:selection.name});throw error;}
 $('#storage-dialog').close();storageSelection=null;await refresh();notice('原件存储额度已保存，已有原件保留。');
});};

async function showHTTPLimits(tenant){
 const request=++httpRequest,g=generation,data=await api('/tenants/'+tenant.id+'/http-limits');
 if(g!==generation||request!==httpRequest)return;
 httpSelection={...data,name:tenant.name};$('#http-form').reset();$('#http-title').textContent=tenant.name+' · 请求频率';
 $('#http-bucket').replaceChildren(...data.items.map(i=>{const o=node('option',i.label);o.value=i.bucket;return o;}));
 $('#http-bucket').value=data.items[0]?.bucket||'';renderHTTPLimit();
 if(!$('#http-dialog').open)$('#http-dialog').showModal();
}
function renderHTTPLimit(){
 const item=httpSelection?.items.find(i=>i.bucket===$('#http-bucket').value);if(!item)return;
 $('#http-rate').value=item.rate_per_minute===null?'':item.rate_per_minute;
 $('#http-burst').value=item.burst===null?'':item.burst;
 $('#http-counts').textContent='限流启用期间累计受理 '+item.accepted+' 次 · 拦截 '+item.rejected+' 次；不限期间不累计。';
}
$('#http-bucket').onchange=renderHTTPLimit;
$('#cancel-http').onclick=()=>{httpRequest++;httpSelection=null;$('#http-dialog').close();};
$('#http-dialog').oncancel=()=>{httpRequest++;httpSelection=null;};
$('#http-form').onsubmit=e=>{e.preventDefault();const selection=httpSelection;if(!selection)return;submit(e.target,async f=>{
 const raw=f.get('rate_per_minute').trim(),other=f.get('burst').trim(),rate=raw===''?null:Number(raw),burst=other===''?null:Number(other);
 if((rate===null)!==(burst===null)||(rate!==null&&(!Number.isSafeInteger(rate)||rate<1||rate>1000000||!Number.isSafeInteger(burst)||burst<1||burst>10000)))throw Error('请同时留空，或填写有效的速率和突发容量。');
 try{await api('/tenants/'+selection.workspace_id+'/http-limits',{version_no:selection.version_no,bucket:f.get('bucket'),rate_per_minute:rate,burst,reason:f.get('reason')});}
 catch(error){if(error.code==='VERSION_CONFLICT')await showHTTPLimits({id:selection.workspace_id,name:selection.name});throw error;}
 $('#http-dialog').close();httpSelection=null;await refresh();notice('请求频率已保存。超过频率的请求会提示稍后重试。');
});};

async function showCalls(tenant){
 const request=++callRequest,g=generation,data=await api('/tenants/'+tenant.id+'/call-quotas');
 if(g!==generation||request!==callRequest)return;
 callSelection={...data,name:tenant.name};$('#calls-form').reset();$('#calls-title').textContent=tenant.name+' · AI 与语音额度';
 $('#calls-resource').replaceChildren(...data.items.map(i=>{const o=node('option',i.label);o.value=i.resource;return o;}));
 $('#calls-period').replaceChildren(...data.periods.map(i=>{const o=node('option',i.label);o.value=i.code;return o;}));
 $('#calls-resource').value=data.items[0]?.resource||'';renderCallQuota();
 if(!$('#calls-dialog').open)$('#calls-dialog').showModal();
}
function renderCallQuota(){
 const item=callSelection?.items.find(i=>i.resource===$('#calls-resource').value);if(!item)return;
 $('#calls-limit').value=item.call_limit===null?'':item.call_limit;$('#calls-period').value=item.period;
 $('#calls-counts').textContent='当前周期已用 '+item.used+' · 预占 '+item.reserved+' · '+(item.remaining===null?'未设上限':'剩余 '+item.remaining)+'；全部周期未结算请求 '+item.pending_attempts+'。';
}
$('#calls-resource').onchange=renderCallQuota;
$('#cancel-calls').onclick=()=>{callRequest++;callSelection=null;$('#calls-dialog').close();};
$('#calls-dialog').oncancel=()=>{callRequest++;callSelection=null;};
$('#calls-form').onsubmit=e=>{e.preventDefault();const selection=callSelection;if(!selection)return;submit(e.target,async f=>{
 const value=f.get('call_limit').trim(),limit=value===''?null:Number(value);
 if(limit!==null&&(!Number.isSafeInteger(limit)||limit<0))throw Error('请输入非负整数，或留空表示不限。');
 try{await api('/tenants/'+selection.workspace_id+'/call-quotas',{version_no:selection.version_no,resource:f.get('resource'),call_limit:limit,period:f.get('period'),reason:f.get('reason')});}
 catch(error){if(error.code==='VERSION_CONFLICT')await showCalls({id:selection.workspace_id,name:selection.name});throw error;}
 $('#calls-dialog').close();callSelection=null;await refresh();notice('调用额度已保存，历史用量和待对账记录保留。');
});};
