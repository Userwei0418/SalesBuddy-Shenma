import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Alert, App, Button, Dropdown, Pagination, Select, Table, Tag} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';
import MetricStrip from './pc/MetricStrip.jsx';
import OpportunityWorkspaceFilters from './OpportunityWorkspaceFilters.jsx';
import {dateFilterLabel, scopeFilterLabel} from './pc/opportunity-scope-date.js';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import {can, identity, open as navigate} from './pc/data.js';
import {PageHead} from './pc/ui.jsx';
import {opportunityWorkspaceApi as service} from './pc/opportunity-workspace-api.js';
import {BOARD_ROUTE, EDITOR_ROUTE, LatestRead, appendPage, clampWidth, copy, fieldLabel, formatValue, moveColumn, presentationError, querySignature, rowCanEditInline, stable, tableRows} from './pc/opportunity-workspace.js';
import {EditableCell, EditOpportunityPanel, OpportunityPeoplePanel, SaveViewDialog, ViewSettings} from './OpportunityViewControls.jsx';
import './opportunity-workspace.css';

const DEFAULT_ID = '__default__';
const emptyPage = {items: [], total: 0, has_more: false, next_offset: null, summary: {}};
const visibilityLabel = {private: '个人', team: '部门', company: '公司'};

function TableHeader({fieldKey, columnWidth, onMove, onResize, editable, children, ...props}) {
  const resize = useRef(null);
  if (!fieldKey) return <th {...props}>{children}</th>;
  return <th {...props} draggable={editable} onDragStart={event => {event.dataTransfer.setData('application/x-salesbuddy-column', fieldKey);}} onDragOver={event => {if (editable && event.dataTransfer.types.includes('application/x-salesbuddy-column')) event.preventDefault();}} onDrop={event => {event.preventDefault(); const source = event.dataTransfer.getData('application/x-salesbuddy-column'); if (source) onMove(source, fieldKey);}}>
    {children}
    {editable && <span className="opp-column-resize" role="separator" aria-label={`调整${fieldKey}列宽`} aria-orientation="vertical" tabIndex={0}
      onKeyDown={event => {if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {event.preventDefault(); onResize(fieldKey, columnWidth + (event.key === 'ArrowLeft' ? -16 : 16));}}}
      onDragStart={event => event.preventDefault()}
      onPointerDown={event => {event.preventDefault(); event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId); resize.current = {x: event.clientX, width: columnWidth};}}
      onPointerMove={event => {if (resize.current) onResize(fieldKey, resize.current.width + event.clientX - resize.current.x);}}
      onPointerUp={event => {if (resize.current) {resize.current = null; event.currentTarget.releasePointerCapture(event.pointerId);}}}
      onPointerCancel={() => {resize.current = null;}} />}
  </th>;
}

export default function OpportunityWorkspace({page, data}) {
  const account = identity();
  const board = page.route === BOARD_ROUTE;
  const {modal, message} = App.useApp();
  const cached = page._opportunityWorkspace?.owner === account ? page._opportunityWorkspace : null;
  const [boot, setBoot] = useState(null), [loadingOptions, setLoadingOptions] = useState(true), [bootError, setBootError] = useState('');
  const [config, setConfig] = useState(cached?.config || null), [activeId, setActiveId] = useState(cached?.activeId || DEFAULT_ID);
  const [pageSize, setPageSize] = useListPageSize('opportunity-workspace');
  const [pagination, setPagination] = useState(cached?.pagination || null);
  const [result, setResult] = useState(cached?.result || emptyPage), [loading, setLoading] = useState(false), [queryError, setQueryError] = useState('');
  const [settings, setSettings] = useState(false), [saveMode, setSaveMode] = useState(null), [editing, setEditing] = useState(null), [refresh, setRefresh] = useState(0), [bootRefresh, setBootRefresh] = useState(0);
  const [blockedViewId, setBlockedViewId] = useState('');
  const [busy, setBusy] = useState(false), [mutationError, setMutationError] = useState('');
  const initRead = useRef(new LatestRead()), queryRead = useRef(new LatestRead()), state = useRef(null), restored = useRef(false), resultsScroll = useRef(null);
  const resultSignature = useRef(cached?.resultSignature || '');
  const searchIntent = useRef(null);
  const currentConfig = useRef(config); currentConfig.current = config;
  const selected = boot?.views.find(view => view.id === activeId);
  const fields = useMemo(() => (boot?.options.fields || []).map(field => field.directory?.source === 'scope_options.teams' || field.key === 'team' ? {...field, options: (boot.options.scope_options?.teams || []).map(item => ({value: item.id, label: item.name}))} : field.directory?.source === 'scope_options.owners' || field.key === 'owner' ? {...field, options: (boot.options.scope_options?.owners || []).map(item => ({value: item.id, label: item.name}))} : field), [boot?.options]);
  const migrationBlocked = Boolean(blockedViewId && blockedViewId === activeId);
  const configError = migrationBlocked ? selected?.migration_notice?.message || '旧方案条件需要重新配置，请确认后再查看' : config && boot ? presentationError(config, fields) : '';
  const dirty = Boolean(config && boot && (selected?.migration_blocked ? !migrationBlocked : stable(config) !== stable(selected?.config || boot.options.default_config)));
  const signature = config ? querySignature(config) : '';
  const currentPage = pagination?.signature === signature && pagination?.pageSize === pageSize ? pagination.current : 1;
  const requestSignature = `${signature}:${currentPage}:${pageSize}`;
  const patchConfig = values => {searchIntent.current = null; setPagination(null); setConfig(current => ({...current, ...values}));};
  const changeSearch = q => {
    setPagination(null);
    searchIntent.current = {account, activeId, signature: querySignature({...config, q}), refresh, bootRefresh};
    setConfig(current => ({...current, q}));
  };

  useEffect(() => {
    setBoot(null); setLoadingOptions(true); setBootError(''); setEditing(null); setMutationError(''); setSettings(false); setSaveMode(null);
    initRead.current.run(account, identity, async () => {
      const [options, views, preference] = await Promise.all([service.options(), service.views(), service.preference()]);
      if (!Array.isArray(options.fields) || options.default_config?.schema_version !== 2 || !Array.isArray(views.items)) throw Error('商机字段与方案服务尚未配套，请稍后重试');
      return {owner: account, options, views: views.items, preference};
    }).then(({current, value}) => {
      if (!current) return;
      const prior = page._opportunityWorkspace?.owner === account ? page._opportunityWorkspace : null;
      // A refresh revalidates the current selection, not the route used to
      // enter the page before the user chose another scheme.
      const requestedId = bootRefresh > 0 && prior ? undefined : data.options?.view;
      const id = requestedId || prior?.activeId || value.preference.effective_view_id || DEFAULT_ID;
      const view = value.views.find(item => item.id === id);
      if (id !== DEFAULT_ID && !view) setMutationError('之前的方案已删除或不在当前权限范围，已恢复默认方案');
      setBoot(value); setActiveId(view?.id || DEFAULT_ID); setBlockedViewId(view && (view.migration_blocked || !view.config) ? view.id : '');
      setConfig(prior && !requestedId && (id === DEFAULT_ID || view && prior.activeId === view.id && prior.viewVersion === view.version_no) ? prior.config : copy(view?.config || value.options.default_config));
      setLoadingOptions(false);
    }).catch(error => {setBootError(error.message); setLoadingOptions(false);});
    return () => {initRead.current.invalidate(); queryRead.current.invalidate();};
  }, [account, bootRefresh]);

  const readRows = useCallback(async () => {
    const current = currentConfig.current;
    if (migrationBlocked || !current || !boot || boot.owner !== account || presentationError(current, boot.options.fields)) return;
    setLoading(true); setQueryError('');
    const offset = (currentPage - 1) * pageSize;
    if (resultSignature.current !== requestSignature) setResult(emptyPage);
    try {
      const read = await queryRead.current.run(account, identity, async () => {
        const response = await service.query({config: current, offset, limit: pageSize});
        return {...response, items: appendPage([], response, false)};
      });
      if (!read.current) return;
      const lastPage = Math.max(1, Math.ceil(read.value.total / pageSize));
      if (currentPage > lastPage) {
        setPagination({signature: querySignature(current), pageSize, current: lastPage});
      } else {
        resultSignature.current = requestSignature;
        setResult(read.value);
      }
      setLoading(false);
    } catch (error) {
      resultSignature.current = ''; setResult(emptyPage);
      if (state.current) state.current.result = emptyPage;
      if (page._opportunityWorkspace) {page._opportunityWorkspace.result = emptyPage; page._opportunityWorkspace.resultSignature = '';}
      setQueryError(error.message); setLoading(false);
    }
  }, [account, boot, migrationBlocked, currentPage, pageSize, requestSignature]);

  useEffect(() => {
    queryRead.current.invalidate();
    const input = searchIntent.current;
    searchIntent.current = null;
    if (!boot || boot.owner !== account || !config || configError) {setLoading(false); return;}
    // Coalesce keystrokes only. Opening a page, choosing a scheme/filter and
    // explicit refresh should send the authorized query as soon as it is ready.
    const typing = input && input.account === account && input.activeId === activeId && input.signature === signature
      && input.refresh === refresh && input.bootRefresh === bootRefresh;
    setLoading(true);
    if (requestSignature !== resultSignature.current && resultsScroll.current) resultsScroll.current.scrollTop = 0;
    const timer = typing ? setTimeout(() => readRows(), 180) : null;
    if (!typing) readRows();
    return () => {clearTimeout(timer); queryRead.current.invalidate();};
  }, [boot?.owner, signature, currentPage, pageSize, refresh, configError]);
  useEffect(() => {
    state.current = {owner: account, config, result, activeId, signature, pagination, resultSignature: resultSignature.current, viewVersion: selected?.version_no};
    if (boot?.owner === account && config) page._opportunityWorkspace = {...state.current, scrollTop: page._opportunityWorkspace?.scrollTop || 0, scrollLeft: page._opportunityWorkspace?.scrollLeft || 0};
  }, [account, config, result, activeId, signature, pagination, selected?.version_no]);
  useEffect(() => {
    if (loading || !result.items.length || restored.current) return;
    const scrollTop = page._opportunityWorkspace?.scrollTop;
    requestAnimationFrame(() => {
      if (resultsScroll.current) resultsScroll.current.scrollTop = scrollTop || 0;
      const horizontal = resultsScroll.current?.querySelector('.ant-table-content');
      if (horizontal) horizontal.scrollLeft = page._opportunityWorkspace?.scrollLeft || 0;
    });
    restored.current = true;
  }, [loading, result.items.length]);
  const retainScroll = () => {if (page._opportunityWorkspace) Object.assign(page._opportunityWorkspace, {scrollTop: resultsScroll.current?.scrollTop || 0, scrollLeft: resultsScroll.current?.querySelector('.ant-table-content')?.scrollLeft || 0});};
  const route = target => {retainScroll(); navigate.route(target);};
  const openOpportunity = row => {retainScroll(); navigate.opportunity(row.customer_id, row.id);};
  const editScheme = () => route(`${EDITOR_ROUTE}?view=${encodeURIComponent(activeId)}`);
  const switchView = id => {
    const apply = () => {searchIntent.current = null; setPagination(null); const view = boot.views.find(view => view.id === id); setActiveId(id); setBlockedViewId(view && (view.migration_blocked || !view.config) ? id : ''); setConfig(copy(view?.config || boot.options.default_config)); setMutationError('');};
    if (dirty && !board) modal.confirm({title: '切换方案？', content: '当前调整尚未保存。切换会放弃这些调整，已保存的方案保持不变。', okText: '放弃调整并切换', cancelText: '继续编辑', onOk: apply}); else apply();
  };
  const refreshWorkspace = () => {
    if (busy) return;
    // Stop stale row responses immediately. Hide the old workspace until its
    // field permissions, shared schemes and default preference are current.
    // The bootstrap preserves edits only for a still-visible, same-version
    // scheme, and the row effect resumes after the new boot is installed.
    initRead.current.invalidate(); queryRead.current.invalidate();
    searchIntent.current = null;
    setBoot(null); setLoadingOptions(true); setLoading(false);
    setBootRefresh(value => value + 1);
  };
  const saveScheme = async values => {
    if (migrationBlocked) throw Error('旧方案需先重新配置并应用，再保存方案');
    const error = presentationError(config, fields); if (error) throw Error(error);
    const payload = {...values, config};
    const saved = saveMode === 'save' && selected?.can_edit ? await service.update(selected.id, {...payload, version_no: selected.version_no}) : await service.create(payload);
    setBoot(current => ({...current, views: [...current.views.filter(view => view.id !== saved.id), saved]})); setActiveId(saved.id); setBlockedViewId(''); setConfig(copy(saved.config)); message.success('方案已保存，可在商机看板选择');
  };
  const mutate = async operation => {setBusy(true); setMutationError(''); try {await operation();} catch (error) {setMutationError(error.message);} finally {setBusy(false);}};
  const setDefault = () => mutate(async () => {if (selected?.migration_blocked) throw Error('旧方案需重新配置并保存后，才能设为默认看板'); try {const preference = await service.setDefault(selected?.id || null, boot.preference.version_no); setBoot(current => ({...current, preference})); message.success('已设为默认看板');} catch (error) {const preference = await service.preference(); setBoot(current => ({...current, preference})); throw error;}});
  const removeScheme = () => modal.confirm({title: `删除“${selected.name}”？`, content: '只删除此方案配置，商机数据保留。', okText: '删除方案', okButtonProps: {danger: true}, cancelText: '取消', onOk: async () => {
    await service.remove(selected.id, selected.version_no);
    const preference = await service.preference();
    setBoot(current => ({...current, views: current.views.filter(view => view.id !== selected.id), preference})); setActiveId(DEFAULT_ID); setConfig(copy(boot.options.default_config));
  }});
  const saveRow = async (row, values, reason) => {
    await service.edit(row.id, {version_no: row.version_no, reason, values});
    message.success('商机已更新'); setRefresh(value => value + 1);
  };
  const openEdit = (row, mode = 'fields') => mutate(async () => {const full = await service.fields(row.id); setEditing({...full, mode});});
  const savePeople = async (row, values, reason) => {await service.people(row.id, {version_no: row.version_no, reason, ...values}); message.success('项目人员已更新'); setRefresh(value => value + 1);};
  const columns = useMemo(() => {
    if (!config) return [];
    const visible = config.columns.map(column => ({...column, definition: fields.find(field => field.key === column.key)})).filter(column => column.definition);
    const dataColumns = visible.map(({key, width, pinned, definition: field}, index) => ({key, title: fieldLabel(field), width, fixed: pinned || undefined,
      onHeaderCell: () => ({fieldKey: key, columnWidth: width, editable: !board, onMove: (from, to) => setConfig(current => ({...current, columns: moveColumn(current.columns, from, to)})), onResize: (fieldKey, nextWidth) => setConfig(current => ({...current, columns: current.columns.map(column => column.key === fieldKey ? {...column, width: clampWidth(nextWidth)} : column)}))}),
      onCell: row => row.__group ? {colSpan: index === 0 ? visible.length + 1 : 0} : {},
      render: (_, row) => {
        if (row.__group) return index === 0 ? <span className="opp-group-label"><b>{row.label}</b>{row.summary && <> · {row.summary.count} 个商机 · ACV {formatValue(row.summary.amount, {type: 'money'})} 万元{row.summary.amount_unknown_count > 0 && `（${row.summary.amount_unknown_count} 个未维护，已维护小计 ${formatValue(row.summary.known_amount_subtotal, {type: 'money'})} 万元）`}</>}</span> : null;
        const text = formatValue(row.values[key], field);
        const meta = row.finance_meta?.[key];
        const value = meta && !meta.complete ? <span className="opp-value-wrap" title={`已维护小计 ${formatValue(meta.confirmed_subtotal, {type: 'money'})} 万元`}>未完整维护{meta.confirmed_subtotal !== null && meta.confirmed_subtotal !== undefined && <small>小计 {formatValue(meta.confirmed_subtotal, {type: 'money'})} 万元</small>}</span> : key === 'name' ? <button className="opp-name-link" onClick={() => openOpportunity(row)}>{text}</button> : <span className={config.wrap ? 'opp-value-wrap' : 'opp-value'} title={text}>{text}</span>;
        return board ? value : <EditableCell key={`${row.id}:${row.version_no}:${key}`} row={row} field={field} onSave={(values, reason) => saveRow(row, values, reason)} onReload={() => setRefresh(value => value + 1)}>{value}</EditableCell>;
      }}));
    return [...dataColumns, {key: 'actions', title: '', width: 92, fixed: 'right', onCell: row => row.__group ? {colSpan: 0} : {}, render: (_, row) => row.__group ? null : <Dropdown trigger={['click']} menu={{items: [{key: 'detail', label: '查看详情'}, ...(!board && fields.some(field => rowCanEditInline(row, field)) ? [{key: 'edit', label: '维护信息'}] : []), ...(!board && row.capabilities?.editable_fields?.some(key => ['stage', 'status', 'amount', 'sales_channel'].includes(key)) ? [{key: 'core', label: '阶段与交易信息'}] : []), ...(!board && (row.capabilities?.can_manage_fde_members || row.capabilities?.editable_fields?.includes('participants')) ? [{key: 'people', label: '项目人员'}] : [])], onClick: ({key}) => key === 'edit' || key === 'people' ? openEdit(row, key === 'people' ? 'people' : 'fields') : key === 'core' ? route(`pages/opportunity-create/index?customerId=${encodeURIComponent(row.customer_id)}&opportunityId=${encodeURIComponent(row.id)}`) : openOpportunity(row)}}><Button size="small" type="text" aria-label={`${row.values.name}操作`}>操作 ▾</Button></Dropdown>}];
  }, [config, fields, board]);

  if (loadingOptions || boot?.owner !== account && !bootError) return <SbStatePanel state="loading" title="正在读取商机字段与方案" />;
  if (bootError || !boot || !config) return <SbStatePanel state="error" title="商机工作区暂不可用" description={bootError} action={<Button onClick={() => setBootRefresh(value => value + 1)}>重试</Button>} />;
  const options = boot.options;
  const teams = options.scope_options?.teams || [], owners = options.scope_options?.owners || [];
  const period = config.period;
  const shown = requestSignature === resultSignature.current && !queryError && !configError ? result : emptyPage;
  const summary = shown.summary || {};
  const unavailable = Boolean(queryError || configError || requestSignature !== resultSignature.current);
  const schemeItems = [{key: 'default', label: '设为默认看板', disabled: Boolean(selected?.migration_blocked)}, ...(!board ? [{key: 'copy', label: '另存为新方案', disabled: migrationBlocked}] : []), ...(selected?.can_delete ? [{key: 'delete', label: '删除方案', danger: true}] : [])];
  return <section className="pc-page pc-list-page opp-workspace" data-mode={board ? 'board' : 'editor'}>
    <PageHead title={board ? '商机看板' : '商机经营'} extra={<div className="opp-actions">{board ? <Button onClick={editScheme}>编辑方案</Button> : <><Button onClick={() => route(`${BOARD_ROUTE}?view=${encodeURIComponent(activeId)}`)}>打开看板</Button>{can('opportunity.create') && <Button type="primary" onClick={() => route('pages/opportunity-create/index')}>新增商机</Button>}</>}</div>} />
    <MetricStrip loading={loading} items={[{key: 'count', label: '商机总数', value: unavailable ? '—' : shown.total}, {key: 'amount', label: 'ACV · 万元', value: formatValue(summary.amount, {type: 'money'}), note: summary.amount_unknown_count ? `已维护小计 ${formatValue(summary.known_amount_subtotal, {type: 'money'})} 万元` : ''}, {key: 'unknown', label: '未维护 ACV', value: summary.amount_unknown_count ?? '—'}]} />
    <div className="opp-scheme-bar"><Select aria-label="选择看板方案" value={activeId} options={[{value: DEFAULT_ID, label: '默认商机视图'}, ...boot.views.map(view => ({value: view.id, label: `${view.name} · ${visibilityLabel[view.visibility] || ''}`}))]} onChange={switchView} className="opp-scheme-select" />
      {(boot.preference.effective_view_id || DEFAULT_ID) === activeId && <Tag color="blue">默认</Tag>}{dirty && <Tag>未保存调整</Tag>}
      <div className="opp-scheme-actions">{!board && <><Button onClick={() => setSettings(true)}>字段与布局</Button><Button type="primary" disabled={migrationBlocked} onClick={() => setSaveMode(selected?.can_edit ? 'save' : 'copy')}>保存方案</Button></>}
        <Dropdown trigger={['click']} menu={{items: schemeItems, onClick: ({key}) => key === 'default' ? setDefault() : key === 'copy' ? setSaveMode('copy') : removeScheme()}}><Button loading={busy}>更多 ▾</Button></Dropdown>
        <Button aria-label="刷新商机" onClick={refreshWorkspace} disabled={busy} loading={loading}>刷新</Button>
      </div>
    </div>
    {selected?.migration_notice && !selected.migration_blocked && !migrationBlocked && <Alert type="info" message={selected.migration_notice.message} />}
    {boot.preference.unavailable_reason && <Alert type="info" message="之前的默认方案已不可用，请重新选择并设为默认" />}
    {mutationError && <Alert type="error" showIcon message={mutationError} closable onClose={() => setMutationError('')} />}
    {configError && <Alert type="warning" showIcon message={configError} action={<Button onClick={() => board ? editScheme() : setSettings(true)}>编辑方案</Button>} />}
    {!board && <OpportunityWorkspaceFilters key={activeId} config={config} options={options} onChange={patchConfig} onSearchChange={changeSearch} onRefresh={() => setRefresh(value => value + 1)} onSettings={() => setSettings(true)} />}
    <div className={board ? 'opp-board-context' : 'opp-filter-context'}>
      {board && <span>{selected?.name || '默认商机视图'}</span>}
      <span>{scopeFilterLabel(config.scope, teams, owners)}</span><span>{dateFilterLabel(config.date_filter)}</span>
      <span>财务期间：{(shown.resolved_period ? `${shown.resolved_period.year} Q${shown.resolved_period.quarter}` : '') || (period.kind === 'current_quarter' ? '本季度' : `${period.year} Q${period.quarter}`)}</span>
      {board && <span>{config.filters.length} 项其他筛选</span>}
    </div>
    {queryError && <Alert type="error" message={queryError} action={<Button onClick={() => readRows()}>重新加载</Button>} />}
    <div className="opp-table-panel pc-results-scroll" ref={resultsScroll} onScroll={retainScroll} role="region" aria-label="商机明细" tabIndex={0}><Table rowKey="id" aria-label={board ? '商机看板表格' : '商机经营表格'} className={config.wrap ? 'opp-wrap-table' : ''} size={config.density === 'compact' ? 'small' : 'middle'} bordered={false} tableLayout="fixed" components={{header: {cell: TableHeader}}} columns={columns} dataSource={configError ? [] : tableRows(shown.items, config.group_by, fields, shown.groups)} loading={loading} pagination={false} scroll={{x: columns.reduce((total, column) => total + Number(column.width || 160), 0)}} rowClassName={row => row.__group ? 'opp-group-row' : ''} locale={{emptyText: configError ? '请修正方案后查看' : queryError ? '列表读取失败' : '没有符合条件的商机'}} />
      <div className="opp-table-footer"><span>本页 {shown.items.length} 条</span><Pagination current={currentPage} pageSize={pageSize} total={shown.total} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS} showTotal={total => `共 ${total} 条`} disabled={loading || Boolean(queryError || configError)} onChange={(number, size) => {
        setPageSize(size); setPagination({signature, pageSize: size, current: size === pageSize ? number : 1});
        resultsScroll.current?.scrollIntoView({block: 'start', behavior: 'auto'});
      }} /></div>
    </div>
    {!board && <ViewSettings open={settings} onClose={() => setSettings(false)} config={config} fields={fields} onApply={next => {setPagination(null); setConfig(next); setBlockedViewId('');}} />}
    <SaveViewDialog open={Boolean(saveMode)} onClose={() => setSaveMode(null)} source={saveMode === 'save' ? selected : null} options={options} onSave={saveScheme} onReload={() => {setSaveMode(null); page._opportunityWorkspace = null; setBootRefresh(value => value + 1);}} />
    {editing?.mode === 'fields' && <EditOpportunityPanel row={editing} fields={fields} onClose={() => setEditing(null)} onSave={(values, reason) => saveRow(editing, values, reason)} onReload={() => openEdit(editing)} />}
    {editing?.mode === 'people' && <OpportunityPeoplePanel row={editing} onClose={() => setEditing(null)} onSave={(values, reason) => savePeople(editing, values, reason)} onReload={() => openEdit(editing, 'people')} />}
  </section>;
}
