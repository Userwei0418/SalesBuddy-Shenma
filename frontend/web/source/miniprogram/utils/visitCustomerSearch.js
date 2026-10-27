const access = require('./access');

function query(value) {
  return typeof value === 'string' ? [...value].slice(0, 100).join('') : '';
}

// Recording targets are still validated by each visit action on the server.
// Assigned-only actors must retain their existing read directory. Other actors
// use the company reference directory only with its independent current grant.
function list(api, session, options = {}) {
  const params = {q:query(options.q).trim(),pageSize:options.pageSize || 100,order:'recent_follow_up'};
  if (access.assignedVisitOnly(session)) return api.listCustomers({...params,scope:'mine'});
  if (access.can(session, 'customer.claim_directory')) return api.listCustomerClaimPool({...params,offset:0});
  return api.listCustomers({...params,scope:'company'});
}

function errorMessage(error = {}) {
  if (error.statusCode === 403) return '当前账号无权读取此客户目录，请刷新权限后重试';
  if (error.statusCode === 401) return '登录已失效，请重新登录后重试';
  if (error.statusCode === 422) return '客户列表请求暂不可用，请重试';
  return '客户列表加载失败，请稍后重试';
}

module.exports = {query,list,errorMessage};
