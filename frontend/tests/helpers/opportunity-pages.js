// Test-only server fixture. Production filtering is implemented by the SQL endpoint.
const {stageOf,gradeOfAmount}=require('../../miniprogram/utils/opportunity');
const {beijingDateParts,groupOpportunitiesByQuarter}=require('../../miniprogram/utils/opportunityQuarter');
module.exports=function pageResponse(rows,options={}){
 const all=value=>!value||value==='all';
 const today=beijingDateParts(new Date());
 const filtered=rows.filter(row=>{
  if(options.query && ![row.name,row.customer_name,row.product_line].filter(Boolean).join(' ').toLowerCase().includes(options.query.toLowerCase()))return false;
  if(!all(options.team)&&row.team_name!==options.team)return false;
  if(!all(options.teamId)&&(row.team_id||row.team_name)!==options.teamId)return false;
  if(options.owner&&row.owner_name!==options.owner)return false;
  if(options.stages&&options.stages.length&&!options.stages.includes(stageOf(row).code))return false;
  if(!all(options.grade)&&(gradeOfAmount(row.amount)||{}).code!==options.grade)return false;
  const when=beijingDateParts(row.expected_close_date);
  if(options.quarters&&options.quarters.length&&(!when||when.year!==options.year||!options.quarters.includes(when.quarter)))return false;
  if(!all(options.closePeriod)&&(!when||when.year!==today.year||(options.closePeriod==='quarter'&&when.quarter!==today.quarter)||(options.closePeriod==='month'&&when.month!==today.month)))return false;
  return true;
 });
 const sorted=groupOpportunitiesByQuarter(filtered.map(row=>({...row,progressPercent:row.probability||0}))).flatMap(group=>group.items);
 const offset=options.offset||0,size=options.pageSize||20,items=sorted.slice(offset,offset+size),has_more=offset+items.length<sorted.length;
 const open=filtered.filter(row=>row.status==='open'),unknown=open.filter(row=>row.amount===null||row.amount===undefined||row.amount<0).length;
 const distinct=(source,key)=>[...new Set(source.map(row=>row[key]).filter(Boolean))];
 return {team_options: options.directoryTeams || distinct(rows,'team_name').map(name=>({id:(rows.find(row=>row.team_name===name)||{}).team_id||name,name})),items,has_more,next_offset:has_more?offset+size:null,summary:{total:filtered.length,open_count:open.length,open_amount:unknown?null:open.reduce((n,row)=>n+Number(row.amount),0),unknown_open_amount_count:unknown},facets:{owners:distinct(rows.filter(row=>(all(options.team)||row.team_name===options.team)&&(all(options.teamId)||(row.team_id||row.team_name)===options.teamId)),'owner_name'),teams:distinct(rows,'team_name'),product_lines:distinct(rows,'product_line'),years:distinct(rows.map(row=>({year:(beijingDateParts(row.expected_close_date)||{}).year})),'year')}};
};
