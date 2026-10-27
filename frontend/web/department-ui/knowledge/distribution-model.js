export const DISTRIBUTION_SOURCE = 'salesbuddy.customer_distribution';
export const QUESTION_FIELDS = [{name:'name',label:'姓名',maxLength:100},{name:'company',label:'公司',maxLength:200},{name:'contact',label:'联系方式',maxLength:200},{name:'contact_intent',label:'联系意向',maxLength:500}];
export const publicErrorCode = value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,99}$/.test(value) ? value : '';
const messages = {
  KNOWLEDGE_TEMPORARILY_UNAVAILABLE:'知识库暂不能使用',
  KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED:'资料正文或来源状态不满足发布条件，请核对归档文件和原表状态',
  KNOWLEDGE_DISTRIBUTION_DENIED:'当前没有资料分发或所选统计范围的权限',
  KNOWLEDGE_QUESTIONNAIRE_REQUIRED:'请先填写问卷，再查看正文或下载资料',
  KNOWLEDGE_SHARE_UNAVAILABLE:'分享已失效、被撤销或资料已下架，请联系分享人',
  KNOWLEDGE_PUBLICATION_CONFLICT:'发布版本已变化，请重新读取并核对后保存',
  KNOWLEDGE_QUESTIONNAIRE_CHANGED:'问卷已更新，请重新读取后填写',
  KNOWLEDGE_PREVIEW_UNSUPPORTED:'此文件暂不支持在线预览，请下载查看',
  KNOWLEDGE_PUBLIC_RATE_LIMIT:'操作较频繁，请稍后重试',
};
export function distributionError(error) {
  const code = publicErrorCode(error?.serverCode) || publicErrorCode(error?.code) || publicErrorCode(error?.message);
  const disabled = code === 'KNOWLEDGE_TEMPORARILY_UNAVAILABLE';
  const message = !disabled && error?.outcomeUnknown ? '提交结果尚未确认，请先重新读取处理结果，勿重复提交'
    : messages[code] || (error?.statusCode === 422 ? '请检查版本、文件和问卷设置是否完整' : '请求未完成，请重新读取确认；系统不会自动重复提交');
  return Object.assign(new Error(message), {code, serverCode:code, statusCode:error?.statusCode,
    outcomeUnknown:!disabled && error?.outcomeUnknown === true, retryable:!disabled && error?.retryable === true,
    ...(['check_status','retry_read','login','contact_admin','correct_request','refresh'].includes(error?.recoveryAction) ? {recoveryAction:error.recoveryAction} : {}),
    ...(disabled ? {recoveryAction:'contact_admin'} : {}),
    ...(typeof error?.requestId === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(error.requestId) ? {requestId:error.requestId} : {}),
  });
}

export function distributionEntry(options={}) {
  const id=options.material_id;
  return typeof id==='string'&&/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id)
    ? {materialId:id,distribution:String(options.distribution)==='1'} : null;
}
