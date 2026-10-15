import {api, identity} from './data.js';

const send = async (path, method = 'GET', data) => {
  const owner = identity();
  if (!owner) throw Error('请先登录');
  const result = await api().request({path, method, data, fresh: true});
  if (owner !== identity()) throw Error('身份或权限已变化，请重新打开商机页面');
  return result;
};
export const opportunityWorkspaceApi = {
  options: () => send('/opportunity-views/options'),
  views: async () => {
    const items = [], seen = new Set();
    for (let offset = 0; offset < 10000; offset += 100) {
      const page = await send(`/opportunity-views?limit=100&offset=${offset}`);
      if (!Array.isArray(page.items) || !Number.isInteger(page.total)) throw Error('方案列表响应不完整，请重试');
      for (const item of page.items) {
        if (!item.id || seen.has(item.id)) throw Error('方案列表已变化，请重新加载');
        seen.add(item.id); items.push(item);
      }
      if (items.length >= page.total) return {...page, items};
      if (!page.items.length) throw Error('方案列表分页不完整，请重试');
    }
    throw Error('方案数量超过读取上限，请联系管理员整理');
  },
  preference: () => send('/opportunity-views/preference'),
  setDefault: (viewId, version) => send('/opportunity-views/preference', 'PUT', {default_view_id: viewId, version_no: version}),
  query: data => send('/opportunity-views/query', 'POST', data),
  create: data => send('/opportunity-views', 'POST', data),
  update: (id, data) => send(`/opportunity-views/${encodeURIComponent(id)}`, 'PUT', data),
  remove: (id, version) => send(`/opportunity-views/${encodeURIComponent(id)}`, 'DELETE', {version_no: version}),
  edit: (id, data) => send(`/opportunities/${encodeURIComponent(id)}/fields`, 'PATCH', data),
  fields: id => send(`/opportunities/${encodeURIComponent(id)}/fields`),
  people: (id, data) => send(`/opportunities/${encodeURIComponent(id)}/people`, 'PUT', data),
  members: ({q = '', kind = 'all', offset = 0} = {}) => send(`/directory/opportunity-members?q=${encodeURIComponent(q)}&kind=${kind}&offset=${offset}&limit=50`),
  directory: ({url, kind}, {q = '', offset = 0} = {}) => {
    if (!url.startsWith('/') || url.startsWith('//')) throw Error('该字段的可选目录尚未配套');
    const parsed = new URL(url, 'https://workspace.invalid');
    const path = parsed.pathname.replace(/^\/api\/v1/, '');
    if (!['/directory/opportunity-members', '/directory/fde-members', '/directory/partners'].includes(path)) throw Error('该字段的可选目录尚未配套');
    parsed.searchParams.set('q', q); parsed.searchParams.set('offset', offset); parsed.searchParams.set('limit', 50);
    if (path === '/directory/opportunity-members' && ['all', 'fde'].includes(kind)) parsed.searchParams.set('kind', kind);
    return send(`${path}?${parsed.searchParams}`);
  },
};
