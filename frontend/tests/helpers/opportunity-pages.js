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
  if(options.ownerId&&(row.owner_user_ref_id||row.owner_name)!==options.ownerId)return false;
  if(!options.ownerId&&options.owner&&row.owner_name!==options.owner)return false;
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
 const owner_options=options.directoryMembers || [...new Map(rows.filter(row=>row.owner_name).map(row=>{const id=row.owner_user_ref_id||row.owner_name;return [id,{id,name:row.owner_name,account_code:row.owner_account_code||id,team_ids:[...new Set(rows.filter(other=>(other.owner_user_ref_id||other.owner_name)===id).map(other=>other.team_id||other.team_name).filter(Boolean))]}];})).values()];
 const summary={total:filtered.length,open_count:open.length,open_amount:unknown?null:open.reduce((n,row)=>n+Number(row.amount),0),unknown_open_amount_count:unknown};
  const response={owner_options,defaults:{team_id:options.defaultTeamId||null},team_options: options.directoryTeams || distinct(rows,'team_name').map(name=>({id:(rows.find(row=>row.team_name===name)||{}).team_id||name,name})),items,has_more,next_offset:has_more?offset+size:null,summary,facets:{owners:distinct(rows.filter(row=>(all(options.team)||row.team_name===options.team)&&(all(options.teamId)||(row.team_id||row.team_name)===options.teamId)),'owner_name'),teams:distinct(rows,'team_name'),product_lines:distinct(rows,'product_line'),years:distinct(rows.map(row=>({year:(beijingDateParts(row.expected_close_date)||{}).year})),'year')}};
  // The production endpoint returns the filtered overview only when explicitly
  // requested. Keep this fixture contract-bound: malformed or legacy test
  // responses must continue to fail the client validation rather than being
  // silently upgraded.
  if(options.includeOverview){
    const periodQuarters=[...new Set((options.overviewQuarters||[]).map(Number))].sort((a,b)=>a-b);
    const inPeriod=value=>{
      if(!periodQuarters.length)return true;
      const date=beijingDateParts(value);
      return Boolean(date&&date.year===Number(options.overviewYear)&&periodQuarters.includes(date.quarter));
    };
    const creationDate=row=>row.historical_created_at||row.source_created||row.created_at;
    const activityRows=row=>Array.isArray(row.visits)?row.visits:Array.isArray(row.followups)?row.followups:[];
    const hasActivity=row=>row.active===true||Number(row.activity_count)>0||activityRows(row).some(visit=>
      ['confirmed','archived'].includes(visit.status)&&inPeriod(visit.interaction_at||visit.visit_date||visit.recorded_on));
    const creationDates=filtered.map(creationDate);
    const metrics={
      total:filtered.length,
      won:filtered.filter(row=>row.status==='won'&&inPeriod(row.closed_at)).length,
      active:filtered.filter(row=>row.status!=='lost'&&hasActivity(row)).length,
      newCount:creationDates.filter(value=>{const date=beijingDateParts(value);return date&&inPeriod(value);}).length,
      missingCloseDates:periodQuarters.length?filtered.filter(row=>row.status!=='lost'&&!row.expected_close_date&&!row.expected_close_year).length:0,
      missingWonDates:periodQuarters.length?filtered.filter(row=>row.status==='won'&&!row.closed_at).length:0,
      missingCreatedDates:creationDates.filter(value=>!beijingDateParts(value)).length,
      demo_scene_count:filtered.reduce((count,row)=>{
        if(Array.isArray(row.demo_scenes))return count+row.demo_scenes.filter(scene=>inPeriod(scene.created_at)).length;
        return count+(Number(row.demo_scene_count)||0);
      },0),
    };
    response.overview={metrics,year:Number(options.overviewYear),quarters:periodQuarters,definition_version:'opportunity_filtered_overview_v1'};
  }
  return response;
};
