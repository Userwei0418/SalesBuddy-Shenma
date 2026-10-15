// PC-only personal-view configuration. These values narrow server queries; they never grant access.
export const VIEW_COLUMNS = ['name','stage','owner','amount','signal','money','close','product'];
export const DEFAULT_COLUMNS = ['name','stage','signal','money','close'];
export const VIEW_ORDERS = ['quarter_stage','close_date'];
export const VIEW_GROUPS = ['quarter','none'];
const FILTER_KEYS = ['q','scope','team_id','owner_id','stages','grade','product_line','year','quarters','close_period','include_closed'];
export function defaultViewConfig(year = new Date().getFullYear()) {
  return {schema_version:1,columns:[...DEFAULT_COLUMNS],filters:{q:'',scope:null,team_id:null,owner_id:null,stages:[],grade:null,product_line:null,year,quarters:[],close_period:'all',include_closed:true},order:'quarter_stage',group_by:'quarter'};
}
export function validateViewConfig(value) {
  if(!value || typeof value!=='object' || Array.isArray(value) || Object.keys(value).some(key=>!['schema_version','columns','filters','order','group_by'].includes(key)))throw Error('此视图配置无法识别，请重新选择或更新视图');
  const {columns,filters:f,order,group_by}=value;
  if(value.schema_version!==1 || !Array.isArray(columns) || !columns.includes('name') || columns.some(v=>!VIEW_COLUMNS.includes(v)) || new Set(columns).size!==columns.length || !VIEW_ORDERS.includes(order) || !VIEW_GROUPS.includes(group_by))throw Error('此视图的字段、排序或分组已失效');
  if(!f || typeof f!=='object' || Array.isArray(f) || Object.keys(f).some(key=>!FILTER_KEYS.includes(key)))throw Error('此视图的筛选配置无法识别');
  const filters={...defaultViewConfig().filters,...f};
  if(typeof filters.q!=='string' || ![null,'self','team'].includes(filters.scope) || ![null,'A','B','C','D'].includes(filters.grade) || !['all','month','quarter','year'].includes(filters.close_period) || typeof filters.include_closed!=='boolean' || !Array.isArray(filters.stages) || filters.stages.some(v=>typeof v!=='string') || !Array.isArray(filters.quarters) || filters.quarters.some(v=>![1,2,3,4].includes(v)) || (filters.year!==null&&!Number.isInteger(filters.year)) || (filters.quarters.length && (filters.year===null||filters.close_period!=='all')) || ['team_id','owner_id','product_line'].some(key=>filters[key]!==null&&typeof filters[key]!=='string'))throw Error('此视图的筛选值已失效');
  return JSON.parse(JSON.stringify({schema_version:1,columns,filters,order,group_by}));
}
function selected(options,index) {return (options?.[index]||{}).value;}
export function captureViewConfig(d) {
  const defaults=defaultViewConfig(),owner=selected(d.opportunityOwnerOptions,d.opportunityOwnerIndex);
  return validateViewConfig({schema_version:1,columns:d.opportunityViewColumns||DEFAULT_COLUMNS,filters:{
    q:d.opportunityQuery||'',scope:d.opportunityViewScope??null,
    team_id:d.executionTeamKey&&d.executionTeamKey!=='all'?d.executionTeamKey:null,
    owner_id:owner&&owner!=='all'?owner:null,
    stages:d.opportunitySelectedStages||[],grade:selected(d.opportunityGradeOptions,d.opportunityGradeIndex)==='all'?null:selected(d.opportunityGradeOptions,d.opportunityGradeIndex)||null,
    product_line:d.opportunityProductLine||null,year:d.opportunityViewYear===null&&!d.listQuarter?.quarters?.length?null:d.listQuarter?.year??defaults.filters.year,quarters:d.listQuarter?.quarters||[],
    close_period:selected(d.opportunityCloseOptions,d.opportunityCloseIndex)||'all',include_closed:d.opportunityIncludeClosed!==false,
  },order:d.opportunityViewOrder||'quarter_stage',group_by:d.opportunityViewGroupBy||'quarter'});
}
export function viewState(config,d) {
  const c=validateViewConfig(config),f=c.filters,year=f.year??new Date().getFullYear();
  const ensure=(options,value,label)=>value&&!options.some(o=>o.value===value)?[...options,{value,label}]:options;
  const teams=ensure(d.executionTeamOptions||[{value:'all',label:'全部团队'}],f.team_id,'已保存团队');
  const owners=ensure(d.opportunityOwnerOptions||[{value:'all',label:'全部负责人'}],f.owner_id,'已保存负责人');
  const years=ensure(d.quarterYearOptions || [],year,`${year}年`);
  const index=(options,value)=>Math.max(0,(options||[]).findIndex(o=>o.value===value));
  return {opportunityViewColumns:c.columns,opportunityViewOrder:c.order,opportunityViewGroupBy:c.group_by,
    opportunityViewScope:f.scope,opportunityViewYear:f.year,opportunityIncludeClosed:f.include_closed,opportunityProductLine:f.product_line||'',
    opportunityQuery:f.q,executionTeamOptions:teams,executionTeamKey:f.team_id||'all',executionTeamIndex:index(teams,f.team_id||'all'),executionTeamLabel:teams[index(teams,f.team_id||'all')].label,
    executionMemberName:'all',opportunityOwnerOptions:owners,opportunityOwnerIndex:index(owners,f.owner_id||'all'),
    opportunitySelectedStages:f.stages,opportunityStageLabel:f.stages.length?`已选${f.stages.length}项`:'全部阶段',
    opportunityStageOptions:(d.opportunityStageOptions||[]).map(o=>({...o,selected:f.stages.includes(o.value)})),
    opportunityGradeIndex:index(d.opportunityGradeOptions,f.grade||'all'),opportunityCloseIndex:index(d.opportunityCloseOptions,f.close_period),
    listQuarter:{year,quarters:f.quarters,options:[1,2,3,4].map(value=>({value,label:`Q${value}`,selected:f.quarters.includes(value)})),label:f.quarters.length?`${year}年 ${f.quarters.map(v=>`Q${v}`).join(' + ')}`:'全部时间（跨年）'},
    quarterYearOptions:years,listYearIndex:index(years,year),showOpportunityStageFilter:false,showOpportunityQuarterFilter:false};
}
export function groupViewRows(rows,groupBy) {
  if(groupBy==='none')return rows.length?[{key:'all',label:'全部商机',items:rows}]:[];
  const groups=new Map();
  for(const row of rows){const date=/^(\d{4})-(\d{2})/.exec(row.expected_close_date||'');const year=date?Number(date[1]):row.expected_close_year,quarter=date?Math.ceil(Number(date[2])/3):row.expected_close_quarter;
    const key=year&&quarter?`${year}-Q${quarter}`:'undated';
    if(!groups.has(key))groups.set(key,{key,label:key==='undated'?'待确认季度':`${year}年 · Q${quarter}`,items:[]});
    groups.get(key).items.push(row);
  }
  return [...groups.values()];
}
