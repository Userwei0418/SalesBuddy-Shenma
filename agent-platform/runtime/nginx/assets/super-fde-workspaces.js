(() => {
  const endpoint = '/console/api/super-fde/workspace-creation';
  const favicon = '/fde-tab.png?v=raccoon-source-20260914';
  function updateSignInBrand() {
    const signin=location.pathname.startsWith('/signin');
    document.documentElement.classList.toggle('fde-account',location.pathname.startsWith('/account'));
    document.documentElement.classList.toggle('fde-signin',signin);
    if(!signin)return;
    const logo=document.querySelector('img[alt="Dify"],img[data-fde-signin-logo]');
    if(logo){
      logo.dataset.fdeSigninLogo='true';
      if(logo.getAttribute('src')!=='/sensetime-logo.png?v=1')logo.setAttribute('src','/sensetime-logo.png?v=1');
      if(logo.alt!=='商汤 SenseTime')logo.alt='商汤 SenseTime';
      logo.parentElement.classList.add('fde-signin-header');
    }
    document.querySelectorAll('h1').forEach(heading=>{
      if(/Dify|Super FDE Team|Supreme FDE/.test(heading.textContent))heading.textContent=heading.textContent.replace(/Super FDE Team|Supreme FDE|Dify/g,'商汤销售小浣熊');
      heading.dataset.fdeBrandReady='true';
    });
  }
  function updateIcon() {
    updateSignInBrand();
    if (/Dify|Super FDE Team|Supreme FDE/.test(document.title)) document.title=document.title.replace(/Super FDE Team|Supreme FDE|Dify/g,'商汤销售小浣熊');
    document.querySelectorAll('link[rel="icon"],link[rel="shortcut icon"]').forEach(link => {
      if (link.getAttribute('href') !== favicon) { link.href = favicon; link.type = 'image/png'; link.removeAttribute('sizes'); }
    });
  }
  updateIcon();
  let allowed = false;
  const style = document.createElement('style');
  style.textContent = `#fde-create-workspace{display:block;margin:4px 16px 8px;padding:8px 12px;border-radius:8px;font-size:13px;text-align:left;color:var(--color-text-secondary,#667085)}#fde-create-workspace:hover{background:var(--color-background-section,#eee)}#fde-workspace-dialog{background:var(--color-background-default,#fff);color:var(--color-text-primary,#101828);border:1px solid #8884;border-radius:16px;padding:24px;width:420px;max-width:90vw;box-shadow:0 16px 60px #0005}#fde-workspace-dialog::backdrop{background:#0006}#fde-workspace-dialog h2{font-size:20px;font-weight:600;margin-bottom:12px}#fde-workspace-dialog p{font-size:13px;opacity:.7;margin-bottom:18px}#fde-workspace-dialog input{display:block;width:100%;padding:10px;border:1px solid #8886;border-radius:8px;margin:8px 0 16px;background:transparent}#fde-workspace-dialog footer{display:flex;justify-content:flex-end;gap:12px}#fde-workspace-dialog button{padding:8px 16px;border:1px solid #8885;border-radius:8px}#fde-workspace-dialog button[type=submit]{background:#d9252a;color:white}#fde-workspace-dialog button:disabled{opacity:.5}#fde-workspace-error{color:#d92d20;min-height:20px;font-size:13px}`;
  document.head.append(style);
  function attach() {
    updateIcon();
    if (!allowed || document.getElementById('fde-create-workspace')) return;
    const trigger = document.querySelector('aside button[aria-label="打开工作空间菜单"],aside button[aria-label="Open workspace menu"]');
    if (!trigger) return;
    const button = document.createElement('button');
    button.id='fde-create-workspace';button.textContent='＋ 创建工作空间';button.type='button';button.onclick=openDialog;
    trigger.parentElement.insertAdjacentElement('afterend', button);
  }
  async function openDialog() {
    let dialog = document.getElementById('fde-workspace-dialog');
    if (!dialog) {
      dialog = document.createElement('dialog'); dialog.id='fde-workspace-dialog';
      dialog.innerHTML='<form><h2>创建工作空间</h2><p>你将成为新空间的所有者。成员、应用、知识库和模型配置独立管理。</p><label>工作空间名称<input name="name" required maxlength="80" autocomplete="off" placeholder="例如：销售管理"></label><div id="fde-workspace-error" role="alert"></div><footer><button type="button">取消</button><button type="submit">创建并进入</button></footer></form>';
      document.body.append(dialog);
      dialog.querySelector('button[type=button]').onclick=()=>dialog.close();
      dialog.querySelector('form').onsubmit=async event=>{
        event.preventDefault(); const submit=dialog.querySelector('button[type=submit]'); const error=dialog.querySelector('[role=alert]');
        const name=dialog.querySelector('input').value.trim(); if(!name)return;
        submit.disabled=true;error.textContent='';
        const cookies=Object.fromEntries(document.cookie.split('; ').map(v=>{const i=v.indexOf('=');return [v.slice(0,i),v.slice(i+1)];}));
        try {
          const response=await fetch(endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(cookies['__Host-csrf_token']||cookies.csrf_token||'')},body:JSON.stringify({name})});
          if(!response.ok)throw new Error(response.status===403?'当前账号没有创建权限。':response.status===401?'登录已过期，请刷新并重新登录。':response.status===409?'正在创建，请稍后重试。':'创建失败，请稍后重试。');
          const workspace=await response.json();
          const switched=await fetch('/console/api/workspaces/switch',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(cookies['__Host-csrf_token']||cookies.csrf_token||'')},body:JSON.stringify({tenant_id:workspace.id})});
          if(!switched.ok)throw new Error('空间已创建，请从工作空间菜单中切换。');
          location.assign('/apps');
        } catch(e){error.textContent=e.message;submit.disabled=false;}
      };
    }
    dialog.showModal();dialog.querySelector('input').focus();
  }
  new MutationObserver(attach).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['href','rel']});
  fetch(endpoint,{credentials:'same-origin',headers:{'X-CSRF-Token':decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('__Host-csrf_token=')||c.startsWith('csrf_token='))?.split('=').slice(1).join('=')||'')}}).then(r=>r.ok?r.json():{}).then(data=>{allowed=data.allowed===true;attach();}).catch(()=>{});
})();
