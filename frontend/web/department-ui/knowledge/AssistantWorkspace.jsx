import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Select} from 'antd';
import {Input} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {Empty2, LoadState, Panel} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {newRequestKey} from './model.js';
import {errorText} from './service.js';
import {ProviderNotice, SourcePicker} from './FlowControls.jsx';
import {composeKnowledgeQuestion} from './flowModel.js';
import './flows.css';
export function KnowledgeAssistant({service, onOpen, onGenerate}) {
  const actor = identity(), flight = useRef(false);
  const status = useKnowledgeRead(() => service.status(), [service]);
  const [question, setQuestion] = useState(''), [sources, setSources] = useState([]), [busy, setBusy] = useState(false), [error, setError] = useState(''), [answer, setAnswer] = useState(null);
  const [context, setContext] = useState(''), [purpose, setPurpose] = useState('对客沟通'), [askedQuestion, setAskedQuestion] = useState('');
  const available = status.value?.retrieval?.status === 'available';
  // Never feed cached answers/history back as evidence; the backend freezes a fresh scope.
  const ask = async () => {
    if (flight.current || !available || !question.trim() || !sources.length || actor !== identity()) return;
    flight.current = true; setAskedQuestion(question.trim()); setBusy(true); setError(''); setAnswer(null);
    try {
      const value = await service.ask({question: composeKnowledgeQuestion(question, context, purpose), material_ids: sources}, newRequestKey());
      if (actor !== identity()) return;
      if (!['answered', 'no_evidence'].includes(value?.status)) throw Error('知识助手尚未返回可核验的回答');
      if (value.status === 'answered' && (!value.answer || !value.citations?.length || value.citations.some(row => !row.material_id || !row.version_id || !row.location || !sources.includes(row.material_id)))) throw Error('回答缺少当前材料版本与出处，暂不展示');
      setAnswer(value);
    } catch (failure) {if (actor === identity()) setError(errorText(failure));}
    finally {flight.current = false; if (actor === identity()) setBusy(false);}
  };
  useEffect(() => { const clear = () => {if (document.visibilityState === 'hidden') setAnswer(null);}; document.addEventListener('visibilitychange', clear); return () => document.removeEventListener('visibilitychange', clear); }, []);
  return <LoadState loading={status.loading} error={status.error} onRetry={status.reload}>
    <ProviderNotice value={status.value?.retrieval} label="知识检索" />
    <div className="kb-flow-context">
      <label>客户 / 商机背景<Input aria-label="客户或商机背景" placeholder="选填：本次沟通的客户与业务背景" value={context} maxLength={400} disabled={busy} onChange={event => {setContext(event.target.value); setAnswer(null);}} /></label>
      <label>参考资料<SourcePicker service={service} values={sources} onChange={value => {setSources(value); setAnswer(null);}} enabled={available && !busy} /></label>
      <label>本次用途<Select aria-label="本次用途" value={purpose} disabled={busy} options={['对客沟通', '内部准备', '方案整理'].map(value => ({value, label: value}))} onChange={value => {setPurpose(value); setAnswer(null);}} /></label>
    </div>
    <p className="pc-muted">背景由你填写，随本次问题提交；不会自动读取客户或商机记录。回答依据仅限所选资料。</p>
    <Panel title="围绕资料提问" note="仅使用当前账号有权访问的资料" bodyClassName="kb-flow-conversation">
      {error && <Alert type="error" title={error} />}
      {(busy || answer) && <div className="kb-flow-question">{askedQuestion}</div>}
      {busy ? <p role="status">正在查找资料依据…</p> : answer?.status === 'no_evidence' ? <Empty2 title="当前授权范围内未找到依据" description="可调整问题或参考资料后再试。" /> : answer ? <>
        <div className="kb122-answer">{answer.answer}</div>
        <div className="kb-flow-citations">{answer.citations.map((row, index) => <article key={`${row.material_id}:${row.version_id}:${index}`}><button className="pc-link" onClick={() => onOpen(row.material_id, row.version_id)}>[{index + 1}] {row.material_name || '引用资料'} · V{row.version_no || row.version_id}</button><p>{row.location}</p>{row.quote && <blockquote>{row.quote}</blockquote>}</article>)}</div>
        {onGenerate && <Button onClick={() => onGenerate([...new Set(answer.citations.map(row => row.material_id))])}>基于引用资料生成材料</Button>}
      </> : <Empty2 title="选择参考资料，开始一次有依据的沟通" description={available ? '可以询问资料内容、准备沟通提纲，或比较方案。' : '检索服务尚未就绪；仍可在资料库中浏览和分享原文件。'} />}
      <div className="kb-flow-composer"><Input.TextArea aria-label="知识问题" rows={3} value={question} maxLength={3000} onChange={event => setQuestion(event.target.value)} placeholder="问资料细节、整理沟通要点，或描述需要的内容…" disabled={!available || busy} />
        <div className="kb-flow-toolbar"><small className="pc-muted">选择资料后发送，引用随回答展示。</small><Button aria-label="发送" type="primary" onClick={ask} loading={busy} disabled={!available || !question.trim() || !sources.length}>发送</Button></div>
      </div>
    </Panel>
  </LoadState>;
}
