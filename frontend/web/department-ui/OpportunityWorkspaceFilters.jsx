import React, {useEffect, useState} from 'react';
import {Button, DatePicker, Select} from 'antd';
import dayjs from 'dayjs';
import FilterBar from './pc/FilterBar.jsx';
import {SbSearch} from './ImeInput.jsx';
import {changeScopeSelection, creationQuarterFilter, creationQuarterSelection, OPPORTUNITY_DATE_FIELDS, ownersInTeams, scopeSelection} from './pc/opportunity-scope-date.js';

function CreationQuarterFilter({value, onChange}) {
  const selection = creationQuarterSelection(value), signature = JSON.stringify(value);
  const [choice, setChoice] = useState(null);
  const current = new Date(Date.now() + 8 * 3600000);
  const currentYear = current.getUTCFullYear(), currentQuarter = Math.floor(current.getUTCMonth() / 3) + 1;
  const year = selection.year || currentYear, quarter = selection.quarter || currentQuarter;
  const kind = choice && choice.signature === signature ? choice.kind : selection.kind;
  const select = (nextKind, nextYear = year, nextQuarter = quarter) => {
    const range = nextKind === 'all' ? null : creationQuarterFilter(nextYear, nextQuarter);
    setChoice({kind: nextKind, signature: JSON.stringify(range)});
    onChange(range);
  };
  return <div className="opp-creation-quarter" data-toolbar-width={460}>
    <div className="opp-finance-period"><span>商机创建季度</span><Select aria-label="商机创建季度" value={kind}
      options={[{value: 'all', label: '不限'}, {value: 'current', label: '本季度'}, {value: 'fixed', label: '指定季度'}, ...(kind === 'custom' ? [{value: 'custom', label: '自定义日期', disabled: true}] : [])]}
      onChange={next => select(next, next === 'current' ? currentYear : year, next === 'current' ? currentQuarter : quarter)} /></div>
    {kind === 'fixed' && <div className="opp-fixed-period">
      <Select aria-label="商机创建年份" value={year} showSearch optionFilterProp="label" options={Array.from({length: 101}, (_, i) => ({value: 2000 + i, label: `${2000 + i}年`}))} onChange={next => select('fixed', next, quarter)} />
      <Select aria-label="商机创建季度值" value={quarter} options={[1, 2, 3, 4].map(value => ({value, label: `Q${value}`}))} onChange={next => select('fixed', year, next)} />
    </div>}
  </div>;
}

function OpportunityDateFilter({value, onChange}) {
  const [field, setField] = useState(value?.field || 'business_created_on');
  useEffect(() => {if (value?.field) setField(value.field);}, [value?.field]);
  return <div className="opp-date-filter" data-toolbar-width={460}>
    <Select aria-label="商机日期字段" value={field} options={OPPORTUNITY_DATE_FIELDS} onChange={next => {setField(next); if (value) onChange({...value, field: next});}} />
    <DatePicker.RangePicker aria-label="商机日期范围" placeholder={['开始日期', '结束日期']} format="YYYY-MM-DD"
      value={value ? [dayjs(value.start), dayjs(value.end)] : null}
      onChange={(_, dates) => onChange(dates?.[0] && dates?.[1] ? {field, start: dates[0], end: dates[1]} : null)} />
  </div>;
}

export default function OpportunityWorkspaceFilters({config, options, onChange, onSearchChange, onRefresh, onSettings}) {
  const teams = options.scope_options?.teams || [], owners = options.scope_options?.owners || [];
  const selection = scopeSelection(config.scope), period = config.period;
  const current = new Date(Date.now() + 8 * 3600000);
  const activeYear = period.kind === 'fixed' ? period.year : current.getUTCFullYear();
  const activeQuarter = period.kind === 'fixed' ? period.quarter : Math.floor(current.getUTCMonth() / 3) + 1;
  const multi = {'mode': 'multiple', allowClear: true, showSearch: true, optionFilterProp: 'label', maxTagCount: 1,
    maxTagTextLength: 5, maxCount: 100, maxTagPlaceholder: omitted => `+${omitted.length}`, popupMatchSelectWidth: 260};
  return <FilterBar label="商机筛选" className="opp-workspace-filters"
    search={<SbSearch value={config.q || ''} onChange={onSearchChange} onSearch={onRefresh} placeholder="搜索商机或客户" />}
    actions={<Button onClick={onSettings}>条件 {config.filters.length} · 排序 {config.sorts.length}</Button>}>
    <Select aria-label="查看范围" value={config.scope.kind} options={options.scope_options?.kinds || []} onChange={kind => onChange({scope: {kind}})} />
    {config.scope.kind === 'authorized' && <Select {...multi} data-toolbar-width={180} aria-label="部门" placeholder="全部可见部门" value={selection.team_ids}
      options={teams.map(team => ({value: team.id, label: team.name}))}
      onChange={team_ids => onChange({scope: changeScopeSelection(config.scope, {team_ids}, owners)})} />}
    {config.scope.kind === 'authorized' && <Select {...multi} data-toolbar-width={180} aria-label="成员" placeholder="全部可见成员" value={selection.owner_ids}
      options={ownersInTeams(owners, selection.team_ids).map(owner => ({value: owner.id, label: owner.name}))}
      onChange={owner_ids => onChange({scope: changeScopeSelection(config.scope, {owner_ids}, owners)})} />}
    <CreationQuarterFilter data-toolbar-width={460} value={config.date_filter} onChange={date_filter => onChange({date_filter})} />
    <OpportunityDateFilter data-toolbar-width={460} value={config.date_filter} onChange={date_filter => onChange({date_filter})} />
    <div className="opp-creation-quarter" data-toolbar-width={config.active_quarter ? 460 : 210}>
      <div className="opp-finance-period"><Select aria-label="商机活跃范围" value={config.active_quarter ? 'active' : 'all'}
        options={[{value: 'all', label: '全部商机'}, {value: 'active', label: '季度活跃商机'}]}
        onChange={value => onChange({active_quarter: value === 'active'})} /></div>
      {config.active_quarter && <div className="opp-finance-period">
        <span>活跃季度</span>
        <Select aria-label="活跃年份" value={activeYear} showSearch optionFilterProp="label" options={Array.from({length: 101}, (_, i) => ({value: 2000 + i, label: `${2000 + i}年`}))}
          onChange={year => onChange({period: {kind: 'fixed', year, quarter: activeQuarter}})} />
        <Select aria-label="活跃季度" value={activeQuarter} options={[1, 2, 3, 4].map(value => ({value, label: `Q${value}`}))}
          onChange={quarter => onChange({period: {kind: 'fixed', year: activeYear, quarter}})} />
      </div>}
    </div>
  </FilterBar>;
}
