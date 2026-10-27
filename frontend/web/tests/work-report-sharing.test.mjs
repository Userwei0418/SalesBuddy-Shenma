import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sharingAudience} from '../department-ui/work-report/sharing.js';
test('report sharing choices use their separate authorized audience directory',()=>{
  const targets={allowed_kinds:['members','team','company'],members:[{id:'reader'}],teams:[{id:'allowed-team'}]};
  assert.deepEqual(sharingAudience({kind:'members',member_ids:['reader','reader'],team_id:'ignored'},targets),{kind:'members',member_ids:['reader']});
  assert.deepEqual(sharingAudience({kind:'team',team_id:'allowed-team',member_ids:['ignored']},targets),{kind:'team',team_id:'allowed-team'});
  assert.deepEqual(sharingAudience({kind:'company',member_ids:['ignored']},targets),{kind:'company'});
  for(const value of [{kind:'members',member_ids:[]},{kind:'members',member_ids:['viewable-but-not-shareable']},{kind:'team',team_id:'outside'}])assert.throws(()=>sharingAudience(value,targets));
  assert.throws(()=>sharingAudience({kind:'company'},{...targets,allowed_kinds:['members']}));
});

test('document preview requires explicit current partial-visibility consent and preserves exact server counts',async()=>{
 const {checkedSharePreview,shareConfirmation,shareErrorText,shareSelectionFailure,shareResultText}=await import('../department-ui/work-report/sharing.js');
 const partial={version_no:3,can_share:true,recipient_count:5,readable_count:3,unreadable_count:2,reason:'PARTIAL_SOURCE_ACCESS',preview_hash:'a'.repeat(64),confirmation_required:true};
 assert.equal(checkedSharePreview(partial,3),partial);assert.equal(shareConfirmation(partial,''),null);
 assert.deepEqual(shareConfirmation(partial,partial.preview_hash),{visibility_confirmation:partial.preview_hash});
 assert.equal(shareConfirmation({...partial,preview_hash:'b'.repeat(64)},partial.preview_hash),null);
 assert.equal(shareConfirmation({...partial,can_share:false,readable_count:0,unreadable_count:5},partial.preview_hash),null);
 assert.deepEqual(shareConfirmation({...partial,unreadable_count:0,recipient_count:3,confirmation_required:false},''),{visibility_confirmation:partial.preview_hash});
 for(const broken of [{...partial,version_no:2},{...partial,recipient_count:6},{...partial,confirmation_required:false},{...partial,preview_hash:null}])assert.throws(()=>checkedSharePreview(broken,3));
 const failure={code:'HTTP_409',serverCode:'WORK_REPORT_SHARE_CONFIRMATION_REQUIRED',message:'generic'};
 assert.equal(shareSelectionFailure(failure),true);assert.match(shareErrorText(failure),/重新核对人数并确认/);
 assert.match(shareResultText(partial),/可查看 3 人，不可查看 2 人/);
});

test('real sharing dialog drops late selection results, resets consent on refresh and never auto-shares',async()=>{
 const [{default:fs},{default:vm},{default:React},{transformSync},{fileURLToPath},sharing,{stable}]=await Promise.all([import('node:fs'),import('node:vm'),import('react'),import('esbuild'),import('node:url'),import('../department-ui/work-report/sharing.js'),import('../department-ui/work-report/state.js')]);
 const root=fileURLToPath(new URL('../department-ui/',import.meta.url)),cells=[];let cursor=0,dirty=false,effects=[],tree,denied=0,saves=[];
 const changed=(a,b)=>!a || !b || a.length!==b.length || a.some((x,i)=>!Object.is(x,b[i]));
 const hooks={...React,
  useState(value){const i=cursor++;cells[i] ||= {value:typeof value==='function'?value():value};return [cells[i].value,next=>{cells[i].value=typeof next==='function'?next(cells[i].value):next;dirty=true;}];},
  useRef(value){const i=cursor++;return cells[i] ||= {current:value};},
  useMemo(fn,deps){const i=cursor++;if(changed(cells[i]?.deps,deps))cells[i]={deps,value:fn()};return cells[i].value;},
  useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
  useEffect(fn,deps){const i=cursor++,old=cells[i];if(changed(old?.deps,deps)){cells[i]={deps,cleanup:old?.cleanup};effects.push(()=>{cells[i].cleanup?.();cells[i].cleanup=fn();});}},
 };
 const load=(file,extra={})=>{const module={exports:{}};vm.runInNewContext(transformSync(fs.readFileSync(root+file,'utf8'),{loader:'jsx',format:'cjs'}).code,{module,exports:module.exports,AbortController,console,require:name=>name==='react'?hooks:name==='antd'?{Button:'button',Checkbox:'checkbox',Select:'select'}:name.endsWith('data.js')?{identity:()=> 'owner'}:extra[name] || {}});return module.exports;};
 const {useAsync}=load('pc/ui.jsx');
 const {DocumentShare}=load('work-report/ReportDocumentDialogs.jsx',{'../pc/ui.jsx':{useAsync},'./sharing.js':sharing,'./state.js':{stable},'./composition.js':{accessLost:error=>error.statusCode===403},'./ReportDialogs.jsx':{ShareDialog:'dialog'}});
 const targets={allowed_kinds:['company','team'],members:[],teams:[{id:'team',name:'合成团队'}]},pending=[];
 const service={options:async()=>({can_share:true,share_targets:targets}),sharePreview:(id,body,{signal})=>new Promise((resolve,reject)=>pending.push({body,signal,resolve,reject}))};
 const props={document:{id:'doc',version_no:3,audience:{kind:'company'}},owner:'owner',service,onSave:(...args)=>saves.push(args),onAccessLost:()=>denied++};
 const render=()=>{cursor=0;dirty=false;tree=DocumentShare(props);const tasks=effects;effects=[];tasks.forEach(fn=>fn());};
 const flush=async()=>{for(let n=0;n<30;n++){await new Promise(resolve=>setImmediate(resolve));if(!dirty)return;render();}throw Error('hooks did not settle');};
 const nodes=element=>!React.isValidElement(element)?[]:[element,...React.Children.toArray(element.props.children).flatMap(nodes)];
 const control=type=>nodes(tree.props.selector).find(node=>node.type===type);
 const response=(hash='a')=>({version_no:3,can_share:true,recipient_count:5,readable_count:3,unreadable_count:2,reason:'PARTIAL_SOURCE_ACCESS',preview_hash:hash.repeat(64),confirmation_required:true});
 try{
  render();await flush();assert.equal(pending.length,1);assert.equal(tree.props.canSubmit,false);
  control('select').props.onChange('team');render();await flush();assert.equal(pending[0].signal.aborted,true);
  pending[0].reject(Object.assign(Error('stale denied'),{statusCode:403}));await flush();assert.equal(denied,0);
  nodes(tree.props.selector).find(node=>node.props['aria-label']==='共享给部门').props.onChange('team');render();await flush();assert.equal(pending.length,2);
  pending[1].resolve(response());await flush();assert.equal(tree.props.canSubmit,false);assert.equal(control('checkbox').props.checked,false);assert.equal(saves.length,0);
  control('checkbox').props.onChange({target:{checked:true}});render();await flush();assert.equal(tree.props.canSubmit,true);
  tree.props.onSave();assert.equal(saves.length,1);assert.equal(saves[0][1].visibility_confirmation,'a'.repeat(64));assert.equal(saves[0][0].team_id,'team');
  control('button').props.onClick();render();await flush();assert.equal(tree.props.canSubmit,false);assert.equal(pending.length,3);
  pending[2].resolve(response('b'));await flush();assert.equal(control('checkbox').props.checked,false);assert.equal(tree.props.canSubmit,false);
  control('button').props.onClick();render();await flush();pending[3].resolve({...response('c'),can_share:false,readable_count:0,unreadable_count:5,confirmation_required:false,reason:'NO_RECIPIENT'});await flush();
  assert.equal(tree.props.canSubmit,false);assert.equal(saves.length,1);
 }finally{cells.forEach(cell=>cell?.cleanup?.());}
});
