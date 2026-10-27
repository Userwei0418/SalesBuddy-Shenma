import {importedSource} from './model.js';

const states = new Set(['saved', 'syncing', 'parsing', 'retrievable', 'failed', 'deleted']);
const availability = new Set(['unconfigured', 'files_required', 'links_not_archived', 'ready', 'running', 'retryable', 'unknown', 'complete', 'stale']);
const uuid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || '');
export function assertIngestion(value, materialId, versionId) {
  if (!value || value.material_id !== materialId || value.version_id !== versionId || !states.has(value.state)
    || !availability.has(value.availability) || typeof value.can_retry !== 'boolean'
    || (value.ingestion_id !== null && !uuid(value.ingestion_id))
    || !Number.isSafeInteger(value.total_files) || value.total_files < 0
    || !Number.isSafeInteger(value.processed_files) || value.processed_files < 0 || value.processed_files > value.total_files
    || (value.error_code !== null && typeof value.error_code !== 'string')
    || (value.observed_at !== null && (typeof value.observed_at !== 'string' || !Number.isFinite(Date.parse(value.observed_at))))) {
    throw Error('解析状态与当前材料版本不匹配，请重新读取');
  }
  return value;
}

export function ingestionPresentation(value) {
  const copy = {
    unconfigured: ['待解析', '检索服务尚未配置，材料已保存；配置完成后可在此开始解析。', 'info'],
    files_required: ['待解析', '此版本没有已归档附件，请新增版本并上传材料文件。', 'info'],
    links_not_archived: ['待解析', '链接已保存，但不会自动抓取为检索资料；请取得文件后新增版本并上传附件。', 'info'],
    ready: ['待解析', '附件已归档，可以开始解析。完成并验证后才会用于检索。', 'info'],
    running: ['解析中', '正在同步和解析附件，可点击“重新读取”查看最新状态。', 'info'],
    retryable: ['解析失败', '本次处理未完成，可安全重试解析，无需重新上传附件。', 'warning'],
    unknown: ['处理结果待核对', '附件上传至检索服务的结果未确认，请联系管理员核对；暂不提供重试，避免重复上传。', 'warning'],
    complete: ['可检索', '该版本已完成解析和检索验证。', 'success'],
    stale: ['此版本不可重新解析', '材料版本或权限已变化，请重新读取并查看当前版本。', 'info'],
  };
  const [title, description, type] = value?.state === 'deleted' ? ['已停用', '此版本已停用，不可重新解析。', 'info']
    : copy[value?.availability] || ['状态待确认', '请重新读取解析状态。', 'warning'];
  const canRetry = value?.can_retry === true && ['ready', 'retryable'].includes(value.availability) && !['deleted', 'retrievable'].includes(value.state);
  return {title, description, type, canRetry, button: value?.availability === 'retryable' ? '重试解析' : '开始解析'};
}

export function ingestionErrorText(error) {
  if (error?.statusCode === 403) return '当前没有操作该材料的权限，请重新读取';
  if (error?.statusCode === 404) return '材料版本不存在或已不可访问，请重新读取';
  if (error?.statusCode === 409) return '材料版本或处理状态已变化，请重新读取后核对';
  if (error?.statusCode === 422) return '当前版本不满足解析条件，请核对附件和材料版本';
  // Do not render provider bodies, document IDs, signed URLs or unknown error codes.
  return '解析请求结果未确认，请重新读取状态后核对；系统不会自动重复提交';
}

// Recheck both authorization and optimistic revision immediately before the
// explicit action. A saved/list state is never permission to start ingestion.
export async function requestIngestion(service, context, key, stillCurrent = () => true) {
  const {material_id, version_id, expected_material_revision} = context;
  const current = () => { if (!stillCurrent()) throw Error('身份或页面已变化，请重新打开材料'); };
  current();
  const detail = await service.getMaterial(material_id); current();
  const version = detail?.versions?.find(row => row.version_id === version_id);
  if (detail?.material_id !== material_id || detail.material_revision !== expected_material_revision || !version || version.state === 'deleted' || importedSource(version.contribution)) {
    throw Object.assign(Error('材料版本已变化，请重新读取'), {statusCode: 409});
  }
  const status = assertIngestion(await service.getIngestion(material_id, version_id), material_id, version_id); current();
  if (!ingestionPresentation(status).canRetry) throw Object.assign(Error('当前状态不允许重新解析，请重新读取'), {statusCode: 409});
  const result = assertIngestion(await service.startIngestion(material_id, version_id, {expected_material_revision}, key), material_id, version_id);
  current();
  return result;
}
