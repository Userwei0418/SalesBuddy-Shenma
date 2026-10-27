// Explicit collaboration contract; directory entries never grant object permissions.
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const invalid = () => { throw Error('商机协作数据不完整，请重新读取'); };
const ids = members => members.map(person => person.user_id).sort();
const sameIds = (left, right) => JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
function members(value) {
  if (!Array.isArray(value) || value.length > 30 || value.some(person => !person || !uuid(person.user_id) || typeof person.display_name !== 'string') ||
      new Set(value.map(person => person.user_id)).size !== value.length) invalid();
  return value.map(person => ({user_id:person.user_id, display_name:person.display_name}));
}
function body(value) {
  if (!value || Object.keys(value).sort().join(',') !== 'member_ids,version_no' || !Number.isSafeInteger(value.version_no) || value.version_no < 1 ||
      !Array.isArray(value.member_ids) || value.member_ids.length > 30 || value.member_ids.some(id => !uuid(id)) ||
      new Set(value.member_ids).size !== value.member_ids.length) invalid();
  return {version_no:value.version_no, member_ids:[...value.member_ids].sort()};
}
function candidates(value, offset = 0, limit = 20) {
  if (!value || !Array.isArray(value.items) || value.items.length > limit || !Number.isSafeInteger(value.total) || value.total < 0 ||
      value.items.some(person => !person || !uuid(person.user_id) || typeof person.display_name !== 'string' || !Array.isArray(person.teams) ||
        person.teams.some(team => !team || !uuid(team.team_id) || typeof team.name !== 'string')) ||
      new Set(value.items.map(person => person.user_id)).size !== value.items.length || (value.items.length && offset + value.items.length > value.total)) invalid();
  return {items:value.items.map(person => ({user_id:person.user_id, display_name:person.display_name,
    teamNames:person.teams.map(team => team.name).join('、')})), total:value.total};
}
function receipt(value, request) {
  const expected = body(request), people = members(value && value.collaborators);
  if (typeof value.changed !== 'boolean' || (value.changed ? !uuid(value.event_id) : value.event_id !== null) ||
      value.version_no !== expected.version_no + (value.changed ? 1 : 0) || !sameIds(ids(people), expected.member_ids)) invalid();
  return {changed:value.changed, version_no:value.version_no, collaborators:people};
}
function pending(value) {
  if (!value || !uuid(value.key)) invalid();
  return {key:value.key, body:body(value.body)};
}
module.exports = {uuid, ids, sameIds, members, body, candidates, receipt, pending};
