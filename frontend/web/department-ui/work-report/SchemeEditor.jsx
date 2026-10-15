import React from 'react';
import {Alert, Button, Checkbox, Input, Select} from 'antd';
import {PERIOD_CHOICES} from './state.js';
import {SALES_WEEK_FACTS} from './sales-week.js';

// Controlled editor model. HTTP/domain serialization belongs to the report service adapter.
export default function SchemeEditor({value, onChange, choices, busy, error, onSave, onCopy, readOnly = false}) {
  const patch = fields => onChange({...value, ...fields});
  const style = fields => patch({style: {...value.style, ...fields}});
  const sectionPatch = (id, fields) => patch({sections:value.sections.map(item=>item.id===id?{...item,...fields}:item)});
  const references = value.references || [];
  const salesWeekly=value.baseConfig?.facts_contract===SALES_WEEK_FACTS;
  const invalid = !value.name.trim() || !value.style.audience.trim() || !value.sections.some(item=>item.enabled) || value.sections.some(item=>!item.title.trim()) || references.reduce((sum,item)=>sum+item.text.length,0)>60000;
  const move = (from, to) => {
    const sections = [...value.sections];
    sections.splice(to, 0, sections.splice(from, 1)[0]);
    patch({sections});
  };
  return <section className="wr-panel wr-scheme-form" aria-label="报告方案配置">
    <h2>{readOnly ? '方案详情' : '编辑报告方案'}</h2>
    {error && <Alert className="wr-coverage" type="error" title={error} showIcon />}
    <fieldset disabled={busy || readOnly} style={{border:0,padding:0,margin:0,minWidth:0}}>
      <div className="wr-field"><label htmlFor="wr-scheme-name">方案名称</label><Input id="wr-scheme-name" value={value.name} maxLength={100} onChange={event => patch({name:event.target.value})} disabled={busy || readOnly} /></div>
      <div className="wr-field"><label>使用哪些数据</label><Checkbox.Group aria-label="方案数据来源" options={choices.sources} value={value.sources} onChange={sources=>patch({sources})} disabled={busy || readOnly} /></div>
      <div className="wr-field"><label>默认报告周期</label><Select aria-label="默认报告周期" value={value.period} options={salesWeekly?PERIOD_CHOICES.filter(item=>item.value==='week'):PERIOD_CHOICES} onChange={period=>patch({period})} disabled={busy || readOnly} />{salesWeekly && <small>销售六节按自然周取材；月报、季报请新建通用方案。</small>}</div>
      <div className="wr-field"><label>报告章节与顺序</label><div className="wr-sections">
        {value.sections.map((section,index)=><div className="wr-section-config" key={section.id}>
          <div className="wr-section-order">
            <Checkbox aria-label={`启用章节 ${index+1}`} checked={section.enabled} onChange={event=>sectionPatch(section.id,{enabled:event.target.checked})} disabled={busy || readOnly} />
            <Input aria-label={`章节 ${index+1} 标题`} value={section.title} maxLength={240} onChange={event=>sectionPatch(section.id,{title:event.target.value})} disabled={busy || readOnly} />
            {!readOnly && <div className="wr-section-actions"><Button size="small" type="text" disabled={busy || index===0} aria-label={`上移${section.title}`} onClick={()=>move(index,index-1)}>↑</Button><Button size="small" type="text" disabled={busy || index===value.sections.length-1} aria-label={`下移${section.title}`} onClick={()=>move(index,index+1)}>↓</Button><Button size="small" type="text" disabled={busy || value.sections.length===1} aria-label={`删除章节 ${index+1}`} onClick={()=>patch({sections:value.sections.filter(item=>item.id!==section.id)})}>删除</Button></div>}
          </div>
          <Input.TextArea aria-label={`章节 ${index+1} 写作要求`} placeholder="这一节希望写什么（选填）" value={section.instructions} maxLength={2000} autoSize={{minRows:1,maxRows:4}} onChange={event=>sectionPatch(section.id,{instructions:event.target.value})} disabled={busy || readOnly} />
        </div>)}
        {!readOnly && <Button disabled={busy || value.sections.length>=20} onClick={()=>patch({sections:[...value.sections,{id:`section_${crypto.randomUUID().replaceAll('-','')}`,title:'新章节',enabled:true,instructions:''}]})}>添加章节</Button>}
      </div></div>
      <div className="wr-field-grid">
        <div className="wr-field"><label>组织方式</label><Select aria-label="报告组织方式" value={value.groupBy} options={choices.groupBy} onChange={groupBy=>patch({groupBy})} disabled={busy || readOnly} /></div>
        <div className="wr-field"><label htmlFor="wr-audience">汇报对象</label><Input id="wr-audience" value={value.style.audience} maxLength={120} placeholder="例如：直属负责人、区域销售团队" onChange={event=>style({audience:event.target.value})} disabled={busy || readOnly} /></div>
        <div className="wr-field"><label>表达风格</label><Select aria-label="表达风格" value={value.style.tone} options={choices.tone} onChange={tone=>style({tone})} disabled={busy || readOnly} /></div>
        <div className="wr-field"><label>报告篇幅</label><Select aria-label="报告篇幅" value={value.style.length} options={choices.length} onChange={length=>style({length})} disabled={busy || readOnly} /></div>
        <div className="wr-field"><label>叙述口吻</label><Select aria-label="叙述口吻" value={value.style.voice} options={choices.voice} onChange={voice=>style({voice})} disabled={busy || readOnly} /></div>
        <div className="wr-field"><label>语言</label><Select aria-label="报告语言" value={value.style.language} options={choices.language} onChange={language=>style({language})} disabled={busy || readOnly} /></div>
      </div>
      <div className="wr-field"><label htmlFor="wr-focus">重点与写作要求</label><Input.TextArea id="wr-focus" value={value.style.focus} rows={3} maxLength={4819} placeholder="每行一条，例如：突出客户推进结果；问题和需要协调的事项单独列出。" onChange={event=>style({focus:event.target.value})} disabled={busy || readOnly} /><small>最多 20 条，每条 240 字。</small></div>
      <div className="wr-field"><label>参考范文</label><small>只参考结构与表达，范文中的业务内容不作为本期事实。最多 5 篇，每篇 20000 字，合计 60000 字。</small>
        {references.map((reference,index)=><div className="wr-reference" key={reference.id}>
          <div className="wr-source-heading"><label htmlFor={`wr-reference-${reference.id}`}>参考范文 {index+1}</label>{!readOnly && <Button size="small" disabled={busy} onClick={()=>patch({references:references.filter(item=>item.id!==reference.id)})}>移除范文 {index+1}</Button>}</div>
          <Input.TextArea id={`wr-reference-${reference.id}`} value={reference.text} rows={5} maxLength={20000} placeholder="粘贴希望参考的报告写法" onChange={event=>patch({references:references.map(item=>item.id===reference.id?{...item,text:event.target.value}:item)})} disabled={busy || readOnly} />
        </div>)}
        {!readOnly && <Button disabled={busy || references.length>=5} onClick={()=>patch({references:[...references,{id:crypto.randomUUID(),version_no:1,text:''}]})}>添加参考范文</Button>}
      </div>
    </fieldset>
    <footer className="wr-form-actions">
      {onCopy && <Button onClick={onCopy} disabled={busy}>另存为我的方案</Button>}
      {!readOnly && <Button type="primary" onClick={onSave} loading={busy} disabled={invalid}>保存方案</Button>}
    </footer>
  </section>;
}
