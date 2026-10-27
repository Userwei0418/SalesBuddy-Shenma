import {reviewContentProblem,reportText} from '../review/report/model.js';
// Human edits drop main-text claims, but retain independent backend source notes.
// Notes describe frozen materials and do not verify the adjacent prose.
const metricPattern = /\{\{metric:([a-z][a-z0-9_.-]{0,95})\}\}/g;
export function displayItem(item, metrics) {
  const values = new Map(metrics.map(metric=>[metric.metric_id,metric.display_value]));
  const missing = [];
  const text = item.text.replace(metricPattern,(_,id)=>{
    if (!values.has(id)) {missing.push(id);return '〔统计暂不可用〕';}
    return values.get(id);
  });
  return {text:['manual','user_supplement'].includes(item.kind)?text:reportText(text),missing:[...new Set(missing)]};
}

export function editItem(content, sectionId, index, text) {
  return {...content,sections:content.sections.map(section=>section.id!==sectionId?section:{...section,
    items:section.items.map((item,i)=>i!==index?item:{kind:'manual',text,source_refs:[],metric_refs:[],
      ...(item.source_notes?.length?{source_notes:item.source_notes}:{})}),
  })};
}

export function itemSourceRefs(item) {
  return [...new Set([...(item.source_refs || []),...(item.source_notes || []).flatMap(note=>note.source_refs)])];
}

export function itemContentLength(item) {
  return Array.from(item.text).length+(item.source_notes || []).reduce((total,note)=>
    total+Array.from(note.title).length+Array.from(note.notice_text).length+Array.from(note.text).length,0);
}

export function contentProblem(content) {
  if (!content.title.trim()) return '请填写报告标题';
  const items=content.sections.flatMap(section=>section.items);
  if (content.review_content) {const problem=reviewContentProblem(content.review_content);if(problem)return problem;}
  if (!items.length && !content.review_content) return '请至少保留一段报告正文';
  if (items.some(item=>!item.text.trim())) return '请填写或移除空白段落';
  if (items.some(item=>item.kind==='manual' && item.text.includes('{{metric:'))) return '人工段落请直接填写文字，不使用系统统计占位符';
  if (items.some(item=>itemContentLength(item)>12000) || items.reduce((length,item)=>length+itemContentLength(item),0)>64000) return '报告正文及来源说明超出长度限制，请精简正文后保存';
  return '';
}
