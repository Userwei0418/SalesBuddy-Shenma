import React,{useId,useRef,useState} from 'react';
import {Alert,Button,Tag} from 'antd';
import {ReportStatus} from './ReportFrame.jsx';
import {ReportMetrics} from './MaterialReview.jsx';
import {contentProblem,displayItem,itemSourceRefs} from './content.js';
import SourceNotes from './SourceNotes.jsx';
import {dateLabel,metricViews} from './presentation.js';
import MeetingFactsBoard from './MeetingFactsBoard.jsx';
import {meetingView,overviewView} from './meeting-presentation.js';
import {meetingSlides} from './MeetingBoardView.jsx';
import {paragraphPages} from './presentation-pages.js';
import ReportPresentation from './ReportPresentation.jsx';
import ReviewReportContent from '../review/report/ReviewReportContent.jsx';
import {DAILY,reviewContentProblem} from '../review/report/model.js';

function ReaderItem({item,shown,onSources,busy}) {
  const kind={manual:'人工编辑',user_supplement:'用户补充',plan:'计划',suggestion:'建议'}[item.kind];
  const refs=itemSourceRefs(item);
  return <div className={`wr-reader-item${item.kind==='plan'?' is-plan':''}`}>
    {shown.missing.length>0 && <Alert type="error" title="该段引用的统计暂不可用，请重新读取报告"/>}
    {item.continued && <Tag>续页</Tag>}<p>{shown.text}</p>
    <SourceNotes notes={item.source_notes}/>
    {(kind || item.metric_refs?.length>0 || (refs.length>0 && onSources)) && <div className="wr-reader-item-meta">
      {kind && <Tag color={item.kind==='plan'?'blue':item.kind==='suggestion'?'purple':undefined}>{kind}</Tag>}
      {item.metric_refs?.length>0 && <span className="wr-muted">含系统统计</span>}
      {refs.length>0 && onSources && <Button size="small" type="link" disabled={busy} onClick={()=>onSources(refs)}>查看材料来源</Button>}
    </div>}
  </div>;
}

const sectionGuidance={
  next_week_plan:'可补充计划日期、客户或商机、负责人和下一步行动。',
  next_week_collection:'可补充预计回款日期、金额、币种及对应客户或商机；预计不代表实际到账。',
  ppl_first_30:'首次达到 30% 需要阶段变更历史佐证；记录不足时保留未知，不按 0 个处理。',
};
function EmptySection({section,missingData=[],dirty=false}) {
  return <div className="wr-reader-empty">
    <b>本节尚无正文</b>
    <p>{dirty?'当前预览尚未填写本节内容。':'报告已保存，本节尚未形成可展示的内容。'}{sectionGuidance[section.id] || '可查看材料来源，或在编辑报告中补充经确认的内容。'}</p>
    {missingData.length>0 && <details className="wr-reader-material-notes"><summary>查看本报告材料说明 · {missingData.length} 项</summary>
      <p>以下是整份报告的已记录材料缺口，供补充内容时参考。</p><ul>{missingData.map((text,index)=><li key={index}>{text}</li>)}</ul>
    </details>}
  </div>;
}

export function reportSlides({value,metrics=[],meetingFacts,overviewFacts,reviewFacts,status,meta,periodLabel,scopeLabel,schemeLabel,dirty}) {
  const frozenFacts=overviewFacts || meetingFacts;
  const slides=[{key:'cover',group:'报告概览',title:'报告概览',content:<div className="wr-presentation-cover wr-paper"><ReportStatus status={status}/>{dirty && <Tag color="orange">未保存预览</Tag>}
    <h1>{value.title}</h1><p>{[scopeLabel,periodLabel,schemeLabel].filter(Boolean).join(' · ')}</p><p>{meta}</p>
    {!frozenFacts && metrics.length>0 && <ReportMetrics items={metricViews(metrics)}/>}</div>}];
  if(frozenFacts){
    try{slides.push(...meetingSlides((overviewFacts?overviewView:meetingView)(frozenFacts),{compact:true}));}
    catch(error){slides.push({key:'facts-unavailable',group:'报告概览',title:'统计暂不可用',content:<div><Alert type="error" title={error.message}/></div>});}
  }
  const typed=value.review_content;
  if(typed){
    const problem=reviewContentProblem(typed,reviewFacts);
    if(problem || !reviewFacts)slides.push({key:'review-error',group:'AI Review',title:'Review 依据暂不可用',content:<Alert type="error" title={problem || '报告依据不可用，请重新读取'}/>});
    else if(typed.contract_version===DAILY)typed.items.forEach((item,index)=>slides.push({key:`review-daily:${index}`,group:'AI 日总结',title:`AI 日总结（${index+1}/${typed.items.length}）`,content:<ReviewReportContent content={typed} facts={reviewFacts} itemIndex={index}/>}));
    else typed.groups.forEach((group,index)=>slides.push({key:`review-weekly:${group.group_id}`,group:'指标与 KR 回顾',title:reviewFacts.groups[index].title,content:<ReviewReportContent content={typed} facts={reviewFacts} groupIndex={index}/>}));
  }
  value.sections.filter(section=>!typed || typed.contract_version!==DAILY || section.items.length>0).forEach(section=>{
    const pages=paragraphPages(section.items.map(item=>({...item,...displayItem(item,metrics)})));
    pages.forEach((items,index)=>slides.push({key:`section:${section.id}:${index}`,group:section.title,title:`${section.title}${pages.length>1?`（${index+1}/${pages.length}）`:''}`,
      content:<div className="wr-paper wr-reader-items">{items.length?items.map((item,i)=><ReaderItem key={i} item={item} shown={item}/>):<EmptySection section={section} missingData={value.missing_data} dirty={dirty}/>}</div>}));
  });
  if(value.missing_data?.length)slides.push({key:'missing',group:'材料说明',title:'材料说明',content:<div className="wr-paper"><Alert type="warning" title="材料说明" description={<ul>{value.missing_data.map((text,index)=><li key={index}>{text}</li>)}</ul>}/></div>});
  return slides;
}

// The reading view uses the saved revision and its authorized snapshot metrics.
// It never summarizes prose, guesses amounts, or requests additional sources.
export default function DocumentReader(props) {
  const {value,metrics=[],meetingFacts,overviewFacts,reviewFacts,status,meta,periodLabel,scopeLabel,schemeLabel,dirty,busy,saveBusy,
    onEdit,onSave,onShare,onExport,onHistory,onSources,presenting=false,onExit}=props;
  const [meeting,setMeeting]=useState(null);
  const [selectedSection,setSelectedSection]=useState(null);
  const frozenFacts=overviewFacts || meetingFacts;
  const typed=value.review_content,readSections=value.sections.filter(section=>!typed || typed.contract_version!==DAILY || section.items.length>0);
  const reviewProblem=typed?(reviewContentProblem(typed,reviewFacts) || (!reviewFacts?'报告依据不可用':'')):null;
  const prefix=useId(),sections=useRef([]);
  const unavailable=Boolean(reviewProblem) || value.sections.some(section=>section.items.some(item=>displayItem(item,metrics).missing.length));
  const jump=index=>{const section=sections.current[index];section?.scrollIntoView({block:'start'});section?.focus({preventScroll:true});};
  return <>
    {presenting && <div className="wr-reader-presentation-actions"><span>{scopeLabel} · {periodLabel}</span><Button onClick={onExit}>退出会议模式 · Esc</Button></div>}
    <article className={`wr-reader${presenting?' is-presenting':''}`} aria-label="报告阅读视图">
    <header className="wr-paper wr-reader-hero">
      <div className="wr-reader-topline"><div className="wr-toolbar"><ReportStatus status={status}/>{dirty && <Tag color="orange">未保存预览</Tag>}<span className="wr-muted">{meta}</span></div>
        <div className="wr-toolbar">
          {!presenting && <Button disabled={busy} onClick={()=>setMeeting({slides:reportSlides(props),title:value.title,subtitle:[scopeLabel,periodLabel,meta,frozenFacts?.data_as_of?`材料准备于 ${dateLabel(frozenFacts.data_as_of)}`:null].filter(Boolean).join(' · ')})}>会议模式</Button>}
          {onSources && <Button size="small" disabled={busy} onClick={onSources}>查看来源</Button>}
          {onHistory && <Button size="small" disabled={busy} onClick={onHistory}>历史版本</Button>}
          {onExport && <Button size="small" disabled={dirty || busy || unavailable} onClick={onExport}>复制与下载</Button>}
          {onShare && <Button size="small" disabled={dirty || busy || unavailable} onClick={onShare}>{status==='shared'?'管理共享':'确认并共享'}</Button>}
          {onEdit && <Button type="primary" disabled={busy} onClick={onEdit}>{dirty?'继续编辑':'编辑报告'}</Button>}
        </div>
      </div>
      <h1>{value.title}</h1>
      <div className="wr-reader-context">
        {periodLabel && <span><b>报告周期</b>{periodLabel}</span>}
        {scopeLabel && <span><b>报告范围</b>{scopeLabel}</span>}
        {schemeLabel && <span><b>报告方案</b>{schemeLabel}</span>}
      </div>
    </header>
    {dirty && <Alert type="info" title="正在预览尚未保存的修改" description="切回编辑会保留当前内容；保存前不会更新已共享的报告。"
      action={onSave && <Button disabled={busy || unavailable || Boolean(contentProblem(value))} loading={saveBusy} onClick={onSave}>保存草稿</Button>}/>}
    {frozenFacts?<MeetingFactsBoard facts={frozenFacts} title={value.title} presenting={presenting} hideMeetingButton/>:metrics.length>0 && <div className="wr-reader-overview"><ReportMetrics items={metricViews(metrics)}/></div>}
    {typed && <ReviewReportContent content={typed} facts={reviewFacts}/>}
    <div className="wr-reader-layout">
      <div className="wr-reader-sections">
        {readSections.map((section,index)=><section className="wr-paper wr-reader-section" key={section.id} tabIndex={-1}
          ref={element=>{sections.current[index]=element;}} onFocus={()=>setSelectedSection(section.id)} aria-labelledby={`${prefix}-section-${index}`}>
          <header className="wr-reader-section-head"><span className="wr-reader-number" aria-hidden="true">{String(index+1).padStart(2,'0')}</span><h2 id={`${prefix}-section-${index}`}>{section.title}</h2></header>
          {section.items.length?<div className="wr-reader-items">{section.items.map((item,itemIndex)=><ReaderItem key={itemIndex} item={item} shown={displayItem(item,metrics)} onSources={onSources} busy={busy}/>)}</div>:<EmptySection section={section} missingData={value.missing_data} dirty={dirty}/>}
        </section>)}
        {value.missing_data?.length>0 && <Alert type="warning" title="材料说明" description={<ul>{value.missing_data.map((text,index)=><li key={index}>{text}</li>)}</ul>}/>}
      </div>
      {readSections.length>0 && <aside className="wr-reader-aside"><nav className="wr-paper wr-reader-outline" aria-label="报告章节目录"><h2>报告目录</h2>
        {readSections.map((section,index)=><button type="button" key={section.id} aria-current={selectedSection===section.id?'location':undefined} onClick={()=>jump(index)}><span aria-hidden="true">{String(index+1).padStart(2,'0')}</span>{section.title}</button>)}
      </nav></aside>}
    </div>
  </article>
  {!presenting && <ReportPresentation open={Boolean(meeting)} {...meeting} onClose={()=>setMeeting(null)}/>}
  </>;
}
