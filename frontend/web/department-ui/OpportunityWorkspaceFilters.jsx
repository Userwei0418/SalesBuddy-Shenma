import React, {useEffect, useState} from 'react';
import {Button, DatePicker, Select} from 'antd';
import dayjs from 'dayjs';
import FilterBar from './pc/FilterBar.jsx';
import {SbSearch} from './ImeInput.jsx';
import {changeScopeSelection, OPPORTUNITY_DATE_FIELDS, ownersInTeams, scopeSelection} from './pc/opportunity-scope-date.js';

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
    <OpportunityDateFilter data-toolbar-width={460} value={config.date_filter} onChange={date_filter => onChange({date_filter})} />
    <div className="opp-finance-period" data-toolbar-width={210}>
      <span>财务期间</span><Select aria-label="财务期间" value={period.kind} options={[{value: 'current_quarter', label: '本季度'}, {value: 'fixed', label: '指定季度'}]}
        onChange={kind => onChange({period: kind === 'fixed' ? {kind, year: new Date().getFullYear(), quarter: Math.floor(new Date().getMonth() / 3) + 1} : {kind}})} />
    </div>
    {period.kind === 'fixed' && <div className="opp-fixed-period" data-toolbar-width={210}>
      <Select aria-label="财务年份" value={period.year} showSearch optionFilterProp="label" options={Array.from({length: 101}, (_, i) => ({value: 2000 + i, label: `${2000 + i}年`}))} onChange={year => onChange({period: {...period, year}})} />
      <Select aria-label="财务季度" value={period.quarter} options={[1, 2, 3, 4].map(value => ({value, label: `Q${value}`}))} onChange={quarter => onChange({period: {...period, quarter}})} />
    </div>}
  </FilterBar>;
}
