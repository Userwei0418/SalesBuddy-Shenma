import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';
import {editItem,itemSourceRefs,itemContentLength,contentProblem} from '../department-ui/work-report/content.js';
import {paragraphPages,presentationDirection} from '../department-ui/work-report/presentation-pages.js';
import {revisionText} from '../department-ui/work-report/presentation.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const compiled=buildSync({absWorkingDir:root,stdin:{contents:"export {default as Reader,reportSlides} from './department-ui/work-report/DocumentReader.jsx'; export {default as Editor} from './department-ui/work-report/DocumentEditor.jsx';",resolveDir:root},bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
const module=new Module(root+'source-notes-test.cjs');module.filename=root+'source-notes-test.cjs';module.paths=Module._nodeModulePaths(root);module._compile(compiled,module.filename);
const {Reader,Editor,reportSlides}=module.exports;
const note=index=>({title:`合成商机 ${index}`,notice_text:`贡献者：合成成员 ${index}。取数时点状态不代表本周进展；下一步日期尚未核实。来源说明仅用于核对冻结材料，不核定摘要或人工正文。`,text:`冻结事实 ${index}：当前阶段、金额和原登记计划。\n未登记不等于没有。`,source_refs:[`opportunity:synthetic-${index}`,`visit:synthetic-${index}`]});
const item=index=>({kind:'fact',text:`独立摘要 ${index}。`,source_refs:[`opportunity:synthetic-${index}`],metric_refs:[],source_notes:[note(index)]});
const content=()=>({title:'合成周报',sections:[{id:'ppl_open_50',title:'在推商机',items:Array.from({length:8},(_,index)=>item(index))}],missing_data:[]});
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,{value:content(),metrics:[],...props}));
function elements(element) {return !element || typeof element!=='object'?[]:[element,...React.Children.toArray(element.props?.children).flatMap(elements)];}

test('eight summaries stay singular while notices stay outside closed source disclosures in reading and meeting',()=>{
  const value=content(),before=JSON.stringify(value);
  const views=[render(Reader,{value}),reportSlides({value}).map(slide=>renderToStaticMarkup(slide.content)).join('')];
  for(const html of views){
    assert.equal((html.match(/class="wr-source-note"/g)||[]).length,8);
    assert.equal((html.match(/<details class="wr-source-note-details"/g)||[]).length,8);
    assert.doesNotMatch(html,/<details[^>]*open|opportunity:synthetic|visit:synthetic|已验证正文/);
    for(let index=0;index<8;index++){
      assert.equal(html.split(`独立摘要 ${index}。`).length-1,1);
      assert.equal(html.split(`冻结事实 ${index}：`).length-1,1);
      assert.ok(html.indexOf(`贡献者：合成成员 ${index}`)<html.indexOf('<details',html.indexOf(`合成商机 ${index}`)));
    }
  }
  assert.equal(JSON.stringify(value),before);
});

test('editing prose preserves notes exactly and source lookup unions main and note refs',()=>{
  const value=content(),before=JSON.stringify(value),notes=value.sections[0].items[0].source_notes;
  const edited=editItem(value,'ppl_open_50',0,'人工补充，不核定来源。'),first=edited.sections[0].items[0];
  assert.equal(first.kind,'manual');assert.deepEqual(first.source_refs,[]);assert.deepEqual(first.metric_refs,[]);
  assert.equal(first.source_notes,notes);assert.equal(JSON.stringify(first.source_notes),JSON.stringify(notes));
  assert.deepEqual(itemSourceRefs(first),notes[0].source_refs);
  assert.deepEqual(itemSourceRefs(value.sections[0].items[0]),notes[0].source_refs);
  assert.equal(JSON.stringify(value),before);assert.equal(contentProblem(edited),'');
});

test('real editor callbacks preserve notes, prevent paragraph removal and allow note-only source lookup',()=>{
  const value=editItem(content(),'ppl_open_50',0,'人工正文'),changes=[],lookups=[];
  const tree=Editor({value,onChange:next=>changes.push(next),onSources:refs=>lookups.push(refs)}),all=elements(tree);
  const input=all.find(element=>element.props?.['aria-label']==='报告正文：在推商机 1');
  input.props.onChange({target:{value:'更新人工正文'}});
  assert.deepEqual(changes[0].sections[0].items[0].source_notes,value.sections[0].items[0].source_notes);
  const remove=all.find(element=>element.props?.['aria-label']==='移除在推商机第1段');assert.equal(remove.props.disabled,true);
  all.find(element=>element.props?.children==='查看材料来源').props.onClick();
  assert.deepEqual(lookups,[note(0).source_refs]);
  const html=render(Editor,{value,onChange(){},onSources(){}});
  assert.match(html,/可编辑正文；本段附有来源说明，需保留/);
  assert.equal((html.match(/class="wr-source-note"/g)||[]).length,8);
});

test('old reports without notes preserve their rendering, editing and history shape',()=>{
  const value={title:'旧报告',sections:[{id:'old',title:'旧章节',items:[{kind:'fact',text:'旧内容。',source_refs:[],metric_refs:[]}]}]};
  assert.doesNotMatch(render(Reader,{value}),/wr-source-note|事实与来源/);
  assert.equal(revisionText(value,[]),'旧章节\n旧内容。');
  const edited=editItem(value,'old',0,'修改旧内容');assert.equal('source_notes' in edited.sections[0].items[0],false);
  for(const notes of [null,[]]){
    value.sections[0].items[0].source_notes=notes;
    assert.doesNotMatch(render(Reader,{value}),/wr-source-note|事实与来源/);
    assert.equal('source_notes' in editItem(value,'old',0,'修改').sections[0].items[0],false);
  }
});

test('notes are literal escaped text with no metric interpolation or raw refs displayed',()=>{
  const value=content();value.sections[0].items[0].source_notes[0]={...note(0),title:'<img src=x>',notice_text:'<script>notice</script>',text:'<b>plain</b> {{metric:amount}}'};
  for(const Component of [Reader,Editor]){
    const html=render(Component,{value,metrics:[{metric_id:'amount',display_value:'999'}],onChange(){}});
    assert.match(html,/&lt;img src=x&gt;/);assert.match(html,/&lt;script&gt;notice&lt;\/script&gt;/);assert.match(html,/&lt;b&gt;plain&lt;\/b&gt; \{\{metric:amount\}\}/);
    assert.doesNotMatch(html,/<img src=x>|<script>notice|opportunity:synthetic/);
  }
});

test('paragraph paging keeps notes exactly once on the first segment and leaves the source untouched',()=>{
  const original={...item(0),text:'长摘要🙂。'.repeat(600)},before=JSON.stringify(original);
  const parts=paragraphPages([original]).flat();assert.ok(parts.length>1);
  assert.equal(parts.map(part=>part.text).join(''),original.text);
  assert.deepEqual(parts[0].source_notes,original.source_notes);
  assert.ok(parts.slice(1).every(part=>!Object.hasOwn(part,'source_notes')));
  assert.equal(JSON.stringify(original),before);
  for(const control of ['summary','[data-presentation-interactive]'])
    assert.equal(presentationDirection({key:' ',target:{closest:selector=>selector.split(',').includes(control)?{}:null}}),0);
});

test('history includes notices next to prose and the complete deterministic texts in a separate appendix',()=>{
  const value=content(),history=revisionText(value,[]),appendix=history.indexOf('事实与来源（冻结材料附录）');
  assert.ok(appendix>0);
  for(let index=0;index<8;index++){
    assert.equal(history.split(`独立摘要 ${index}。`).length-1,1);
    assert.ok(history.indexOf(`贡献者：合成成员 ${index}`)<appendix);
    assert.ok(history.indexOf(`冻结事实 ${index}：`)>appendix);
    assert.equal(history.split(`冻结事实 ${index}：`).length-1,1);
  }
  assert.doesNotMatch(history,/opportunity:synthetic/);
});

test('per-item and document limits include all note text and count unicode as backend characters',()=>{
  const one={...item(0),text:'🙂'.repeat(3000),source_notes:[{...note(0),title:'🙂',notice_text:'🙂',text:'🙂'.repeat(8998)}]};
  const value={title:'限制测试',sections:[{id:'length',items:[one]}]};
  assert.equal(itemContentLength(one),12000);assert.equal(contentProblem(value),'');
  one.source_notes[0].text+='字';assert.match(contentProblem(value),/长度限制/);
  one.source_notes[0].text='字'.repeat(7664); // 10666 per item; six items = 63996.
  value.sections[0].items=Array.from({length:6},()=>structuredClone(one));
  assert.equal(contentProblem(value),'');
  value.sections[0].items[5].text+='五个额外字';assert.match(contentProblem(value),/长度限制/);
});
