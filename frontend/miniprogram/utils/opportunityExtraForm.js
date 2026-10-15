const access=require('./access');
const extra=require('./opportunityExtraFields');
const data={extraFields:[],extraLoading:false,extraError:'',extraReady:false,extraCanManageFde:false,extraCanEditParticipants:false,extraPrimaryOptions:[],extraParticipants:[],extraParticipantRoles:[],extraMemberOpen:false,extraMemberQuery:'',extraMemberRole:null,extraMemberRows:[],extraMemberTotal:0,extraMemberOffset:0,extraMemberMore:false,extraMemberLoading:false,extraMemberError:''};
const COPY_FIELDS=extra.KEYS.filter(key=>!['primary_fde_id','participants'].includes(key));
function create(api,session){
 const identity=()=>access.identity(session());
 function current(c,key){const seq=c[key]=(c[key]||0)+1,source=c.sourceKey,owner=identity();return ()=>!c.closed&&c[key]===seq&&c.sourceKey===source&&owner===identity();}
 function writable(c,key){
  if(c.closed||c.properties.disabled||!c.data.extraReady||c.extraIdentity!==identity())return false;
  const field=(c.extraCatalog.fields||[]).find(f=>f.key===key),row=c.properties.existing;
  if(!field||field.editable!==true||field.read_only===true)return false;
  if(row){const caps=row.capabilities||{};if(!access.can(session(),'opportunity.update')||!Array.isArray(caps.editable_fields)||!caps.editable_fields.includes(key))return false;}
  else if(!access.can(session(),'opportunity.create'))return false;
  if(['primary_fde_id','fde_member_ids'].includes(key))return access.can(session(),'opportunity.fde_members')&&(!row||row.capabilities&&row.capabilities.can_manage_fde_members===true);
  return true;
 }
 function edit(c,key,value){
  if(!writable(c,key))return false;
  c.setData({'form.extra_values':{...c.data.form.extra_values,[key]:value},'form.extra_dirty':{...c.data.form.extra_dirty,[key]:true}});c.decorateExtras();c.emit();return true;
 }
 return {
  resetExtras(){
   clearTimeout(this.extraMemberTimer);this.extraCatalog=null;this.extraIdentity=identity();this.extraOptionsSeq=(this.extraOptionsSeq||0)+1;this.extraMemberSeq=(this.extraMemberSeq||0)+1;
   this.setData({...data,form:extra.ensure(this.data.form,this.properties.existing)});
  },
  async loadExtraOptions(){
   const active=current(this,'extraOptionsSeq');this.setData({extraLoading:true,extraError:'',extraReady:false});
   try{
    const catalog=await api.request({path:'/opportunities/field-options'});if(!active())return;
    if(!catalog||catalog.definition_version!=='opportunity_workspace_v2'||!Array.isArray(catalog.fields)||!Array.isArray(catalog.participant_roles))throw Error('补充信息选项不完整，请重试');
    if(catalog.fields.some(f=>!f||typeof f.key!=='string'||typeof f.label!=='string'||(['enum','multi_enum'].includes(f.type)&&(!Array.isArray(f.options)||f.options.some(o=>!o||typeof o.value!=='string'||typeof o.label!=='string')))))throw Error('补充信息选项格式不正确，请重试');
    this.extraCatalog=catalog;this.extraIdentity=identity();this.setData({extraLoading:false,extraReady:true,extraParticipantRoles:catalog.participant_roles});this.decorateExtras();
   }catch(error){if(active())this.setData({extraLoading:false,extraReady:false,extraFields:[],extraCanManageFde:false,extraCanEditParticipants:false,extraError:error.message||'补充信息选项加载失败'});}
  },
  decorateExtras(){
   if(!this.extraCatalog)return;
   const values=this.data.form.extra_values||{};
   const fields=COPY_FIELDS.map(key=>(this.extraCatalog.fields||[]).find(f=>f.key===key)).filter(Boolean).map(field=>{
    const value=values[field.key],options=(field.options||[]).map(o=>({...o,selected:Array.isArray(value)&&value.includes(o.value)}));
    if(field.type==='boolean')options.push({value:true,label:'重点'},{value:false,label:'非重点'});
    const unknown=Array.isArray(value)?value.filter(v=>!options.some(o=>o.value===v)):value!==null&&value!==undefined&&!['text','boolean'].includes(field.type)&&!options.some(o=>o.value===value)?[value]:[];
    unknown.forEach(v=>options.push({value:v,label:`历史值：${v}`,selected:true,legacy:true}));
    return {...field,value:value===undefined?null:value,disabled:!writable(this,field.key),options:field.type==='multi_enum'?options:[{value:null,label:'未维护'},...options.map(o=>({...o,disabled:!!o.legacy}))],maxLength:extra.TEXT_LIMITS[field.key]||field.max_length||200};
   });
   const primary=values.primary_fde_id,roster=this.data.form.extra_roster||[];
   const primaryOptions=[{value:null,label:'未设置主责'},...roster.filter(p=>p.is_active!==false||p.id===primary).map(p=>({value:p.id,label:(p.name||p.id)+(p.is_active===false?'（已停用）':''),disabled:p.is_active===false}))];
   if(primary&&!primaryOptions.some(p=>p.value===primary))primaryOptions.push({value:primary,label:((this.properties.existing||{}).primary_fde||{}).name||'已设置主责（暂无成员详情）',disabled:true});
   this.setData({extraFields:fields,extraCanManageFde:writable(this,'primary_fde_id')&&writable(this,'fde_member_ids'),extraCanEditParticipants:writable(this,'participants'),extraPrimaryOptions:primaryOptions,extraParticipants:(values.participants||[]).map(p=>({...p,label:p.name||p.user_id,roleLabel:(this.data.extraParticipantRoles.find(r=>r.value===p.role)||{}).label||p.role}))});
  },
  extraChange(e){const key=e.currentTarget.dataset.key,field=this.data.extraFields.find(f=>f.key===key);if(!field||field.disabled)return;const value=e.detail.value;if(['enum','boolean'].includes(field.type)&&!field.options.some(o=>o.value===value&&!o.disabled))return;edit(this,key,value);},
  extraToggleSource(e){const key=e.currentTarget.dataset.key,value=e.currentTarget.dataset.value,field=this.data.extraFields.find(f=>f.key===key);if(!field||field.type!=='multi_enum'||field.disabled)return;const selected=this.data.form.extra_values[key]||[];const option=field.options.find(o=>o.value===value);if(!option||option.legacy&&!selected.includes(value))return;edit(this,key,selected.includes(value)?selected.filter(v=>v!==value):selected.concat(value));},
  extraPrimary(e){const value=e.detail.value;if(!this.data.extraPrimaryOptions.some(o=>o.value===value&&!o.disabled))return;edit(this,'primary_fde_id',value);},
  extraRosterChanged(e){
   if(!writable(this,'fde_member_ids'))return;
   this.setData({'form.extra_roster':e.detail.members,'form.extra_dirty':{...this.data.form.extra_dirty,fde_member_ids:true}});this.decorateExtras();this.emit();
  },
  openExtraMembers(){if(!writable(this,'participants'))return;this.setData({extraMemberOpen:true,extraMemberQuery:'',extraMemberRole:null,extraMemberRows:[],extraMemberOffset:0,extraMemberMore:false});},
  closeExtraMembers(){clearTimeout(this.extraMemberTimer);this.extraMemberSeq=(this.extraMemberSeq||0)+1;this.setData({extraMemberOpen:false,extraMemberLoading:false});},
  extraMemberRole(e){if(!writable(this,'participants')||!this.data.extraParticipantRoles.some(r=>r.value===e.detail.value))return;this.setData({extraMemberRole:e.detail.value,extraMemberRows:[],extraMemberOffset:0,extraMemberMore:false});return this.loadExtraMembers();},
  extraMemberSearch(e){if(!writable(this,'participants'))return;this.setData({extraMemberQuery:e.detail.value});this.extraMemberSeq=(this.extraMemberSeq||0)+1;clearTimeout(this.extraMemberTimer);this.extraMemberTimer=setTimeout(()=>this.loadExtraMembers(),250);},
  async loadExtraMembers(e){
   if(!writable(this,'participants')||!this.data.extraMemberOpen||!this.data.extraMemberRole)return;
   const more=!!(e&&e.currentTarget&&e.currentTarget.dataset.more);if(more&&this.data.extraMemberLoading)return;
   const active=current(this,'extraMemberSeq'),offset=more?this.data.extraMemberOffset:0;
   this.setData({extraMemberLoading:true,extraMemberError:'',...(!more?{extraMemberRows:[]}:{} )});
   try{
    const result=await api.request({path:`/directory/opportunity-members?kind=${this.data.extraMemberRole==='fde'?'fde':'all'}&q=${encodeURIComponent(this.data.extraMemberQuery.trim())}&limit=50&offset=${offset}`});
    if(!active()||!this.data.extraMemberOpen)return;
    if(!result||!Array.isArray(result.items)||!Number.isInteger(result.total)||typeof result.has_more!=='boolean'||result.has_more&&(!Number.isInteger(result.next_offset)||result.next_offset<=offset)||result.items.some(p=>!p||typeof p.id!=='string'||!p.name||p.is_active!==true))throw Error('成员目录响应不完整，请重试');
    const selected=new Set((this.data.form.extra_values.participants||[]).map(p=>p.user_id));
    const rows=[...new Map((more?this.data.extraMemberRows.concat(result.items):result.items).map(p=>[p.id,{...p,selected:selected.has(p.id)}])).values()];
    this.setData({extraMemberRows:rows,extraMemberTotal:result.total,extraMemberMore:result.has_more,extraMemberOffset:result.next_offset,extraMemberLoading:false});
   }catch(error){if(active())this.setData({extraMemberLoading:false,extraMemberError:error.message||'成员目录加载失败'});}
  },
  chooseExtraMember(e){
   if(!writable(this,'participants'))return;
   const row=this.data.extraMemberRows.find(p=>p.id===e.currentTarget.dataset.id),role=this.data.extraMemberRole,participants=this.data.form.extra_values.participants||[];
   if(!row||row.is_active!==true||participants.some(p=>p.user_id===row.id)||!this.data.extraParticipantRoles.some(r=>r.value===role))return;
   if(role==='fde'&&!(row.roles||[]).some(r=>['fde','fde_lead'].includes(r)))return;
   if(participants.length>=30){this.setData({extraMemberError:'最多选择 30 名项目参与者'});return;}
   if(edit(this,'participants',participants.concat({user_id:row.id,role,name:row.name,is_active:true})))this.closeExtraMembers();
  },
  removeExtraMember(e){edit(this,'participants',(this.data.form.extra_values.participants||[]).filter(p=>p.user_id!==e.currentTarget.dataset.id));},
  validateExtras(){
   const dirty=Object.keys(this.data.form.extra_dirty||{}).filter(key=>this.data.form.extra_dirty[key]);if(!dirty.length)return;
   if(!this.data.extraReady||this.extraIdentity!==identity())throw Error('请展开更多信息，重新加载补充选项后保存');
   for(const key of dirty){if(!writable(this,key))throw Error('部分补充信息已不可编辑，请重新打开当前商机');const field=(this.extraCatalog.fields||[]).find(f=>f.key===key),value=this.data.form.extra_values[key];if(field&&['enum','multi_enum'].includes(field.type)&&value!==null){const choices=Array.isArray(value)?value:[value];if(choices.some(v=>!(field.options||[]).some(o=>o.value===v)))throw Error(`${field.label}包含失效选项，请重新选择`);}}
   extra.payload(this.data.form);
  }
 };
}
module.exports={data,create};
