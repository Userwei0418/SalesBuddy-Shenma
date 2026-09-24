"""Product permission names; organization/job titles are not authorization roles.

The catalog describes implemented actions. Tenant role templates and account
exceptions supply grants; neither frontend labels nor request fields grant access.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    code: str
    label: str
    module: str
    scopes: tuple[str, ...]
    sensitive: bool = False


BUSINESS_SCOPES = ("self", "assigned", "teams", "workspace")
COMPANY_SCOPE = ("workspace",)


def _module(module, resource, actions, *, scopes=BUSINESS_SCOPES, sensitive=()):
    return tuple(PermissionDefinition(f"{resource}.{action}", label, module,
                 ("self",) if (resource, action) in {("visit", "upload"), ("visit", "transcribe"),
                  ("visit", "retry_import"), ("agent", "history"), ("profile", "fde_review")} else scopes, action in sensitive)
                 for action, label in actions)


PERMISSIONS = (
    *_module("使用入口", "access", (("mini_program", "使用小程序"), ("console", "使用运营后台")),
             scopes=COMPANY_SCOPE),
    *_module("总览与经营", "overview", (("read", "查看总览"),)),
    *_module("客户管理", "customer", (
        ("reference", "搜索与选择客户基本信息"), ("read", "查看客户详情"),
        ("create", "新建客户"), ("update", "修改客户资料"), ("claim", "申请认领客户"),
        ("claim_directory", "查看公司客户认领目录"), ("claim_review", "审批客户认领"), ("release", "释放客户"),
        ("resolve_owner", "核对历史客户归属"), ("export", "导出客户"),
    ), sensitive=("export", "release", "resolve_owner")),
    *_module("作战地图", "battle_map", (("read", "查看客户作战地图"),)),
    *_module("商机管理", "opportunity", (
        ("read", "查看商机"), ("create", "创建商机"), ("update", "修改商机"),
        ("close", "确认赢单或丢单"), ("reopen", "重新打开商机"),
        ("create_for_others", "代他人创建商机"), ("quote_create", "记录报价"),
        ("fde_members", "维护商机协作 FDE"), ("export", "导出商机"),
    ), sensitive=("export", "reopen", "create_for_others")),
    *_module("跟进记录", "visit", (
        ("read", "查看跟进记录"), ("create", "新增跟进记录"), ("supplement", "补充跟进记录"),
        ("upload", "上传跟进材料"), ("transcribe", "语音转写"),
        ("retry_import", "重试材料识别"), ("download_original", "下载原始材料"),
        ("structure", "AI 整理跟进原文"), ("quality_review", "AI 质检跟进记录"),
        ("first_visit", "登记首次拜访信息"), ("attendance_manage", "登记协同人与参与 FDE"),
    ), sensitive=("download_original",)),
    *_module("待办事项", "task", (
        ("read", "查看待办"), ("create_daily", "创建日常待办"), ("create_customer", "创建客户待办"),
        ("assign", "向他人下发待办"), ("accept", "接收待办"), ("decline", "拒绝待办"),
        ("complete", "完成待办"), ("cancel", "取消待办"),
        ("coordinate", "协调岗位待办"), ("review", "处理待办结果"),
    )),
    *_module("经营建议与风险", "advice", (
        ("request", "生成经营建议"), ("read", "查看经营建议"), ("decide", "采纳或忽略建议"),
        ("customer", "分析客户整体经营"), ("opportunity", "分析商机经营"), ("visit", "分析单次跟进"),
    )),
    *_module("经营建议与风险", "risk", (("read", "查看风险"), ("resolve", "处理风险"), ("auto_review", "自动评估客户风险"))),
    *_module("客户经营实绩", "actual", (
        ("read", "查看客户经营实绩"), ("create", "登记确收与回款"), ("void", "作废实绩登记"),
    ), sensitive=("void",)),
    *_module("经营看板", "dashboard", (("read", "查看经营看板"), ("ranking", "查看销售排名"))),
    *_module("个人与团队画像", "profile", (
        ("sales_read", "查看销售画像"), ("sales_review", "生成销售成长评估"),
        ("fde_read", "查看 FDE 画像"), ("fde_review", "生成 FDE 成长评估"),
        ("fde_activity", "查看 FDE 协作记录"),
    )),
    *_module("目标管理", "target", (
        ("read", "查看目标"), ("submit", "设置目标或提交变更"),
        ("manage", "运营维护目标"), ("approve", "审批目标变更"), ("history", "查看目标变更历史"),
        ("fde_department", "访问 FDE 部门汇总目标"),
    )),
    *_module("智能助手", "agent", (
        ("chatbi", "公司数据问答"), ("customer_chatbi", "客户数据问答"),
        ("today_tasks", "生成今日待办建议"), ("personal_risks", "分析个人经营风险"),
        ("operating_report", "生成经营报告"), ("opportunity_draft", "起草商机建议"),
        ("management_task", "起草管理任务"), ("history", "查看本人智能助手会话"),
    )),
    *_module("伙伴与演示方案", "partner", (("read", "查看伙伴目录"), ("manage", "维护伙伴目录")), scopes=COMPANY_SCOPE),
    *_module("伙伴与演示方案", "demo_scene", (
        ("read", "查看商机演示方案"), ("create", "新增演示方案"),
        ("update", "修改演示方案"), ("delete", "删除演示方案"),
    ), sensitive=("delete",)),
    *_module("组织与账号", "organization", (("read", "查看组织与账号"), ("manage", "维护部门与任职")),
             scopes=COMPANY_SCOPE),
    *_module("组织与账号", "account", (
        ("create", "开通账号"), ("update", "修改或停用账号"),
        ("reset_password", "设置或重置账号密码"), ("unlock", "解除登录限制"),
        ("password_policy", "维护公司密码策略"),
    ), scopes=COMPANY_SCOPE, sensitive=("create", "update", "reset_password", "unlock", "password_policy")),
    *_module("权限管理", "authorization", (
        ("read", "查看角色与有效权限"), ("roles_manage", "维护权限角色模板"),
        ("accounts_manage", "配置账号角色与额外权限"), ("audit", "查看授权变更记录"),
    ), scopes=COMPANY_SCOPE, sensitive=("roles_manage", "accounts_manage")),
    *_module("历史数据导入", "history", (("import", "执行已审核历史数据导入"),), scopes=COMPANY_SCOPE, sensitive=("import",)),
    *_module("公司管理", "company", (("read", "查看本公司资料"), ("update", "修改本公司资料")),
             scopes=COMPANY_SCOPE),
    *_module("业务规则", "rule", (
        ("read", "查看业务规则"), ("draft", "编辑业务规则草稿"),
        ("preview", "预览规则影响"), ("publish", "发布业务规则"), ("restore", "恢复历史规则"),
    ), scopes=COMPANY_SCOPE, sensitive=("publish", "restore")),
    *_module("AI 配置与运行", "ai", (
        ("usage_rules_manage", "维护 AI 用量提醒规则"), ("usage_read", "查看 AI 调用统计"), ("usage_export", "导出 AI 调用统计"),
        ("run_read", "查看 Agent 运行审计"), ("run_export", "导出 Agent 运行审计"),
        ("execution_read", "查看 Agent 运行规则"), ("config_read", "查看模型与 Agent 配置"), ("config_test", "测试模型与连接"),
        ("config_publish", "发布模型与 Agent 配置"), ("config_rollback", "回退 Agent 配置"),
    ), scopes=COMPANY_SCOPE, sensitive=("usage_export", "run_export", "config_publish", "config_rollback")),
    *_module("飞书同步", "feishu", (
        ("read", "查看飞书同步配置和状态"), ("configure", "修改飞书同步配置"),
        ("control", "启动或暂停飞书同步"), ("recover", "重试飞书同步"),
    ), scopes=COMPANY_SCOPE, sensitive=("configure", "control", "recover")),
    *_module("审计与系统", "audit", (
        ("read", "查看操作审计"), ("export", "导出操作审计"),
        ("events_read", "查看系统事件"), ("events_export", "导出系统事件"),
        ("business_read", "查看业务活动"), ("business_export", "导出业务活动"),
    ), scopes=COMPANY_SCOPE, sensitive=("export", "events_export", "business_export")),
    *_module("团队与通知", "directory", (("read", "查看授权团队与成员目录"),)),
    *_module("团队与通知", "notification", (("read", "查看本人通知"), ("mark_read", "将本人通知标为已读")),
             scopes=("self",)),
)

CATALOG = {permission.code: permission for permission in PERMISSIONS}
if len(CATALOG) != len(PERMISSIONS):
    raise RuntimeError("Duplicate permission code")
