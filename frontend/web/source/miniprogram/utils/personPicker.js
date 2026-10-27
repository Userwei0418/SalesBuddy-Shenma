// Candidate filters only affect the picker; they never change the page's committed scope.
function normalizeTeams(teams = []) {
  return [...new Map(teams.filter(row => row && row.id && row.id !== 'all').map(row => [String(row.id), { ...row, id: String(row.id), name: String(row.name || '') }])).values()];
}
function normalizeMembers(members = [], teams = []) {
  const teamRows = normalizeTeams(teams);
  return [...new Map(members.filter(row => row && (row.id || row.user_id)).map(row => {
    const id = String(row.id || row.user_id);
    const teamIds = [...new Set((row.team_ids || (row.team_id ? [row.team_id] : [])).map(String))];
    const teamLabel = row.teamLabel || teamRows.filter(team => teamIds.includes(team.id)).map(team => team.name).join(' · ') || row.team || row.team_name || '';
    return [id, { ...row, id, name: String(row.name || row.display_name || ''), account_code: String(row.account_code || ''), team_ids: teamIds, teamLabel: String(teamLabel) }];
  })).values()];
}
function candidateTeam(teams, requested) {
  return normalizeTeams(teams).some(row => row.id === String(requested || '')) ? String(requested) : '';
}
function validSelection(ids, members, multiple = false, maxSelected = 100) {
  const allowed = new Set(members.map(row => row.id));
  return [...new Set((ids || []).map(String))].filter(id => allowed.has(id)).slice(0, multiple ? Math.max(1, Number(maxSelected) || 100) : 1);
}
function filterMembers(members, teamId, query) {
  const q = String(query || '').trim().toLowerCase();
  return members.filter(row => (!teamId || row.team_ids.includes(teamId)) && (!q || [row.name, row.account_code, row.teamLabel].some(value => value.toLowerCase().includes(q))));
}
function assertPersonDirectory(members, teams, defaults) {
  const invalid = () => { throw Error('人员目录缺少团队字段或范围不完整，请更新配套后端后重试'); };
  const text = value => typeof value === 'string' && value.trim().length > 0;
  if (!Array.isArray(members) || !Array.isArray(teams) || !defaults || !Object.prototype.hasOwnProperty.call(defaults, 'team_id')) invalid();
  const teamIds = new Set();
  for (const team of teams) {
    if (!team || !text(team.id) || !text(team.name) || team.id === 'all' || teamIds.has(team.id)) invalid();
    teamIds.add(team.id);
  }
  if (defaults.team_id !== null && (!text(defaults.team_id) || !teamIds.has(defaults.team_id))) invalid();
  const memberIds = new Set();
  for (const member of members) {
    if (!member || !text(member.id || member.user_id) || !text(member.name || member.display_name) || !Array.isArray(member.team_ids)) invalid();
    const id = member.id || member.user_id;
    if (memberIds.has(id) || member.team_ids.some(teamId => !text(teamId) || !teamIds.has(teamId))) invalid();
    if (member.account_code != null && typeof member.account_code !== 'string') invalid();
    memberIds.add(id);
  }
  return true;
}
module.exports = { assertPersonDirectory, normalizeTeams, normalizeMembers, candidateTeam, validSelection, filterMembers };
