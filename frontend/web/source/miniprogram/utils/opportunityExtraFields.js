// Optional formal fields: untouched fields are omitted, including absent legacy data.
const SCALARS=['priority','is_key','trial_status','summary','note','ppl_status','external_system_status','primary_fde_id','fde_support_stage','fde_progress'];
const ARRAYS=['customer_source','participants'];
const KEYS=[...SCALARS,...ARRAYS];
const TEXT_LIMITS={summary:500,note:5000,fde_progress:5000};
const clone=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
function formFields(row){
 const values={};for(const key of KEYS)if(row&&Object.prototype.hasOwnProperty.call(row,key))values[key]=clone(row[key]);
 return {extra_values:values,extra_dirty:{},extra_roster:clone(row&&row.fde_members||[])};
}
function ensure(form,row){
 const initial=formFields(row);
 return {...form,extra_values:form.extra_values||initial.extra_values,extra_dirty:form.extra_dirty||{},extra_roster:form.extra_roster||initial.extra_roster};
}
function payload(form){
 const values=form.extra_values||{},dirty=form.extra_dirty||{},out={};
 for(const key of KEYS){
  if(!dirty[key])continue;
  let value=values[key];
  if(Object.prototype.hasOwnProperty.call(TEXT_LIMITS,key)){
   if(value!==null&&typeof value!=='string')throw Error('补充信息格式不正确');
   value=value===null?null:value.trim()||null;
   if(value&&[...value].length>TEXT_LIMITS[key])throw Error(`补充信息超出 ${TEXT_LIMITS[key]} 字限制`);
  }else if(key==='is_key'){
   if(value!==null&&typeof value!=='boolean')throw Error('是否重点请选择有效选项');
  }else if(key==='customer_source'){
   if(!Array.isArray(value)||value.length>7||value.some(v=>typeof v!=='string')||new Set(value).size!==value.length)throw Error('客户来源请选择有效且不重复的选项');
   value=[...value];
  }else if(key==='participants'){
   if(!Array.isArray(value)||value.length>30||value.some(v=>!v||typeof v.user_id!=='string'||!v.user_id||!['sales','fde','other'].includes(v.role))||new Set(value.map(v=>v.user_id)).size!==value.length)throw Error('项目参与人员或职责不正确');
   value=value.map(({user_id,role})=>({user_id,role}));
  }else if(value!==null&&typeof value!=='string')throw Error('补充信息请选择有效选项');
  out[key]=value;
 }
 if(dirty.fde_member_ids||form.fde_roster_edited)out.fde_member_ids=(form.extra_roster||[]).map(row=>row.id);
 const primary=values.primary_fde_id;
 if(dirty.primary_fde_id&&primary){
  const ids=(form.extra_roster||[]).map(row=>row.id);
  if(!ids.includes(primary))throw Error('请先将主责 FDE 加入长期协助名单');
  out.fde_member_ids=ids;
 }
 if(out.fde_member_ids&&(out.fde_member_ids.length>30||out.fde_member_ids.some(id=>typeof id!=='string'||!id)||new Set(out.fde_member_ids).size!==out.fde_member_ids.length))throw Error('长期协助名单请选择不重复的有效成员，最多 30 人');
 if((dirty.fde_member_ids||form.fde_roster_edited||dirty.primary_fde_id)&&primary&&!((out.fde_member_ids||(form.extra_roster||[]).map(row=>row.id)).includes(primary)))throw Error('移除主责 FDE 前，请先清空或更换主责');
 if(Object.keys(out).length)out.fields_schema_version=2;
 return out;
}
module.exports={KEYS,SCALARS,ARRAYS,TEXT_LIMITS,formFields,ensure,payload};
