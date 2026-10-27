const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const access=require('../miniprogram/utils/access');
const markup=fs.readFileSync(path.join(__dirname,'../miniprogram/pages/customer-assets/index.wxml'),'utf8').replace(/<wxs\b[\s\S]*?<\/wxs>/g,'');

// Evaluate the actual card's ancestor branches, so an unrelated permission on
// an outer block cannot hide a readable card while its own condition passes.
function visible(marker,data){
  const position=markup.indexOf(marker);assert.ok(position>=0,marker);
  const stack=[{guards:[],branch:[]}];
  for(const match of markup.matchAll(/<\/?[\w:-]+(?:[^"'<>]|"[^"]*"|'[^']*')*>/g)){
    if(match.index>position)break;
    const tag=match[0];
    if(tag.startsWith('</')){stack.pop();continue;}
    const parent=stack.at(-1),attrs=Object.fromEntries(Array.from(tag.matchAll(/([\w:-]+)="([^"]*)"/g),m=>[m[1],m[2]]));
    const expr=name=>attrs[name]?.replace(/^\{\{|\}\}$/g,'');
    let own=[];
    if('wx:if' in attrs){own=[expr('wx:if')];parent.branch=own;}
    else if('wx:elif' in attrs){own=[...parent.branch.map(x=>`!(${x})`),expr('wx:elif')];parent.branch=[...parent.branch,expr('wx:elif')];}
    else if(/\bwx:else(?:\s|>|\/)/.test(tag)){own=parent.branch.map(x=>`!(${x})`);parent.branch=[];}
    else parent.branch=[];
    const node={guards:[...parent.guards,...own],branch:[]};
    if(position<match.index+tag.length)return node.guards.every(x=>!!vm.runInNewContext(x,data));
    if(!tag.endsWith('/>'))stack.push(node);
  }
  return stack.at(-1).guards.every(x=>!!vm.runInNewContext(x,data));
}
const history='class="op-related-card change-history"';
const state=permissions=>({accessBlocked:false,...access.flags({role:'sales',permissions}),'opportunityId':'o',opportunity:{id:'o'},opportunityLoading:false,opportunityError:'',opportunityTab:'opportunity'});
const readOnlyPermissions={'access.mini_program':true,'opportunity.read':true};

test('readable opportunity history is visible without advice read or generation permission',()=>{
  const data=state(readOnlyPermissions);
  assert.equal(access.pageAllowed({permissions:readOnlyPermissions},'customer-assets'),true);
  assert.equal(data.canOpportunityAdvice,false);assert.equal(data.canReadAdvice,false);
  assert.equal(visible(history,data),true);
  assert.equal(visible('>已记录动态<',data),false);
  assert.equal(visible('>当前商机阶段<',data),false);
  assert.equal(visible('class="op-advice"',data),false);
});

test('history still requires a successful readable object and the progress tab',()=>{
  const data=state(readOnlyPermissions);
  for(const changes of [{accessBlocked:true},{opportunityId:''},{opportunity:null},{opportunityLoading:true},{opportunityError:'无权查看'},{opportunityTab:'overview'},{opportunityTab:'visits'}]){
    assert.equal(visible(history,{...data,...changes}),false,JSON.stringify(changes));
  }
});

test('existing advice-enabled stage and timeline remain visible beside history',()=>{
  const data=state({...readOnlyPermissions,'advice.read':true,'advice.request':true,'advice.opportunity':true});
  for(const marker of [history,'>已记录动态<','>当前商机阶段<','class="op-advice"'])assert.equal(visible(marker,data),true,marker);
});


test('one general history block comes before stage and preserves the recorded timeline',()=>{
 assert.equal((markup.match(/class="op-related-card change-history"/g)||[]).length,1);
 assert.equal(markup.includes('>意向产品线变更<'),false);
 assert.ok(markup.indexOf(history)<markup.indexOf('>当前商机阶段<'));
 assert.ok(markup.indexOf(history)<markup.indexOf('>已记录动态<'));
});
