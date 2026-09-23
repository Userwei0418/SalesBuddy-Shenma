const test = require('node:test');
const assert = require('node:assert/strict');
const {filterCustomers} = require('../miniprogram/utils/customerMap');
const {plotAxis} = require('../miniprogram/utils/quadrant');
test('quadrant composes with search and resets to all without changing data', () => {
  const rows=[{id:'1',name:'甲',owner:'张',quadrant:'主攻区',level:'Tier-1'},{id:'2',name:'乙',owner:'张',quadrant:'客户资产',level:'Tier-1'}];
  assert.deepEqual(filterCustomers(rows,{quadrant:'主攻区',keyword:'张',levels:['Tier-1']}).map(x=>x.id),['1']);
  assert.equal(filterCustomers(rows,{quadrant:'主攻区',keyword:'乙'}).length,0);
  assert.equal(filterCustomers(rows,{quadrant:'all'}).length,2);
});
test('zoom expands both low and high intervals and honors saved thresholds', () => {
  const policy={definition:{potential_threshold:60,relationship_threshold:80,inclusive:false}};
  const c={potential:60,relationship:90,quadrant_policy:policy};
  assert.equal(plotAxis(c,'potential',0,true),94);
  assert.equal(plotAxis(c,'relationship',0,true),50);
  assert.equal(c.potential,60);
  assert.equal(c.relationship,90);
  assert.equal(plotAxis({potential:70},'potential',0,true),6);
  assert.equal(plotAxis({potential:100},'potential',20,true),96);
  assert.equal(plotAxis({potential:0},'potential',-20,true),4);
  assert.ok(plotAxis({potential:35},'potential',0,true)>plotAxis({potential:35},'potential'));
});

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function mapPage() {
  const filename = path.resolve(__dirname, '../miniprogram/pages/customers/index.js');
  let definition;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page:value => definition=value,
    require:name => require(path.resolve(path.dirname(filename), name)),
    getApp:() => ({globalData:{session:{role:'manager'}}}),
  });
  const page = {...definition, data:JSON.parse(JSON.stringify(definition.data)),
    setData(value, callback) { Object.assign(this.data,value); if(callback)callback(); }};
  page.visibleCustomers = ['主攻区','客户资产','见单打单','客户资源'].map((quadrant,i)=>({
    id:String(i),name:'测试客户'+i,owner:'张',owner_team_id:'t',owner_user_ref_id:'u',
    quadrant,potential:i<2?85:35,relationship:i%2?85:35,level:'Tier-1',acv_amount:100,
    agentPlanSegment:'current_year',
  }));
  return page;
}
const touch = (x,y) => ({touches:[{clientX:x,clientY:y}]});
function tap(page, quadrant) { page.tapMapQuadrant({currentTarget:{dataset:{quadrant}}}); }
for (const quadrant of ['主攻区','客户资产','见单打单','客户资源']) {
  test('blank tap zooms '+quadrant+' and shares filtered customers with the list',()=>{
    const page=mapPage();
    Object.assign(page.data,{role:'manager',selectedTeam:'t',selectedMember:'u',keyword:'测试',
      mapSelectedLevels:['Tier-1'],mapPlanSegment:'current_year'});
    page.applyFilters();
    const before=page.data.plotCustomers.find(c=>c.quadrant===quadrant).plotStyle;
    page.startMapTouch(touch(100,100)); page.moveMapTouch(touch(102,101)); tap(page,quadrant);
    assert.equal(page.data.mapQuadrant,quadrant);
    assert.equal(page.data.quadrantOptions[page.data.quadrantIndex].value,quadrant);
    assert.equal(page.data.customers.length,1);
    assert.deepEqual(Array.from(page.data.plotCustomers,c=>c.id),Array.from(page.data.customers,c=>c.id));
    assert.notEqual(page.data.plotCustomers[0].plotStyle,before);
    assert.equal(page.data.keyword,'测试'); assert.equal(page.data.selectedTeam,'t');
    assert.equal(page.data.selectedMember,'u'); assert.equal(page.data.mapPlanSegment,'current_year');
    assert.deepEqual(page.data.mapSelectedLevels,['Tier-1']);
    tap(page,'客户资源'); assert.equal(page.data.mapQuadrant,quadrant,'zoomed map cannot select a second quadrant');
    page.startMapTouch(touch(100,100)); tap(page,'all');
    assert.equal(page.data.mapQuadrant,'all'); assert.equal(page.data.quadrantIndex,0);
    assert.equal(page.data.customers.length,4); assert.equal(page.data.plotCustomers.length,4);
    assert.equal(page.data.selectedTeam,'t'); assert.equal(page.data.keyword,'测试');
  });
}
test('blank tap keeps an empty quadrant empty and does not reset other filters',()=>{
  const page=mapPage(); page.data.keyword='不存在';
  page.startMapTouch(touch(10,10)); tap(page,'客户资产');
  assert.equal(page.data.mapQuadrant,'客户资产');
  assert.equal(page.data.customers.length,0); assert.equal(page.data.plotCustomers.length,0);
  page.startMapTouch(touch(10,10)); tap(page,'all');
  assert.equal(page.data.quadrantIndex,0); assert.equal(page.data.customers.length,0);
});
test('drag, multitouch and cancellation never zoom; a following tap recovers',()=>{
  const page=mapPage();
  page.startMapTouch(touch(100,100)); page.moveMapTouch(touch(100,125));
  page.moveMapTouch(touch(100,100)); tap(page,'主攻区'); assert.equal(page.data.mapQuadrant,'all');
  page.startMapTouch({touches:[{clientX:0,clientY:0},{clientX:10,clientY:10}]});
  tap(page,'主攻区'); assert.equal(page.data.mapQuadrant,'all');
  page.startMapTouch(touch(100,100)); page.cancelMapTouch(); tap(page,'主攻区');
  assert.equal(page.data.mapQuadrant,'all');
  page.startMapTouch(touch(100,100)); tap(page,'invalid'); assert.equal(page.data.mapQuadrant,'all');
  tap(page,'主攻区'); assert.equal(page.data.mapQuadrant,'主攻区');
});
test('only quadrant backgrounds zoom and customer points retain their own non-bubbling action',()=>{
  const wxml=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customers/index.wxml'),'utf8');
  const zones=[...wxml.matchAll(/<view class="map-zone zone-(?:attack|asset|order|resource)"[^>]+>/g)].map(match=>match[0]);
  assert.equal(zones.length,4);
  assert.match(wxml, /wx:else class="map-zone zone-expanded" data-quadrant="all" bindtap="tapMapQuadrant"/);
  zones.forEach((zone,i)=>{
    assert.ok(zone.includes('quadrantOptions['+(i+1)+'].value'));
    assert.ok(zone.includes('bindtap="tapMapQuadrant"'));
  });
  assert.match(wxml,/class="plot-hit-area"[^>]+catchtap="openBattleCustomer"/);
  assert.doesNotMatch(wxml,/<view class="battle-map surface"[^>]+bindtap=/);
});

test('drag in a zoomed quadrant does not zoom out; picker can still return to full map',()=>{
  const page=mapPage(); page.changeQuadrant({detail:{value:2}});
  page.startMapTouch(touch(10,10)); page.moveMapTouch(touch(10,40)); tap(page,'all');
  assert.equal(page.data.mapQuadrant,'客户资产');
  page.changeQuadrant({detail:{value:0}}); assert.equal(page.data.customers.length,4);
});
