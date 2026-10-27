import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {transformSync} from 'esbuild';

const require = createRequire(import.meta.url);
const code = transformSync(fs.readFileSync(new URL('../department-ui/CustomerBattleMap.jsx', import.meta.url), 'utf8'),
  {loader:'jsx', format:'cjs'}).code;
const points = [
  {id:'a', name:'Synthetic A', potential:8, relationship:8, amountBand:'large'},
  {id:'b', name:'Synthetic B', potential:2, relationship:3, amountBand:'small'},
  {id:'c', name:'Synthetic C', potential:2, relationship:3, amountBand:'small'},
];

// Isolated component/hook contract, no browser or network. Actual CSS layout is
// verified separately on the paired page; this checks measured coordinate math.
function renderer(props = {}) {
  let cursor = 0, resize;
  const state = [], effects = [], frame = {clientWidth:600, clientHeight:400};
  const useState = initial => {const i=cursor++; if (!(i in state)) state[i]=initial;
    return [state[i], value=>{state[i]=typeof value==='function'?value(state[i]):value;}];};
  const React = {useState, useRef:value=>({current:value}), useMemo:fn=>fn(), useEffect:fn=>effects.push(fn), useLayoutEffect:fn=>effects.push(fn),
    createElement(type, props, ...children) {if (props?.ref) props.ref.current=frame; return {type,props:props || {},children};}};
  const module={exports:{}};
  vm.runInNewContext(code, {module, exports:module.exports, ResizeObserver:class {constructor(callback){resize=callback;} observe(){} disconnect(){}},
    require:name=>name==='react'?React:name==='antd'?{Button:'button',Skeleton:'skeleton'}:require(name)});
  const render=()=>{cursor=0;effects.length=0;return module.exports.default({points,thresholds:{potential:7,relationship:7},unrated:2,...props});};
  render(); effects.forEach(fn=>fn());
  return {render, size(width,height){frame.clientWidth=width;frame.clientHeight=height;resize();return render();}};
}
function nodes(tree, predicate) {
  const result=[];
  function visit(node){if (!node || typeof node!=='object') return;if (Array.isArray(node)) return node.forEach(visit);
    if (predicate(node)) result.push(node);node.children?.forEach(visit);}
  visit(tree);return result;
}

test('rectangular resizes fill both axes with only label insets; circles and fonts stay in pixels', () => {
  const r=renderer();
  for (const [width,height] of [[480,300],[700,400],[300,480]]) {
    const tree=r.size(width,height), svg=nodes(tree,n=>n.type==='svg')[0];
    assert.equal(svg.props.viewBox, `0 0 ${width} ${height}`);
    const cells=nodes(tree,n=>n.props.className==='sb-bmap-cell').map(n=>n.children[0]);
    assert.equal(cells.length,4);
    assert.equal(Math.min(...cells.map(n=>n.props.x)),40);
    assert.equal(Math.max(...cells.map(n=>n.props.x+n.props.width)),width-8);
    assert.equal(Math.min(...cells.map(n=>n.props.y)),8);
    assert.equal(Math.max(...cells.map(n=>n.props.y+n.props.height)),height-36);
    const dots=nodes(tree,n=>String(n.props.className||'').split(' ').includes('sb-bmap-point'));
    assert.equal(dots.length,2,'overlapping customers stay in one cluster');
    assert.equal(nodes(dots[0],n=>n.type==='circle').at(-1).props.r,11);
    assert.ok(nodes(tree,n=>n.type==='text' && n.props.fontSize != null).every(n=>[12,14].includes(n.props.fontSize)));
    assert.equal(nodes(tree,n=>n.props.className==='sb-bmap-axes')[0].props.fontSize,12);
  }
});

test('quadrant zoom, point/cluster selection and unrated callbacks retain their original payloads', () => {
  const events=[], r=renderer({onZoomChange:v=>events.push(['zoom',v]),onPointClick:v=>events.push(['point',v.id]),
    onClusterClick:v=>events.push(['cluster',v.map(p=>p.id).join(',')]),onUnratedClick:()=>events.push(['unrated'])});
  let tree=r.size(480,300);
  const dots=nodes(tree,n=>String(n.props.className||'').split(' ').includes('sb-bmap-point'));
  dots.forEach(n=>n.props.onClick({stopPropagation(){}}));
  nodes(tree,n=>n.props.className==='sb-bmap-unrated')[0].props.onClick();
  nodes(tree,n=>n.props.className==='sb-bmap-cell')[0].props.onClick();
  tree=r.render();assert.equal(nodes(tree,n=>n.props.className==='sb-bmap-cell').length,1);
  nodes(tree,n=>n.props.className==='sb-bmap-back')[0].props.onClick();
  assert.deepEqual(events,[['point','a'],['cluster','b,c'],['unrated'],['zoom','asset'],['zoom',null]]);
});

test('loading and empty states retain the measured frame and empty action', () => {
  const r=renderer({loading:true});assert.equal(nodes(r.size(480,300),n=>n.type==='skeleton').length,1);
  const empty=renderer({points:[],emptyText:'Synthetic empty',emptyAction:{label:'Reset',onClick(){}}}).size(480,300);
  assert.equal(nodes(empty,n=>n.type==='svg')[0].props.viewBox,'0 0 480 300');
  assert.equal(nodes(empty,n=>n.props.className==='sb-bmap-empty').length,1);
});
