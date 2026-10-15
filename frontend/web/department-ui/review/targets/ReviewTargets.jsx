import React, {forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState} from 'react';
import {Alert, Button, Input, Modal, Pagination, Select, Tabs, Tag} from 'antd';
import {can, identity, readScopeFor, todayKey} from '../../pc/data.js';
import {PageHead, Panel} from '../../pc/ui.jsx';
import {reviewTargetsApi} from './api.js';
import {decimalText, PERIOD_LABELS, periodText, rowDraft, rowSubject, SOURCE_LABELS, subjectChoices, targetBody, targetError, targetRows} from './model.js';
import './targets.css';

const choices = values => values.map(row => ({value: row.id, label: row.name}));
const scopeName = {team: '团队', person: '个人', department: '部门汇总'};
const nowQuarter = date => date.slice(0, 4) === todayKey().slice(0, 4) && Math.floor((Number(date.slice(5, 7)) - 1) / 3) === Math.floor((Number(todayKey().slice(5, 7)) - 1) / 3);
const time = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '—';

function Field({label, children}) { return <label className="review-target-field"><span>{label}</span>{children}</label>; }

const ReviewTargets = forwardRef(function ReviewTargets(props, ref) {
  const owner = identity();
  if (!can('target.read')) return <Alert type="info" showIcon title="当前账号没有目标查看权限" />;
  return <Targets ref={ref} key={owner} owner={owner} {...props} />;
});
export default ReviewTargets;

const Targets = forwardRef(function Targets({owner, onBack, client = reviewTargetsApi}, ref) {
  const [filters, setFilters] = useState({period_type: 'quarter', anchor_date: todayKey(), q: '', limit: 20, offset: 0});
  const [state, setState] = useState({loading: true, error: '', rows: [], total: 0, metrics: [], options: null, editable: false});
  const [draft, setDraft] = useState(null), [saveState, setSaveState] = useState({saving: false, error: '', conflict: false});
  const [notice, setNotice] = useState(''), [tab, setTab] = useState('targets'), [history, setHistory] = useState(null);
  const [metricEdit, setMetricEdit] = useState({dirty: false, busy: false}), [dictionaryEpoch, setDictionaryEpoch] = useState(0);
  const mounted = useRef(true), serial = useRef(0), saving = useRef(false);
  const current = () => mounted.current && owner === identity();
  useEffect(() => {mounted.current = true; return () => {mounted.current = false; serial.current++;};}, []);
  const load = useCallback(async () => {
    const sequence = ++serial.current;
    setState(previous => ({...previous, loading: true, error: '', rows: [], total: 0}));
    const [targetResult, metricResult, optionResult] = await Promise.allSettled([client.targets(filters), client.metrics(), client.options()]);
    if (!current() || sequence !== serial.current) return;
    try {
      if (targetResult.status !== 'fulfilled') throw targetResult.reason;
      if (metricResult.status !== 'fulfilled') throw metricResult.reason;
      if (!Array.isArray(metricResult.value?.items)) throw Error('指标字典响应不完整');
      const options = optionResult.status === 'fulfilled' ? optionResult.value : null;
      setState({loading: false, error: '', rows: targetRows(targetResult.value), total: targetResult.value.total,
        metrics: metricResult.value.items, options, editable: targetResult.value.editable === true,
        optionError: optionResult.status === 'rejected' ? optionResult.reason.message || '维护对象暂不可用' : '',
      });
    } catch (error) {setState(previous => ({...previous, loading: false, error: error.message || '读取失败，请重试'}));}
  }, [owner, filters, client]);
  useEffect(() => {load();}, [load]);
  const writeGranted = can('target.submit') || can('target.manage');
  const allowedScopes = Object.keys(scopeName).filter(scope => subjectChoices(state.options, scope).length);
  const canAdd = writeGranted && state.editable && allowedScopes.length > 0 && !state.loading && !state.error;
  const selectedMetric = state.metrics.find(metric => metric.code === draft?.metricCode);
  const availableMetrics = state.metrics.filter(metric => metric.active && metric.goal_selectable);
  const updateDraft = patch => {setDraft(previous => ({...previous, ...patch})); setSaveState(previous => ({...previous, error: ''}));};
  const newRow = () => {
    if (!canAdd || draft) return;
    const scope = allowedScopes.includes('team') ? 'team' : allowedScopes[0];
    setDraft({scope, subjectId: '', metricCode: '', amount: '', reason: '', period_type: filters.period_type, anchor_date: filters.anchor_date});
    setSaveState({saving: false, error: '', conflict: false}); setNotice('');
  };
  const editRow = row => {
    if (draft || !writeGranted || !row.editable || row.pending_change) return;
    const metric = state.metrics.find(metric => metric.code === row.kind) || row.metric;
    setDraft(rowDraft(row, metric)); setSaveState({saving: false, error: '', conflict: false}); setNotice('');
  };
  const save = async () => {
    if (!draft || saving.current || !current() || !writeGranted || saveState.conflict) return;
    let body;
    try {
      body = targetBody(draft, state.metrics, state.options);
      const subject = subjectChoices(state.options, draft.scope).find(row => row.id === draft.subjectId);
      if (!subject.can_manage && !(draft.period_type === 'quarter' && nowQuarter(draft.anchor_date))) throw Error('当前账号仅可提交本季度目标');
    } catch (error) {setSaveState({saving: false, error: error.message, conflict: false}); return;}
    saving.current = true; setSaveState({saving: true, error: '', conflict: false});
    try {
      const result = await client.saveTarget(body);
      if (!current()) return;
      if (!['effective', 'pending', 'unchanged'].includes(result?.status)) throw Error('保存状态暂未确认，请刷新核对');
      setDraft(null); setSaveState({saving: false, error: '', conflict: false});
      setNotice(result.status === 'pending' ? '已提交审批。待审值独立展示，生效目标保持不变。' : result.status === 'unchanged' ? '目标没有变化。' : '目标已保存并生效。');
      await load();
    } catch (error) {if (current()) setSaveState({saving: false, error: targetError(error), conflict: error.statusCode === 409});}
    finally {saving.current = false;}
  };
  const refreshConflict = async () => {
    await load();
    // Do not silently rebase and submit an old amount against a new version.
    // The user's draft remains on screen until they explicitly cancel/re-edit.
  };
  const showHistory = async row => {
    if (!row.can_history || !row.id) return;
    setHistory({key: row.key, title: `${rowSubject(row)} · ${row.metric?.name || row.kind}`, loading: true});
    try {const result = await client.history(row.id); if (current()) setHistory(previous => previous?.key === row.key ? {...previous, loading: false, result} : previous);}
    catch (error) {if (current()) setHistory(previous => previous?.key === row.key ? {...previous, loading: false, error: error.message} : previous);}
  };
  const changeFilter = patch => {setFilters(previous => ({...previous, ...patch, offset: 0})); setHistory(null);};
  const dictionaryManage = can('target.manage') && readScopeFor('target.manage').scopes.includes('workspace');
  const leave = apply => {
    if (saving.current || metricEdit.busy) {
      Modal.info({title: '正在保存，请稍候', content: '收到保存结果后再离开，避免无法确认提交状态。', okText: '知道了'});
      return false;
    }
    if (!draft && !metricEdit.dirty) {apply?.(); return true;}
    Modal.confirm({title: '还有未保存的内容', content: '离开会放弃当前目标或指标的编辑内容。', okText: '放弃并离开', cancelText: '继续编辑', onOk: () => {
      setDraft(null); setSaveState({saving:false,error:'',conflict:false});setDictionaryEpoch(value => value + 1);setMetricEdit({dirty:false,busy:false});apply?.();
    }});
    return false;
  };
  useImperativeHandle(ref, () => ({leave, hasUnsavedChanges: () => Boolean(draft || metricEdit.dirty)}), [draft, metricEdit]);

  const editor = draft && <tr className="review-target-edit" data-testid="target-edit-row"><td colSpan={8}>
    <div className="review-target-form">
      <Field label="目标周期"><div className="review-target-period"><Select aria-label="目标周期" value={draft.period_type} disabled={!!draft.rowKey || saveState.saving} options={Object.entries(PERIOD_LABELS).map(([value, label]) => ({value, label}))} onChange={period_type => updateDraft({period_type})} />
        <Input type="date" aria-label="周期日期" value={draft.anchor_date} min="2000-01-01" max="2100-12-31" disabled={!!draft.rowKey || saveState.saving} onChange={event => updateDraft({anchor_date: event.target.value})} /></div></Field>
      <Field label="维度"><Select aria-label="目标维度" value={draft.scope} disabled={!!draft.rowKey || saveState.saving} options={allowedScopes.map(value => ({value, label: scopeName[value]}))} onChange={scope => updateDraft({scope, subjectId: ''})} /></Field>
      <Field label="目标对象"><Select aria-label="目标对象" showSearch optionFilterProp="label" value={draft.subjectId || undefined} placeholder="选择团队或成员" disabled={!!draft.rowKey || saveState.saving} options={choices(subjectChoices(state.options, draft.scope))} onChange={subjectId => updateDraft({subjectId})} /></Field>
      <Field label="指标"><Select aria-label="目标指标" showSearch optionFilterProp="label" value={draft.metricCode || undefined} placeholder="选择指标" disabled={!!draft.rowKey || saveState.saving} options={availableMetrics.map(metric => ({value: metric.code, label: metric.name}))} onChange={metricCode => updateDraft({metricCode})} /></Field>
      <Field label={`目标值${selectedMetric ? `（${selectedMetric.unit}）` : ''}`}><Input aria-label="目标值" inputMode="decimal" value={draft.amount} disabled={saveState.saving} onChange={event => updateDraft({amount: event.target.value})} placeholder={selectedMetric?.decimal_places === 0 ? '正整数' : '填写正数'} /></Field>
      <Field label="设置或调整原因"><Input aria-label="调整原因" maxLength={2000} value={draft.reason} disabled={saveState.saving} onChange={event => updateDraft({reason: event.target.value})} placeholder="与负责人商定的目标或调整依据" /></Field>
    </div>
    {selectedMetric && <div className="review-target-definition">{SOURCE_LABELS[selectedMetric.source_kind]} · {selectedMetric.definition}</div>}
    {saveState.error && <Alert role="alert" type="error" showIcon title={saveState.error} action={saveState.conflict && <Button size="small" onClick={refreshConflict}>刷新核对</Button>} />}
    <div className="review-target-edit-actions"><span className="pc-muted">{draft.rowKey ? '保存后按当前权限生效或送审' : '同一周期、对象和指标只维护一份目标'}</span>
      <Button aria-label="取消" disabled={saveState.saving} onClick={() => {setDraft(null); setSaveState({saving: false, error: '', conflict: false});}}>取消</Button>
      <Button aria-label="保存目标" type="primary" loading={saveState.saving} disabled={saveState.conflict} onClick={save}>保存目标</Button>
    </div>
  </td></tr>;
  const targetTable = <>
    <div className="review-target-toolbar">
      <Field label="周期"><Select aria-label="查看周期" value={filters.period_type} disabled={!!draft} options={Object.entries(PERIOD_LABELS).map(([value, label]) => ({value, label}))} onChange={period_type => changeFilter({period_type})} /></Field>
      <Field label="周期日期"><Input aria-label="查看周期日期" type="date" value={filters.anchor_date} min="2000-01-01" max="2100-12-31" disabled={!!draft} onChange={event => {if (event.target.value) changeFilter({anchor_date: event.target.value});}} /></Field>
      <Field label="对象"><Input.Search aria-label="搜索目标对象" allowClear placeholder="搜索团队或成员" disabled={!!draft} onSearch={q => changeFilter({q})} /></Field>
      <div className="review-target-toolbar-actions"><Button aria-label="刷新" disabled={state.loading || saveState.saving} onClick={load}>刷新</Button><Button type="primary" disabled={!canAdd || !!draft} onClick={newRow}>新增目标</Button></div>
    </div>
    {notice && <Alert type="success" showIcon title={notice} closable onClose={() => setNotice('')} />}
    {state.optionError && <Alert type="warning" showIcon title={`${state.optionError}，暂不能新增或编辑目标。`} />}
    {state.error && <Alert type="error" showIcon title={state.error} action={<Button onClick={load}>重试</Button>} />}
    <div className="review-target-table-wrap" aria-busy={state.loading}>
      <table className="review-target-table"><thead><tr><th>目标周期</th><th>维度 / 对象</th><th>指标</th><th>生效目标</th><th>待审目标</th><th>状态</th><th>维护人 / 时间</th><th>操作</th></tr></thead>
        <tbody>{editor}{!state.error && state.rows.map(row => <tr key={row.key} data-testid="target-row" className={draft?.rowKey === row.key ? 'is-editing' : ''}>
          <td>{periodText(row)}</td><td><b>{rowSubject(row)}</b><small>{scopeName[row.scope_type]}</small></td>
          <td><span>{row.metric?.name || row.kind}</span><small>{row.metric?.unit} · {SOURCE_LABELS[row.metric?.source_kind] || '—'}{row.metric?.active === false ? ' · 已停用' : ''}</small></td>
          <td className="review-target-number" data-testid="target-effective">{decimalText(row.effective_amount, row.metric?.decimal_places)}</td>
          <td className="review-target-number" data-testid="target-pending">{row.pending_change ? decimalText(row.pending_change.proposed_amount, row.metric?.decimal_places) : '—'}</td>
          <td><Tag color={row.pending_change ? 'gold' : 'green'}>{row.pending_change ? row.effective_amount === null ? '新目标待审批' : '变更待审批' : '生效中'}</Tag></td>
          <td>{row.updated_by_name || row.pending_change?.applicant_name || '—'}<small>{time(row.updated_at || row.pending_change?.created_at)}</small></td>
          <td><div className="review-target-row-actions">{row.editable && writeGranted && state.options && <Button type="link" size="small" disabled={!!draft || !!row.pending_change || state.loading} onClick={() => editRow(row)}>编辑</Button>}{row.can_history && row.id && <Button type="link" size="small" onClick={() => showHistory(row)}>历史</Button>}</div></td>
        </tr>)}{!state.rows.length && !draft && <tr><td colSpan={8} className="review-target-empty">{state.loading ? '正在读取目标…' : state.error ? '目标暂未读到' : '当前周期还没有目标'}</td></tr>}</tbody>
      </table>
    </div>
    <div className="review-target-footer"><span className="pc-muted">待审值不计入达成率；未设目标时仅展示业务绝对值。</span><Pagination current={Math.floor(filters.offset / filters.limit) + 1} total={state.total} pageSize={filters.limit} showSizeChanger pageSizeOptions={[10, 20, 50, 100]} disabled={!!draft || state.loading} onChange={(page, limit) => setFilters(previous => ({...previous, limit, offset: (page - 1) * limit}))} showTotal={total => `共 ${total} 项`} /></div>
    {history && <Panel title={`目标历史 · ${history.title}`} extra={<Button onClick={() => setHistory(null)}>收起</Button>}><History state={history} /></Panel>}
  </>;
  return <div className="review-targets" data-testid="review-targets">
    <PageHead title="目标管理" extra={onBack && <Button onClick={onBack}>返回本期概览</Button>} />
    <Panel><Tabs activeKey={tab} onChange={value => {if (value !== tab) leave(() => setTab(value));}} items={[
      {key: 'targets', label: '目标制定', children: targetTable},
      {key: 'metrics', label: '指标字典', children: <MetricDictionary key={dictionaryEpoch} onEditState={setMetricEdit} metrics={state.metrics} loading={state.loading} canManage={dictionaryManage} client={client} owner={owner} reload={load} />},
    ]} /></Panel>
  </div>;
});

function History({state}) {
  if (state.loading) return <p>正在读取历史…</p>;
  if (state.error) return <p role="alert">{state.error}</p>;
  const rows = state.result?.items || state.result?.history || [];
  return rows.length ? <ol className="review-target-history">{rows.map((row, index) => <li key={row.id || index}><b>{decimalText(row.before_snapshot?.amount_text ?? row.before_snapshot?.amount)} → {decimalText(row.after_snapshot?.amount_text ?? row.after_snapshot?.amount ?? row.amount_text ?? row.amount)}</b><span>{row.after_snapshot?.change_reason || row.change_reason || row.reason || '目标变更'}</span><small>{row.updated_by_name || row.actor_name || ''} · {time(row.occurred_at || row.created_at || row.updated_at)}</small></li>)}</ol> : <p className="pc-muted">暂无可查看的历史记录</p>;
}

function MetricDictionary({metrics, loading, canManage, client, owner, reload, onEditState}) {
  const [draft, setDraft] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pending = useRef(false), mounted = useRef(true);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {onEditState({dirty: Boolean(draft), busy});}, [draft, busy, onEditState]);
  const valid = () => mounted.current && owner === identity();
  const mutate = async action => {
    if (!canManage || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {await action(); if (valid()) {setDraft(null); await reload();}}
    catch (failure) {if (valid()) setError(failure.statusCode === 409 ? '指标版本已更新，输入已保留。请刷新后核对。' : failure.message || '指标保存失败');}
    finally {pending.current = false; if (valid()) setBusy(false);}
  };
  const save = () => {
    if (!draft?.name?.trim() || !draft?.unit?.trim() || !draft?.definition?.trim() || !/^custom_[a-z0-9_]{1,57}$/.test(draft.code)) {setError('请填写名称、单位、定义和 custom_ 开头的英文指标代码'); return;}
    mutate(() => client.createMetric({...draft, active: true}));
  };
  return <div className="review-target-dictionary"><div className="review-target-dictionary-head"><p className="pc-muted">目标、任务和工作报告共用。停用指标保留历史。</p>{canManage && <Button disabled={!!draft || busy} onClick={() => {setError(''); setDraft({code: 'custom_', name: '', unit: '次', decimal_places: 0, definition: ''});}}>新增手工指标</Button>}</div>
    {error && <Alert type="error" showIcon title={error} />}
    {draft && <div className="review-target-metric-form" data-testid="metric-edit-row">{[['code', '指标代码'], ['name', '名称'], ['unit', '单位'], ['definition', '口径定义']].map(([field, label]) => <Field key={field} label={label}><Input aria-label={label} value={draft[field]} disabled={busy} maxLength={field === 'definition' ? 2000 : field === 'name' ? 80 : field === 'unit' ? 20 : 64} onChange={event => setDraft(previous => ({...previous, [field]: event.target.value}))} /></Field>)}
      <Field label="小数位"><Select aria-label="小数位" value={draft.decimal_places} options={[{value: 0, label: '整数'}, {value: 2, label: '2 位小数'}]} disabled={busy} onChange={decimal_places => setDraft(previous => ({...previous, decimal_places}))} /></Field><div className="review-target-edit-actions"><Button disabled={busy} onClick={() => {setDraft(null); setError('');}}>取消新增指标</Button><Button type="primary" loading={busy} onClick={save}>保存指标</Button></div></div>}
    <div className="review-target-table-wrap"><table className="review-target-table"><thead><tr><th>指标</th><th>单位 / 精度</th><th>来源</th><th>定义</th><th>口径版本</th><th>状态</th>{canManage && <th>操作</th>}</tr></thead><tbody>{metrics.map(metric => <tr key={metric.code}><td><b>{metric.name}</b><small>{metric.code}</small></td><td>{metric.unit} · {metric.decimal_places} 位</td><td>{SOURCE_LABELS[metric.source_kind]}</td><td>{metric.definition}</td><td>{metric.definition_version}</td><td>{metric.active ? '启用' : '停用'}</td>{canManage && <td><Button type="link" size="small" disabled={busy || !!draft} onClick={() => mutate(() => client.updateMetric(metric.code, {active: !metric.active, version_no: metric.version_no}))}>{metric.active ? '停用' : '启用'}</Button></td>}</tr>)}</tbody></table>{!metrics.length && <p className="review-target-empty">{loading ? '正在读取指标…' : '暂无指标'}</p>}</div>
  </div>;
}
