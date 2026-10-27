import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from 'antd';
import {Panel, Tag2} from './pc/ui.jsx';
import {api, can, identity, runtime} from './pc/data.js';
import {businessErrorMessage} from './pc/error-message.js';

function present(value, customerId, allowEmpty) {
  if (value === null && allowEmpty) return {status: 'none'};
  if (!value?.id || value.subject_kind !== 'customer' || String(value.subject_id) !== customerId
    || String(value.customer_id) !== customerId || value.section !== 'overview') {
    throw Error('建议与当前客户不匹配，请重新读取');
  }
  if (value.stale || value.status === 'superseded') return {id: value.id, status: 'stale'};
  if (value.status === 'succeeded') return runtime().require('utils/customerAdvice').adviceResult(value);
  if (['queued', 'running'].includes(value.status)) return {id: value.id, status: value.status};
  if (value.status === 'failed') return {id: value.id, status: 'failed', error: businessErrorMessage({code:value.error_code}, '本次分析未完成，可重试生成。')};
  throw Error('建议状态暂时无法确认，请重新读取');
}

// Keep discovery separate from generation: mount, reentry and refresh never POST.
// A request is scoped to this mounted panel, actor and customer, including its first render.
export default function KnowledgeAdvice({customerId, customerOverview}) {
  const id = String(customerId || ''), owner = identity();
  const confirmed = Boolean(id && customerOverview && String(customerOverview.id) === id);
  // Older readable customer overviews omit identities. Explicit partner-only
  // overviews are never eligible, including a formerly selected customer.
  const eligible = confirmed && customerOverview.customer_analysis_applicable !== false
    && (!Array.isArray(customerOverview.business_identities) || customerOverview.business_identities.includes('customer'));
  const scope = JSON.stringify([confirmed, customerOverview?.business_identities, customerOverview?.customer_analysis_applicable]);
  const permitted = Boolean(id && owner && can('advice.read') && can('advice.customer'));
  const readable = permitted && eligible;
  const requestable = readable && can('advice.request');
  const key = JSON.stringify([owner, id, scope]);
  const context = useRef(null), serial = useRef(0), flight = useRef(null), mounted = useRef(false);
  context.current = {key, readable, requestable};
  const [saved, setSaved] = useState({key: null, status: 'checking'});
  const state = saved.key === key ? saved : {status: 'checking'};

  const run = useCallback(async (generate = false, retry = false) => {
    const current = () => mounted.current && context.current.key === key && owner === identity()
      && context.current.readable && can('advice.read') && can('advice.customer')
      && (!generate || (context.current.requestable && can('advice.request')));
    if (!current() || flight.current?.key === key) return;
    const sequence = ++serial.current;
    flight.current = {key, sequence};
    setSaved({key, status: generate ? 'submitting' : 'checking'});
    try {
      const value = generate
        ? await api().requestBusinessAdvice('customer', id, 'overview', retry)
        : await api().getLatestBusinessAdvice('customer', id, 'overview');
      if (sequence !== serial.current || !current()) return;
      setSaved({key, ...present(value, id, !generate)});
    } catch (error) {
      if (sequence !== serial.current || !current()) return;
      // A POST response can be lost after acceptance. Discover its receipt first;
      // an error retry must not silently create another generation request.
      setSaved({key, status: 'error', error: error.message || '建议暂不可用，请重新读取。', uncertain: generate});
    } finally {
      if (flight.current?.sequence === sequence) flight.current = null;
    }
  }, [key, owner, id]);

  useEffect(() => {
    mounted.current = true;
    if (readable) run();
    return () => {mounted.current = false; serial.current++; flight.current = null;};
  }, [run, readable]);

  if (!readable) return permitted && confirmed && !eligible
    ? <Panel title="AI 经营建议"><p className="pc-muted">当前企业不适用客户经营建议。</p></Panel> : null;
  const busy = ['checking', 'submitting'].includes(state.status);
  const pending = ['queued', 'running'].includes(state.status);
  const canGenerate = requestable && ['none', 'ready', 'stale', 'failed'].includes(state.status);
  return <Panel title="AI 经营建议" note="当前客户的经营建议，供业务讨论参考"
    extra={<Button type="link" size="small" disabled={busy} onClick={() => run()}>{state.status === 'checking' ? '读取中' : pending ? '刷新进度' : '刷新已有建议'}</Button>}>
    <div className="pc-kb-advice" aria-live="polite">
      {state.status === 'checking' && <p className="pc-muted">正在读取已有建议…</p>}
      {state.status === 'submitting' && <p className="pc-muted">正在提交生成请求…</p>}
      {pending && <><Tag2 tone="primary">{state.status === 'queued' ? '排队中' : '处理中'}</Tag2><p className="pc-muted">建议正在后台生成，可刷新进度或稍后回来查看。</p></>}
      {state.status === 'none' && <p className="pc-muted">还没有生成经营建议，需要时点击下方按钮。</p>}
      {state.status === 'stale' && <p className="pc-muted">客户资料已变化，原建议已失效，需要时可重新生成。</p>}
      {['error', 'failed'].includes(state.status) && <div role="alert"><p className="pc-error">{state.error}</p>
        {state.uncertain && <p className="pc-muted">提交结果尚未确认，请先重新读取已有建议。</p>}
        {state.status === 'error' && <Button onClick={() => run()}>重新读取</Button>}
      </div>}
      {state.status === 'ready' && <><p>{state.summary}</p>
        {(state.rows || []).length > 0 && <ol>{state.rows.map((row, index) => <li key={row.id || index}><b>{row.title}</b><p>{row.detail}</p></li>)}</ol>}
        <small className="pc-muted">建议仅供参考，由业务人员判断是否采纳。{state.updatedAt ? ` · ${state.updatedAt}` : ''}</small>
      </>}
      {canGenerate && <Button onClick={() => run(true, Boolean(state.id))}>{state.status === 'failed' ? '重试生成' : state.id ? '重新生成建议' : '生成经营建议'}</Button>}
    </div>
  </Panel>;
}
