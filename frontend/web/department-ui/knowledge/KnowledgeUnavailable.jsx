import React from 'react';

export default function KnowledgeUnavailable({title = '知识库'}) {
  return <main className="pc-page" style={{padding:24}}>
    <section style={{padding:24, background:'#fff', border:'1px solid #e1e7f0', borderRadius:12}}>
      <h1 style={{fontSize:24, margin:'0 0 16px'}}>{title}</h1>
      <p role="status" style={{margin:0, color:'#65758c'}}>暂不能使用</p>
    </section>
  </main>;
}
