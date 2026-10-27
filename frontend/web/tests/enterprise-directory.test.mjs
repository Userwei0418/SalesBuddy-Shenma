import test from 'node:test';
import assert from 'node:assert/strict';
import {enterpriseDirectoryParams, normalizeEnterpriseDirectory, loadEnterpriseDirectory} from '../department-ui/pc/enterprise-directory.js';
import {portfolioParams, initialCustomerFilters} from '../department-ui/pc/customer-portfolio.js';
const page=()=>({items:[{id:'dual',name:'双身份',business_identities:['customer','partner'],can_read_customer:false}],total:52,has_more:true,next_offset:20});
test('server-side identity filter preserves full count, explicit readability and dual single row',()=>{
 const result=normalizeEnterpriseDirectory(page());assert.equal(result.total,52);assert.equal(result.items.length,1);assert.equal(result.items[0].can_read_customer,false);
 assert.deepEqual(enterpriseDirectoryParams({q:'  企业  ',businessIdentity:'partner',offset:20,pageSize:20}),{q:'企业',business_identity:'partner',offset:20,page_size:20});
 for(const field of ['business_identities','can_read_customer']){const broken=page();delete broken.items[0][field];assert.throws(()=>normalizeEnterpriseDirectory(broken),/配套服务/);}
});
test('identity changes during an enterprise directory request cannot install stale rows',async()=>{
 let owner='first';globalThis.SalesRuntime={app:{globalData:{session:{}}},require:name=>name==='utils/access'?{identity:()=>owner}:{request:async()=>{owner='second';return page();}}};
 await assert.rejects(loadEnterpriseDirectory(enterpriseDirectoryParams()),/身份或权限/);delete globalThis.SalesRuntime;
});
test('partner filter on customer portfolio remains a separate customer-base request',()=>{
 const filters=initialCustomerFilters({kind:'self'});assert.equal(portfolioParams(filters).business_identity,undefined);
 assert.equal(portfolioParams({...filters,businessIdentity:'partner'}).business_identity,'partner');
 assert.equal(portfolioParams({...filters,businessIdentity:'partner'}).segment,'claimed');
});
