// Isolated fixture for a single manual/CUA regression: click chapter 1 while
// the sticky outline moves under the stationary pointer, then keyboard-jump.
// REPORT_OUTLINE_BASE optionally renders the fixed parent for the same check.
// No business API, saved document, model request, or production data is used.
import {build} from 'esbuild';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const variants={after:root,...(process.env.REPORT_OUTLINE_BASE?{before:process.env.REPORT_OUTLINE_BASE}:{})};
const pages=new Map();
for(const [name,source] of Object.entries(variants)){
  const built=await build({absWorkingDir:source,stdin:{resolveDir:source,loader:'jsx',contents:`
    import React from 'react';import{createRoot}from'react-dom/client';import{App}from'antd';
    import{PcProvider}from'./department-ui/pc/PcProvider.jsx';
    import DocumentReader from './department-ui/work-report/DocumentReader.jsx';
    import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';
    import './department-ui/shared.css';import './department-ui/pc/tokens.css';
    import './department-ui/pc/pc-theme.css';import './department-ui/work-report/work-report.css';
    const titles=['本周工作总结','本周实际回款','下周工作计划','下周预计回款','本周新增商机','重点商机'];
    const value={title:'合成目录定位回归',sections:titles.map((title,i)=>({id:'section-'+i,title,
      items:Array.from({length:i===0?12:2},(_,j)=>({kind:'fact',text:title+'，合成段落 '+(j+1)+'。'.repeat(2),source_refs:[],metric_refs:[]}))})),missing_data:['本例为合成材料，不连接业务服务。']};
    createRoot(document.getElementById('root')).render(<PcProvider><App>
      <header style={{height:80,padding:24}}>工作报告 · 目录回归（${name}）</header>
      <main className="department-ui" style={{height:'calc(100vh - 80px)',overflow:'auto',padding:20}}>
        <DocumentReader value={value} metrics={[]} status="private" meta="合成验证 · 未保存" periodLabel="2026-10-05—2026-10-11" scopeLabel="本人"/>
      </main></App></PcProvider>);
  `},bundle:true,write:false,outdir:'.runtime/outline-fixture',define:{'process.env.NODE_ENV':'"production"'}});
  const js=built.outputFiles.find(file=>file.path.endsWith('.js')).contents;
  const css=built.outputFiles.find(file=>file.path.endsWith('.css')).contents;
  pages.set('/'+name+'/fixture.js',{type:'text/javascript',body:js});
  pages.set('/'+name+'/fixture.css',{type:'text/css',body:css});
  pages.set('/'+name+'/',{type:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>报告目录回归</title><link rel="stylesheet" href="fixture.css"><body style="margin:0"><div id="root"></div><script src="fixture.js"></script></body></html>`});
}
const server=http.createServer((req,res)=>{
  const page=pages.get(req.url);res.writeHead(page?200:404,{'Content-Type':page?.type||'text/plain','Cache-Control':'no-store'});res.end(page?.body||'Not found');
});
server.listen(0,'127.0.0.1',()=>console.log(JSON.stringify({port:server.address().port,variants:Object.keys(variants),source_root:path.resolve(root)})));
process.on('SIGINT',()=>server.close());
