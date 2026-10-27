import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Drawer, Pagination, Segmented, Select} from 'antd';
import {Input} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {LoadState} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {newRequestKey} from './model.js';
import {errorText} from './service.js';

const recipientKey = row => `${row.subject_kind}:${row.subject_id}`;
const recipientsInput = rows => rows.map(({subject_kind, subject_id}) => ({subject_kind, subject_id})).sort((a, b) => recipientKey(a).localeCompare(recipientKey(b)));

function editableWork(value, workId) {
  if (value?.work_id !== workId || typeof value.can_manage !== 'boolean' || !Number.isInteger(value.revision) || value.revision < 1) throw Error('作品共享信息不完整，请重新读取');
  if (value.can_manage && (!Array.isArray(value.recipients) || value.recipients.some(row => !['user', 'team'].includes(row.subject_kind) || !row.subject_id))) throw Error('作品共享范围不完整，请重新读取');
  return value;
}

function WorkRecipients({service, work, onSaved, onConflict}) {
  const actor = identity(), mounted = useRef(true), flight = useRef(false), intent = useRef(null);
  const [recipients, setRecipients] = useState(work.recipients), [kind, setKind] = useState('team');
  const [query, setQuery] = useState(''), [offset, setOffset] = useState(0), [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const directory = useKnowledgeRead(() => service.listWorkShareCandidates(work.work_id, {subject_kind: kind, q: query, limit: 20, offset}), [service, work.work_id, kind, query, offset]);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  const current = () => mounted.current && actor === identity();
  const add = () => {
    if (!current() || busy) return;
    const additions = selected.map(row => ({subject_kind: kind, subject_id: row.value, display_name: row.label}));
    const next = [...recipients.filter(old => !additions.some(row => recipientKey(row) === recipientKey(old))), ...additions];
    if (next.length > 200) {setError('最多选择 200 个接收对象，请先移除部分对象'); return;}
    setRecipients(next);
    setSelected([]); setError('');
  };
  const save = async (next = recipients) => {
    if (flight.current || !current()) return;
    const body = {expected_revision: work.revision, recipients: recipientsInput(next)};
    const fingerprint = JSON.stringify(body);
    // A network failure has an unknown outcome. Reuse this exact intent until
    // its receipt is confirmed; editing recipients creates a new request.
    if (intent.current?.fingerprint !== fingerprint) intent.current = {fingerprint, key: newRequestKey()};
    flight.current = true; setBusy(true); setError('');
    try {
      const saved = editableWork(await service.publishWork(work.work_id, body, intent.current.key), work.work_id);
      if (!current()) return;
      if (!saved.can_manage || saved.revision < work.revision || JSON.stringify(recipientsInput(saved.recipients)) !== JSON.stringify(body.recipients)) throw Error('共享保存回执与提交范围不一致，请重新读取核对');
      intent.current = null;
      onSaved(saved, next.length ? '作品共享范围已保存。接收人仍需具备全部来源资料的访问权限。' : '已撤销全部共享。只有作品作者可在来源权限有效时访问。');
    } catch (failure) {
      if (!current()) return;
      if (failure.statusCode === 409 || ['KNOWLEDGE_WORK_CHANGED', 'KNOWLEDGE_WORK_REVISION_CONFLICT'].includes(failure.code)) {
        intent.current = null;
        onConflict('共享范围已发生变化，已重新读取最新设置。请核对后重新选择并保存。');
      } else setError(errorText(failure));
    } finally {flight.current = false; if (current()) setBusy(false);}
  };
  const options = (directory.value?.items || []).map(row => ({value: row.subject_id, label: row.display_name}));
  const changed = JSON.stringify(recipientsInput(recipients)) !== JSON.stringify(recipientsInput(work.recipients));
  return <div className="kb122-stack">
    <Alert type="info" showIcon title="仅对所选成员或团队共享" description="接收人还需具备作品全部来源资料的访问权限；来源权限被撤回后，该作品也将无法查看或下载。共享作品不会额外授予来源资料权限。" />
    <div><h3>当前接收范围</h3>{recipients.length ? recipients.map((row, index) => <div className="kb122-file-row" key={recipientKey(row)}>
      <span>{row.subject_kind === 'team' ? '团队' : '成员'}：{row.display_name?.trim() || `已保存的${row.subject_kind === 'team' ? '团队' : '成员'}授权 ${index + 1}（名称暂不可用）`}</span>
      <Button size="small" disabled={busy} onClick={() => setRecipients(current => current.filter(value => recipientKey(value) !== recipientKey(row)))}>移除接收对象</Button>
    </div>) : <p className="pc-muted">未共享 · 仅作者</p>}</div>
    <Segmented aria-label="作品共享对象类型" options={[{value: 'team', label: '团队'}, {value: 'user', label: '成员'}]} value={kind} disabled={busy} onChange={value => {setKind(value); setSelected([]); setQuery(''); setOffset(0);}} />
    <Input aria-label="搜索作品共享对象" placeholder={kind === 'team' ? '搜索团队名称' : '搜索成员姓名'} maxLength={100} value={query} disabled={busy} onChange={event => {setQuery(event.target.value); setOffset(0); setSelected([]);}} />
    {directory.error && <Alert type="error" title={directory.error} action={<Button onClick={directory.reload}>重新读取候选</Button>} />}
    <Select mode="multiple" labelInValue aria-label="作品共享对象" placeholder="选择接收作品的对象" value={selected} options={options} loading={directory.loading} disabled={busy || directory.loading || Boolean(directory.error)} onChange={setSelected} optionFilterProp="label" />
    {directory.value && <><p className="pc-muted">共 {directory.value.total} 个 · 当前页 {directory.value.items.length} 个</p><Pagination size="small" current={offset / 20 + 1} pageSize={20} total={directory.value.total} showSizeChanger={false} onChange={page => {setOffset((page - 1) * 20); setSelected([]);}} /></>}
    <Button disabled={busy || !selected.length || directory.loading || Boolean(directory.error)} onClick={add}>添加接收对象</Button>
    {error && <Alert type="error" showIcon title={error} />}
    <div className="kb122-file-row"><Button type="primary" loading={busy} disabled={!changed || busy} onClick={() => save()}>保存共享范围</Button><Button danger disabled={busy || !work.recipients.length} onClick={() => save([])}>撤销全部共享</Button></div>
  </div>;
}

export default function WorkShareDrawer({service, workId, onClose, onSaved}) {
  const actor = identity();
  const read = useKnowledgeRead(async () => editableWork(await service.getWork(workId), workId), [service, workId]);
  const [notice, setNotice] = useState('');
  return <Drawer open width={640} title="作品共享" onClose={onClose} destroyOnHidden>
    {notice && <Alert type="warning" showIcon title={notice} />}
    <LoadState loading={read.loading} error={read.error} onRetry={read.reload}>
      {read.value && <><h2>{read.value.title || read.value.purpose || '生成作品'}</h2>{read.value.can_manage
        ? <WorkRecipients key={`${actor}:${workId}:${read.value.revision}`} service={service} work={read.value} onSaved={onSaved} onConflict={message => {setNotice(message); read.reload();}} />
        : <Alert type="info" title="只有作品作者可以管理共享范围" />}</>}
    </LoadState>
  </Drawer>;
}
