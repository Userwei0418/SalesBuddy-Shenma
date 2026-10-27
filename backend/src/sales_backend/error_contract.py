"""Public API failures: stable codes, safe copy, traceability and recovery.

Exception text is diagnostic data, never a display message. A new business
message must be registered here (or added to its module's code catalogue), not
made public by passing str(exc). Legacy detail shapes remain available while
clients migrate to code/message.
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any
from uuid import UUID, uuid4

from fastapi.responses import JSONResponse

from sales_backend.domain.opportunity_creation import CODES as OPPORTUNITY_CREATION_ERRORS
from sales_backend.domain.collection_actual_errors import COLLECTION_ACTUAL_ERRORS
from sales_backend.domain.recognized_actual_errors import RECOGNIZED_ACTUAL_ERRORS

CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
request_error_context: ContextVar[tuple[str, str] | None] = ContextVar("request_error_context", default=None)

MESSAGES = {
    **OPPORTUNITY_CREATION_ERRORS,
    "KNOWLEDGE_TEMPORARILY_UNAVAILABLE": "知识库暂不能使用",
    "RESTRICTED_SESSION_EXPIRED": "登录已失效，请重新登录",
    "RESTRICTED_ENDPOINT_DENIED": "当前账号无法访问该页面或执行此操作",
    "RESTRICTED_CLIENT_UPDATE_REQUIRED": "请更新小程序后重新登录",
    "RESTRICTED_CHANNEL_UNSUPPORTED": "该账号暂不支持此端登录",
    "RESTRICTED_RECORD_UNAVAILABLE": "该记录不存在或当前不可查看",
    "RESTRICTED_RATE_LIMITED": "请求较多，请稍后重试",
    "ASR_HOTWORD_INVALID": "热词格式不正确，请检查名称和适用场景",
    "ASR_HOTWORD_DUPLICATE": "存在重复热词，请编辑已有词条",
    "ASR_HOTWORD_LIMIT_EXCEEDED": "词条或场景有效词数量超限，请减少后保存",
    "ASR_HOTWORD_VERSION_CONFLICT": "热词已被其他人更新，请刷新后重新确认，当前编辑未保存",
    "ASR_OPERATION_CONFLICT": "操作标识与原请求内容不一致，请先核对原操作",
    "ASR_OPERATION_NOT_FOUND": "操作记录不存在或当前账号无权查看",
    "ASR_SNAPSHOT_CHANGED": "识别配置已变化，原结果已保留，请确认后重新处理",
    "ASR_RESULT_UNKNOWN": "识别结果暂未确认，请保留录音并查看处理状态，勿重复识别",
    "ASR_OPERATION_IN_PROGRESS": "录音正在识别，请查看已有任务进度",
    "ASR_BUDGET_EXCEEDED": "语音识别预算已用完，请联系管理员或稍后处理",
    "ASR_CAPACITY_BUSY": "语音识别通道忙，请稍后继续，录音尚未发送",
    "ASR_SCOPE_DENIED": "当前账号无权执行本次语音识别",
    "ASR_UPSTREAM_FAILED": "本次语音识别失败，录音已保留，请核对后再处理",
    "ASR_AUDIO_INVALID": "录音文件无法解码，请确认文件完整后重试",
    "BAD_REQUEST": "请求内容不符合要求，请检查后重试",
    "VALIDATION_ERROR": "填写内容不完整或格式不正确，请检查后再提交",
    "AUTH_REQUIRED": "请先登录后继续操作",
    "INVALID_CREDENTIALS": "账号或密码错误",
    "TOKEN_INVALID": "登录状态已失效，请重新登录",
    "SESSION_EXPIRED": "登录已过期，请重新登录",
    "ACTOR_INACTIVE": "当前账号已停用，请联系管理员",
    "ACTOR_SCOPE_CHANGED": "账号权限已更新，请重新登录",
    "TENANT_INACTIVE": "当前企业暂不可用，请联系管理员",
    "PASSWORD_CHANGE_REQUIRED": "请先修改密码后继续操作",
    "FORBIDDEN": "当前账号没有此操作权限，请联系管理员",
    "NOT_FOUND": "记录不存在或已不可访问，请刷新后查看",
    "METHOD_NOT_ALLOWED": "当前操作暂不支持，请刷新页面后重试",
    "TARGET_INPUT_INVALID": "请核对目标指标、金额精度和版本后重新提交",
    "TARGET_METRIC_UNAVAILABLE": "目标指标已停用或不可配置，请重新选择指标",
    "TARGET_VALUE_INVALID": "请核对目标金额及小数精度；数量指标须填写整数",
    "TARGET_PENDING_CONFLICT": "已有相关目标申请待审批，请等待处理后再调整",
    "CONFLICT": "记录状态已变化，请刷新后核对再操作",
    "VERSION_CONFLICT": "记录已被更新，请刷新后重新确认",
    "RESOURCE_GONE": "该记录或功能已不可用，请刷新后查看",
    "PAYLOAD_TOO_LARGE": "提交内容过大，请减少内容或选择较小的文件",
    "UNSUPPORTED_MEDIA_TYPE": "文件或内容格式不支持，请更换后重试",
    "RATE_LIMITED": "操作过于频繁，请稍后再试",
    "TENANT_RATE_LIMITED": "当前请求较多，请稍后再试",
    "INTERNAL_ERROR": "服务暂时异常，请稍后重试",
    "BAD_GATEWAY": "服务连接暂时异常，请稍后重试",
    "SERVICE_UNAVAILABLE": "服务暂时不可用，请稍后再试",
    "REQUEST_TIMEOUT": "处理时间较长，请稍后重试",
    "DATABASE_BUSY": "服务正在处理较多请求，请稍后再试",
    "AI_SERVICE_NOT_CONFIGURED": "智能服务暂不可用，请联系管理员检查配置",
    "RUNTIME_CREDENTIAL_UNAVAILABLE": "智能服务暂不可用，请联系管理员检查配置",
    "TENANT_CALL_QUOTA_EXCEEDED": "智能服务调用额度已用完，请联系管理员",
    "KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED": "知识内容生成服务尚未配置，请联系管理员",
    "FINANCE_RECOVERY_READ_ONLY": "当前为只读恢复模式，暂不支持保存，请联系管理员恢复服务",
    "IDEMPOTENCY_CONFLICT": "本次提交标识与原内容不一致，请先核对原提交结果",
    "IDEMPOTENCY_IN_PROGRESS": "本次提交正在处理中，请稍后查看结果",
    "IDEMPOTENCY_UNRESOLVED": "本次提交结果尚未确认，请先查看处理结果，勿重复提交",
    "OPPORTUNITY_SAVE_CANCELLED": "本次商机保存已取消，请重新确认后操作",
    "TASK_EXECUTION_FORBIDDEN": "当前账号无权执行此任务操作",
    "TASK_EXECUTION_UNKNOWN_ACTION": "该任务操作不存在或已不可用，请刷新后查看",
    "TASK_EXECUTION_INVALID_INPUT": "任务信息不完整或格式不正确，请检查后提交",
    "OPPORTUNITY_ACCESS_DENIED": "商机不存在或当前账号没有操作权限",
    "OPPORTUNITY_COLLABORATION_RETIRED": "商机协作名单设置已停用，请刷新页面",
    "INVALID_ACCESS_TARGET": "请选择有效且不重复的协作成员",
    "OPPORTUNITY_INTENT_HISTORY_CHANGED": "商机保存记录已变化，请刷新后重新查看",
    "OPPORTUNITY_CHANGE_HISTORY_CHANGED": "商机变更记录已变化，请刷新后重新查看",
    "UPLOAD_INTENT_CONFLICT": "上传内容与原提交不一致，请先核对原上传结果",
    "UPLOAD_INTENT_NOT_FOUND": "未找到本次上传记录，请核对后再操作",
    "UPLOAD_STATUS_UNAVAILABLE": "上传结果暂不可核实，请保留原文件并稍后查看结果",
    "CUSTOMER_ALREADY_EXISTS": "客户已存在，请核对后再操作",
    "VISIT_REVIEW_REQUIRED": "请先完成本次拜访质检，再确认归档",
    "VISIT_REVIEW_STALE": "拜访内容或审核规则已变化，请重新质检后归档",
    "VISIT_PARTICIPANTS_CHANGED": "本次参与人员已变化，请重新质检后归档",
    "VISIT_REVIEW_REJECTED": "本次拜访未通过质检，请完善内容后重新审核",
}

MESSAGES.update({code: value[1] for code, value in COLLECTION_ACTUAL_ERRORS.items()})
MESSAGES.update({code: value[1] for code, value in RECOGNIZED_ACTUAL_ERRORS.items()})

# Task execution failures keep stable codes across every client and export surface.
MESSAGES.update({
    "TASK_EXECUTION_EXECUTOR_INVALID": "当前执行人已失效或不具备执行权限，请重新选择",
    "TASK_SUBMISSION_STALE": "本次完成提交已失效，请刷新后查看最新轮次",
    "TASK_EXECUTION_SINGLE_OWNER_REQUIRED": "请明确选择一位任务执行人",
    "TASK_EXECUTION_NEXT_STEP_REQUIRED": "请填写下一步行动",
    "TASK_LEGACY_RESUME_REQUIRED": "此历史延期任务需要有权协调人明确恢复阶段，再接入新版流程",
    "TASK_LEGACY_RESUME_INVALID": "所选恢复阶段与历史接受记录不一致，请重新核对",
    "TASK_EXECUTION_MULTIPLE_OWNERS": "此历史任务有多位主责，请先通过任务协调明确唯一主责，再接入新版流程",
    "TASK_CANDIDATES_FORBIDDEN": "当前账号不能为此任务选择交接候选人",
    "TASK_CANDIDATES_INPUT_INVALID": "交接候选查询参数不正确，请重新搜索",
    "TASK_ALREADY_INITIALIZED": "任务已接入新版执行流程，请刷新查看",
    "TASK_EXECUTION_CONFIRMATION_REQUIRED": "请确认任务交付物、验收标准与执行设置",
    "TASK_EXECUTION_CURSOR_INVALID": "历史翻页位置已失效，请重新打开历史记录",
    "TASK_CANDIDATE_CHANGED": "候选人处理状态已变化，请刷新任务",
    "TASK_CANDIDATE_INELIGIBLE": "当前账号已不具备领取此任务的资格",
    "TASK_CHECKLIST_INVALID": "推进清单不完整或格式错误，请检查条目和完成标准",
    "TASK_EXECUTION_ACTION_UNAVAILABLE": "当前任务状态不能执行此操作，请刷新后查看",
    "TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED": "此任务使用新版执行流程，请刷新页面或升级客户端",
    "TASK_EXECUTION_IDEMPOTENCY_CONFLICT": "本次操作标识与原提交内容不一致，请先核对原操作结果",
    "TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED": "缺少操作标识，请重新打开任务后提交",
    "TASK_EXECUTION_INITIAL_CHECKLIST_REQUIRED": "任务执行设置未完整保存，请重新确认后提交",
    "TASK_EXECUTION_INVALID_COMMAND": "操作信息不完整，请检查后提交",
    "TASK_EXECUTION_INVALID_DECISION": "请选择有效的处理结果",
    "TASK_EXECUTION_INVALID_STATE": "任务状态已变化，请刷新后查看",
    "TASK_EXECUTION_LEGACY_MODE": "此任务尚未接入新版执行流程，请先明确接入",
    "TASK_EXECUTION_MODE_IMMUTABLE": "不能直接变更任务执行模式，请使用明确接入操作",
    "TASK_EXECUTION_NOT_INITIALIZED": "任务执行信息暂不可用，请刷新或联系管理员",
    "TASK_EXECUTION_PENDING_FLOW": "请先处理未结束的要求确认或交接，再接入新版流程",
    "TASK_EXECUTION_REASON_REQUIRED": "请填写本次操作原因或完成说明",
    "TASK_EXECUTION_RESPONSIBILITY_CHANGED": "任务责任人或权限已变化，请刷新后查看",
    "TASK_EXECUTION_ROLLOUT_DISABLED": "当前公司暂未启用此任务创建方式",
    "TASK_EXECUTION_VERSION_CONFLICT": "任务、要求、责任或推进清单已更新，请刷新后重新确认",
    "TASK_EXECUTOR_TARGET_CONFLICT": "新执行人不能与当前责任设置冲突，请重新选择",
    "TASK_FEEDBACK_CORRECTION_FORBIDDEN": "只能更正本人发布的反馈，原记录会保留",
    "TASK_FEEDBACK_INVALID": "反馈内容不完整或格式不正确，请检查后提交",
    "TASK_FEEDBACK_REFERENCE_INVALID": "引用的反馈或推进项不存在于此任务，请重新选择",
    "TASK_HANDOVER_CHANGED": "交接申请已变化或已被处理，请刷新后查看",
    "TASK_HANDOVER_TARGET_INELIGIBLE": "交接候选人已不具备接任资格，请重新选择",
    "TASK_INITIALIZE_FORBIDDEN": "当前账号不能将此任务接入新版执行流程",
    "TASK_ITEM_DEFINITION_CONFLICT": "推进项定义已变化，请刷新清单后重新确认",
    "TASK_ITEM_STATE_INVALID": "请选择有效的推进项状态",
    "TASK_MISSING_DELIVERABLES_RESOLUTION_REQUIRED": "请说明上次验收指出的遗漏交付物如何补齐，再重新提交",
    "TASK_NOT_FOUND": "任务不存在或当前账号无权查看",
    "TASK_OWNER_REQUIRED": "此操作仅限当前任务执行人",
    "TASK_PAUSE_CHANGED": "暂停记录已变化，请刷新后查看",
    "TASK_PAUSE_RECHECK_MUST_BE_FUTURE": "暂停复查时间必须晚于当前时间",
    "TASK_REQUIREMENTS_CHANGED": "任务要求变更申请已变化，请刷新后重新确认",
    "TASK_REQUIREMENTS_CONFIRMATION_EXPIRED": "要求确认人已失效，请协调撤回申请后重新发起",
    "TASK_REQUIREMENTS_CONFIRMATION_FORBIDDEN": "当前账号不是此要求变更的有效确认人",
    "TASK_REQUIREMENTS_INVALID": "整体任务要求不完整，请填写交付物、验收标准和截止时间",
    "TASK_REQUIREMENTS_RESPONSIBILITY_INVALID": "任务责任信息不完整，请先完成责任交接",
    "TASK_REVIEWER_HANDOVER_REQUIRED": "请先调整验收责任，避免执行人与验收人冲突",
    "TASK_REVIEWER_TARGET_INELIGIBLE": "验收接任人已不具备资格，请重新选择",
    "TASK_REVIEW_DECISION_INVALID": "请选择通过或驳回，并填写必要的验收意见",
    "TASK_REWORK_ITEMS_REQUIRED": "驳回时请明确返工项或遗漏的交付物",
    "TASK_REWORK_ITEM_INVALID": "指定的返工项不属于本次提交，请重新选择",
    "TASK_SELF_ACCEPT_SOURCE_INVALID": "此任务来源不能代替本人接受，请使用正常接受流程",
    "TASK_SELF_REVIEW_FORBIDDEN": "执行人不能自行验收他人发起的任务，请办理验收交接",
    "TASK_SUBMISSION_CHANGED": "完成提交已变化，请刷新后查看最新提交",
    "TASK_LEGACY_REVIEW_REQUIRED": "请先按原流程处理这次验收，再接入轻量流程",
    "TASK_SUBMISSION_MISSING": "缺少有效的完成提交记录，请核对任务历史",
    "TASK_SUBMISSION_WITHDRAW_FORBIDDEN": "本次完成提交暂不能撤回，请刷新后查看"
})

STATUS_CODES = {
    400: "BAD_REQUEST", 401: "AUTH_REQUIRED", 403: "FORBIDDEN", 404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED", 408: "REQUEST_TIMEOUT", 409: "CONFLICT", 410: "RESOURCE_GONE",
    413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE", 422: "VALIDATION_ERROR",
    429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 502: "BAD_GATEWAY", 503: "SERVICE_UNAVAILABLE",
    504: "REQUEST_TIMEOUT",
}

# Exact reviewed legacy prose. No regular expression or language heuristic can
# safely distinguish an arbitrary exception string from a user-facing message.
PUBLIC_MESSAGES = frozenset(MESSAGES.values()) | frozenset({
    "请求字段不符合要求，请检查填写内容", "当前账号无权执行此操作", "记录不存在",
    "记录已存在，请刷新后核对", "客户不存在", "商机不存在", "任务不存在",
    "客户已存在，请核对后再建档", "客户已存在，请核对后再修改",
    "公司标识无效", "没有此公司的管理权限", "没有此公司的管理权限，或公司已停用",
    "公司入口无效或暂不可用，请向公司管理员核对", "配置版本已变化，请刷新",
    "客户或商机不存在或不在当前权限范围内", "商机或物料已变化，请刷新后重试",
    "手机号已被其他账号使用，请核对或联系管理员处理", "该账号名为演示专用，请使用其他账号名",
    "公司启用账号额度已用完，请停用不使用的账号或联系平台调整额度",
    "请选择有效且不重复的协作成员", "商机不存在或当前动作无权", "商机已变化，请刷新后重新确认",
    "未找到本次上传意图", "上传结果暂不可核实，请保留原文件并查回",
    "上传结果暂不可核实，请保留原文件稍后查询", "文件任务不存在",
    "导出服务正在处理其他请求，请稍后重试", "导出查询超时，请减少分类后重试",
    "上传服务繁忙，请稍后重试", "不能选择未来日期", "当前账号无权查询保存结果，请保留原提交标识",
    "当前账号无权终止提交，请保留原提交标识并查询结果", "当前状态无法执行此操作，请刷新后重试",
    "记录已被他人更新，请刷新后再试", "这次商机提交已终止，请重新填写并发起新的提交",
    "该提交标识已用于不同内容，请刷新后重新提交", "必须提交正整数商机版本",
    "来源商机或目标团队不在转交授权范围内", "请选择有效销售及其有效任职团队",
    "本阶段仅允许转交进行中的商机", "历史业绩归属尚未确认，不能执行本次转交",
    "仅当前商机主责可维护协作人", "协作人须为有效企业成员且不能重复主责",
    "客户不存在或不可用于录入", "商机不存在、不可见或不属于当前客户",
    "请使用本人已解析完成的材料", "结构化原始记录不存在或客户已变化，请重新整理",
    "请使用结构化原始记录作为来源", "原始材料已变化，请重新整理", "原商机已变化，请重新整理",
    "客户类型请选择客户或伙伴", "客户名称已变化，请重新核对", "请输入拜访原文",
    "商机不存在或不在当前权限范围内", "客户或关联商机不在本次跟进操作的授权范围，请重新选择",
    "请重新进入拜访核对页，仅选择本次参与的FDE；商机完整名单请在商机详情维护",
    "内容或关联已修改，请重新审核", "本次参与人员已修改，请重新审核后归档",
    "同时有其他修改正在保存，本次归档未完成，请刷新后重试",
    "请重新执行AI审核后再确认归档", "审核记录不存在、已使用或不属于当前账号",
    "关联商机已修改，请按当前商机重新执行AI审核", "审核权限信息已失效，请重新执行AI审核",
    "请完成独立质检后再确认归档", "拜访内容或关联已修改，请重新质检",
    "审核规则已更新，请重新质检", "服务端下一步审核未通过",
    "拜访审核未返回当前公司评分政策回执", "AI总分格式不正确，请重新审核",
    "AI未返回下一步审核结果", "下一步时间和行动审核格式不正确", "下一步审核缺少时间或行动依据",
    "当前为财务应急只读模式，仅支持已开放的查询；业务保存和后台任务暂不可用，请联系管理员恢复服务。",
    "读取暂未完成，请稍后重试", "提交结果暂未确认，请先查看处理结果，勿重复提交",
})

LEGACY_CODES = {
    "目标指标、金额或版本格式无效，请刷新表单": "TARGET_INPUT_INVALID",
    "指标不存在、已停用或不可设置目标": "TARGET_METRIC_UNAVAILABLE",
    "目标金额须为非负数且最多两位小数，数量须为非负整数": "TARGET_VALUE_INVALID",
    "目标状态已变化，请刷新表单": "VERSION_CONFLICT",
    "目标已更新，请刷新后再提交": "VERSION_CONFLICT",
    "已有待审批的相关目标申请，请等待运营处理": "TARGET_PENDING_CONFLICT",
    "已有待审批的历史目标申请，请等待运营处理": "TARGET_PENDING_CONFLICT",

    "客户已存在，请核对后再建档": "CUSTOMER_ALREADY_EXISTS",
    "客户已存在，请核对后再修改": "CUSTOMER_ALREADY_EXISTS",
    "记录已被他人更新，请刷新后再试": "VERSION_CONFLICT",
    "请完成独立质检后再确认归档": "VISIT_REVIEW_REQUIRED",
    "请重新执行AI审核后再确认归档": "VISIT_REVIEW_REQUIRED",
    "内容或关联已修改，请重新审核": "VISIT_REVIEW_STALE",
    "拜访内容或关联已修改，请重新质检": "VISIT_REVIEW_STALE",
    "审核规则已更新，请重新质检": "VISIT_REVIEW_STALE",
    "关联商机已修改，请按当前商机重新执行AI审核": "VISIT_REVIEW_STALE",
    "审核记录不存在、已使用或不属于当前账号": "VISIT_REVIEW_STALE",
    "审核权限信息已失效，请重新执行AI审核": "VISIT_REVIEW_STALE",
    "本次参与人员已修改，请重新审核后归档": "VISIT_PARTICIPANTS_CHANGED",
    "服务端下一步审核未通过": "VISIT_REVIEW_REJECTED",
}

DEFINITE_REJECTIONS = frozenset({
    "KNOWLEDGE_TEMPORARILY_UNAVAILABLE",
    "AI_SERVICE_NOT_CONFIGURED", "RUNTIME_CREDENTIAL_UNAVAILABLE", "FINANCE_RECOVERY_READ_ONLY",
    "TENANT_CALL_QUOTA_EXCEEDED",
    "KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED",
})
CHECK_STATUS_CODES = frozenset({
    "ASR_RESULT_UNKNOWN", "ASR_OPERATION_IN_PROGRESS", "ASR_OPERATION_CONFLICT",
    "IDEMPOTENCY_IN_PROGRESS", "IDEMPOTENCY_UNRESOLVED", "UPLOAD_STATUS_UNAVAILABLE",
})


def machine_code(value: Any) -> str | None:
    return value if isinstance(value, str) and CODE_PATTERN.fullmatch(value) else None


def public_message(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value in PUBLIC_MESSAGES else fallback


def trace_context(request=None) -> tuple[str, str]:
    context = request_error_context.get()
    state = getattr(request, "state", None)
    candidate = getattr(state, "request_id", None) or (context[0] if context else "")
    try:
        request_id = str(UUID(candidate))
    except (ValueError, TypeError, AttributeError):
        request_id = str(uuid4())
    method = str(getattr(request, "method", None) or (context[1] if context else "POST")).upper()
    return request_id, method


def validation_details(errors: list[dict]) -> list[dict]:
    """Only location/type are machine data. Input, ctx and original msg are not public."""
    result = []
    for error in errors[:50]:
        location = error.get("loc", [])
        if not isinstance(location, (tuple, list)) or not 0 < len(location) <= 8:
            continue
        if not all((isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", part))
                   or (isinstance(part, int) and not isinstance(part, bool) and part >= 0) for part in location):
            continue
        kind = error.get("type", "value_error")
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", kind):
            kind = "value_error"
        result.append({"loc": list(location), "type": kind,
                       "msg": public_message(error.get("msg"), "请求字段不符合要求，请检查填写内容")})
    return result


def _legacy_detail(detail: Any, code: str, message: str):
    if isinstance(detail, list):
        return validation_details([item for item in detail if isinstance(item, dict)])
    if isinstance(detail, dict):
        safe = {}
        for key, value in detail.items():
            if key == "code":
                safe[key] = machine_code(value) or code
            elif key in {"message", "detail"}:
                safe[key] = public_message(value, message)
            elif key == "errors" and isinstance(value, list):
                safe[key] = validation_details([item for item in value if isinstance(item, dict)])
            elif key == "current_version" and isinstance(value, int) and not isinstance(value, bool):
                safe[key] = value
        return safe or {"code": code}
    return machine_code(detail) or public_message(detail, message)


def error_response(request=None, *, status_code: int, detail=None, code: str | None = None,
                   headers: dict | None = None, extra: dict | None = None,
                   known_rejection: bool = False, validation: list[dict] | None = None,
                   retryable: bool | None = None) -> JSONResponse:
    """All failures use this boundary; no response/body logging happens here."""
    nested = detail.get("code") if isinstance(detail, dict) else detail
    legacy_code = LEGACY_CODES.get(detail) if isinstance(detail, str) else None
    code = machine_code(code) or machine_code(nested) or legacy_code or STATUS_CODES.get(status_code, "BAD_REQUEST")
    if status_code >= 500 and code == "BAD_REQUEST":
        code = "INTERNAL_ERROR"
    default = MESSAGES.get(STATUS_CODES.get(status_code, "BAD_REQUEST"), MESSAGES["BAD_REQUEST"])
    supplied = detail.get("message", detail.get("detail")) if isinstance(detail, dict) else detail
    message = public_message(supplied, MESSAGES.get(code, default))
    request_id, method = trace_context(request)
    readonly = method in {"GET", "HEAD"}
    rejected = known_rejection or code in DEFINITE_REJECTIONS
    transient = status_code in {408, 429, 500, 502, 503, 504} and not rejected
    outcome_unknown = not readonly and (status_code >= 500 or status_code == 408) and not rejected
    retryable = readonly and transient and retryable is not False
    if outcome_unknown:
        message = "提交结果尚未确认，请先查看处理结果，勿重复提交"
    action = ("correct_request" if code == "INVALID_CREDENTIALS"
              else "contact_admin" if rejected else "check_status" if outcome_unknown or code in CHECK_STATUS_CODES
              else "login" if status_code == 401 else "contact_admin" if status_code == 403
              else "retry_read" if retryable else "correct_request")
    body = {"code": code, "message": message, "detail": _legacy_detail(detail, code, message),
            "request_id": request_id, "retryable": retryable, "recovery_action": action}
    if validation is not None:
        # This list is assembled from reviewed messages by our validation handler.
        body["detail"] = validation
    if extra:
        for key in ("current_version", "retry_after"):
            value = extra.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                body[key] = value
    response_headers = dict(headers or {})
    cache_key = next((key for key in response_headers if key.lower() == "cache-control"), "Cache-Control")
    cache_value = response_headers.get(cache_key, "")
    response_headers[cache_key] = cache_value if "no-store" in cache_value.lower() else "no-store"
    response_headers["X-Request-Id"] = request_id
    return JSONResponse(status_code=status_code, content=body, headers=response_headers)


async def http_exception_handler(request, exc):
    return error_response(request, status_code=exc.status_code, detail=exc.detail, headers=exc.headers)
