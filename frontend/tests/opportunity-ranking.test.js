const test=require('node:test'),assert=require('node:assert/strict');
const {rankingDisplay}=require('../miniprogram/utils/rankingDisplay');
test('保留服务器名次和参评人数，只有本人一行也不变成第一名',()=>{
 const result=rankingDisplay({rows:[{user_id:'a',name:'同名',value:100,record_count:2,customer_count:1,rank:3,population:5}],groups:[]},false,true);
 assert.equal(result.rows[0].rank,3);assert.equal(result.rows[0].population,5);assert.equal(result.total,'¥100');
});
test('同分不重排，未知团队单列，0金额没有最小虚假条长',()=>{
 const result=rankingDisplay({rows:[],groups:[{code:'north_east',name:'北区＋东区',value:0,record_count:0,customer_count:0,rank:2},{code:'unassigned',value:50,record_count:1,customer_count:1,rank:1}]},true,true);
 assert.equal(result.rows[0].rank,2);assert.equal(result.rows[0].width,'0%');assert.equal(result.unassignedAmount,'¥50');
 assert.equal(result.rows.length,1);
});
test('缺少统计契约报错，空行仍为真实空状态',()=>{
 assert.throws(()=>rankingDisplay({rows:[]}),/格式/);
 assert.deepEqual(rankingDisplay({rows:[],groups:[]}),{rows:[],total:0,unassignedCount:0,unassignedAmount:'¥0'});
});
