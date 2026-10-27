const test=require('node:test'),assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
const {readFileInParts}=require('../miniprogram/utils/knowledgeFileTransfer');
const PART=256*1024;
const bytes=Buffer.alloc(PART*6+79,0xab);
const etag='"'+createHash('sha256').update(bytes).digest('hex')+'"';
function part(range){const [start,end]=range.slice(6).split('-').map(Number),last=Math.min(end,bytes.length-1),data=bytes.subarray(start,last+1);return {data:data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength),statusCode:206,headers:{'Content-Range':`bytes ${start}-${last}/${bytes.length}`,ETag:etag,'Content-Type':'application/pdf'}};}

test('out-of-order concurrent segments reconstruct exact original bytes with bounded concurrency',async()=>{
 let active=0,max=0;const seen=[],progress=[];
 const result=await readFileInParts(async options=>{
  seen.push(options);active++;max=Math.max(max,active);
  await new Promise(r=>setTimeout(r,options.rangeHeader.includes('524288')?30:2));active--;
  return part(options.rangeHeader);
 },{onProgress:x=>progress.push(x)});
 assert.deepEqual(Buffer.from(result.data),bytes);assert.equal(max,3);assert.equal(seen.length,7);
 assert.ok(seen.slice(1).every(x=>x.fileIfMatch===etag));assert.ok(seen.every(x=>x.timeout<=120000));
 assert.equal(progress.at(-1).received,bytes.length);assert.ok(progress.every((x,i)=>!i||x.received>progress[i-1].received));
});

test('only the failed segment retries once, retaining revision and completed bytes',async()=>{
 const seen=[];let dropped=false;
 await readFileInParts(async options=>{seen.push(options);if(options.rangeHeader===`bytes=${PART}-${2*PART-1}`&&!dropped){dropped=true;throw Object.assign(Error('timeout'),{code:'NETWORK_ERROR'});}return part(options.rangeHeader);});
 assert.equal(seen.length,8);assert.equal(seen.filter(x=>x.rangeHeader.startsWith('bytes=0-')).length,1);
 assert.equal(seen.filter(x=>x.rangeHeader===`bytes=${PART}-${2*PART-1}`).length,2);
});

for(const statusCode of [401,403,404,412,416])test(`denied/changed segment ${statusCode} is never retried`,async()=>{
 let calls=0;
 await assert.rejects(readFileInParts(async options=>{calls++;if(calls>1)throw Object.assign(Error('denied'),{statusCode});return part(options.rangeHeader);}),{statusCode});
 assert.ok(calls<=4);
});

for(const change of ['range','etag','length','whole'])test(`refuse inconsistent ${change} during assembly`,async()=>{
 let calls=0;
 await assert.rejects(readFileInParts(async options=>{const value=part(options.rangeHeader);if(++calls>1){if(change==='range')value.headers['Content-Range']='bytes 0-1/2';if(change==='etag')value.headers.ETag='"'+ 'f'.repeat(64)+'"';if(change==='length')value.data=new ArrayBuffer(2);if(change==='whole')value.statusCode=200;}return value;}),{code:'FILE_TRANSFER_INVALID'});
});

test('cancellation prevents subsequent segments and drops the partial file',async()=>{
 let cancelled=false,calls=0;
 await assert.rejects(readFileInParts(async options=>{calls++;return part(options.rangeHeader);},{check:()=>{if(cancelled)throw Object.assign(Error('cancel'),{code:'FILE_TRANSFER_CANCELLED'});},onProgress:()=>{cancelled=true;}}),{code:'FILE_TRANSFER_CANCELLED'});
 assert.equal(calls,1);
});

test('cancellation aborts in-flight native request tasks without waiting for their timeout',async()=>{
 let cancelled=false,calls=0,aborted=0;
 const pending=readFileInParts(options=>{
  if(++calls===1)return Promise.resolve(part(options.rangeHeader));
  return new Promise((resolve,reject)=>{
   options.onRequestTask({abort(){aborted++;reject(Object.assign(Error('aborted'),{code:'NETWORK_ERROR'}));}});
  });
 },{check:()=>{if(cancelled)throw Object.assign(Error('cancel'),{code:'FILE_TRANSFER_CANCELLED'});}});
 setTimeout(()=>{cancelled=true;},10);
 await assert.rejects(pending,{code:'FILE_TRANSFER_CANCELLED'});
 assert.equal(calls,4);assert.equal(aborted,3);
});

test('first request is not retried and oversized metadata is rejected before allocation',async()=>{
 let calls=0;await assert.rejects(readFileInParts(async()=>{calls++;throw Object.assign(Error('timeout'),{code:'NETWORK_ERROR'});}),{code:'NETWORK_ERROR'});assert.equal(calls,1);
 await assert.rejects(readFileInParts(async options=>{const value=part(options.rangeHeader);value.headers['Content-Range']=`bytes 0-${PART-1}/999999999999`;return value;}),{code:'FILE_TRANSFER_INVALID'});
});

test('real HTTP range transport recovers a dropped segment and preserves SHA256',async()=>{
 const {createServer}=require('node:http'),{once}=require('node:events');let dropped=false;const seen=[];
 const server=createServer((req,res)=>{seen.push({range:req.headers.range,etag:req.headers['if-match']});if(req.headers.range===`bytes=${PART}-${2*PART-1}`&&!dropped){dropped=true;req.socket.destroy();return;}const value=part(req.headers.range);res.writeHead(206,value.headers);res.end(Buffer.from(value.data));});
 server.listen(0,'127.0.0.1');await once(server,'listening');
 try {
  const result=await readFileInParts(async options=>{try{const response=await fetch(`http://127.0.0.1:${server.address().port}/fixture`,{headers:{Range:options.rangeHeader,...(options.fileIfMatch?{'If-Match':options.fileIfMatch}:{})},signal:AbortSignal.timeout(options.timeout)});return {statusCode:response.status,headers:Object.fromEntries(response.headers),data:await response.arrayBuffer()};}catch(error){throw Object.assign(Error(error.message),{code:'NETWORK_ERROR'});}});
  assert.equal('"'+createHash('sha256').update(Buffer.from(result.data)).digest('hex')+'"',etag);
  assert.equal(seen.length,8);assert.equal(seen.filter(x=>x.range.startsWith('bytes=0-')).length,1);
 } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
