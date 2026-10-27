const uuid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || '');
const token = value => /^[A-Za-z0-9_-]{1,100}$/.test(value || '');
const revision = value => Number.isSafeInteger(value) && value >= 1;
const attachment = row => token(row?.source_token) && typeof row.name === 'string' && Boolean(row.name.trim()) && (row.size_bytes === null || (Number.isSafeInteger(row.size_bytes) && row.size_bytes >= 0));
export function assertSourceFiles(value, materialId) {
  if (!value || value.material_id !== materialId || !uuid(value.version_id) || !revision(value.version_no) || !revision(value.material_revision) || !revision(value.acl_revision)
    || !Number.isSafeInteger(value.source_revision) || value.source_revision < 0 || !['可用', '待复核', '已停用'].includes(value.source_status)
    || typeof value.can_archive !== 'boolean' || typeof value.changed !== 'boolean' || !Array.isArray(value.attachments) || !Array.isArray(value.files)
    || value.attachments.some(row => !attachment(row)) || new Set(value.attachments.map(row => row.source_token)).size !== value.attachments.length
    || new Set(value.files.map(row => row.source_token)).size !== value.files.length || new Set(value.files.map(row => row.file_id)).size !== value.files.length
    || value.files.some(row => !attachment(row) || !uuid(row.file_id) || !/^[a-f0-9]{64}$/.test(row.sha256 || '') || typeof row.media_type !== 'string'
      || !value.attachments.some(source => source.source_token === row.source_token && source.name === row.name && source.size_bytes === row.size_bytes))) {
    throw Error('源附件回读不完整，请重新读取');
  }
  return value;
}
export function validateSourceFile(source, file) {
  if (!Number.isSafeInteger(source?.size_bytes) || source.size_bytes <= 0) throw Error('原表缺少准确文件大小，请先补齐来源信息');
  if (!file?.size || file.size > 50 * 1024 * 1024) throw Error('附件需大于 0 且不超过 50 MiB');
  if (file.name !== source.name || file.size !== source.size_bytes) throw Error('所选文件名称或大小与原表不一致，请选择对应原附件');
}
export function assertSourceUpload(receipt, source) {
  if (!uuid(receipt?.file_id) || receipt.filename !== source.name || receipt.size_bytes !== source.size_bytes || !/^[a-f0-9]{64}$/.test(receipt.sha256 || '')) throw Error('附件上传回执尚未确认，请使用同一文件确认上传');
  return receipt;
}
export function sameSourceRevision(before, after) {
  return after?.can_archive === true && ['material_id', 'version_id', 'material_revision', 'acl_revision', 'source_revision'].every(key => before[key] === after[key])
    && JSON.stringify(before.attachments) === JSON.stringify(after.attachments);
}
export function sourceArchiveError(error) {
  const messages = {
    KNOWLEDGE_SOURCE_ARCHIVE_DENIED: '当前没有归档源附件的权限，请重新读取。',
    KNOWLEDGE_SOURCE_ARCHIVE_UNAVAILABLE: '当前来源或版本不可归档，请重新读取并核对原表状态。',
    KNOWLEDGE_SOURCE_REVISION_CONFLICT: '材料、共享权限或飞书来源已变化，请重新读取后选择附件。',
    KNOWLEDGE_SOURCE_ATTACHMENT_MISMATCH: '所选附件与飞书原记录不一致，请核对名称和大小。',
    KNOWLEDGE_SOURCE_FILE_MISMATCH: '上传文件与原附件不一致，请核对名称和大小。',
    KNOWLEDGE_SOURCE_FILE_ALREADY_ARCHIVED: '该源附件已有不同内容的归档，请核对来源版本，不会直接覆盖。',
    KNOWLEDGE_SOURCE_FILE_DUPLICATE: '同一上传文件不能对应多个源附件，请逐项核对原文件。',
    KNOWLEDGE_SOURCE_ATTACHMENT_DUPLICATE: '同一源附件不能重复选择，请重新读取后核对。',
    KNOWLEDGE_FILE_DENIED: '当前无法使用该上传文件，请核对权限。',
    KNOWLEDGE_FILE_TOO_LARGE: '单个附件不能超过 50 MiB。',
    KNOWLEDGE_FILE_SIZE_INVALID: '附件为空，请选择有效文件。',
    IDEMPOTENCY_CONFLICT: '归档请求内容已变化，请重新读取后核对。',
  };
  const known = [error?.serverCode, error?.code, error?.message].find(value => messages[value]);
  if (known) return messages[known];
  if (error?.statusCode === 403) return messages.KNOWLEDGE_SOURCE_ARCHIVE_DENIED;
  if (error?.statusCode === 404) return messages.KNOWLEDGE_SOURCE_ARCHIVE_UNAVAILABLE;
  if (error?.statusCode === 409) return messages.KNOWLEDGE_SOURCE_REVISION_CONFLICT;
  if (error?.statusCode === 422) return '附件不满足归档要求，请核对原记录与所选文件。';
  return '本次处理结果尚未确认，请确认原操作；系统不会自动重复提交。';
}
