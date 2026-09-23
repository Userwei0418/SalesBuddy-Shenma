const test=require('node:test');
const assert=require('node:assert/strict');
const scores=require('../miniprogram/utils/profileScores');
test('只呈现服务端评分及规则版本，不自行重算',()=>{
 const server={value:57.2,text:'57',coverage_percent:70,count:2,total:3,rule:{version:4},explanation:'实际规则'};
 assert.deepEqual({...scores.displayScore(server),signal:undefined},{...server,signal:undefined});
});
test('真实零分保留，缺失或非法评分保持待评估',()=>{
 assert.equal(scores.displayScore({value:0,text:'0'}).text,'0');
 for(const value of [null,undefined,false,'',Infinity,'bad',101,-1]) assert.equal(scores.displayScore({value}).text,'--');
});
