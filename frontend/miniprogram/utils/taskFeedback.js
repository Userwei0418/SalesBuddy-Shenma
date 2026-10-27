function prepare(draft,versions){
 const text=String(draft.text||'').trim();if(!text||[...text].length>4000)throw Error('请填写 1–4000 字反馈内容');
 const next_step=String(draft.next_step||'').trim(),blocker=String(draft.blocker||'').trim();
 if([...next_step].length>2000||[...blocker].length>2000)throw Error('下一步和问题说明各最多 2000 字');
 const evidence_urls=(Array.isArray(draft.evidence_urls)?draft.evidence_urls:String(draft.evidence_urls||'').split(/\n/)).map(v=>String(v).trim()).filter(Boolean);
 if(evidence_urls.length>10||evidence_urls.some(url=>url.length>2000||!/^https?:\/\/[^\s]+$/i.test(url)))throw Error('成果链接请每行填写一个有效网址，最多 10 个');
 if(draft.reply_to_event_id&&draft.corrects_event_id)throw Error('一次只能回复或更正一条反馈');
 return {...versions,text,reply_to_event_id:draft.reply_to_event_id||null,corrects_event_id:draft.corrects_event_id||null,
  ...(Object.prototype.hasOwnProperty.call(versions,'checklist_revision')?{feedback_kind:draft.feedback_kind==='comment'?'comment':'progress',item_id:draft.item_id||null,next_step:next_step||null,blocker:blocker||null,evidence_urls}:{})};
}
module.exports={prepare};
