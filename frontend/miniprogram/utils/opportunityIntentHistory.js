const {businessTimeLabel}=require('./customerAdvice');

function historySide(ids,snapshot,side){
  if(!Array.isArray(ids)||ids.some(id=>typeof id!=='string'||!id)){
    return {rows:[],emptyLabel:'历史内容未完整记录'};
  }
  if(!ids.length)return {rows:[],emptyLabel:'未选择产品线'};
  const recorded=snapshot&&snapshot.version===1&&snapshot.basis==='directory_at_change'&&Array.isArray(snapshot[side])?snapshot[side]:[];
  return {emptyLabel:'',rows:ids.map(id=>{
    // Match the original ID, never the position or today's product directory.
    const matches=recorded.filter(row=>row&&row.id===id);
    const row=matches.length===1?matches[0]:null;
    const known=!!(row&&row.status==='recorded'&&typeof row.name==='string'&&row.name.trim());
    return {id,name:known?row.name:'名称未记录',missingName:!known};
  })};
}

function intentHistoryItem(item={}){
  const fields=Array.isArray(item.fields)?item.fields:[];
  const changes=fields.filter(field=>field&&field.field==='intent_product_line_ids').map((field,index)=>({
    key:`intent-${index}`,label:'意向产品线',
    before:historySide(field.before,field.display_snapshot,'before'),
    after:historySide(field.after,field.display_snapshot,'after'),
  }));
  return {
    id:item.id,
    actor:typeof item.actor?.name==='string'&&item.actor.name.trim()?item.actor.name:'操作人未记录',
    at:businessTimeLabel(item.created_at),
    versionLabel:Number.isInteger(item.version_no)&&item.version_no>=0?`商机版本 ${item.version_no}`:'版本未记录',
    reason:typeof item.reason==='string'?item.reason:'',
    changes,
    unavailable:item.detail_status==='unavailable'||!changes.length,
  };
}

const emptyHistory=()=>({items:[],loading:false,loaded:false,error:'',conflict:false,hasMore:false,nextOffset:0,revision:null,coverageNote:''});

// An identity/subject-bound reader. The endpoint adapter supplies this small
// read model; no generic timeline or editable form state is changed here.
class IntentHistoryReader{
  constructor(fetch,changed,identity){this.fetch=fetch;this.changed=changed;this.identity=identity;this.generation=0;this.close();}
  close(){this.generation++;this.closed=true;this.state=emptyHistory();}
  reset(subject){this.generation++;this.subject=subject;this.owner=this.identity();this.closed=false;this.state=emptyHistory();}
  current(generation=this.generation){return !this.closed&&generation===this.generation&&this.owner===this.identity();}
  emit(){if(this.current())this.changed(this.state);}
  async load({more=false,retry=false,refresh=false}={}){
    const prior=this.state;
    if(retry&&prior.failedMore)more=true;
    if(!this.current()||prior.loading||(!refresh&&prior.conflict)||(!refresh&&!more&&!retry&&prior.loaded)||(!refresh&&more&&!prior.hasMore))return;
    const generation=this.generation,offset=more&&!refresh?prior.nextOffset:0,limit=10;
    this.state={...prior,loading:true,error:''};this.emit();
    try{
      const result=await this.fetch(this.subject,{limit,offset,...(offset?{expected_history_revision:prior.revision}:{})});
      if(!this.current(generation))return;
      if(!result||result.opportunity_id!==this.subject||!Array.isArray(result.items)||result.items.length>limit||result.items.some(row=>!row||typeof row.id!=='string'||!row.id)||
        result.offset!==offset||typeof result.has_more!=='boolean'||!(/^[a-f0-9]{64}$/.test(result.history_revision||''))||
        (result.has_more&&(!Number.isInteger(result.next_offset)||result.next_offset<=offset))){
        throw Error('历史记录响应不完整，请重试');
      }
      if(offset&&result.history_revision!==prior.revision)throw Object.assign(Error('历史记录已更新，请重新读取'),{statusCode:409});
      const items=offset?prior.items.slice():[],seen=new Set(items.map(row=>row.id));
      for(const row of result.items)if(!seen.has(row.id)){items.push(intentHistoryItem(row));seen.add(row.id);}
      this.state={items,loading:false,loaded:true,error:'',conflict:false,hasMore:result.has_more,nextOffset:result.next_offset,revision:result.history_revision,coverageNote:typeof result.coverage_note==='string'?result.coverage_note:''};
    }catch(error){
      if(!this.current(generation))return;
      const conflict=error.statusCode===409;
      this.state={...prior,loading:false,failedMore:offset>0,conflict,error:conflict?'历史记录已更新，当前已读内容保留，请重新读取。':error.message||'历史记录加载失败，请重试'};
    }
    this.emit();
  }
}

module.exports={historySide,intentHistoryItem,emptyHistory,IntentHistoryReader};
