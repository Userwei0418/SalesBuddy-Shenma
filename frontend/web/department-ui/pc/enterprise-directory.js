import {api, identity} from './data.js';

export const IDENTITY_FILTERS = [{value: 'all', label: '全部身份'}, {value: 'customer', label: '客户'}, {value: 'partner', label: '合作伙伴'}];
export function enterpriseDirectoryParams({q = '', businessIdentity = 'all', offset = 0, pageSize = 20} = {}) {
  return {q: [...String(q).trim()].slice(0, 100).join(''), business_identity: IDENTITY_FILTERS.some(x => x.value === businessIdentity) ? businessIdentity : 'all',
    offset: Math.max(0, Number(offset) || 0), page_size: [10, 20, 50, 100].includes(pageSize) ? pageSize : 20};
}
export function normalizeEnterpriseDirectory(value) {
  if (!value || !Array.isArray(value.items) || !Number.isInteger(value.total) || value.total < 0 || typeof value.has_more !== 'boolean' ||
    value.items.length > value.total || new Set(value.items.map(row => row.id)).size !== value.items.length ||
    value.items.some(row => !row.id || !row.name || typeof row.can_read_customer !== 'boolean' || !Array.isArray(row.business_identities) || !row.business_identities.length || row.business_identities.some(x => !['customer', 'partner'].includes(x)))) {
    throw Error('企业档案目录响应不完整，请更新配套服务后重试');
  }
  return value;
}
export async function loadEnterpriseDirectory(params) {
  const actor = identity();
  const value = await api().request({path: `/customers/claim-pool?${new URLSearchParams(params)}`, fresh: true});
  if (actor !== identity()) throw Error('身份或权限已变化，请重新读取企业档案');
  return normalizeEnterpriseDirectory(value);
}
