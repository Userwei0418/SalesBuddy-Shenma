function memberRanking(rows, metric, period, selectedId, subject = 'person') {
  const key = {followup:'followup_count',opportunities:'opportunity_count',demo:'demo_scene_count'}[metric];
  if (!key) return [];
  const values = rows.map(row => ({...row, value: row[key] == null ? null : Number(row[key])}))
    .filter(row => Number.isFinite(row.value) && row.value >= 0)
    .sort((a,b) => b.value-a.value || String(a.user_id).localeCompare(String(b.user_id)));
  const max = values.length ? values[0].value : 0;
  let rank=0;
  return values.map((row,index) => {
    if(!index || row.value!==values[index-1].value) rank=index+1;
    const team=subject==='team',roleLabel=team?'FDE 团队':row.role==='fde_lead'?'FDE主管':'FDE';
    const isSelected=!!selectedId&&String(row.user_id)===String(selectedId);
    return {...row,rank,barWidth:max?row.value/max*100:0,roleLabel,isSelected,isSelf:!team&&isSelected,
      meta:team?'公司同类团队 · '+roleLabel:[row.team_name||row.team,roleLabel].filter(Boolean).join(' · ')};
  });
}
module.exports={memberRanking};
