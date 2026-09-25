import React, {useEffect, useMemo, useState} from 'react';
import {App, Button, Checkbox, Collapse, Input, Select, Tag} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';
import {composeReport, periodRecords, reportPeriod, uploadDate} from './weekly-report-model.mjs';
import './weekly-report.css';

const STATUS = {archived: '已归档', confirmed: '已确认', draft: '草稿', pending: '待确认'};
const loadDraft = key => {try {const d = JSON.parse(sessionStorage.getItem(key)); return typeof d?.text === 'string' && Array.isArray(d.sources) ? d : null;} catch {return null;}};
const fingerprint = rows => JSON.stringify(rows.map(r => [r.id, r.follow_up_record, r.next_action, r.created_at, r.version_no]));
function ReportWorkspace({page, data: d, period, session}) {
  const {message, modal} = App.useApp();
  const rows = useMemo(() => periodRecords(d.records || [], period), [d.records, period.start, period.end, period.inputStart, period.inputEnd, period.cutoffAt]);
  const [excluded, setExcluded] = useState([]);
  const selected = rows.filter(r => !excluded.includes(r.id));
  const key = `sales-web:weekly-draft:v2:${globalThis.SALES_MODE}:${session.workspaceId}:${session.userId}:${session.role}:${session.permissionVersion}:${period.start}:${period.end}`;
  const [draft, setDraft] = useState(() => loadDraft(key));
  const [saveError, setSaveError] = useState(false);
  const [query, setQuery] = useState('');
  const filtered = rows.filter(r => [r.customer_name, r.opportunity_name, r.follow_up_record, r.next_action].join(' ').toLowerCase().includes(query.trim().toLowerCase()));
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!draft) return;
    try {sessionStorage.setItem(key, JSON.stringify(draft)); setSaveError(false);} catch {setSaveError(true);}
  }, [key, draft]);
  const generate = () => {
    const run = () => {
      setBusy(true);
      // Yield one frame to paint progress; all text is composed from selected source fields.
      requestAnimationFrame(() => {
        try {setDraft({...composeReport(selected, period, session.userName), fingerprint: fingerprint(selected), edited: false}); message.success('周报草稿已生成，请核对后使用');}
        catch (error) {message.error(error.message || '生成失败，请重试');}
        finally {setBusy(false);}
      });
    };
    if (draft?.edited) modal.confirm({title: '重新生成将替换当前编辑内容', content: '已编辑的周报会被所选记录重新整理的草稿替换。', okText: '替换并重新生成', cancelText: '保留当前内容', onOk: run});
    else run();
  };
  const copy = async () => {
    try {await navigator.clipboard.writeText(`${draft.title}\n${period.start} — ${period.end}\n\n${draft.text}`); message.success('周报已复制');}
    catch {message.error('复制失败，请选中周报正文手动复制');}
  };
  const changed = draft && !d.loading && !d.error && draft.fingerprint !== fingerprint(selected);
  const customers = new Set(rows.map(r => r.customer_id).filter(Boolean)).size;
  return <div className="ds-weekly-columns">
    <section className="ds-panel ds-weekly-records" aria-label="更新记录">
      <header className="ds-weekly-panel-head"><div><h2>更新记录 <span className="ds-weekly-count">{d.loading || d.error ? '—' : rows.length}</span></h2><span className="ds-muted">本人上传 · {period.inputStart} — {period.inputEnd} · 近 14 天</span></div><Button onClick={() => page.loadRecords()} loading={d.loading} disabled={busy}>刷新</Button></header>
      {d.loading ? <SbStatePanel state="loading" title="正在读取更新记录" /> : d.error ? <SbStatePanel state="error" title="更新记录读取失败" description={d.error} onRetry={() => page.loadRecords()} /> : rows.length === 0 ? <SbStatePanel state="empty" title="这个周期还没有更新记录" description="可切换周期，或先在「拜访与跟进」中保存记录。" /> : <>
        <div className="ds-weekly-record-tools"><Input.Search aria-label="搜索更新记录" placeholder="搜索客户、商机或记录内容" value={query} allowClear onChange={e => setQuery(e.target.value)} />
          <div className="ds-weekly-selection"><Checkbox checked={selected.length === rows.length} indeterminate={selected.length > 0 && selected.length < rows.length} onChange={e => setExcluded(e.target.checked ? [] : rows.map(r => r.id))}>全选近 14 天素材</Checkbox><span className="ds-muted">已选 {selected.length} / {rows.length} 条 · {customers} 个客户</span></div>
        </div>
        <div className="ds-weekly-record-list">
          {filtered.length === 0 ? <SbStatePanel state="empty" title="没有匹配的记录" description="请调整搜索词；已选记录仍会用于生成周报。" /> : filtered.map((row,index) => <React.Fragment key={row.id}>{(index === 0 || reportPeriod('recent',Date.parse(row.created_at || row.created_date)).start !== reportPeriod('recent',Date.parse(filtered[index-1].created_at || filtered[index-1].created_date)).start) && <h3 className="ds-sync-note">{reportPeriod('recent',Date.parse(row.created_at || row.created_date)).start} — {reportPeriod('recent',Date.parse(row.created_at || row.created_date)).end}{uploadDate(row) >= period.start ? ' · 本周' : ' · 前期素材'}</h3>}<article className={`ds-weekly-record ${excluded.includes(row.id) ? '' : 'is-selected'}`} key={row.id}>
            <div className="ds-weekly-record-title"><Checkbox aria-label={`选择记录 ${row.id}`} checked={!excluded.includes(row.id)} onChange={e => setExcluded(values => e.target.checked ? values.filter(id => id !== row.id) : [...values, row.id])} /><div><strong>{row.customer_name || '未关联客户'}</strong><span>{row.opportunity_name || '未关联商机'}</span></div><time>{uploadDate(row).slice(5)}</time></div>
            <p className="ds-weekly-excerpt">{row.follow_up_record || '沟通内容未填写'}</p>
            <Collapse ghost size="small" items={[{key: 'detail', label: '查看原始记录', children: <dl className="ds-weekly-source"><dt>沟通内容</dt><dd>{row.follow_up_record || '未填写'}</dd><dt>下一步行动</dt><dd>{row.next_action || '未填写'}</dd><dt>记录信息</dt><dd>{row.recorder_name || '未填写'} · 上传于 {uploadDate(row)} · {STATUS[row.status] || '状态待核对'}<br />跟进日期：{row.visit_date?.slice(0, 10) || '未填写'}</dd></dl>}]} />
          </article></React.Fragment>)}
        </div>
      </>}
      <footer className="ds-weekly-generate"><Button type="primary" block size="large" aria-label={draft ? '重新生成周报' : '一键生成周报'} disabled={d.loading || !!d.error || !selected.length} loading={busy} onClick={generate}>{draft ? '重新生成周报' : '一键生成周报'}</Button><span className="ds-muted">{selected.length && !d.loading && !d.error ? `根据所选 ${selected.length} 条记录整理` : '选择更新记录后生成'}</span></footer>
    </section>
    <section className="ds-panel ds-weekly-output" aria-label="周报草稿">
      <header className="ds-weekly-panel-head"><div><h2>周报草稿 {draft && <Tag>待核对</Tag>}</h2><span className="ds-muted">{draft ? `基于 ${draft.sources.length} 条记录 · 可直接编辑` : '生成后可编辑、复制'}</span></div><Button disabled={!draft?.text.trim()} onClick={copy}>复制周报</Button></header>
      {draft ? <>
        {changed && <div className="ds-weekly-note" role="status">所选记录已变化，当前草稿仍保留上次生成内容。</div>}
        <div className="ds-weekly-paper"><h3>{draft.title}</h3><div className="ds-muted">{draft.period.start} — {draft.period.end}<br/>素材：{draft.period.inputStart || draft.period.start} — {draft.period.inputEnd || draft.period.end}</div><label className="ds-weekly-editor-label" htmlFor="weekly-report-body">周报正文</label><Input.TextArea id="weekly-report-body" aria-label="周报正文" value={draft.text} onChange={e => setDraft({...draft, text: e.target.value, edited: true})} spellCheck={false} /></div>
        <details className="ds-weekly-citations"><summary>来源记录（{draft.sources.length} 条）</summary><ol>{draft.sources.map((r, i) => <li key={r.id}>[{i + 1}] {r.customer || '未关联客户'} · {r.date}</li>)}</ol></details>
      </> : <div className="ds-weekly-empty"><svg viewBox="0 0 64 64" aria-hidden="true"><rect x="14" y="7" width="36" height="50" rx="5"/><path d="M23 21h18M23 30h18M23 39h11"/></svg><h3>用近两周素材，整理本周周报</h3><p>记录按周分组展示；生成时使用近 14 天所选素材，并区分本周进展与前期背景。</p><div><span>本周概览</span><span>客户与商机进展</span><span>后续行动</span></div></div>}
      <footer className="ds-weekly-output-foot"><span>{draft ? saveError ? '暂存失败，请先复制保留内容' : '草稿已暂存于当前标签页' : '等待生成'}</span><span>Demo · 规则整理，未接入 AI</span></footer>
    </section>
  </div>;
}
export default function WeeklyReport({page, data}) {
  const [kind, setKind] = useState('recent');
  const [cutoff, setCutoff] = useState(Date.now);
  useEffect(()=>{if(data.loadedAt)setCutoff(data.loadedAt);},[data.loadedAt]);
  const period = useMemo(()=>reportPeriod(kind,cutoff),[kind,cutoff]);
  const session = globalThis.SalesRuntime.app.globalData.session || {};
  return <section className="ds-weekly" aria-label="周报">
    <div className="ds-weekly-heading"><div><h1>周报 <Tag>Demo</Tag></h1><p className="ds-muted">按周查看 · 使用近 14 天上传记录生成</p></div><div className="ds-weekly-period"><span className="ds-muted">{session.userName} · 本人</span><Select aria-label="周报周期" value={kind} options={[{value: 'recent', label: '本周（截至当前）'}, {value: 'previous', label: '上一个自然周'}]} onChange={setKind} /><span className="ds-muted">{period.start} — {period.end}</span></div></div>
    <ReportWorkspace key={`${session.workspaceId}:${session.userId}:${session.role}:${session.permissionVersion}:${period.start}`} page={page} data={data} period={period} session={session} />
  </section>;
}
