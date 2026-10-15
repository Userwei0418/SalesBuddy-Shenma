import React,{useEffect,useRef,useState} from 'react';
import {Button,Input,Modal,Select} from 'antd';
import {captureViewConfig,defaultViewConfig,validateViewConfig,viewState,DEFAULT_COLUMNS} from './opportunityViewState.mjs';
const LABELS={name:'商机 / 客户',stage:'阶段',owner:'负责人 / 团队',amount:'商机金额',signal:'状态',money:'确收 / 回款',close:'预计关单',product:'产品线'};
export default function OpportunityViews({page,data:d}) {
  const [views,setViews]=useState([]),[selected,setSelected]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[dialog,setDialog]=useState(''),[name,setName]=useState(''),[options,setOptions]=useState(null);
  const identity=d.opportunityViewIdentity;
  const serial=useRef(0),mounted=useRef(true);
  const live=()=>mounted.current&&globalThis.SalesRuntime?.current===page&&!page._destroyed&&identity===page.data.opportunityViewIdentity;
  const api=(path='',args={})=>page.opportunityViewsRequest(path,args);
  const current=views.find(v=>v.id===selected);
  const apply=config=>{clearTimeout(page.opportunitySearchTimer);page.setData(viewState(config,page.data),()=>page.applyOpportunityFilters());};
  async function load(){
    const turn=++serial.current;setBusy(true);setError('');
    try {
      const spec=await api('/options');let offset=0,all=[];
      while(true){const response=await api(`?limit=100&offset=${offset}`);if(!Array.isArray(response.items))throw Error('个人视图列表格式不完整');all.push(...response.items);if(!Number.isInteger(response.total)||!Number.isInteger(response.offset)||!Number.isInteger(response.limit))throw Error('个人视图分页信息不完整');if(all.length>=response.total)break;if(!response.items.length)throw Error('个人视图分页信息不完整');offset=response.offset+response.items.length;}
      if(live()&&turn===serial.current){setOptions(spec);setViews(all);if(selected&&!all.some(row=>row.id===selected)){setSelected('');apply(defaultViewConfig());setError('原视图已不可用，已恢复默认视图');}}
    }catch(e){if(live()&&turn===serial.current){if([401,403].includes(e.statusCode)){setOptions(null);setViews([]);setSelected('');apply(defaultViewConfig());}setError(e.message||'个人视图加载失败，请重试');}}
    finally{if(live()&&turn===serial.current)setBusy(false);}
  }
  useEffect(()=>{mounted.current=true;load();return()=>{mounted.current=false;serial.current++;};},[page]);
  async function selectView(id){
    if(busy)return;
    if(!id){apply(defaultViewConfig());setSelected('');setError('');return;}
    const turn=++serial.current;setBusy(true);setError('');
    try {
      const row=await api(`/${encodeURIComponent(id)}`);
      if(!live()||turn!==serial.current)return;
      let config;
      try{config=validateViewConfig(row.config);}catch(e){apply(defaultViewConfig());setSelected('');throw e;}
      setViews(old=>old.map(v=>v.id===row.id?row:v));apply(config);setSelected(row.id);if(row.config_valid===false)setError(row.config_error||'视图配置已失效，已恢复默认列和筛选，请重新保存。');
    }catch(e){if(live()&&turn===serial.current){if([401,403,404].includes(e.statusCode)){apply(defaultViewConfig());setSelected('');setViews(old=>old.filter(v=>v.id!==id));if(e.statusCode!==404)setOptions(null);}setError(e.message||'视图切换失败，当前设置已保留');}}
    finally{if(live()&&turn===serial.current)setBusy(false);}
  }
  function open(kind){setName(kind==='create'?'':current?.name||'');setError('');setDialog(kind);}
  async function save(){
    if(busy)return;
    if(dialog!=='create'&&!current){setDialog('');setError('原视图已不可用，请刷新后重试');return;}
    const trimmed=name.trim();if(dialog!=='delete'&&!trimmed){setError('请填写视图名称');return;}
    setBusy(true);setError('');
    try {
      if(dialog==='delete'){
        await api(`/${encodeURIComponent(current.id)}`,{method:'DELETE',data:{version_no:current.version_no}});
        if(!live())return;setViews(old=>old.filter(v=>v.id!==current.id));setSelected('');apply(defaultViewConfig());
      }else{
        const data={name:trimmed,...(dialog==='rename'?{}:{config:captureViewConfig(page.data)}),...(dialog==='create'?{}:{version_no:current.version_no})};
        const row=await api(dialog==='create'?'':`/${encodeURIComponent(current.id)}`,{method:dialog==='create'?'POST':'PUT',data});
        if(!live())return;validateViewConfig(row.config);setViews(old=>[row,...old.filter(v=>v.id!==row.id)]);setSelected(row.id);if(row.config_valid===false)setError(row.config_error||'视图配置已失效，请重新保存完整配置。');
      }
      setDialog('');
    }catch(e){if(live()){if([401,403,404].includes(e.statusCode)){setViews(old=>old.filter(v=>v.id!==current?.id));setSelected('');setDialog('');apply(defaultViewConfig());if(e.statusCode!==404)setOptions(null);}setError(e.message||'保存失败，当前设置仍保留，请重试');}}
    finally{if(live())setBusy(false);}
  }
  function setDisplay(patch,reload=false){page.setData(patch,()=>{if(reload)page.loadAllOpportunities();});}
  let changed=false;try{changed=!!current&&JSON.stringify(captureViewConfig(d))!==JSON.stringify(validateViewConfig(current.config));}catch{}
  const available=(options?.columns||Object.entries(LABELS).map(([value,label])=>({value,label}))).map(o=>({...o,disabled:o.value==='name'}));
  return <div className="ds-opp-views">
    <div className="ds-opp-view-row">
      <label className="ds-opp-view-select">个人视图<Select aria-label="个人视图" value={selected} loading={busy} disabled={busy} options={[{value:'',label:'默认视图'},...views.map(v=>({value:v.id,label:v.name}))]} onChange={selectView}/></label>
      <Button disabled={busy||!options?.can_create_private} onClick={()=>open('create')}>保存为新视图</Button>
      {current&&<><Button disabled={busy||!current.can_edit} onClick={()=>open('update')}>更新当前视图</Button><Button disabled={busy||!current.can_edit} onClick={()=>open('rename')}>重命名</Button><Button danger disabled={busy||!current.can_delete} onClick={()=>open('delete')}>删除视图</Button></>}
      <Button disabled={busy} onClick={load}>刷新视图</Button>
      {changed&&<span className="ds-muted">未保存更改</span>}
    </div>
    <div className="ds-opp-view-row">
      <label className="ds-opp-columns">显示字段<Select aria-label="显示字段" mode="multiple" value={d.opportunityViewColumns||DEFAULT_COLUMNS} options={available} maxTagCount={3} disabled={busy} onChange={columns=>setDisplay({opportunityViewColumns:columns.includes('name')?columns:['name',...columns]})}/></label>
      <label>排序<Select aria-label="商机排序" value={d.opportunityViewOrder||'quarter_stage'} disabled={busy} options={[{value:'quarter_stage',label:'季度与阶段'},{value:'close_date',label:'预计关单日期'}]} onChange={opportunityViewOrder=>setDisplay({opportunityViewOrder},true)}/></label>
      <label>分组<Select aria-label="商机分组" value={d.opportunityViewGroupBy||'quarter'} disabled={busy} options={[{value:'quarter',label:'按季度分组'},{value:'none',label:'不分组'}]} onChange={opportunityViewGroupBy=>setDisplay({opportunityViewGroupBy})}/></label>
    </div>
    {error&&!dialog&&<div className="ds-opp-view-error" role="alert">{error}<Button type="link" onClick={load}>重试读取</Button></div>}
    <Modal open={!!dialog} title={{create:'保存个人视图',update:'更新个人视图',rename:'重命名个人视图',delete:'删除个人视图'}[dialog]} okText={dialog==='delete'?'确认删除':'保存'} cancelText="取消" confirmLoading={busy} closable={!busy} maskClosable={false} cancelButtonProps={{disabled:busy}} onOk={save} onCancel={()=>{if(!busy){setDialog('');setError('');}}}>
      {dialog==='delete'?<p>删除“{current?.name}”后将恢复默认视图。商机数据不受影响。</p>:<label>视图名称<Input aria-label="视图名称" value={name} maxLength={40} disabled={busy} onChange={e=>setName(e.target.value)}/></label>}
      {error&&<p className="ds-opp-view-error" role="alert">{error}</p>}
    </Modal>
  </div>;
}
