// Object capabilities are authoritative; no role/global-permission fallback.
export function createCollaborationController(id, api) {
  let alive = true, readSequence = 0, directorySequence = 0, pending = null;
  let state = {phase:'idle', access:null, draft:[], candidates:[], total:0, offset:0, query:'', directoryLoading:false, directoryError:'', message:''};
  const listeners = new Set();
  const set = patch => {if (!alive) return;state = {...state, ...patch};listeners.forEach(fn => fn(state));};
  const manageable = () => state.access?.capabilities?.can_manage_collaborators === true;
  const selected = () => state.draft.map(p => p.user_id).sort();
  const current = () => (state.access?.collaborators || []).map(p => p.user_id).sort();
  const dirty = () => JSON.stringify(selected()) !== JSON.stringify(current());
  async function load(preserve = false, conflict = false) {
    if (!alive || state.phase === 'saving' || state.phase === 'uncertain') return;
    const seq = ++readSequence;++directorySequence;
    set({phase:'loading', access:null, directoryLoading:false, candidates:[], message:''});
    try {
      const access = await api.access(id);
      if (!alive || seq !== readSequence) return;
      set({access, draft:preserve ? state.draft : access.collaborators.slice(), phase:conflict ? 'conflict' : 'ready',
        message:conflict ? '商机版本已变化。已重新读取当前名单，请核对后再确认；你的选择仍保留。' : ''});
    } catch (error) {
      if (!alive || seq !== readSequence) return;
      set({phase:[403,404].includes(error.statusCode) ? 'denied' : 'error', access:null,
        message:[403,404].includes(error.statusCode) ? '当前无法访问或维护此商机，未提交的选择仍保留。请联系商机负责人核对。' : error.message});
    }
  }
  async function search(query = '', more = false) {
    if (!alive || !manageable() || !['ready','conflict'].includes(state.phase)) return;
    const seq = ++directorySequence, offset = more ? state.offset : 0;
    set({directoryLoading:true, directoryError:'', query, ...(more ? {} : {candidates:[], total:0, offset:0})});
    try {
      const result = await api.candidates(id, query, offset);
      if (!alive || seq !== directorySequence) return;
      const rows = more ? [...state.candidates, ...result.items] : result.items;
      set({candidates:[...new Map(rows.map(p => [p.user_id,p])).values()], total:result.total, offset:offset+result.items.length, directoryLoading:false});
    } catch (error) {
      if (!alive || seq !== directorySequence) return;
      set({directoryLoading:false, directoryError:error.message});
      if ([403,404].includes(error.statusCode)) set({phase:'denied', access:null, message:'协作管理权限已变化，选择仍保留，请重新读取商机。'});
    }
  }
  function toggle(person, checked) {
    if (!manageable() || !['ready','conflict'].includes(state.phase)) return;
    if (checked && state.draft.length >= 30) {set({message:'最多选择 30 位协作人'});return;}
    set({draft:checked ? [...new Map([...state.draft,person].map(p => [p.user_id,p])).values()] : state.draft.filter(p => p.user_id !== person.user_id), message:''});
  }
  async function save() {
    if (!alive || !manageable() || !['ready','uncertain'].includes(state.phase)) return;
    if (state.phase === 'ready') {
      if (!dirty()) return;
      pending = {version_no:state.access.version_no, member_ids:selected()};
    }
    if (!pending) return;
    set({phase:'saving', message:''});++directorySequence;
    try {
      const result = await api.save(id, pending);
      if (!alive) return;
      pending = null;
      set({phase:'ready', access:{...state.access, version_no:result.version_no, collaborators:result.collaborators}, draft:result.collaborators.slice(), directoryLoading:false, message:result.changed ? '协作名单已保存' : '名单未变化，无需重复保存'});
    } catch (error) {
      if (!alive) return;
      if (error.code === 'SESSION_CHANGED') {set({phase:'denied', access:null, draft:[], candidates:[], message:error.message});return;}
      if ([403,404].includes(error.statusCode)) {pending=null;set({phase:'denied', access:null, message:'协作管理权限已变化，本次选择未提交。请重新读取商机核对。'});return;}
      if (error.statusCode === 409 && !String(error.serverCode || '').startsWith('IDEMPOTENCY_')) {
        pending=null;set({phase:'ready'});await load(true, true);return;
      }
      if ([400,422].includes(error.statusCode)) {pending=null;set({phase:'ready', message:error.message});return;}
      set({phase:'uncertain', directoryLoading:false, message:'尚未确认保存结果，选择已锁定。请用“重试本次保存”核对同一次请求，避免重复提交。'});
    }
  }
  return {getState:() => state, subscribe:fn => {listeners.add(fn);return () => listeners.delete(fn);}, load, search, toggle, save, dirty,
    acknowledge() {if (state.phase === 'conflict' && manageable()) set({phase:'ready', message:'已确认最新版本，请再次点击保存协作名单。'});},
    dispose() {alive=false;++readSequence;++directorySequence;listeners.clear();pending=null;state={...state,access:null,draft:[],candidates:[]};},
  };
}
