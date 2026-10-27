const test=require('node:test'),assert=require('node:assert/strict');
const {createKnowledgeDistribution,customerShareUrl,SOURCE}=require('../miniprogram/utils/knowledgeDistribution');
const knowledgeFile=require('../miniprogram/utils/knowledgeFile');
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const pub=(files=[],source_links=[])=>({material_id:id(1),publication_id:id(2),version_id:id(3),version_no:2,enabled:true,can_share:true,files,source_links,content_name:'合成资料'});
const receipt=()=>({...pub(),share_id:id(4),sales_user_id:id(5),share_path:'/web/knowledge-share.html#token='+'a'.repeat(32)});
test('distribution adapter uses authenticated same-source total and exact frozen version with idempotency',async()=>{
  const calls=[],client={getBaseUrl:()=> 'https://example.test/api/v1',request:async options=>{calls.push(options);return options.method==='POST'?receipt():{source:SOURCE,items:[],total:3,offset:0,limit:1};}};
  const api=createKnowledgeDistribution(client);assert.equal((await api.list({limit:1,q:'方案'})).total,3);
  const result=await api.share(id(1),id(2),id(6));assert.equal(result.url,'https://example.test'+receipt().share_path);
  assert.equal(calls[0].path,'/knowledge/distribution/materials?q=%E6%96%B9%E6%A1%88&limit=1&offset=0');assert.deepEqual(calls[1].data,{publication_id:id(2)});assert.equal(calls[1].idempotencyKey,id(6));
});
test('no external URL or malformed/foreign total can be promoted to a valid share or count',async()=>{
  assert.throws(()=>customerShareUrl({share_path:'https://evil.test'},'https://example.test'));
  const api=createKnowledgeDistribution({request:async()=>({source:'salesbuddy.internal_materials',items:[],total:114,offset:0,limit:1})});await assert.rejects(api.list());
});
test('share URL falls back to the production API origin and supports an explicit separate LOCAL Web origin',()=>{
 assert.equal(customerShareUrl(receipt(),'https://www.ericepc.com/api/v1',''),'https://www.ericepc.com'+receipt().share_path);
 assert.equal(customerShareUrl(receipt(),'http://127.0.0.1:18822/api/v1','http://127.0.0.1:5222'),'http://127.0.0.1:5222'+receipt().share_path);
 assert.equal(customerShareUrl(receipt(),'http://127.0.0.1:18822/api/v1','https://customer.example.test/'),'https://customer.example.test'+receipt().share_path);
});
test('configured share origin cannot carry credentials, query, fragment, another path or invalid host/port',()=>{
 for(const origin of ['javascript:alert(1)','//evil.test','https://user:password@example.test','https://user@example.test','https://example.test?token=private','https://example.test#fragment','https://example.test/web','https://example.test/api/v1','https://example.test\\evil','https://exam ple.test','https://example.test:0','https://example.test:65536','https://example..test','https://-example.test',null,42]){
  assert.throws(()=>customerShareUrl(receipt(),'https://valid.test/api/v1',origin),/配置无效/,String(origin));
 }
 for(const base of ['https://user:password@example.test/api/v1','https://example.test/api/v1?private=1','https://example.test/api/v1#private','https://example.test\\evil'])assert.throws(()=>customerShareUrl(receipt(),base,''),/地址不可用/);
 for(const share_path of ['https://evil.test/web/knowledge-share.html#token='+'a'.repeat(32),'//evil.test/','/web/../knowledge-share.html#token='+'a'.repeat(32),receipt().share_path+'\n',receipt().share_path+'&redirect=https://evil.test'])assert.throws(()=>customerShareUrl({share_path},'https://valid.test/api/v1','http://127.0.0.1:5222'),/分享地址不完整/);
});

test('knowledge file adapter maps supported document and image types without trusting a URL',()=>{
  for(const [filename,expected] of [['方案.doc','doc'],['方案.docx','docx'],['表格.xls','xls'],['表格.xlsx','xlsx'],['演示.ppt','ppt'],['演示.pptx','pptx'],['说明.pdf','pdf']]){
    assert.equal(knowledgeFile.documentType({filename}),expected,filename);
    assert.equal(knowledgeFile.canOpen({filename}),true,filename);
  }
  assert.equal(knowledgeFile.documentType({filename:'无后缀',media_type:'application/pdf'}),'pdf');
  assert.equal(knowledgeFile.isImage({filename:'封面.png',media_type:'image/png'}),true);
  assert.equal(knowledgeFile.canOpen({filename:'未归档.pdf'}),true);
  assert.equal(knowledgeFile.shouldPreview({filename:'资料.pdf',preview_supported:true}),true);
  assert.equal(knowledgeFile.shouldPreview({filename:'资料.docx',preview_supported:true}),false);
  assert.equal(knowledgeFile.safeFilename({filename:'../原始\\文件.pdf'}),'.._原始_文件.pdf');
});

test('the real Mini adapter consumes generated Web origin without rewriting the backend receipt',async()=>{
 const config=require('../miniprogram/config'),original=config.KNOWLEDGE_CUSTOMER_WEB_ORIGIN;
 try{
  config.KNOWLEDGE_CUSTOMER_WEB_ORIGIN='http://127.0.0.1:5222';
  const raw=receipt(),api=createKnowledgeDistribution({getBaseUrl:()=> 'http://127.0.0.1:18822/api/v1',request:async()=>raw});
  assert.equal((await api.share(id(1),id(2),id(6))).url,'http://127.0.0.1:5222'+raw.share_path);assert.equal(raw.share_path,receipt().share_path);assert.equal('url' in raw,false);
 }finally{config.KNOWLEDGE_CUSTOMER_WEB_ORIGIN=original;}
});
