import React,{forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {Alert,Button,Empty,Spin} from 'antd';
import {identity} from '../pc/data.js';
import {useAsync} from '../pc/ui.jsx';
import SchemeEditor from './SchemeEditor.jsx';
import {allSchemes,schemeEditor,schemeConfig,STYLE_CHOICES,SOURCE_LABELS} from './presentation.js';
import {clone,stable} from './state.js';
import {useLeaveGuard} from './useLeaveGuard.js';
import {workReportApi} from './api.js';

// The parent uses ref.leave before switching report tabs. Route changes use the
// existing Page exit hook. Personal references never enter browser persistence.
const ReportSchemeState=forwardRef(function ReportSchemeState({page,options,owner,onUse,service=workReportApi},ref){
 const [selected,setSelected]=useState(null),[editor,setEditor]=useState(null),[saved,setSaved]=useState(null);
 const [saving,setSaving]=useState(false),[error,setError]=useState(null),[reading,setReading]=useState(false);
 const sequence=useRef(0),active=useRef(false),writeBusy=useRef(false);
 useEffect(()=>{active.current=true;return()=>{active.current=false;sequence.current++;};},[]);
 const valid=()=>active.current && owner===identity();
 const list=useAsync(signal=>allSchemes(service,signal),[owner,service]);
 const dirty=Boolean(editor && stable(editor)!==stable(saved));
 const leave=useLeaveGuard({page,dirty,saving,owner});useImperativeHandle(ref,()=>({leave}),[leave]);
 const show=(view)=>{setSelected(view);const model=schemeEditor(view);setEditor(model);setSaved(clone(model));setError(null);};
 const open=id=>leave(async()=>{
  const request=++sequence.current;setReading(true);setError(null);setSelected(null);setEditor(null);setSaved(null);
  try{const view=await service.scheme(id);if(valid() && request===sequence.current)show(view);}
  catch(failure){if(valid() && request===sequence.current)setError(failure.message || '方案暂时无法读取');}
  finally{if(valid() && request===sequence.current)setReading(false);}
 });
 const create=(general=false)=>leave(()=>{
  if(!options.can_create || !valid())return;
  const config=general?options.general_scheme:options.default_scheme;if(!config)return;
  sequence.current++;setReading(false);
  show({name:general?'我的通用报告方案':'我的报告方案',config,visibility:'private',can_edit:true});
 });
 const copy=()=>{
  if(!options.can_create || !selected || !editor || saving || !valid())return;
  sequence.current++;setSelected({name:`${editor.name}（副本）`,visibility:'private',can_edit:true});
  setEditor({...clone(editor),name:`${editor.name.slice(0,90)}（副本）`});setSaved(null);setError(null);
 };
 const save=async()=>{
  if(!valid() || writeBusy.current || !editor || !selected?.can_edit || !options.can_create)return;
  let config;try{config=schemeConfig(editor);}catch(failure){setError(failure.message);return;}
  const request=++sequence.current;writeBusy.current=true;setSaving(true);setError(null);
  const payload={name:editor.name.trim(),config,visibility:selected.visibility,team_id:selected.team_id || null};
  try{
   const view=selected.id?await service.updateScheme(selected.id,{...payload,version_no:selected.version_no}):await service.createScheme(payload);
   if(valid() && request===sequence.current){show(view);list.reload();}
  }catch(failure){if(valid() && request===sequence.current){
   if([401,403,404].includes(failure.statusCode || failure.status)){setSelected(null);setEditor(null);setSaved(null);list.reload();}
   setError(failure.message || '保存失败，请重试');
  }}
  finally{writeBusy.current=false;if(valid() && request===sequence.current)setSaving(false);}
 };
 if(owner!==identity())return <Alert type="warning" title="身份或权限已变化，请重新打开报告方案"/>;
 const choices={...STYLE_CHOICES,sources:options.sources.filter(source=>editor?.baseConfig.facts_contract || source.id!=='actual' || editor?.sources.includes(source.id)).map(source=>({value:source.id,label:source.label || SOURCE_LABELS[source.id],disabled:!source.available && !editor?.sources.includes(source.id)}))};
 return <div className="wr-scheme-layout">
  <aside className="wr-scheme-list" aria-label="报告方案列表">
   <Button type="primary" onClick={()=>create()} disabled={!options.can_create || saving}>新建个人方案</Button>
   {options.general_scheme && <Button onClick={()=>create(true)} disabled={!options.can_create || saving}>新建通用方案</Button>}
   {list.loading?<Spin/>:list.error?<Alert type="error" title={list.error} action={<Button onClick={list.reload}>重试</Button>}/>:!list.value?.length?<Empty description="还没有报告方案"/>:list.value.map(view=><button key={view.id} type="button" className={`wr-scheme-item${selected?.id===view.id?' is-active':''}`} onClick={()=>open(view.id)} disabled={saving}>
    {view.name}<small>{{private:'个人方案',team:'部门方案',company:'公司方案'}[view.visibility]}</small>
   </button>)}
  </aside>
  <div>
   {reading?<Spin/>:editor?<>
    <SchemeEditor value={editor} onChange={setEditor} choices={choices} busy={saving} readOnly={!selected.can_edit || !options.can_create} error={error} onSave={save} onCopy={options.can_create?copy:undefined}/>
    {selected.id && onUse && <div className="wr-form-actions"><Button disabled={saving} onClick={()=>leave(()=>onUse(selected))}>使用此方案生成报告</Button></div>}
   </>:error?<Alert type="error" title={error}/>:<div className="wr-panel"><h2>为报告设置写法</h2><p className="wr-muted">选择已有方案查看配置，或创建自己的数据来源、章节和参考范文。</p></div>}
  </div>
 </div>;
});
export default forwardRef(function ReportSchemes(props,ref){return <ReportSchemeState key={props.owner} {...props} ref={ref}/>;});
