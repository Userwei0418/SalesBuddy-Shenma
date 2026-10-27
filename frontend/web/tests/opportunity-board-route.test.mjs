import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const runtime=fs.readFileSync(new URL('../runtime.js',import.meta.url),'utf8');
const board='pages/opportunities/index',editor='pages/workbench/index',home='pages/index/index',login='pages/login/index';
// Execute the actual route/history functions. Rendering and lifecycle effects
// are inert; no server, API, generated bundle or logged-in account is required.
function harness(signedIn=true) {
  const storage=new Map(),mounted=[],writes=[];
  const context=vm.createContext({URL,Map,Set,window:{SALES_MODE:'live'},globalThis:null,
    bundle:{images:{},pages:Object.fromEntries([board,editor,home,login].map(id=>[id,{config:{}}])),config:{pages:[home],tabBar:{list:[{pagePath:home},{pagePath:editor}]}}},
    app:{globalData:{session:signedIn?{}:null}},current:null,stack:[],tabPages:new Map(),pendingDeepLink:'',serial:0,navigationEpoch:'epoch',hiddenTabs:false,titleOverride:'',
    location:{href:'https://web.invalid/web/?mode=live',hash:''},history:{state:null},
    sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    clone:value=>structuredClone(value),instanceDefinition:id=>{mounted.push(id);return {definition:{data:{}}};},
    root:{replaceChildren(){},append(){}},style:{},wx:{},createEventChannel:()=>({}),
    installAccountLogin(){},attachMethods(page){page._components=new Map();},installVisitExit(){},visitDraftPage:()=>false,
    pageCss:()=>'',scrollPageTo(){},lifecycle(){},render(){},requestAnimationFrame(){},scheduleRender(){},pageComponentsLifecycle(){},
    hidePage(){},toast(message){throw Error(message);},CustomEvent:function(type,options){return {type,...options};},
  });
  context.globalThis=context;context.window.dispatchEvent=()=>{};
  context.authenticated=()=>!!context.app.globalData.session;
  context.sessionKey=name=>'test:'+name;
  context.userNavigation=action=>action();
  context.unload=page=>{if(!page)return;page._destroyed=true;if(context.tabPages.get(page.route)===page)context.tabPages.delete(page.route);};
  for(const method of ['pushState','replaceState'])context.history[method]=(state,_,path)=>{
    const url=new URL(path,context.location.href);Object.assign(context.location,{href:url.href,hash:url.hash});context.history.state=state;writes.push(method);
  };
  vm.runInContext(runtime.slice(runtime.indexOf('  function updateHash('),runtime.indexOf('  // Web navigation exits'))+
    runtime.slice(runtime.indexOf('  function back('),runtime.indexOf("  window.addEventListener('hashchange'")),context);
  return {context,storage,mounted,writes};
}

test('old board links and pending login destinations become the editor without losing the selected view',()=>{
  for(const signedIn of [true,false]){
    const h=harness(signedIn),c=h.context;
    c.route('/'+board+'?view=saved%20view&scope=team&member_id=allowed',{history:true});
    if(!signedIn){
      assert.equal(c.current.route,login);assert.equal(c.pendingDeepLink,'/'+editor+'?view=saved%20view&scope=team&member_id=allowed');
      c.app.globalData.session={};c.route('/'+home,{reset:true});
    }
    assert.equal(c.current.route,editor);assert.equal(c.current.options.view,'saved view');
    assert.equal(c.current.options.scope,'team');assert.equal(c.current.options.member_id,'allowed');
    assert.equal(c.location.hash,'#/'+editor+'?view=saved%20view&scope=team&member_id=allowed');
    assert.equal(h.mounted.includes(board),false);assert.equal(h.writes.at(-1),'replaceState');
  }
});

test('browser history and cached return cannot reopen the hidden board or reuse the wrong saved view',()=>{
  const h=harness(),c=h.context;
  c.route('/'+editor+'?view=first',{tab:true});const previous=c.current;
  c.route('/'+board+'?view=first',{tab:true,history:true});
  assert.equal(c.current,previous);assert.equal(h.writes.at(-1),'replaceState');
  c.route('/'+board+'?view=second',{tab:true});
  assert.equal(previous._destroyed,true);assert.equal(c.current.options.view,'second');
  c.location.hash='#/'+board+'?view=history';c.location.href='https://web.invalid/web/?mode=live'+c.location.hash;
  c.hashNavigation();assert.equal(c.current.route,editor);assert.equal(c.current.options.view,'history');
  assert.equal(c.location.hash,'#/'+editor+'?view=history');
  // A retained page from before the feature was hidden must also be replaced.
  c.stack.push({route:board,_url:'/'+board+'?view=return',_destroyed:false});c.route('/'+home);
  c.back();assert.equal(c.current.route,editor);assert.equal(c.current.options.view,'return');
  assert.equal(c.location.hash,'#/'+editor+'?view=return');assert.equal(h.mounted.includes(board),false);
});
