import React from 'react';
import {Button} from 'antd';
import './opportunity-change-history.css';

// Values are formatted by the shared reader, including money, dates and missing snapshots.
const text = (value, fallback = '') => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;

function HistoryValue({value}) {
  const rows = Array.isArray(value?.rows) ? value.rows : [];
  if (value?.status === 'restricted') return <span className="ds-muted">{text(value.emptyLabel) || '无权查看此字段'}</span>;
  return <>
    {(!rows.length || value?.emptyLabel) && <span className="ds-muted">{text(value?.emptyLabel) || '未记录'}</span>}
    {rows.length > 0 && <ul className="ds-och-values">{rows.map((row, index) => <li key={index}>
    <span>{text(row.name, '名称未记录')}</span>
    {row.detail && <small className="ds-muted">{text(row.detail)}</small>}
    {row.missingName && text(row.id) && <small className="ds-muted">{text(row.idLabel, 'ID')}：{text(row.id)}</small>}
    </li>)}</ul>}
    {value?.truncated && <p className="ds-och-incomplete ds-muted">此历史值较长，已显示部分内容。</p>}
  </>;
}

export default function OpportunityChangeHistory({history = {}, call}) {
  const items = Array.isArray(history.items) ? history.items : [];
  const loading = Boolean(history.loading);
  const failed = Boolean(history.error || history.conflict);
  return <section className="ds-och" aria-label="变更历史" aria-busy={loading}>
    <header className="ds-och-head">
      <h3>变更历史 <small>按保存时间倒序</small></h3>
      {history.loaded && !failed && <Button size="small" disabled={loading} onClick={() => call('refreshChangeHistory')}>刷新历史</Button>}
    </header>
    {history.coverageNote && <p className="ds-och-note ds-muted">{text(history.coverageNote)}</p>}
    <ol className="ds-och-events">{items.map(item => <li key={item.id}>
      <article aria-label={`${text(item.actor, '操作人未记录')} · ${text(item.versionLabel, '版本未记录')}`}>
        <header className="ds-och-event-head">
          <strong>{text(item.actor, '操作人未记录')}</strong>
          <span className="ds-muted">{text(item.at, '时间未记录')}</span>
          <span className="ds-och-version">{text(item.versionLabel, '版本未记录')}</span>
        </header>
        {item.reason && <p className="ds-och-reason"><span className="ds-muted">变更说明：</span>{text(item.reason)}</p>}
        {item.changes?.length > 0 && <table className="ds-och-fields">
          <caption>字段变更</caption>
          <colgroup><col className="ds-och-field-column"/><col/><col/></colgroup>
          <thead><tr><th scope="col">字段</th><th scope="col">变更前</th><th scope="col">变更后</th></tr></thead>
          <tbody>{item.changes.map(change => <tr key={change.key}>
            <th scope="row">{text(change.label, '字段未记录')}</th>
            <td><span className="ds-och-mobile-label ds-muted">变更前</span><HistoryValue value={change.before}/></td>
            <td><span className="ds-och-mobile-label ds-muted">变更后</span><HistoryValue value={change.after}/></td>
          </tr>)}</tbody>
        </table>}
        {item.partial && <p className="ds-och-incomplete ds-muted">{item.unavailableFieldCount > 0 ? `${item.unavailableFieldCount} 个字段信息不完整，仅展示已保存的内容。` : '本条历史部分内容未完整记录，仅展示已保存的内容。'}</p>}
        {(item.unavailable || (!item.changes?.length && !item.partial)) && <p className="ds-och-incomplete ds-muted">本条历史没有可展示的字段明细。</p>}
      </article>
    </li>)}</ol>
    <div className="ds-och-state" aria-live="polite">
      {loading ? <p role="status">{items.length ? '正在读取变更历史…' : '正在加载变更历史…'}</p> : failed ? <div className="ds-och-error">
        <p role="alert">{text(history.error) || (history.conflict ? '变更历史已有更新，请重新读取。' : '变更历史读取失败。')}</p>
        <Button size="small" onClick={() => call(history.conflict ? 'refreshChangeHistory' : 'retryChangeHistory')}>{history.conflict ? '重新读取历史' : '重试读取历史'}</Button>
      </div> : !history.loaded ? <Button size="small" onClick={() => call('loadChangeHistory')}>读取变更历史</Button> : <>
        {!items.length && <p>暂无可展示的变更历史</p>}
        {history.hasMore && <Button size="small" onClick={() => call('moreChangeHistory')}>加载更早变更</Button>}
      </>}
    </div>
  </section>;
}
