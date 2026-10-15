// Only stable codes/IDs cross the saved-view API. Page indexes are reconstructed
// from current authorized directories immediately before applying a view.
const selected = (options, index, fallback = 'all') => options?.[index]?.value ?? fallback;
const nullable = value => value == null || value === '' || value === 'all' ? null : value;

export function opportunityViewConfig(data, columns) {
  return {schema_version: 1, columns: [...columns], order: 'quarter_stage', active_first: true, filters: {
    scope: data.executionScope || 'self', q: data.opportunityQuery || '',
    stages: [...(data.opportunitySelectedStages || [])],
    grade: nullable(selected(data.opportunityGradeOptions, data.opportunityGradeIndex)),
    owner_id: nullable(selected(data.opportunityOwnerOptions, data.opportunityOwnerIndex)),
    team_id: nullable(data.executionTeamKey),
    year: data.listQuarter?.quarters?.length ? Number(data.listQuarter.year) : null,
    quarters: [...(data.listQuarter?.quarters || [])],
    close_period: selected(data.opportunityCloseOptions, data.opportunityCloseIndex),
    activity_window: data.activityWindow || 'all',
    overview_year: data.summaryQuarter?.isAllYears ? null : Number(data.summaryQuarter?.year) || null,
    overview_quarters: [...(data.summaryQuarter?.quarters || [])],
  }};
}

function optionIndex(options, value, label) {
  const found = (options || []).findIndex(option => String(option.value) === String(value));
  if (found < 0) throw Error(`${label}已不在当前授权目录或选项中，请修改视图后再应用`);
  return found;
}

export function opportunityViewPatch(config, data, quarter, directory) {
  if (config.schema_version !== 1 || config.order !== 'quarter_stage' || config.active_first !== true) throw Error('该视图版本暂不支持，请更新视图');
  const f = config.filters || {};
  if (f.product_line) throw Error('当前页面暂不支持已保存的产品线筛选，请修改视图后再应用');
  const scope = f.scope || 'self';
  if (scope === 'team' && !data.canViewTeam) throw Error('当前账号不能应用团队范围视图');
  const scopes = data.executionScopeOptions || [{value: 'self'}, {value: 'team'}];
  const teams = directory?.team_options ? [{value:'all',label:'全部团队'}, ...directory.team_options.map(row=>({value:row.id,label:row.name}))] : data.executionTeamOptions;
  const owners = directory?.owner_options ? [{value:'all',label:'全部负责人'}, ...directory.owner_options.filter(row=>!f.team_id || (row.team_ids||[]).includes(f.team_id)).map(row=>({value:row.id,label:row.name}))] : data.opportunityOwnerOptions;
  const team = f.team_id || 'all';
  const teamIndex = optionIndex(teams, team, '团队');
  const ownerIndex = optionIndex(owners, f.owner_id || 'all', '负责人');
  const activityIndex = optionIndex(data.activityWindowOptions, f.activity_window || 'all', '跟进时间');
  const stageSet = new Set((data.opportunityStageOptions || []).map(row => row.value));
  const stages = f.stages || [];
  if (stages.some(value => !stageSet.has(value))) throw Error('视图中的商机阶段已不可用');
  const nowYear = new Date().getFullYear();
  const listQuarter = quarter.listQuarterSelection(f.year || nowYear, f.quarters || []);
  const summaryQuarter = quarter.summaryQuarterSelection(f.overview_year || 'all', f.overview_quarters || []);
  const years = [...new Set([...(data.summaryYearOptions || []).map(row=>row.value), f.year, f.overview_year].filter(Number.isInteger))].sort((a,b)=>a-b);
  const summaryYearOptions = [{value:'all',label:'全部年份'}, ...years.map(value=>({value,label:`${value}年`}))];
  return {
    opportunityQuery: f.q || '', executionScope: scope, executionScopeIndex: optionIndex(scopes, scope, '范围'),
    scope: scope === 'self' ? '我的' : '我的团队', executionTeamOptions: teams,
    executionTeamKey: team, executionTeamIndex: teamIndex, executionTeamLabel: teams[teamIndex].label,
    executionMemberName: 'all', opportunityOwnerOptions: owners, opportunityOwnerIndex: ownerIndex,
    opportunitySelectedStages: stages, opportunityStageLabel: stages.length ? `已选${stages.length}项` : '全部阶段',
    opportunityStageOptions: (data.opportunityStageOptions || []).map(row=>({...row,selected:stages.includes(row.value)})),
    opportunityGradeIndex: optionIndex(data.opportunityGradeOptions, f.grade || 'all', '等级'),
    opportunityCloseIndex: optionIndex(data.opportunityCloseOptions, f.close_period || 'all', '关单周期'),
    activityWindowIndex: activityIndex, activityWindow: f.activity_window || 'all',
    listQuarter, summaryQuarter, summaryYearOptions,
    summaryYearIndex: optionIndex(summaryYearOptions, summaryQuarter.year, '统计年份'),
    listYearIndex: optionIndex(summaryYearOptions, listQuarter.isAllYears ? 'all' : listQuarter.year, '关单年份'),
  };
}
