/* Personal presentation preference only; independent of accounts and business drafts. */
(function(global){
  'use strict';
  const key='sales-web:color-theme';
  // Old dark links/preferences now mean navy navigation with a light workspace.
  const normalize=value=>value==='dark'?'navy':value==='navy'||value==='light'?value:null;
  const url=new URL(location.href),requested=normalize(url.searchParams.get('theme'));
  let saved;try{saved=localStorage.getItem(key);}catch(_){/* Storage may be restricted. */}
  let current='navy'; // 规范 V-01：导航固定深蓝，不提供浅色侧栏
  const sun='<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>';
  const moon='<path d="M20.5 14A8.5 8.5 0 0 1 10 3.5 8.5 8.5 0 1 0 20.5 14Z"/>';
  function sync(){
    document.documentElement.dataset.theme=current;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content','light');
    document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
      const label=current==='navy'?'切换到浅色侧栏':'切换到深蓝侧栏';
      button.setAttribute('aria-label',label);button.title=label;
      button.innerHTML='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(current==='navy'?sun:moon)+'</svg><span>'+(current==='navy'?'浅色侧栏':'深蓝侧栏')+'</span>';
    });
    document.querySelectorAll('.web-brand-logo').forEach(img=>{
      const inverted=current==='navy'&&img.closest('.web-sidebar');
      const src='assets/brand/raccoon-salesbuddy-horizontal-'+(inverted?'white':'navy')+'-sidebar.svg';
      if(img.getAttribute('src')!==src)img.setAttribute('src',src);
    });
  }
  function set(value,{persist=true,updateURL=true}={}){
    const next=normalize(value);if(!next)return;
    current=next;sync();
    if(persist)try{localStorage.setItem(key,current);}catch(_){}
    if(updateURL){const url=new URL(location.href);if(url.searchParams.has('theme')){url.searchParams.set('theme',current);history.replaceState(history.state,'',url);}}
    global.dispatchEvent(new CustomEvent('sales-theme-change',{detail:{theme:current}}));
  }
  sync();
  if(requested||saved==='dark')try{localStorage.setItem(key,current);}catch(_){}
  if(url.searchParams.get('theme')==='dark'){url.searchParams.set('theme',current);history.replaceState(history.state,'',url);}
  document.addEventListener('DOMContentLoaded',()=>{
    sync();
  },{once:true});
  global.addEventListener('storage',event=>{if(event.key===key&&normalize(event.newValue))set(event.newValue,{persist:false});});
  global.SalesTheme=Object.freeze({get current(){return current;},set});
})(window);
