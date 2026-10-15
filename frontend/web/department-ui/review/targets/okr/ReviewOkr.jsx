import React, {forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState} from 'react';
import {Alert, Button, Checkbox, Input, Modal, Pagination, Select, Tag} from 'antd';
import {can, identity, open, session, todayKey} from '../../../pc/data.js';
import {PageHead, Panel} from '../../../pc/ui.jsx';
import {PAGE_SIZE_OPTIONS, useListPageSize} from '../../../pc/list-pagination.js';
import {decimalText, subjectChoices} from '../model.js';
import {PERIODS, SCOPES, SOURCES, canConfirmProgress, editDraft, errorText, keyResultBody, listItems, mentionBody, objectiveBody, parentChoices, periodFor, progressBody, progressDraft, progressText, reasonText, targetChoices} from './model.js';
import {reviewOkrApi} from './api.js';
import './okr.css';

const names = values => values.map(row => ({value: row.id, label: row.name || row.title}));
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '—';
const Field = ({label, children, wide}) => <label className={`review-okr-field${wide ? ' is-wide' : ''}`}><span>{label}</span>{children}</label>;
const newParticipants = () => ({title:'',owner_user_id:'',collaborator_user_ids:[],parent_id:null,status:'active'});

// The default adapter uses authenticated business APIs. Tests may inject a
// client, but production never falls back to sample data for a missing service.
const ReviewOkr = forwardRef(function ReviewOkr(props, ref) {
  const owner = identity();
  if (!can('target.read')) return <Alert type="info" showIcon title="当前账号没有目标查看权限" />;
  return <OkrWorkspace key={owner} ref={ref} owner={owner} {...props} />;
});
export default ReviewOkr;

const OkrWorkspace = forwardRef(function OkrWorkspace({owner, client=reviewOkrApi, onBack, onTargets}, ref) {
  const [objectivePageSize, setObjectivePageSize] = useListPageSize('review-objectives');
  const [keyResultPageSize, setKeyResultPageSize] = useListPageSize('review-key-results');
  const [filter, setFilter] = useState({anchor_date:todayKey(),offset:0});
  const [state, setState] = useState({loading:true,rows:[],total:0,metrics:[],options:null,people:[],error:''});
  const [expanded, setExpanded] = useState(null), [detail, setDetail] = useState({loading:false,rows:[],total:0,offset:0});
  const [editor, setEditor] = useState(null), [mutation, setMutation] = useState({busy:false,error:'',conflict:false});
  const [notice, setNotice] = useState(''), [evidence, setEvidence] = useState(null);
  const mounted = useRef(true), reads = useRef(0), detailReads = useRef(0), edits = useRef(0), busy = useRef(false);
  const current = () => mounted.current && identity() === owner;
  useEffect(() => {mounted.current=true;return()=>{mounted.current=false;reads.current++;detailReads.current++;edits.current++;};}, []);
  const load = useCallback(async () => {
    const sequence = ++reads.current;
    setState(previous => ({...previous,loading:true,rows:[],total:0,error:''}));
    if (!client) {setState(previous=>({...previous,loading:false,error:'O/KR 服务尚未接入'}));return;}
    const [objectives, metrics, options, participants] = await Promise.allSettled([client.objectives({...filter,limit:objectivePageSize}),client.metrics(),client.options(),client.participants()]);
    if (!current() || sequence !== reads.current) return;
    try {
      if (objectives.status !== 'fulfilled') throw objectives.reason;
      const rows = listItems(objectives.value);
      const failures = [metrics,options,participants].filter(result=>result.status==='rejected');
      setState({loading:false,rows,total:objectives.value.total,error:'',
        metrics:metrics.status==='fulfilled' && Array.isArray(metrics.value?.items)?metrics.value.items:[],
        options:options.status==='fulfilled'?options.value:null,
        people:participants.status==='fulfilled' && Array.isArray(participants.value?.items)?participants.value.items:[],
        teams:participants.status==='fulfilled' && Array.isArray(participants.value?.teams)?participants.value.teams:[],
        editError:failures.length ? failures[0].reason.message || '维护目录暂不可用' : ''});
    } catch (error) {setState(previous=>({...previous,loading:false,error:error.message || '读取目标失败'}));}
  }, [filter,objectivePageSize,client,owner]);
  useEffect(()=>{setExpanded(null);setDetail({loading:false,rows:[],total:0,offset:0});detailReads.current++;load();},[load]);
  const loadDetails = async (objective, offset=0, limit=keyResultPageSize) => {
    const sequence=++detailReads.current;setExpanded(objective);setDetail({loading:true,rows:[],total:0,offset,error:''});setEvidence(null);
    try {const result=await client.keyResults({objective_id:objective.id,limit,offset});if(current()&&sequence===detailReads.current){setDetail({loading:false,rows:listItems(result).map(row=>({...row,can_confirm_progress:canConfirmProgress(row,session()?.userId,state.options)})),total:result.total,offset,error:''});setState(previous=>({...previous,rows:previous.rows.map(row=>row.id===objective.id?{...row,key_result_count:result.total}:row)}));}}
    catch(error){if(current()&&sequence===detailReads.current)setDetail({loading:false,rows:[],total:0,offset,error:error.message||'读取 KR 失败'});}
  };
  const leave = apply => {
    if(busy.current){Modal.info({title:'正在保存，请稍候',content:'收到保存结果后再离开。',okText:'知道了'});return false;}
    if(!editor){apply?.();return true;}
    Modal.confirm({title:'还有未保存的内容',content:'离开会放弃当前编辑内容。',okText:'放弃并离开',cancelText:'继续编辑',onOk:()=>{edits.current++;setEditor(null);setMutation({busy:false,error:'',conflict:false});apply?.();}});return false;
  };
  useImperativeHandle(ref,()=>({leave,hasUnsavedChanges:()=>Boolean(editor)}),[editor]);
  const writeGranted=can('target.submit')||can('target.manage');
  const scopes=Object.keys(SCOPES).filter(scope=>subjectChoices(state.options,scope).length);
  const canCreate=writeGranted&&!state.loading&&!state.error&&!state.editError&&scopes.length>0&&state.people.length>0;
  const canEdit=row=>row.editable===true&&writeGranted&&!state.editError&&state.people.length>0;
  const displayPerson=id=>state.people.find(person=>person.id===id)?.name || '当前目录不可见成员';
  const displaySubject=row=>row.scope_type==='person'?state.options?.people?.find(person=>person.id===row.user_ref_id)?.name||displayPerson(row.user_ref_id):row.scope_type==='team'?state.options?.teams?.find(team=>team.id===row.team_id)?.name||state.teams?.find(team=>team.id===row.team_id)?.name||row.team_name||'团队信息暂不可用':state.options?.departments?.find(item=>item.id===row.department_code)?.name||({sales:'销售部门',fde:'FDE 部门'}[row.department_code])||'部门信息暂不可用';
  const loadEditor = async (type, draft, objective) => {
    const sequence=++edits.current;setMutation({busy:false,error:'',conflict:false});setNotice('');
    setEditor({type,draft,objective,loading:true,parents:[],targets:[],recipients:[]});
    try {
      let parents=[],targets=[],recipients=[];
      if(type==='objective') parents=await client.objectiveParents();
      if(type==='keyResult') [parents,targets]=await Promise.all([client.keyResultParents(),client.targetsFor(objective)]);
      if(type==='mention') recipients=await client.recipients(objective.entityType,draft.id);
      if(current()&&sequence===edits.current)setEditor(previous=>({...previous,loading:false,parents,targets,recipients}));
    }catch(error){if(current()&&sequence===edits.current)setEditor(previous=>({...previous,loading:false,loadError:error.message||'可选项读取失败'}));}
  };
  const addObjective=()=>{if(!canCreate)return;leave(()=>loadEditor('objective',{...newParticipants(),period_type:'quarter',anchor_date:filter.anchor_date,scope_type:scopes.includes('team')?'team':scopes[0],subjectId:''}));};
  const addKr=objective=>leave(()=>loadEditor('keyResult',{...newParticipants(),owner_user_id:objective.owner_user_id,collaborator_user_ids:[...(objective.collaborator_user_ids||[])],metric_code:'',target_id:null},objective));
  const update=patch=>{setEditor(previous=>({...previous,draft:{...previous.draft,...patch}}));setMutation(previous=>({...previous,error:''}));};
  const cancel=()=>{if(busy.current)return;edits.current++;setEditor(null);setMutation({busy:false,error:'',conflict:false});};
  const submit=async()=>{
    if(!editor||busy.current||editor.loading||editor.loadError||mutation.conflict||!current())return;
    let action;
    try {
      const {type,draft,objective,parents,targets,recipients}=editor;
      if(type==='objective'){const body=objectiveBody(draft,{people:state.people,options:state.options,parents});action=()=>client.saveObjective(draft.id,body);}
      else if(type==='keyResult'){const body=keyResultBody(draft,{people:state.people,objective,parents,targets,metrics:state.metrics});action=()=>client.saveKeyResult(draft.id,body);}
      else if(type==='progress'){const body=progressBody(draft,objective);action=()=>client.confirmProgress(objective.id,body);}
      else if(type==='mention'){const body=mentionBody(draft,objective.row,recipients);action=()=>client.mention(objective.entityType,objective.row.id,body);}
      else return;
    }catch(error){setMutation({busy:false,error:error.message,conflict:false});return;}
    busy.current=true;setMutation({busy:true,error:'',conflict:false});
    try {
      await action();if(!current())return;
      const context=editor;setEditor(null);setMutation({busy:false,error:'',conflict:false});
      setNotice(context.type==='mention'?'提醒已提交；接收人的原有查看权限保持不变。':context.type==='progress'?'进展及依据已确认。':'已保存。');
      if(context.type==='keyResult'||context.type==='progress'){await loadDetails(context.type==='keyResult'?context.objective:expanded,detail.offset);}
      else if(context.type==='objective'){if(!context.draft.id&&context.draft.anchor_date!==filter.anchor_date)setFilter(previous=>({...previous,anchor_date:context.draft.anchor_date,offset:0}));else await load();if(expanded?.id===context.draft.id){setExpanded(null);setDetail({loading:false,rows:[],total:0,offset:0});}}
    }catch(error){if(current())setMutation({busy:false,error:editor.type==='mention'&&error.statusCode===403?'当前账号或所选接收人不满足提醒权限，未发送。请核对后重试。':errorText(error),conflict:error.statusCode===409});}
    finally{busy.current=false;}
  };
  const openMention=(entityType,row)=>leave(()=>loadEditor('mention',{id:row.id,recipient_user_ids:[]},{entityType,row}));
  const openProgress=row=>leave(()=>loadEditor('progress',progressDraft(row),row));
  const renderEditor=()=>editor&&<div className="review-okr-editor" data-testid="okr-editor">
    <div className="review-okr-editor-heading"><b>{editor.type==='objective'?editor.draft.id?'编辑目标 O':'新建目标 O':editor.type==='keyResult'?editor.draft.id?'编辑关键结果 KR':'新建关键结果 KR':editor.type==='progress'?'确认手工进展':'发送协作提醒'}</b><span className="pc-muted">{editor.type==='keyResult'?editor.objective.title:''}</span></div>
    {editor.loading?<p>正在读取可选项…</p>:editor.loadError?<Alert type="error" title={editor.loadError}/>:<>
      {['objective','keyResult'].includes(editor.type)?<EntityForm editor={editor} people={state.people} metrics={state.metrics} options={state.options} busy={mutation.busy} update={update}/>:editor.type==='progress'?<div className="review-okr-form">
        <Field label={`当前累计进展（${editor.objective.metric?.unit||''}）`}><Input aria-label="当前累计进展" inputMode="decimal" value={editor.draft.value} disabled={mutation.busy} onChange={event=>update({value:event.target.value})}/></Field>
        <Field label="进展依据" wide><Input.TextArea aria-label="进展依据" autoSize={{minRows:2,maxRows:6}} maxLength={2000} value={editor.draft.evidence} disabled={mutation.busy} onChange={event=>update({evidence:event.target.value})}/></Field>
        <div className="review-okr-confirm"><Checkbox checked={editor.draft.confirmed} disabled={mutation.busy} onChange={event=>update({confirmed:event.target.checked})}>我已核实此累计进展及依据</Checkbox></div>
      </div>:<><Field label="提醒接收人"><Select aria-label="提醒接收人" mode="multiple" showSearch optionFilterProp="label" value={editor.draft.recipient_user_ids} options={names(editor.recipients)} placeholder="从当前成员目录选择" disabled={mutation.busy} onChange={recipient_user_ids=>update({recipient_user_ids})}/></Field><p className="pc-muted">发送时会校验接收人的查看权限；提醒不会授予权限或更改负责人。</p></>}
    </>}
    {mutation.error&&<Alert type="error" showIcon title={mutation.error} action={mutation.conflict&&<Button onClick={async()=>{if(editor.type==='objective')await load();else if(expanded)await loadDetails(expanded,detail.offset);}}>刷新核对</Button>}/>}
    <div className="review-okr-editor-actions"><Button aria-label="取消编辑" disabled={mutation.busy} onClick={cancel}>取消</Button><Button aria-label="保存 O/KR" type="primary" loading={mutation.busy} disabled={editor.loading||!!editor.loadError||mutation.conflict} onClick={submit}>{editor.type==='mention'?'发送提醒':editor.type==='progress'?'确认进展':'保存'}</Button></div>
  </div>;
  const renderKr=objective=><div className="review-okr-results">
    <div className="review-okr-section-head"><b>关键结果 <span className="pc-muted">{detail.total}</span></b>{canEdit(objective)&&objective.status==='active'&&<Button onClick={()=>addKr(objective)}>新增 KR</Button>}</div>
    {editor&&((editor.type==='keyResult'&&editor.objective.id===objective.id)||(editor.type==='progress'&&editor.objective.objective_id===objective.id)||(editor.type==='mention'&&(editor.objective.entityType==='objective'?editor.objective.row.id:editor.objective.row.objective_id)===objective.id))&&renderEditor()}
    {detail.error?<Alert type="error" title={detail.error} action={<Button onClick={()=>loadDetails(objective,detail.offset)}>重试</Button>}/>:detail.loading?<p>正在读取关键结果…</p>:!detail.rows.length?<p className="review-okr-empty">还没有关键结果</p>:<div className="review-okr-table-wrap"><table className="review-okr-table"><thead><tr><th>关键结果 / 指标</th><th>负责人</th><th>生效目标</th><th>当前进展</th><th>状态 / 来源</th><th>操作</th></tr></thead><tbody>{detail.rows.map(row=>{
      const progress=progressText(row);
      return <React.Fragment key={row.id}><tr data-testid="okr-kr-row"><td><b>{row.title}</b><small>{row.metric?.name||row.metric_code} · {row.metric?.unit} {row.parent_id?' · 已对齐上级 KR':''}</small></td><td>{row.owner_name||displayPerson(row.owner_user_id)}<small>{row.collaborator_user_ids?.length?`${row.collaborator_user_ids.length} 位协作人`:''}</small></td>
        <td className="review-okr-number">{row.effective_target?decimalText(row.effective_target.value,row.metric?.decimal_places):'未关联目标'}</td><td className="review-okr-number" data-testid="okr-progress-value">{progress.value}<small>{progress.unit}</small></td><td><Tag color={progress.pending?'gold':'blue'}>{row.status==='archived'?'已归档':progress.label}</Tag><small>{SOURCES[row.metric?.source_kind]||'暂不可用'}</small></td>
        <td><div className="review-okr-row-actions"><Button size="small" type="link" onClick={()=>setEvidence(evidence===row.id?null:row.id)}>依据</Button>{canEdit(row)&&objective.status==='active'&&<><Button size="small" type="link" onClick={()=>leave(()=>loadEditor('keyResult',editDraft(row),objective))}>编辑 KR</Button><Button size="small" type="link" onClick={()=>openMention('key_result',row)}>@ 提醒</Button></>}{row.can_confirm_progress&&row.progress_mode==='manual'&&<Button size="small" type="link" onClick={()=>openProgress(row)}>更新进展</Button>}</div></td></tr>
        {evidence===row.id&&<tr><td colSpan={6}><Evidence row={row} displayPerson={displayPerson} leave={leave}/></td></tr>}</React.Fragment>;
    })}</tbody></table></div>}
    <div data-testid="okr-kr-pagination"><Pagination current={Math.floor(detail.offset/keyResultPageSize)+1} total={detail.total} pageSize={keyResultPageSize} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS} disabled={!!editor||detail.loading} showTotal={total=>`共 ${total} 项关键结果`} onChange={(page,size)=>{setKeyResultPageSize(size);loadDetails(objective,size===keyResultPageSize?(page-1)*size:0,size);}}/></div>
  </div>;
  return <div className="review-okr" data-testid="review-okr"><PageHead title="目标与关键结果" extra={<div className="review-okr-row-actions">{onTargets&&<Button onClick={onTargets}>目标值管理</Button>}{onBack&&<Button onClick={onBack}>返回本期概览</Button>}</div>}/>
    <Panel><div className="review-okr-toolbar"><Field label="查看日期"><Input aria-label="O/KR 查看日期" type="date" value={filter.anchor_date} disabled={!!editor} onChange={event=>{if(event.target.value)setFilter(previous=>({...previous,anchor_date:event.target.value,offset:0}));}}/></Field><span className="pc-muted">包含所选日期的目标</span><div className="review-okr-toolbar-actions"><Button aria-label="刷新 O/KR" disabled={!!editor||mutation.busy||state.loading} onClick={async()=>{await load();setExpanded(null);detailReads.current++;}}>刷新</Button><Button type="primary" disabled={!canCreate||!!editor} onClick={addObjective}>新建目标 O</Button></div></div>
    {notice&&<Alert type="success" showIcon title={notice} closable onClose={()=>setNotice('')}/>}{state.editError&&<Alert type="warning" title={`${state.editError}，暂不能维护负责人或协作人。`}/>}{state.error&&<Alert type="error" title={state.error} action={<Button onClick={load}>重试</Button>}/>}
    {editor?.type==='objective'&&renderEditor()}
    {!state.rows.length?<div className="review-okr-empty">{state.loading?'正在读取目标…':state.error?'目标暂未读取':'当前日期还没有目标'}</div>:<div className="review-okr-objectives">{state.rows.map(row=><section className="review-okr-objective" key={row.id} data-testid="okr-objective">
      <div className="review-okr-objective-head"><button className="review-okr-expand" aria-expanded={expanded?.id===row.id} aria-label={`展开目标：${row.title}`} onClick={()=>leave(()=>expanded?.id===row.id?(detailReads.current++,setExpanded(null)):loadDetails(row))}>{expanded?.id===row.id?'−':'+'}</button>
        <div className="review-okr-objective-title"><h3><Tag color="blue">O</Tag>{row.title}{row.status==='archived'&&<Tag>已归档</Tag>}</h3><span className="pc-muted">{PERIODS[row.period_type]} · {row.period_start} 至 {row.period_end} · {SCOPES[row.scope_type]} / {row.subject_name||displaySubject(row)}</span></div>
        <div className="review-okr-objective-people"><span>负责人 · {row.owner_name||displayPerson(row.owner_user_id)}</span>{row.collaborator_user_ids?.length>0&&<small>协作 · {row.collaborator_user_ids.map(displayPerson).join('、')}</small>}{row.parent_id&&<small>已对齐上级目标</small>}</div>
        <div className="review-okr-row-actions"><Tag>{row.key_result_count||0} 项 KR</Tag>{canEdit(row)&&<><Button type="link" onClick={()=>leave(()=>loadEditor('objective',editDraft(row)))}>编辑 O</Button><Button type="link" onClick={()=>openMention('objective',row)}>@ 提醒</Button></>}</div>
      </div>{expanded?.id===row.id&&renderKr(row)}{editor?.type==='mention'&&editor.objective.entityType==='objective'&&editor.objective.row.id===row.id&&expanded?.id!==row.id&&renderEditor()}
    </section>)}</div>}
    <div className="review-okr-footer" data-testid="okr-objective-pagination"><Pagination current={Math.floor(filter.offset/objectivePageSize)+1} total={state.total} pageSize={objectivePageSize} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS} disabled={!!editor||state.loading} showTotal={total=>`共 ${total} 个目标`} onChange={(page,size)=>{setObjectivePageSize(size);setFilter(previous=>({...previous,offset:size===objectivePageSize?(page-1)*size:0}));}}/></div></Panel>
  </div>;
});

function EntityForm({editor,people,metrics,options,busy,update}) {
  const {draft,type,objective,parents,targets}=editor, isO=type==='objective', editing=!!draft.id;
  let period;try{period=isO?editing?{start:draft.period_start,end:draft.period_end}:periodFor(draft.period_type,draft.anchor_date):{start:objective.period_start,end:objective.period_end};}catch{period={};}
  const parentOptions=parentChoices(parents,draft.id,period),metric=metrics.find(row=>row.code===draft.metric_code),allowedTargets=!isO?targetChoices(targets,objective,metric):[];
  const participantOptions=[...people], knownPeople=new Set(people.map(person=>person.id));
  for(const id of [draft._originalOwnerId,...(draft._originalCollaboratorIds||[])].filter(Boolean))if(!knownPeople.has(id)){participantOptions.push({id,name:'成员信息暂不可用'});knownPeople.add(id);}
  if(draft.parent_id&&draft.parent_id===draft._originalParentId&&!parentOptions.some(row=>row.id===draft.parent_id))parentOptions.push({id:draft.parent_id,title:'原上级（当前不可选，保留原关联）'});
  if(editing&&draft.target_id&&!allowedTargets.some(row=>row.id===draft.target_id))allowedTargets.push({id:draft.target_id,effective_amount:draft.effective_target?.value??null});
  return <div className="review-okr-form">
    <Field label={isO?'目标标题':'关键结果标题'} wide><Input aria-label={isO?'目标标题':'关键结果标题'} value={draft.title} maxLength={200} disabled={busy} onChange={event=>update({title:event.target.value})}/></Field>
    {isO&&!editing&&<><Field label="目标周期"><div className="review-okr-period"><Select aria-label="O 目标周期" value={draft.period_type} options={Object.entries(PERIODS).map(([value,label])=>({value,label}))} disabled={busy} onChange={period_type=>update({period_type,parent_id:null})}/><Input aria-label="O 周期日期" type="date" value={draft.anchor_date} disabled={busy} onChange={event=>update({anchor_date:event.target.value,parent_id:null})}/></div></Field>
      <Field label="归属维度"><Select aria-label="O 归属维度" value={draft.scope_type} options={Object.entries(SCOPES).filter(([value])=>subjectChoices(options,value).length).map(([value,label])=>({value,label}))} disabled={busy} onChange={scope_type=>update({scope_type,subjectId:''})}/></Field>
      <Field label="归属对象"><Select aria-label="O 归属对象" showSearch optionFilterProp="label" value={draft.subjectId||undefined} options={names(subjectChoices(options,draft.scope_type))} disabled={busy} onChange={subjectId=>update({subjectId})}/></Field></>}
    <Field label="负责人"><Select aria-label="O/KR 负责人" showSearch optionFilterProp="label" value={draft.owner_user_id||undefined} options={names(participantOptions)} disabled={busy} onChange={owner_user_id=>update({owner_user_id,collaborator_user_ids:draft.collaborator_user_ids.filter(id=>id!==owner_user_id)})}/></Field>
    <Field label="协作人"><Select aria-label="O/KR 协作人" mode="multiple" showSearch optionFilterProp="label" value={draft.collaborator_user_ids} options={names(participantOptions.filter(row=>row.id!==draft.owner_user_id))} disabled={busy} onChange={collaborator_user_ids=>update({collaborator_user_ids})}/></Field>
    <Field label={isO?'对齐上级目标':'对齐上级 KR'}><Select aria-label="对齐上级" showSearch allowClear optionFilterProp="label" value={draft.parent_id||undefined} options={names(parentOptions)} placeholder="可选" disabled={busy} onChange={parent_id=>update({parent_id:parent_id||null})}/></Field>
    {!isO&&<><Field label="指标"><Select aria-label="KR 指标" showSearch optionFilterProp="label" value={draft.metric_code||undefined} options={metrics.filter(row=>(row.active&&row.source_kind!=='category')||(editing&&row.code===draft.metric_code)).map(row=>({value:row.code,label:`${row.name} · ${row.unit}`}))} disabled={busy||editing} onChange={metric_code=>update({metric_code,target_id:null})}/></Field>
      <Field label="引用生效目标"><Select aria-label="KR 生效目标" allowClear value={draft.target_id||undefined} placeholder={metric?'未关联目标，仅展示绝对值':'先选择指标'} options={allowedTargets.map(row=>({value:row.id,label:`${decimalText(row.effective_amount,metric?.decimal_places)} ${metric?.unit||''}`}))} disabled={busy||editing||!metric} onChange={target_id=>update({target_id:target_id||null})}/></Field>
      {metric&&<div className="review-okr-definition">{SOURCES[metric.source_kind]} · {metric.definition} {allowedTargets.length===0&&!editing?'当前归属和周期暂无可引用的生效目标。':''}</div>}</>}
    {editing&&<Field label="状态"><Select aria-label="O/KR 状态" value={draft.status} options={[{value:'active',label:'进行中'},{value:'archived',label:'归档'}]} disabled={busy} onChange={status=>update({status})}/></Field>}
  </div>;
}

function Evidence({row,displayPerson,leave}) {
  const progress=row.progress||{},text=progressText(row),reasons=[...new Set([progress.reason,...(progress.missing_reasons||[])].filter(Boolean))];
  return <div className="review-okr-evidence"><b>进展依据</b><p>{row.metric?.definition}</p>
    {text.subtotal!==null&&<p>已知部分：{text.subtotal} {text.unit}，未计作完整进展。</p>}
    {reasons.map(reason=><p className="pc-muted" key={reason}>{reasonText(reason)}</p>)}
    {progress.evidence&&<p className="review-okr-evidence-copy">{progress.evidence}</p>}
    {progress.confirmed_at&&<small>确认人 · {displayPerson(progress.confirmed_by_user_id)} · {dateTime(progress.confirmed_at)}</small>}
    {progress.data_as_of&&<small>数据截至 · {dateTime(progress.data_as_of)}</small>}
    {Array.isArray(progress.source_refs)&&progress.source_refs.length>0&&<details><summary>{progress.source_refs.length} 条业务来源</summary><ul>{progress.source_refs.map((source,index)=>{
      const opportunityId=source.opportunity_id||(source.kind==='opportunity'?source.id:null);
      const action=opportunityId&&can('opportunity.read')?()=>open.opportunity(source.customer_id,opportunityId):source.kind==='visit'&&source.id&&can('visit.read')?()=>open.visit(source.id,source.customer_id):null;
      return <li key={`${source.id||source.opportunity_id||''}:${index}`}>{source.title||source.name||({opportunity:'商机记录',visit:'正式跟进',effective_actual:'已确认实际业绩'}[source.kind])||'业务记录'} · {source.id||source.opportunity_id||source.source_ref||'已关联'}{source.version_no?` · 版本 ${source.version_no}`:''}{action&&<Button type="link" size="small" onClick={()=>leave(action)}>查看来源</Button>}</li>;
    })}</ul></details>}
  </div>;
}
