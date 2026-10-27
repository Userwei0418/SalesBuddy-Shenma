import React from 'react';
import {Alert, Button, Input, Spin, Tag} from 'antd';
import {ReportStatus} from './ReportFrame.jsx';
import {contentProblem, displayItem, editItem, itemSourceRefs} from './content.js';
import SourceNotes from './SourceNotes.jsx';
import GenerationNotice from './GenerationNotice.jsx';
import ReviewReportContent from '../review/report/ReviewReportContent.jsx';
import {reviewContentProblem,missingLabel} from '../review/report/model.js';

export function RunProgress({label, description, onCancel, cancelling}) {
  return <div className="wr-panel wr-run-progress" role="status"><Spin size="small" /><div className="wr-run-copy"><b>{label || '正在生成报告'}</b><p>{description || '报告在后台生成，材料较多时需要更长时间。可先浏览其他页面，稍后从工作报告继续查看；完成后保存为个人草稿。'}</p></div>{onCancel && <Button loading={cancelling} onClick={onCancel}>取消生成</Button>}</div>;
}

export default function DocumentEditor({value, metrics = [], reviewFacts, onChange, status = 'draft', dirty, busy, saveBusy=busy, error, readOnly, meta, periodLabel, scopeLabel, onSave, onRewrite, onShare, onExport, onHistory, onSources, onPreview, generationDocument, onRetryGeneration}) {
  const problem=contentProblem(value);
  const unavailableMetrics=Boolean(value.review_content && (!reviewFacts || reviewContentProblem(value.review_content,reviewFacts))) || value.sections.some(section=>section.items.some(item=>displayItem(item,metrics).missing.length>0));
  const patchItems=(sectionId,update)=>onChange({...value,sections:value.sections.map(section=>section.id===sectionId?{...section,items:update(section.items)}:section)});
  return <section className="wr-paper" aria-label="报告编辑器">
    <header className="wr-paper-header"><div><ReportStatus status={status} />{dirty && <Tag color="orange">尚未保存</Tag>}<span className="wr-muted">{[periodLabel,scopeLabel,meta].filter(Boolean).join(' · ')}</span></div><div className="wr-toolbar">
      {onSources && <Button size="small" onClick={onSources}>查看来源</Button>}
      {onHistory && <Button size="small" onClick={onHistory}>历史版本</Button>}
      {onExport && <Button size="small" disabled={dirty || busy || unavailableMetrics} onClick={onExport}>复制与下载</Button>}
      {onPreview && <Button disabled={busy} onClick={onPreview}>{dirty?'预览报告':'阅读报告'}</Button>}
    </div></header>
    <GenerationNotice document={generationDocument} onRetry={onRetryGeneration} busy={busy || dirty}/>
    {error && <Alert type="error" showIcon title={error} />}
    <div className="wr-editor-body">
      <Input className="wr-editor-title" variant="borderless" aria-label="报告标题" value={value.title} maxLength={200} onChange={event=>onChange({...value,title:event.target.value})} readOnly={readOnly} disabled={busy} />
      {value.review_content && <ReviewReportContent compact content={value.review_content} facts={reviewFacts}/>}
      {value.sections.map(section=><section className="wr-editor-section" key={section.id} aria-label={section.title}>
        <header className="wr-editor-section-head"><h3>{section.title}</h3>{onRewrite && !readOnly && <Button size="small" disabled={busy || dirty} onClick={()=>onRewrite([section.id])}>重写本节</Button>}</header>
        {section.items.map((item,index)=>{
          const shown=displayItem(item,metrics);
          const refs=itemSourceRefs(item),hasNotes=Boolean(item.source_notes?.length);
          return <div className="wr-editor-paragraph" key={index}>
            {shown.missing.length>0 && <Alert type="error" title="该段引用的统计暂不可用，请重新读取报告" />}
            <Input.TextArea aria-label={`报告正文：${section.title} ${index+1}`} value={shown.text} onChange={event=>onChange(editItem(value,section.id,index,event.target.value))} readOnly={readOnly} disabled={busy || shown.missing.length>0} autoSize={{minRows:2,maxRows:16}} maxLength={12000} />
            <SourceNotes notes={item.source_notes}/>
            <div className="wr-editor-paragraph-meta"><div className="wr-toolbar">
              {item.kind==='manual' && <Tag>人工编辑</Tag>}
              {item.kind==='user_supplement' && <Tag>用户补充</Tag>}
              {item.kind==='plan' && <Tag color="blue">计划</Tag>}
              {item.kind==='suggestion' && <Tag color="purple">建议</Tag>}
              {refs.length>0 && onSources && <Button size="small" type="link" onClick={()=>onSources(refs)}>查看材料来源</Button>}
              {item.metric_refs?.length>0 && <span className="wr-muted">含系统统计</span>}
            </div>{!readOnly && <div className="wr-editor-remove"><Button type="text" size="small" disabled={busy || hasNotes} aria-label={`移除${section.title}第${index+1}段`} onClick={()=>patchItems(section.id,items=>items.filter((_,i)=>i!==index))}>移除</Button>{hasNotes && <span className="wr-muted">可编辑正文；本段附有来源说明，需保留。</span>}</div>}</div>
          </div>;
        })}
        {!readOnly && <Button size="small" type="dashed" disabled={busy || section.items.length>=200} onClick={()=>patchItems(section.id,items=>[...items,{kind:'manual',text:'',source_refs:[],metric_refs:[]}])}>添加段落</Button>}
      </section>)}
      {value.missing_data?.length>0 && <Alert type="warning" title="材料说明" description={<ul>{value.missing_data.map((text,index)=><li key={index}>{missingLabel(text)}</li>)}</ul>} />}
    </div>
    <footer className="wr-paper-header">
      <div className="wr-toolbar">{onRewrite && !readOnly && <Button disabled={busy || dirty} onClick={()=>onRewrite(value.sections.map(section=>section.id))}>调整表达</Button>}<span className="wr-muted">{readOnly ? '只读报告' : dirty && problem ? problem : '确认后再共享，修改会保留历史版本'}</span></div>
      <div className="wr-toolbar">{onShare && <Button disabled={busy || dirty || unavailableMetrics} onClick={onShare}>{status==='shared'?'管理共享':'确认并共享'}</Button>}{onSave && !readOnly && <Button type="primary" aria-label="保存草稿" aria-busy={Boolean(saveBusy)} disabled={busy || !dirty || Boolean(problem) || unavailableMetrics} loading={saveBusy} onClick={onSave}>保存草稿</Button>}</div>
    </footer>
  </section>;
}

export function SourceList({items, loading, error, onRetry, onOpen, hasMore, onLoadMore}) {
  if (error) return <Alert type="error" title={error} action={onRetry && <Button onClick={onRetry}>重新读取</Button>} />;
  return <div className="wr-source-list">
    {items.map(source=><article className="wr-source" key={source.ref}>
      <div className="wr-source-heading"><strong>{source.title}</strong>{source.canOpen && onOpen && <Button type="link" size="small" onClick={()=>onOpen(source)}>原记录</Button>}</div>
      <div className="wr-source-meta">{[source.kindLabel,source.owner,source.date].filter(Boolean).join(' · ')}</div>
      <p>{source.text}</p>
    </article>)}
    {!loading && !items.length && <p className="wr-muted">没有可展示的来源记录</p>}
    {loading && <Spin size="small" />}
    {hasMore && <Button onClick={onLoadMore} loading={loading}>加载更多来源</Button>}
  </div>;
}
