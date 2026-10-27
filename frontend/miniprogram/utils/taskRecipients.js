// Task recipients are company colleagues; the directory does not grant CRM access.
async function collectRecipients(fetchPage, current, withDirectory) {
  const items = [], seen = new Set();
  let offset = 0, directory = null, directoryKey = '';
  do {
    const page = await fetchPage({page_size:100,offset});
    if (!current()) return null;
    if (!page || !Array.isArray(page.items)) throw Error('人员目录数据不完整，请重试');
    if (withDirectory) {
      const metadata = recipientDirectoryMetadata(page);
      const key = JSON.stringify([metadata.teams.map(team => [team.id,team.name]).sort(),metadata.defaults.team_id]);
      if (directory && key !== directoryKey) throw Error('团队目录已变化，请重新加载');
      directory = metadata; directoryKey = key;
    }
    for (const person of page.items) {
      if (!person.id || seen.has(person.id)) throw Error('人员目录已变化，请重新加载');
      if (withDirectory && (typeof person.id !== 'string' || typeof person.name !== 'string' ||
          typeof person.account_code !== 'string' || !person.account_code || !Array.isArray(person.team_ids) ||
          person.team_ids.some(id => !directory.teams.some(team => team.id === id)))) {
        throw Error('人员团队信息不完整，请重试');
      }
      seen.add(person.id); items.push(person);
    }
    if (!page.has_more) return withDirectory ? {...directory,items} : items;
    if (!Number.isInteger(page.next_offset) || page.next_offset <= offset || !page.items.length) {
      throw Error('人员目录分页异常，请重试');
    }
    offset = page.next_offset;
  } while (current());
  return null;
}

function recipientDirectoryMetadata(page) {
  if (!Array.isArray(page.teams) || !page.defaults || !Object.prototype.hasOwnProperty.call(page.defaults,'team_id')) {
    throw Error('团队目录数据不完整，请重试');
  }
  const ids = new Set();
  for (const team of page.teams) {
    if (!team || typeof team.id !== 'string' || !team.id || typeof team.name !== 'string' || !team.name || ids.has(team.id)) {
      throw Error('团队目录数据不完整，请重试');
    }
    ids.add(team.id);
  }
  const defaultTeamId = page.defaults.team_id;
  if (defaultTeamId !== null && defaultTeamId !== '' && !ids.has(defaultTeamId)) {
    throw Error('默认团队已变化，请重新加载');
  }
  return {teams:page.teams,defaults:{...page.defaults,team_id:defaultTeamId || ''}};
}

// Preserve the original items-only contract for consumers without a team selector.
function allTaskRecipients(api, current = () => true) {
  return collectRecipients(options => api.listTaskRecipients(options),current,false);
}
function allTaskRecipientDirectory(api, current = () => true) {
  return collectRecipients(options => api.listTaskRecipients(options),current,true);
}
function allTaskReassignmentDirectory(api, taskId, current = () => true) {
  return collectRecipients(options => api.getTaskReassignmentOptions(taskId,options),current,true);
}
module.exports = {allTaskRecipients,allTaskRecipientDirectory,allTaskReassignmentDirectory};
