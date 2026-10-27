// Public error copy is registered here. Never render arbitrary server/provider
// strings: even a Chinese sentence may contain a field, SQL or an input value.
const MESSAGES = Object.freeze({
  KNOWLEDGE_TEMPORARILY_UNAVAILABLE: '知识库暂不能使用',
  RESTRICTED_SESSION_EXPIRED:'登录已失效，请重新登录',
  RESTRICTED_ENDPOINT_DENIED:'当前账号无法访问此内容',
  RESTRICTED_CLIENT_UPDATE_REQUIRED:'请更新小程序后重新登录',
  RESTRICTED_CHANNEL_UNSUPPORTED:'该账号暂不支持此端登录',
  RESTRICTED_RECORD_UNAVAILABLE:'该记录暂不可用',
  RESTRICTED_RATE_LIMITED:'访问较频繁，请稍后重试',
  VALIDATION_ERROR: '请检查填写内容后重试',
  BAD_REQUEST: '请求内容不符合要求，请检查后重试',
  AUTH_REQUIRED: '请登录后继续操作', TOKEN_INVALID: '登录状态已失效，请重新登录',
  SESSION_EXPIRED: '登录已过期，请重新登录', ACTOR_INACTIVE: '账号已停用，请联系管理员',
  ACTOR_SCOPE_CHANGED: '账号权限已变化，请重新登录', TENANT_INACTIVE: '公司已停用，请联系管理员',
  PASSWORD_CHANGE_REQUIRED: '请先修改密码，再继续操作',
  INVALID_CREDENTIALS: '账号或密码错误',
  FORBIDDEN: '当前账号没有此操作权限，请联系管理员',
  NOT_FOUND: '记录不存在或当前不可访问，请刷新后查看',
  METHOD_NOT_ALLOWED: '当前版本暂不支持此操作，请刷新页面',
  CONFLICT: '记录已变化，请刷新核对后再操作',
  VERSION_CONFLICT: '记录已被更新，请刷新核对后再保存',
  PAYLOAD_TOO_LARGE: '提交内容过大，请减少内容或压缩文件',
  UNSUPPORTED_MEDIA_TYPE: '暂不支持此文件格式，请更换文件',
  RATE_LIMITED: '操作过于频繁，请稍后再试', TENANT_RATE_LIMITED: '操作过于频繁，请稍后再试',
  INTERNAL_ERROR: '服务暂时异常，请稍后重试',
  REQUEST_TIMEOUT: '读取超时，请稍后重试', DATABASE_BUSY: '服务繁忙，请稍后重试',
  SERVICE_UNAVAILABLE: '服务暂时不可用，请稍后重试',
  BAD_GATEWAY: '服务暂时不可用，请稍后重试', RESOURCE_GONE: '该功能或记录已停用，请刷新后查看',
  IDEMPOTENCY_CONFLICT: '该提交标识对应的内容已变化，请先核对原提交结果',
  IDEMPOTENCY_IN_PROGRESS: '上次提交仍在处理中，请先查看处理结果',
  IDEMPOTENCY_UNRESOLVED: '上次提交结果尚未确认，请先核对记录，勿重复提交',
  UPLOAD_INTENT_CONFLICT: '上传内容与原提交不一致，请先核对原上传结果',
  UPLOAD_INTENT_NOT_FOUND: '未找到本次上传记录，请核对后再操作',
  UPLOAD_STATUS_UNAVAILABLE: '上传结果暂不可核实，请保留原文件并稍后查看结果',
  AI_SERVICE_NOT_CONFIGURED: '智能服务尚未配置，请联系管理员',
  RUNTIME_CREDENTIAL_UNAVAILABLE: '智能服务配置暂不可用，请联系管理员',
  FINANCE_RECOVERY_READ_ONLY: '系统当前处于只读维护状态，请稍后再提交',
  TENANT_CALL_QUOTA_EXCEEDED: '智能服务额度不足，请联系管理员',
  ASR_AUDIO_INVALID: '录音文件无法解析，请重新录音',
  ASR_RATE_LIMITED: '语音服务请求过于频繁，请稍后重试',
  ASR_SERVICE_BUSY: '语音服务暂时繁忙，请稍后重试',
  ASR_UPSTREAM_FAILED: '语音识别暂时不可用，请稍后重试',
  ASR_HOTWORD_INVALID:'专用名词配置不符合要求，请联系管理员核对',
  ASR_HOTWORD_DUPLICATE:'专用名词已存在，请核对后保存',
  ASR_HOTWORD_LIMIT_EXCEEDED:'专用名词数量或长度超出限制，请精简后保存',
  ASR_HOTWORD_VERSION_CONFLICT:'专用名词配置已变化，请刷新后重新核对',
  ASR_OPERATION_CONFLICT:'本次语音处理内容已变化，请先核对原处理结果',
  ASR_OPERATION_IN_PROGRESS:'语音仍在处理中，请稍后查看结果，勿重复上传',
  ASR_SCOPE_DENIED:'当前账号没有处理或查看本次录音的权限',
  ASR_OPERATION_NOT_FOUND:'未找到本次语音处理记录，请保留录音并核对',
  ASR_SNAPSHOT_CHANGED:'识别配置已变化，已有结果已保留；请核对后明确重新处理',
  ASR_RESULT_UNKNOWN:'语音处理结果尚未确认，请先查询处理结果，勿重复上传',
  ASR_BUDGET_EXCEEDED:'本次语音处理已达到调用上限，请保留录音并联系管理员',
  ASR_CAPACITY_BUSY:'语音服务正在处理其他录音，请稍后查看或重试',
  AUDIO_EMPTY: '没有录到有效声音，请重新录音', AUDIO_TOO_LARGE: '录音时间过长，请分段录入',
  CUSTOMER_NOT_FOUND: '客户不存在或当前不可访问，请刷新后查看',
  CUSTOMER_NAME_CONFLICT: '客户已存在，请核对后再建档',
  CUSTOMER_ALREADY_EXISTS: '客户已存在，请核对后再操作',
  VISIT_REVIEW_REQUIRED: '请先完成本次拜访质检，再确认归档',
  VISIT_REVIEW_STALE: '拜访内容或审核规则已变化，请重新质检后归档',
  VISIT_PARTICIPANTS_CHANGED: '本次参与人员已变化，请重新质检后归档',
  VISIT_REVIEW_REJECTED: '本次拜访未通过质检，请完善内容后重新审核',
  OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED: '该客户尚未有效认领，请先认领客户再创建商机',
  OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED: '该客户的认领人不在你可创建商机的范围内，请更换客户',
  OPPORTUNITY_CREATE_FORBIDDEN: '当前账号无权为该客户创建商机',
  OPPORTUNITY_CUSTOMER_NOT_FOUND: '客户不存在或当前不可访问，请重新选择客户',
  OPPORTUNITY_NOT_FOUND: '商机不存在或当前不可访问，请刷新后查看',
  OPPORTUNITY_VERSION_CONFLICT: '商机已变化，请刷新核对后再保存',
  OPPORTUNITY_TRADE_RELATION_REQUIRED: '请先在交易物料中选择交易关系，再保存实际金额',
  OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN: '下游商机不能登记非零我方回款，请核对交易关系与收款归属',
  OPPORTUNITY_COLLECTION_VERSION_CONFLICT: '实际回款或来源已变化，请刷新回款状态并核对后保存',
  OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED: '请确认替换本季度现有回款来源后再保存',
  OPPORTUNITY_COLLECTION_FORBIDDEN: '当前账号无权修改此商机的实际回款',
  OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED: '本次保存标识缺失，请重新打开商机后保存',
  OPPORTUNITY_COLLECTION_INPUT_INVALID: '请检查季度实际回款金额、回款依据和备注',
  OPPORTUNITY_RECOGNIZED_FORBIDDEN: '你当前无权修改该商机的实际确收，请刷新权限后重试',
  OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT: '实际确收已被更新，请刷新确收状态、核对最新登记后再保存',
  OPPORTUNITY_RECOGNIZED_SOURCE_CONFIRMATION_REQUIRED: '本季度已有其他确收来源，请核对后确认替换，避免重复计入',
  OPPORTUNITY_RECOGNIZED_INPUT_INVALID: '请检查季度实际不含税确收金额、确收依据和备注',
  OPPORTUNITY_RECOGNIZED_MATERIALS_CHANGED: '物料或交易关系已有变化。确收输入已保留，请先撤销已修改季度的确收输入，保存商机后再登记确收',
  OPPORTUNITY_RECOGNIZED_MATERIAL_REQUIRED: '请先保存本商机的交易物料，再按物料登记实际确收',
  OPPORTUNITY_RECOGNIZED_MATERIAL_FORBIDDEN: '该物料不允许在本商机登记确收，请核对交易关系和物料归属',
  OPPORTUNITY_RECOGNIZED_PERIOD_INVALID: '请选择有效的确收年份和季度',
  OPPORTUNITY_RECOGNIZED_IDEMPOTENCY_KEY_REQUIRED: '本次保存标识缺失，请重新打开商机后保存',

  OPPORTUNITY_COLLECTION_PERIOD_INVALID: '请选择有效的回款年份和季度',
  TASK_ALREADY_INITIALIZED: "任务已接入新版执行流程，请刷新查看",
  TASK_EXECUTION_CONFIRMATION_REQUIRED: "请确认任务交付物、验收标准与执行设置",
  TASK_EXECUTION_CURSOR_INVALID: "历史翻页位置已失效，请重新打开历史记录",
  TASK_CANDIDATE_CHANGED: "候选人处理状态已变化，请刷新任务",
  TASK_CANDIDATE_INELIGIBLE: "当前账号已不具备领取此任务的资格",
  TASK_CHECKLIST_INVALID: "推进清单不完整或格式错误，请检查条目和完成标准",
  TASK_EXECUTION_EXECUTOR_INVALID: "执行人已失效或无执行权限，请重新选择",
  TASK_SUBMISSION_STALE: "本次提交已失效，请刷新查看最新轮次",
  TASK_EXECUTION_SINGLE_OWNER_REQUIRED: "请明确选择一位任务执行人",
  TASK_EXECUTION_NEXT_STEP_REQUIRED: "请填写下一步行动后保存反馈",
  TASK_LEGACY_REVIEW_REQUIRED: "请先按原流程处理当前验收，再接入轻量任务",
  TASK_EXECUTION_ACTION_UNAVAILABLE: "当前任务状态不能执行此操作，请刷新后查看",
  TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED: "此任务使用新版执行流程，请刷新页面或升级客户端",
  TASK_EXECUTION_IDEMPOTENCY_CONFLICT: "本次操作标识与原提交内容不一致，请先核对原操作结果",
  TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED: "缺少操作标识，请重新打开任务后提交",
  TASK_EXECUTION_INITIAL_CHECKLIST_REQUIRED: "任务执行设置未完整保存，请重新确认后提交",
  TASK_EXECUTION_INVALID_COMMAND: "操作信息不完整，请检查后提交",
  TASK_EXECUTION_INVALID_DECISION: "请选择有效的处理结果",
  TASK_EXECUTION_LEGACY_MODE: "此任务尚未接入新版执行流程，请先明确接入",
  TASK_EXECUTION_MODE_IMMUTABLE: "不能直接变更任务执行模式，请使用明确接入操作",
  TASK_EXECUTION_NOT_INITIALIZED: "任务执行信息暂不可用，请刷新或联系管理员",
  TASK_EXECUTION_PENDING_FLOW: "请先处理未结束的要求确认或交接，再接入新版流程",
  TASK_EXECUTION_REASON_REQUIRED: "请填写本次操作原因或完成说明",
  TASK_EXECUTION_RESPONSIBILITY_CHANGED: "任务责任人或权限已变化，请刷新后查看",
  TASK_EXECUTION_ROLLOUT_DISABLED: "当前公司暂未启用此任务创建方式",
  TASK_EXECUTOR_TARGET_CONFLICT: "新执行人不能与当前责任设置冲突，请重新选择",
  TASK_FEEDBACK_CORRECTION_FORBIDDEN: "只能更正本人发布的反馈，原记录会保留",
  TASK_FEEDBACK_INVALID: "反馈内容不完整或格式不正确，请检查后提交",
  TASK_FEEDBACK_REFERENCE_INVALID: "引用的反馈或推进项不存在于此任务，请重新选择",
  TASK_HANDOVER_CHANGED: "交接申请已变化或已被处理，请刷新后查看",
  TASK_HANDOVER_TARGET_INELIGIBLE: "交接候选人已不具备接任资格，请重新选择",
  TASK_INITIALIZE_FORBIDDEN: "当前账号不能将此任务接入新版执行流程",
  TASK_ITEM_DEFINITION_CONFLICT: "推进项定义已变化，请刷新清单后重新确认",
  TASK_ITEM_STATE_INVALID: "请选择有效的推进项状态",
  TASK_MISSING_DELIVERABLES_RESOLUTION_REQUIRED: "请说明上次验收指出的遗漏交付物如何补齐，再重新提交",
  TASK_NOT_FOUND: "任务不存在或当前账号无权查看",
  TASK_OWNER_REQUIRED: "此操作仅限当前任务执行人",
  TASK_PAUSE_CHANGED: "暂停记录已变化，请刷新后查看",
  TASK_PAUSE_RECHECK_MUST_BE_FUTURE: "暂停复查时间必须晚于当前时间",
  TASK_REQUIREMENTS_CHANGED: "任务要求变更申请已变化，请刷新后重新确认",
  TASK_REQUIREMENTS_CONFIRMATION_EXPIRED: "要求确认人已失效，请协调撤回申请后重新发起",
  TASK_REQUIREMENTS_CONFIRMATION_FORBIDDEN: "当前账号不是此要求变更的有效确认人",
  TASK_REQUIREMENTS_INVALID: "整体任务要求不完整，请填写交付物、验收标准和截止时间",
  TASK_REQUIREMENTS_RESPONSIBILITY_INVALID: "任务责任信息不完整，请先完成责任交接",
  TASK_REVIEWER_HANDOVER_REQUIRED: "请先调整验收责任，避免执行人与验收人冲突",
  TASK_REVIEWER_TARGET_INELIGIBLE: "验收接任人已不具备资格，请重新选择",
  TASK_REVIEW_DECISION_INVALID: "请选择通过或驳回，并填写必要的验收意见",
  TASK_REWORK_ITEMS_REQUIRED: "驳回时请明确返工项或遗漏的交付物",
  TASK_REWORK_ITEM_INVALID: "指定的返工项不属于本次提交，请重新选择",
  TASK_SELF_ACCEPT_SOURCE_INVALID: "此任务来源不能代替本人接受，请使用正常接受流程",
  TASK_SELF_REVIEW_FORBIDDEN: "执行人不能自行验收他人发起的任务，请办理验收交接",
  TASK_SUBMISSION_CHANGED: "完成提交已变化，请刷新后查看最新提交",
  TASK_SUBMISSION_MISSING: "缺少有效的完成提交记录，请核对任务历史",
  TASK_SUBMISSION_WITHDRAW_FORBIDDEN: "本次完成提交暂不能撤回，请刷新后查看",
  TASK_LEGACY_RESUME_REQUIRED: '此历史延期任务需要有权协调人明确恢复阶段，再开启进度管理',
  TASK_LEGACY_RESUME_INVALID: '所选恢复阶段与历史接受记录不一致，请重新核对',
  TASK_EXECUTION_MULTIPLE_OWNERS: '此历史任务有多名负责人，请先协调明确唯一主执行人，再开启进度管理',
  TASK_EXECUTION_FORBIDDEN: '当前账号没有执行此任务的权限',
  TASK_EXECUTION_NOT_FOUND: '任务不存在或当前不可访问，请刷新任务列表',
  TASK_EXECUTION_VERSION_CONFLICT: "任务、要求、责任或推进清单已更新，请刷新后重新确认",
  TASK_EXECUTION_UNKNOWN_ACTION: '此任务暂不支持该操作，请刷新后查看',
  TASK_EXECUTION_INVALID_STATE: "任务状态已变化，请刷新后查看",
  TASK_EXECUTION_INVALID_INPUT: '任务信息不完整或格式不正确，请检查后提交',
  OPPORTUNITY_SAVE_CANCELLED: '本次商机保存已取消，请重新确认后操作',
  OPPORTUNITY_ACCESS_DENIED: '商机不存在或当前账号没有操作权限',
  OPPORTUNITY_COLLABORATION_RETIRED: '商机协作设置已取消，请返回商机继续操作',
  INVALID_ACCESS_TARGET: '请选择有效且不重复的协作成员',
  OPPORTUNITY_INTENT_HISTORY_CHANGED: '商机保存记录已变化，请刷新后重新查看',
  OPPORTUNITY_CHANGE_HISTORY_CHANGED: '商机变更记录已变化，请刷新后重新查看',
  WORK_REPORT_SCOPE_CHANGED: '报告范围或权限已变化，请重新读取后核对',
  WORK_REPORT_NOT_FOUND: '报告不存在或当前不可访问',
  WORK_REPORT_VERSION_CONFLICT: '报告已变化，请刷新核对后再保存',
  WORK_REPORT_MODEL_UNAVAILABLE: '报告生成服务暂不可用，请稍后再试',
  WORK_REPORT_JOB_FAILED: '报告材料准备或生成失败，请稍后重试；持续失败请联系管理员',
  RUN_FAILED: '智能处理未完成，请检查输入后重试',
  RUN_CANCELLED: '智能处理已取消，可核对输入后重新操作',
  KNOWLEDGE_ACCESS_DENIED: '当前没有该材料的访问权限',
  KNOWLEDGE_MATERIAL_NOT_FOUND: '材料不存在或当前不可访问',
  KNOWLEDGE_FILE_TOO_LARGE: '文件过大，请压缩后重新上传',
  KNOWLEDGE_FILE_SIZE_INVALID: '文件为空，请选择有效文件',
  KNOWLEDGE_INPUT_INVALID: '请检查材料信息后重新提交',
  KNOWLEDGE_CATALOG_UNCONFIGURED: '知识库目录尚未配置，请联系管理员',
  KNOWLEDGE_CATALOG_CHANGED: '目录已变化，请重新读取并核对',
  KNOWLEDGE_NAME_CONFLICT: '存在同名资料，请选择已有材料新增版本或修改名称',
  KNOWLEDGE_PREVIEW_UNSUPPORTED: '此格式暂不支持在线预览，请下载查看',
  KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED: '材料生成尚未配置，请联系管理员；已有作品仍可查看',
  KNOWLEDGE_DOCUMENT_SOURCE_LIMIT: '所选材料过多，请减少材料或缩短用途后重新提交',
  KNOWLEDGE_DOCUMENT_INPUT_INVALID: '请检查参考材料和用途后重新提交',
  KNOWLEDGE_WORK_NOT_FOUND: '作品不存在或当前不可访问，请刷新列表',
  KNOWLEDGE_WORK_REVISION_CONFLICT: '作品共享范围已变化，请重新读取后核对',
  KNOWLEDGE_WORK_CHANGED: '作品或共享范围已变化，请重新读取后核对',
});

// Retain reviewed, actionable legacy responses while old servers are supported.
// This is an exact allowlist, not a language/regex heuristic for unknown text.
const LEGACY_MESSAGES = new Set([
  ...Object.values(MESSAGES),
  '当前无权归档此客户拜访', '质检正文与提交内容不一致，请重新核对',
  '当前身份没有此操作权限', '当前账号无权执行此操作',
  '客户已存在，请核对后再建档', '客户已存在，请核对后再修改',
  '客户或商机不存在或不在当前权限范围内', '记录版本已变化，请刷新后重试',
  '配置版本已变化，请刷新', '商机或物料已变化，请刷新后重试',
  '客户来源不在当前选项中，请重新选择', '客户类型不在当前选项中，请重新选择',
  '客户不存在或不可用于录入', '商机不存在、不可见或不属于当前客户',
  '客户或关联商机不在本次跟进操作的授权范围，请重新选择',
  '请使用本人已解析完成的材料', '结构化原始记录不存在或客户已变化，请重新整理',
  '请使用结构化原始记录作为来源', '原始材料已变化，请重新整理', '原商机已变化，请重新整理',
  '客户类型请选择客户或伙伴', '客户名称已变化，请重新核对', '请输入拜访原文',
  '请重新进入拜访核对页，仅选择本次参与的FDE；商机完整名单请在商机详情维护',
  '内容或关联已修改，请重新审核', '本次参与人员已修改，请重新审核后归档',
  '同时有其他修改正在保存，本次归档未完成，请刷新后重试',
  '请重新执行AI审核后再确认归档', '审核记录不存在、已使用或不属于当前账号',
  '关联商机已修改，请按当前商机重新执行AI审核', '审核权限信息已失效，请重新执行AI审核',
  '请完成独立质检后再确认归档', '拜访内容或关联已修改，请重新质检',
  '审核规则已更新，请重新质检', '服务端下一步审核未通过',
  '拜访审核未返回当前公司评分政策回执', 'AI总分格式不正确，请重新审核',
  'AI未返回下一步审核结果', '下一步时间和行动审核格式不正确', '下一步审核缺少时间或行动依据',
]);

const FIELD_LABELS = Object.freeze({
  name: '名称', title: '标题', company_code: '公司编码', account_code: '账号', password: '密码',
  old_password: '原密码', new_password: '新密码', otp: '验证码',
  customer_id: '客户', opportunity_id: '商机', opportunity_ids: '关联商机',
  customer_type: '客户类型', industry: '所属行业', source: '客户来源', level_code: '客户优先级',
  contact_name: '联系人姓名', contact_title: '联系人职位', contact_role: '联系人角色',
  partner_id: '合作伙伴', partner_name: '合作伙伴', target_team: '所属团队', target_team_id: '所属团队',
  stage: '商机阶段', stage_code: '商机阶段', amount: '金额', expected_close_date: '预计关单日期',
  follow_up_record: '跟进内容', next_action: '下一步计划', visit_goal: '跟进目标',
  interaction_at: '跟进日期', interaction_mode_code: '跟进方式', expectation_code: '目标达成情况',
  created_date: '创建日期', duration_minutes: '跟进时长', collaborator_ids: '协同人',
  fde_participant_ids: '协助人员', description: '任务内容', due_at: '截止时间', deliverable: '整体交付物', acceptance_criteria: '完成标准', checklist: '推进清单', next_step: '下一步', blocker: '问题与阻碍', evidence_urls: '成果链接',
  assignee_id: '负责人', assignee_ids: '负责人', file: '文件', q: '搜索内容',
  trade_relation: '交易关系', collection_actuals: '季度实际回款', recognized_actuals: '季度实际不含税确收',
});
const COLLECTION_FIELD_LABELS = Object.freeze({
  amount: '季度实际回款金额', source_ref: '回款依据', note: '回款备注',
  year: '回款年份', quarter: '回款季度', replace_source: '回款来源替换确认',
});
const RECOGNIZED_FIELD_LABELS = Object.freeze({
  amount: '季度累计不含税确收金额', source_ref: '确收依据', note: '确收备注',
  year: '确收年份', quarter: '确收季度', material_line_id: '确收物料', entries: '确收物料明细', replace_source: '确收来源替换确认',
});
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const validCode = value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,99}$/.test(value) ? value : '';
function codeFor(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return '';
  return validCode(body.code) || validCode(body.detail && body.detail.code) || validCode(body.detail);
}
function validationLocations(detail) {
  if (!Array.isArray(detail)) return [];
  return detail.slice(0, 50).filter(item => item && Array.isArray(item.loc) &&
    item.loc.length > 0 && item.loc.length <= 8 && item.loc.every(part =>
      (typeof part === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,99}$/.test(part)) ||
      (Number.isSafeInteger(part) && part >= 0)) &&
    typeof item.type === 'string' && /^[a-z][a-z0-9_]{0,79}$/.test(item.type))
    .map(item => ({loc: item.loc.slice(), type: item.type}));
}
function validationMessage(detail) {
  const errors = validationLocations(detail), labels = [];
  for (const item of errors) {
    const key = item.loc[item.loc.length - 1];
    const collection = item.loc.includes('collection_actuals');
    const recognized = item.loc.includes('recognized_actuals');
    if (recognized && key === 'expected_actual_revision') return MESSAGES.OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT;
    if (collection && key === 'expected_actual_revision') return MESSAGES.OPPORTUNITY_COLLECTION_VERSION_CONFLICT;
    const label = recognized && own(RECOGNIZED_FIELD_LABELS, key) ? RECOGNIZED_FIELD_LABELS[key]
      : collection && own(COLLECTION_FIELD_LABELS, key) ? COLLECTION_FIELD_LABELS[key]
      : own(FIELD_LABELS, key) ? FIELD_LABELS[key] : '';
    if (label && !labels.includes(label)) labels.push(label);
  }
  if (!labels.length) return MESSAGES.VALIDATION_ERROR;
  const required = errors.length && errors.every(item => item.type === 'missing');
  return `${required ? '请填写' : '请检查'}${labels.slice(0, 3).join('、')}${labels.length > 3 ? '等信息' : ''}${required ? '' : '的填写内容'}`;
}
function legacyMessage(body) {
  for (const candidate of [body && body.message, body && body.detail, body && body.detail && body.detail.message]) {
    if (typeof candidate === 'string' && LEGACY_MESSAGES.has(candidate)) return candidate;
  }
  return '';
}
function fallbackFor(status) {
  if (status >= 500) return MESSAGES.INTERNAL_ERROR;
  return ({400: MESSAGES.BAD_REQUEST, 401: MESSAGES.AUTH_REQUIRED, 403: MESSAGES.FORBIDDEN,
    404: MESSAGES.NOT_FOUND, 405: MESSAGES.METHOD_NOT_ALLOWED, 408: MESSAGES.REQUEST_TIMEOUT,
    409: MESSAGES.CONFLICT, 410: '该功能或记录已停用，请刷新后查看', 413: MESSAGES.PAYLOAD_TOO_LARGE,
    415: MESSAGES.UNSUPPORTED_MEDIA_TYPE, 422: MESSAGES.VALIDATION_ERROR, 429: MESSAGES.RATE_LIMITED})[status]
    || '操作未完成，请稍后重试';
}
function messageFor(body, status, recovery = {}) {
  if (recovery.outcomeUnknown) return '提交结果待确认，请先查看记录，勿重复提交';
  if (status === 422 && Array.isArray(body && body.detail)) return validationMessage(body.detail);
  const reviewed = legacyMessage(body);
  if (reviewed) return reviewed;
  const code = codeFor(body);
  if (own(MESSAGES, code)) return MESSAGES[code];
  return fallbackFor(status);
}
function businessMessage(error, fallback = MESSAGES.RUN_FAILED) {
  const code = validCode(error && (error.serverCode || error.code || error.error_code));
  if (own(MESSAGES, code)) return MESSAGES[code];
  return legacyMessage(error) || fallback;
}
const KNOWN_REJECTIONS = new Set(['KNOWLEDGE_TEMPORARILY_UNAVAILABLE', 'AI_SERVICE_NOT_CONFIGURED', 'RUNTIME_CREDENTIAL_UNAVAILABLE',
  'FINANCE_RECOVERY_READ_ONLY', 'TENANT_CALL_QUOTA_EXCEEDED', 'KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED']);
function isKnownRejection(code) { return KNOWN_REJECTIONS.has(code); }
module.exports = {MESSAGES, FIELD_LABELS, codeFor, validationLocations, validationMessage, messageFor, businessMessage, isKnownRejection};
