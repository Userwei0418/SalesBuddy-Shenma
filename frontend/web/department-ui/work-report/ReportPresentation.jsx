import React,{useEffect,useRef,useState} from 'react';
import {Button,Modal,Select} from 'antd';
import {presentationDirection} from './presentation-pages.js';
import './report-presentation.css';

// Both live facts and saved reports provide a frozen, read-only deck on entry.
// This shell has no API, generation, saving or sharing side effects.
export default function ReportPresentation({open,slides=[],title,subtitle,onClose}) {
  const [index,setIndex]=useState(0),body=useRef(null);
  useEffect(()=>{if(open)setIndex(0);},[open]);
  const selected=Math.min(index,Math.max(0,slides.length-1)),slide=slides[selected];
  const move=next=>{
    const destination=Math.max(0,Math.min(slides.length-1,next));
    if(destination===selected)return;
    const active=body.current?.ownerDocument?.activeElement;
    // A control inside the outgoing slide will unmount. Return its focus to the
    // stable reading surface without stealing focus from the footer controls.
    if(active && active!==body.current && body.current?.contains(active))body.current.focus({preventScroll:true});
    setIndex(destination);
  };
  useEffect(()=>{body.current?.scrollTo?.({top:0});},[selected]);
  const groups=[];
  slides.forEach((item,i)=>{
    const name=item.group || '报告章节';
    let group=groups.find(row=>row.label===name);
    if(!group){group={label:name,options:[]};groups.push(group);}
    group.options.push({value:i,label:item.title});
  });
  return <Modal open={open && Boolean(slide)} onCancel={onClose} footer={null} closable={false} keyboard mask={{closable:false}}
    width="100vw" className="wr-presentation-modal" style={{top:0,paddingBottom:0,maxWidth:'100vw'}} destroyOnHidden title="工作报告会议模式"
    afterOpenChange={visible=>{if(visible)body.current?.focus({preventScroll:true});}}>
    <div className="wr-presentation" onKeyDown={event=>{const direction=presentationDirection(event);if(direction){event.preventDefault();move(selected+direction);}}}>
      <header className="wr-presentation-header"><div><span>{title}</span><h2>{slide?.title}</h2><p>{subtitle}</p></div>
        <Button onClick={onClose}>退出会议模式 · Esc</Button></header>
      <main className="wr-presentation-body" ref={body} tabIndex={0} aria-label={slide?.title}><React.Fragment key={slide?.key}>{slide?.content}</React.Fragment></main>
      <footer className="wr-presentation-footer"><Select aria-label="跳转章节或成员" value={selected} options={groups} onChange={move} popupMatchSelectWidth={false}/>
        <span className="wr-presentation-shortcut">← → 翻页 · Esc 退出</span>
        <nav aria-label="会议翻页"><Button disabled={selected===0} onClick={()=>move(selected-1)}>上一页</Button>
          <span role="status" aria-live="polite">{selected+1} / {slides.length}</span>
          <Button type="primary" disabled={selected===slides.length-1} onClick={()=>move(selected+1)}>下一页</Button></nav>
      </footer>
    </div>
  </Modal>;
}
