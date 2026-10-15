const access = require('./access');
const currentIdentity = () => access.identity(getApp().globalData.session);
const emptyPage = () => ({items: [], loading: false, error: '', loaded: false, hasMore: false, nextOffset: 0, nextCursor: null});
const rowId = row => row && (row.id || row.key);

// Each subject owns its history. A close, new subject, or permission change
// invalidates outstanding successes and failures before they can update the UI.
class DetailReadSession {
  constructor(changed, identity = currentIdentity) {
    this.changed = changed;
    this.identity = identity;
    this.generation = 0;
    this.close();
  }
  reset(key) {
    this.generation += 1;
    this.key = String(key);
    this.owner = this.identity();
    this.closed = false;
    this.pages = {};
    this.resources = {};
    return this.token();
  }
  token() { return {generation: this.generation, key: this.key, owner: this.owner}; }
  current(token = this.token()) {
    return !this.closed && token.generation === this.generation &&
      token.key === this.key && token.owner === this.identity();
  }
  close() { this.generation += 1; this.closed = true; this.pages = {}; this.resources = {}; }
  state(name) { return this.pages[name] || emptyPage(); }
  emit() { if (this.current() && this.changed) this.changed(this.pages); }

  resource(name) { return this.resources[name] || {data:null,loading:false,loaded:false,error:''}; }
  async loadResource(name, request, {retry=false}={}) {
    const prior=this.resource(name);
    if(!this.current() || prior.loading || (!retry && (prior.loaded || prior.error)))return;
    const token=this.token();this.resources[name]={...prior,loading:true,error:''};this.emit();
    try {
      const data=await request();if(!this.current(token))return;
      this.resources[name]={data,loading:false,loaded:true,error:''};
    } catch(error) {if(this.current(token))this.resources[name]={...prior,loading:false,error:error.message||'经营汇总加载失败'};}
    if(this.current(token))this.emit();
  }

  async load(name, request, {more = false, retry = false} = {}) {
    const prior = this.state(name);
    if (retry && prior.failedMore) more = true;
    if (!this.current() || prior.loading || (!more && !retry && prior.loaded) || (more && !prior.hasMore)) return;
    const token = this.token(), cursor = more ? prior.nextCursor : null, offset = more && !cursor ? prior.nextOffset : 0;
    this.pages[name] = {...prior, loading: true, error: ''};
    this.emit();
    try {
      const response = await request(cursor ? {page_size: 20, cursor} : {page_size: 20, offset});
      if (!this.current(token)) return;
      if (!response || !Array.isArray(response.items) || response.items.length > 20 ||
          response.items.some(row => !rowId(row)) || typeof response.has_more !== 'boolean' ||
          (response.next_cursor != null && (typeof response.next_cursor !== 'string' || !response.next_cursor)) ||
          (response.has_more && !(typeof response.next_cursor === 'string' && response.next_cursor && response.next_cursor !== cursor) &&
           (!Number.isInteger(response.next_offset) || response.next_offset <= offset || cursor))) {
        throw Error('列表响应不完整，请重试');
      }
      const items = more ? prior.items.slice() : [];
      const seen = new Set(items.map(row => String(rowId(row))));
      for (const row of response.items) {
        const id = String(rowId(row));
        if (!seen.has(id)) { items.push(row); seen.add(id); }
      }
      this.pages[name] = {items, loaded: true, loading: false, error: '', hasMore: response.has_more, nextOffset: response.next_offset, nextCursor: response.next_cursor || null};
    } catch (error) {
      if (this.current(token)) this.pages[name] = {...prior, loading: false, failedMore: more, error: error.message || '加载失败，请重试'};
    }
    if (this.current(token)) this.emit();
  }
}

function customerLoaders(api, id, {visitSort} = {}) {
  return {
    opportunities: options => api.listCustomerOpportunities(id, options),
    contacts: options => api.listCustomerContacts(id, options),
    visits: options => api.listVisits({...options, customer_id: id, ...(visitSort ? {sort: visitSort} : {})}),
    tasks: options => api.listDetailTasks({...options, customer_id: id}),
  };
}
function activeSections(tab) {
  return tab === 'overview' ? ['opportunities', 'contacts'] :
    [{opportunity: 'opportunities', visits: 'visits', tasks: 'tasks'}[tab]].filter(Boolean);
}
function detailWithPages(raw, pages, focused) {
  const result = {...raw};
  for (const name of ['opportunities', 'contacts', 'visits', 'tasks']) result[name] = (pages[name] && pages[name].items) || [];
  if (focused && !result.opportunities.some(row => String(row.id) === String(focused.id))) {
    result.opportunities = [focused, ...result.opportunities];
  }
  return result;
}
function pageStates(pages) {
  return Object.fromEntries(Object.entries(pages).map(([key, {items, ...state}]) => [key, {...state, count: items.length}]));
}
module.exports = {DetailReadSession, customerLoaders, activeSections, detailWithPages, pageStates};
