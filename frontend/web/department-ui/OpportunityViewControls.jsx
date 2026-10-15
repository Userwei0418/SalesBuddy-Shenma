import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Checkbox, Drawer, Modal, Select, Segmented} from 'antd';
import {Input} from './ImeInput.jsx';
import {identity} from './pc/data.js';
import {opportunityWorkspaceApi as service} from './pc/opportunity-workspace-api.js';
import {clampWidth, configureColumns, copy, editFilter, editValue, emptyOperator, LatestRead, moveColumn, presentationError, rowCanEditInline, saveFilter, saveEditValue, stable} from './pc/opportunity-workspace.js';

export function MemberSelect({value, onChange, multiple = false, kind = 'all', directory, retained = [], disabled, label}) {
  const [q, setQ] = useState(''), [result, setResult] = useState({items: []}), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const guard = useRef(new LatestRead());
  const account = identity();
  const known = useRef({account, people: new Map()});
  if (known.current.account !== account) known.current = {account, people: new Map()};
  const read = async (append = false) => {
    setBusy(true); setError('');
    try {
      const params = {q, kind, offset: append ? result.next_offset : 0};
      const read = await guard.current.run(account, identity, () => directory ? service.directory(directory, params) : service.members(params));
      if (!read.current) return;
      read.value.items.forEach(person => known.current.people.set(person.id, person));
      setResult(current => ({...read.value, items: append ? [...current.items, ...read.value.items] : read.value.items}));
      setBusy(false);
    } catch (e) {setError(e.message); setBusy(false);}
  };
  useEffect(() => {guard.current.invalidate(); const timer = setTimeout(() => read(), 180); return () => {clearTimeout(timer); guard.current.invalidate();};}, [q, kind, directory?.url, directory?.kind, account]);
  const options = new Map(retained.map(person => [person.id || person.user_id, {value: person.id || person.user_id, label: `${person.name}${person.is_active === false ? '（已停用）' : ''}`, disabled: person.is_active === false}]));
  result.items.forEach(person => options.set(person.id, {value: person.id, label: person.name + (person.account_code ? ` · ${person.account_code}` : '')}));
  for (const id of (Array.isArray(value) ? value : [value])) {const person = known.current.people.get(id); if (person && !options.has(id)) options.set(id, {value: id, label: person.name});}
  return <div><Select aria-label={label} disabled={disabled} mode={multiple ? 'multiple' : undefined} value={value ?? undefined} onChange={next => onChange(next ?? (multiple ? [] : null))} allowClear showSearch filterOption={false} onSearch={setQ} loading={busy} options={[...options.values()]} style={{width: '100%'}} placeholder="搜索姓名" popupRender={menu => <>{menu}{error && <span role="alert" className="opp-error">{error}</span>}{result.has_more && <Button block type="text" loading={busy} onMouseDown={event => event.preventDefault()} onClick={() => read(true)}>加载更多成员</Button>}</>} /></div>;
}

export function FieldInput({field, value, onChange, disabled, multiple = false, label}) {
  const props = {'aria-label': label || field.label, disabled, style: {width: '100%'}};
  const isMultiple = multiple || ['people', 'multi_enum'].includes(field.type);
  if (['person', 'people'].includes(field.type) && !field.options) return <MemberSelect label={props['aria-label']} disabled={disabled} value={value} onChange={onChange} multiple={isMultiple} directory={field.directory || (field.key === 'partner' ? {url: '/directory/partners'} : undefined)} kind={['primary_fde_id', 'fde_member_ids'].includes(field.key) ? 'fde' : 'all'} />;
  if (field.options || ['enum', 'multi_enum'].includes(field.type)) return <Select {...props} showSearch optionFilterProp="label" allowClear mode={isMultiple ? 'multiple' : undefined} value={value ?? undefined} options={field.options || []} onChange={next => onChange(next ?? (isMultiple ? [] : null))} placeholder="请选择" />;
  if (field.type === 'boolean') return <Select {...props} mode={isMultiple ? 'multiple' : undefined} allowClear value={value ?? undefined} onChange={next => onChange(next ?? null)} options={[{value: true, label: '是'}, {value: false, label: '否'}]} placeholder="未填写" />;
  if (isMultiple) return <Select {...props} mode="tags" tokenSeparators={[',', '，']} value={value || []} onChange={onChange} placeholder="输入值后回车" />;
  if (field.type === 'text' && field.max_length > 500) return <Input.TextArea {...props} value={value ?? ''} onChange={event => onChange(event.target.value)} autoSize={{minRows: 3, maxRows: 8}} maxLength={field.max_length} />;
  return <Input {...props} title={field.type === 'datetime' ? '北京时间' : undefined} type={field.type === 'date' ? 'date' : field.type === 'datetime' ? 'datetime-local' : 'text'} inputMode={['money', 'number'].includes(field.type) ? 'decimal' : undefined} value={value ?? ''} maxLength={field.max_length || 500} onChange={event => onChange(event.target.value)} placeholder={field.type === 'money' ? '单位：万元' : '未填写'} />;
}

const OPS = {eq: '等于', ne: '不等于', in: '属于', contains: '包含', has_any: '包含任一', has_all: '全部包含', between: '介于', gte: '大于等于', lte: '小于等于', gt: '大于', lt: '小于', is_empty: '未填写', is_not_empty: '已填写'};
function FilterValue({field, filter, onChange}) {
  if (filter.operator === 'between') return <div className="opp-actions">{[0, 1].map(index => <FieldInput key={index} field={field} label={`${field.label}${index ? '终点' : '起点'}`} value={filter.value?.[index]} onChange={value => {const next = [...(filter.value || [null, null])]; next[index] = value; onChange(next);}} />)}</div>;
  const multiple = ['in', 'has_any', 'has_all'].includes(filter.operator);
  return <FieldInput field={field} multiple={multiple} value={filter.value} onChange={onChange} />;
}

export function ViewSettings({open, onClose, config, fields, onApply}) {
  const [draft, setDraft] = useState(() => copy(config));
  const [error, setError] = useState('');
  const [dragged, setDragged] = useState('');
  useEffect(() => { if (open) { setDraft({...copy(config), filters: config.filters.map(filter => editFilter(filter, fields))}); setError(''); } }, [open]);
  const patch = values => setDraft(current => ({...current, ...values}));
  const selected = draft.columns.map(column => column.key);
  const filterFields = fields.filter(field => field.filter_ops?.length);
  const sortFields = fields.filter(field => field.sortable);
  const apply = () => {
    try {const filters = draft.filters.map(filter => saveFilter(filter, fields)), next = {...draft, filters}, error = presentationError(next, fields); if (error) throw Error(error); onApply(next); onClose();} catch (e) {setError(e.message);}
  };
  return <Drawer title="配置方案" open={open} onClose={onClose} size={540} footer={<div className="opp-actions"><Button onClick={onClose}>取消</Button><Button type="primary" onClick={apply}>应用到当前表格</Button></div>}>
    {error && <Alert type="error" message={error} />}
    <section className="opp-setting-section"><h3>显示字段</h3>
      <Select aria-label="显示字段" mode="multiple" value={selected} options={fields.map(field => ({value: field.key, label: field.label, disabled: field.key === 'name'}))} onChange={keys => setDraft(current => configureColumns(current, keys, fields))} style={{width: '100%'}} maxTagCount={5} />
      <ol className="opp-column-list">{draft.columns.map((column, index) => <li key={column.key} draggable onDragStart={event => { setDragged(column.key); event.dataTransfer.setData('text/plain', column.key); }} onDragOver={event => event.preventDefault()} onDrop={event => {event.preventDefault(); patch({columns: moveColumn(draft.columns, dragged, column.key)}); setDragged('');}}>
        <span className="opp-drag-handle" aria-hidden="true">⠿</span><span className="opp-column-label">{fields.find(field => field.key === column.key)?.label || column.key}</span>
        <Button size="small" aria-label={`${column.key}前移`} disabled={!index} onClick={() => patch({columns: moveColumn(draft.columns, column.key, draft.columns[index - 1].key)})}>↑</Button>
        <Button size="small" aria-label={`${column.key}后移`} disabled={index === draft.columns.length - 1} onClick={() => patch({columns: moveColumn(draft.columns, column.key, draft.columns[index + 1].key)})}>↓</Button>
        <Input aria-label={`${column.key}列宽`} inputMode="numeric" value={column.width} onChange={event => patch({columns: draft.columns.map(item => item.key === column.key ? {...item, width: clampWidth(event.target.value)} : item)})} style={{width: 70}} />
        <Checkbox checked={column.pinned === 'left'} onChange={event => patch({columns: draft.columns.map(item => item.key === column.key ? {...item, pinned: event.target.checked ? 'left' : undefined} : item)})}>固定</Checkbox>
      </li>)}</ol>
    </section>
    <section className="opp-setting-section"><h3>筛选条件 <small>同时满足</small></h3>
      {draft.filters.map((filter, index) => {
        const field = fields.find(item => item.key === filter.field);
        const replace = next => patch({filters: draft.filters.map((item, i) => i === index ? {...item, ...next} : item)});
        return <div className="opp-filter-item" key={index}>
          <Select aria-label={`筛选字段${index + 1}`} value={filter.field} options={filterFields.map(item => ({value: item.key, label: item.label}))} onChange={key => replace({field: key, operator: fields.find(item => item.key === key).filter_ops[0], value: null})} />
          <Select aria-label={`筛选比较${index + 1}`} value={filter.operator} options={(field?.filter_ops || []).map(value => ({value, label: OPS[value] || value}))} onChange={operator => replace({operator, value: null})} />
          {!emptyOperator(filter.operator) && field && <FilterValue field={field} filter={filter} onChange={value => replace({value})} />}
          <Button danger type="text" aria-label={`移除筛选${index + 1}`} onClick={() => patch({filters: draft.filters.filter((_, i) => i !== index)})}>移除</Button>
        </div>;
      })}
      <Button disabled={!filterFields.length || draft.filters.length >= 30} onClick={() => patch({filters: [...draft.filters, {field: filterFields[0].key, operator: filterFields[0].filter_ops[0], value: null}]})}>添加条件</Button>
    </section>
    <section className="opp-setting-section"><h3>排序</h3>{draft.sorts.map((sort, index) => <div className="opp-sort-item" key={index}>
      <Select aria-label={`排序字段${index + 1}`} value={sort.field} options={sortFields.map(field => ({value: field.key, label: field.label, disabled: draft.sorts.some((item, i) => i !== index && item.field === field.key)}))} onChange={field => patch({sorts: draft.sorts.map((item, i) => i === index ? {...item, field} : item)})} />
      <Select aria-label={`排序方向${index + 1}`} value={sort.direction} options={[{value: 'asc', label: '升序'}, {value: 'desc', label: '降序'}]} onChange={direction => patch({sorts: draft.sorts.map((item, i) => i === index ? {...item, direction} : item)})} />
      <Button danger type="text" onClick={() => patch({sorts: draft.sorts.filter((_, i) => i !== index)})}>移除</Button>
    </div>)}<Button disabled={!sortFields.some(field => !draft.sorts.some(sort => sort.field === field.key)) || draft.sorts.length >= 5} onClick={() => patch({sorts: [...draft.sorts, {field: sortFields.find(field => !draft.sorts.some(sort => sort.field === field.key)).key, direction: 'asc'}]})}>添加排序</Button></section>
    <section className="opp-setting-section"><h3>分组</h3><Select aria-label="分组字段" allowClear value={draft.group_by || undefined} placeholder="不分组" options={fields.filter(field => field.groupable).map(field => ({value: field.key, label: field.label}))} onChange={group_by => patch({group_by: group_by || null})} style={{width: '100%'}} /></section>
    <section className="opp-setting-section"><h3>表格显示</h3><div className="opp-actions"><Segmented aria-label="表格密度" value={draft.density} options={[{value: 'compact', label: '紧凑'}, {value: 'comfortable', label: '标准'}]} onChange={density => patch({density})} /><Checkbox checked={draft.wrap} onChange={event => patch({wrap: event.target.checked})}>长文本换行</Checkbox></div></section>
  </Drawer>;
}

export function SaveViewDialog({open, onClose, source, options, onSave, onReload}) {
  const [name, setName] = useState(''), [visibility, setVisibility] = useState('private'), [teamId, setTeamId] = useState(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => {if (open) {setName(source?.name || ''); setVisibility(source?.visibility || 'private'); setTeamId(source?.team_id || null); setError('');}}, [open, source]);
  const teams = options.teams || [];
  const scopes = (options.publish_scopes || ['private']).map(value => ({value, label: {private: '仅自己', team: '部门共享', company: '公司共享'}[value]}));
  const submit = async () => {
    if (!name.trim()) {setError('请填写方案名称'); return;}
    if (!scopes.some(scope => scope.value === visibility)) {setError('当前账号不能向此范围发布，请选择可用范围'); return;}
    if (visibility === 'team' && !teamId) {setError('请选择分享部门'); return;}
    setBusy(true); setError('');
    try {await onSave({name: name.trim(), visibility, ...(visibility === 'team' ? {team_id: teamId} : {})}); onClose();} catch (e) {setError(e.message);} finally {setBusy(false);}
  };
  return <Modal title={source ? '保存方案' : '另存新方案'} open={open} onCancel={() => !busy && onClose()} onOk={submit} confirmLoading={busy} okText="保存" cancelText="取消" destroyOnHidden>
    {error && <Alert type="error" message={error} action={source && <Button disabled={busy} onClick={onReload}>重新读取方案</Button>} />}
    <label className="opp-form-field">方案名称<Input aria-label="方案名称" value={name} maxLength={40} onChange={event => setName(event.target.value)} /></label>
    <label className="opp-form-field">使用范围<Select aria-label="方案使用范围" options={scopes} value={visibility} onChange={setVisibility} /></label>
    {visibility === 'team' && <label className="opp-form-field">分享部门<Select aria-label="分享部门" value={teamId} options={teams.map(team => ({value: team.id, label: team.name}))} onChange={setTeamId} /></label>}
  </Modal>;
}

export function EditOpportunityPanel({row, fields, onClose, onSave, onReload}) {
  const editable = fields.filter(field => rowCanEditInline(row, field));
  const [values, setValues] = useState({}), [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => {setValues(Object.fromEntries(editable.map(field => [field.key, editValue(row.values[field.key], field)]))); setError(''); setReason('');}, [row.id, row.version_no]);
  const save = async () => {
    setError('');
    try {
      const changes = Object.fromEntries(editable.filter(field => stable(values[field.key]) !== stable(editValue(row.values[field.key], field))).map(field => [field.key, saveEditValue(values[field.key], field)]));
      if (!Object.keys(changes).length) {onClose(); return;}
      if (!reason.trim()) {setError('请填写修改说明'); return;}
      setBusy(true); await onSave(changes, reason.trim()); onClose();
    } catch (e) {setError(e.message);} finally {setBusy(false);}
  };
  return <Drawer title={`维护信息 · ${row.values.name || ''}`} open onClose={() => !busy && onClose()} size={600} footer={<div className="opp-actions"><Button disabled={busy} onClick={onClose}>取消</Button><Button type="primary" loading={busy} onClick={save}>保存修改</Button></div>}>
    {error && <Alert type="error" message={error} action={<Button disabled={busy} onClick={onReload}>重新读取，放弃本次修改</Button>} />}
    <div className="opp-edit-grid">{editable.map(field => <label key={field.key} className={`opp-form-field ${field.max_length > 500 ? 'is-wide' : ''}`}>{field.label}<FieldInput field={field} value={values[field.key]} disabled={busy} onChange={value => setValues(current => ({...current, [field.key]: value}))} /></label>)}</div>
    <label className="opp-form-field">修改说明<Input aria-label="修改说明" value={reason} onChange={event => setReason(event.target.value)} maxLength={500} disabled={busy} placeholder="说明本次补充或调整的原因" /></label>
  </Drawer>;
}

export function EditableCell({row, field, onSave, onReload, children}) {
  const [editing, setEditing] = useState(false), [value, setValue] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  if (!rowCanEditInline(row, field) || ['person', 'people'].includes(field.type) || field.max_length > 500) return children;
  const begin = () => {setValue(editValue(row.values[field.key], field)); setError(''); setEditing(true);};
  const save = async () => {setBusy(true); setError(''); try {await onSave({[field.key]: saveEditValue(value, field)}, `表格内修改${field.label}`); setEditing(false);} catch (e) {setError(e.message);} finally {setBusy(false);}};
  return editing ? <div className="opp-cell-editor" onClick={event => event.stopPropagation()}>
    <FieldInput field={field} value={value} onChange={setValue} disabled={busy} />
    <div className="opp-actions"><Button size="small" type="primary" loading={busy} onClick={save}>保存</Button><Button size="small" disabled={busy} onClick={() => setEditing(false)}>取消</Button></div>
    {error && <><span role="alert" className="opp-error">{error}</span><Button size="small" onClick={onReload}>重新读取，放弃本次修改</Button></>}
  </div> : <span className="opp-editable-cell">{children}<button className="opp-cell-edit" aria-label={`编辑${row.values.name}的${field.label}`} onClick={event => {event.stopPropagation(); begin();}}>✎</button></span>;
}

export function OpportunityPeoplePanel({row, onClose, onSave, onReload}) {
  const [primary, setPrimary] = useState(null), [members, setMembers] = useState([]), [participants, setParticipants] = useState([]);
  const [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const oldMembers = row.values.fde_member_ids || [], oldParticipants = row.values.participants || [];
  const oldPrimary = row.values.primary_fde_id?.id || null;
  const canFde = row.capabilities?.can_manage_fde_members === true;
  const canParticipants = row.capabilities?.editable_fields?.includes('participants');
  const oldPeople = oldParticipants.map(person => ({user_id: person.user_id || person.id, role: person.role}));
  useEffect(() => {setPrimary(oldPrimary); setMembers(oldMembers.map(person => person.id)); setParticipants(oldPeople); setReason(''); setError('');}, [row.id, row.version_no]);
  const save = async () => {
    setError('');
    const values = {};
    if (canFde && primary !== oldPrimary) values.primary_fde_id = primary;
    if (canFde && (primary && primary !== oldPrimary || stable(members) !== stable(oldMembers.map(person => person.id)))) values.fde_member_ids = members;
    if (canParticipants && stable(participants) !== stable(oldPeople)) values.participants = participants;
    if (!Object.keys(values).length) {onClose(); return;}
    if (!reason.trim()) {setError('请填写调整说明'); return;}
    setBusy(true);
    try {await onSave(values, reason.trim()); onClose();} catch (e) {setError(e.message);} finally {setBusy(false);}
  };
  return <Drawer title={`项目人员 · ${row.values.name || ''}`} open onClose={() => !busy && onClose()} size={600} footer={<div className="opp-actions"><Button disabled={busy} onClick={onClose}>取消</Button><Button type="primary" loading={busy} onClick={save}>保存人员</Button></div>}>
    {error && <Alert type="error" message={error} action={<Button disabled={busy} onClick={onReload}>重新读取，放弃本次修改</Button>} />}
    <label className="opp-form-field">主责 FDE<MemberSelect label="主责FDE" kind="fde" value={primary} disabled={busy || !canFde} retained={[...(row.values.primary_fde_id ? [row.values.primary_fde_id] : []), ...oldMembers]} onChange={id => {setPrimary(id); if (id && !members.includes(id)) setMembers([...members, id]);}} /></label>
    <label className="opp-form-field">长期 FDE<MemberSelect label="长期FDE" kind="fde" multiple value={members} disabled={busy || !canFde} retained={oldMembers} onChange={ids => {setMembers(ids); if (primary && !ids.includes(primary)) setPrimary(null);}} /></label>
    <section className="opp-setting-section"><h3>项目参与者</h3>
      <MemberSelect label="项目参与者" multiple value={participants.map(person => person.user_id)} retained={oldParticipants} disabled={busy || !canParticipants} onChange={ids => setParticipants(ids.map(id => participants.find(person => person.user_id === id) || {user_id: id, role: 'other'}))} />
      {participants.map((person, index) => <div key={person.user_id} className="opp-sort-item"><span>参与者 {index + 1}{oldParticipants.find(item => (item.user_id || item.id) === person.user_id)?.name ? ` · ${oldParticipants.find(item => (item.user_id || item.id) === person.user_id).name}` : ''}</span><Select aria-label={`参与者${index + 1}职责`} disabled={busy || !canParticipants} value={person.role} options={[{value: 'sales', label: '销售参与者'}, {value: 'fde', label: 'FDE 参与者'}, {value: 'other', label: '其他参与者'}]} onChange={role => setParticipants(items => items.map(item => item.user_id === person.user_id ? {...item, role} : item))} /></div>)}
    </section>
    <label className="opp-form-field">调整说明<Input aria-label="人员调整说明" value={reason} onChange={event => setReason(event.target.value)} maxLength={500} disabled={busy} /></label>
  </Drawer>;
}
