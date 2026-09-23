"""Business vocabulary and safe presentation of durable operation evidence.

This module never writes facts or infers human actions from polling requests.
"""

CATEGORIES = {
    "advice": "AI 建议处理",
    "partner": "伙伴目录",
    "customer": "客户资料",
    "opportunity": "商机进展",
    "visit": "拜访记录",
    "material": "录音与文件",
    "task": "任务协作",
    "claim": "客户认领",
    "account": "账号管理",
    "department": "部门管理",
    "data": "资料导出",
    "actual": "实绩登记",
    "target": "销售目标",
    "quote": "报价关联",
    "ai_rule": "AI 用量提醒",
    "company_rule": "公司规则配置",
}
ACTIONS = {
    "advice.adopted": "采纳建议并创建待办",
    "advice.no_task": "确认建议无需待办",
    "partner.change": "维护伙伴目录",
    "customer.create": "创建客户",
    "customer.update": "修改客户资料",
    "opportunity.create": "创建商机",
    "opportunity.update": "修改商机",
    "opportunity.fde_members": "调整FDE协助成员",
    "visit.fde_participants": "登记实际协同人员",
    "company_rule.change": "调整公司规则",
    "visit.archive": "确认归档拜访",
    "visit.supplement": "补充拜访信息",
    "material.upload": "上传材料",
    "material.process": "处理上传材料",
    "material.transcribe": "语音转写",
    "task.created": "下发任务",
    "task.accept": "接受任务",
    "task.reject": "拒绝任务",
    "task.complete": "完成任务",
    "task.cancel": "取消任务",
    "task.reassign": "转交任务",
    "claim.request": "申请认领客户",
    "claim.approved": "通过客户认领",
    "claim.rejected": "驳回客户认领",
    "claim.released": "释放客户归属",
    "claim.legacy_resolved": "确认历史归属",
    "account.change": "调整账号",
    "department.change": "调整部门",
    "data.export": "导出资料",
    "actual.change": "登记或调整实绩",
    "target.change": "设定或调整目标",
    "quote.change": "关联报价",
    "ai_rule.change": "调整 AI 用量提醒",
}
FIELD_LABELS = {
    "name": "名称",
    "display_name": "姓名",
    "account_code": "账号",
    "industry_code": "所属行业",
    "customer_type_code": "客户类型",
    "source_code": "来源",
    "level_code": "客户优先级",
    "lifecycle_status": "客户状态",
    "primary_partner_name": "合作伙伴",
    "demand_summary": "客户需求",
    "main_business": "主营业务",
    "customer_budget": "预算",
    "company_reference": "公司客户编号",
    "title": "职位／标题",
    "phone": "联系方式",
    "email": "邮箱",
    "relationship_role_code": "联系人角色",
    "status": "状态",
    "membership_role": "部门角色",
    "role_code": "角色",
    "valid_to": "角色有效期",
    "partner_name_snapshot": "伙伴名称",
    "contact_title_snapshot": "对接人职位",
    "contact_name_snapshot": "对接人",
    "interaction_at": "拜访日期",
    "recorded_on": "填写日期",
    "visit_location": "地点",
    "interaction_mode_code": "沟通方式",
    "amount": "金额（元）",
    "year": "年度",
    "quarter": "季度",
    "recognized_amount": "确收金额（元）",
    "collection_amount": "回款金额（元）",
    "reference_no": "报价编号",
    "enabled": "启用状态",
    "period": "统计周期",
    "calls_limit": "调用次数阈值",
    "follow_up_record": "沟通内容",
    "next_action": "下一步计划",
    "first_visit_profile": "首次拜访资料",
    "version_no": "版本",
    "change_reason": "调整原因",
    "end_reason": "移出原因",
}
VALUES = {
    "active": "启用",
    "inactive": "停用",
    "sales": "一线销售",
    "supervisor": "销售主管",
    "manager": "总经理",
    "fde": "FDE",
    "fde_lead": "FDE主管",
    "operations": "运营",
    "administrator": "系统管理员",
    "pending_confirm": "待接受",
    "in_progress": "进行中",
    "completed": "已完成",
    "rejected": "已拒绝",
    "succeeded": "处理成功",
    "failed": "处理失败",
    "processing": "处理中",
    "queued": "等待处理",
    "pending": "待审批",
    "approved": "已通过",
    "cancelled": "已关闭",
    "archived": "已归档",
    "day": "每天",
    "week": "每周",
    "month": "每月",
}
EXPORTS = {
    "customers": "客户资料",
    "opportunities": "商机资料",
    "calls": "AI 调用明细",
    "roles": "角色用量报表",
    "audit": "技术审计日志",
    "system-events": "系统运行日志",
    "activities": "业务操作记录",
}


def value_text(value):
    if value is None or value == "":
        return "未填写"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        # These structures are described in dedicated detail sections, never dumped as JSON.
        return "已记录"
    if value == "[REDACTED]":
        return "敏感信息不展示"
    return VALUES.get(str(value), str(value))


def _differences(entries):
    changes = []
    for entry in entries:
        prior, after = entry.get("before") or {}, entry.get("after") or {}
        for key in entry.get("fields") or []:
            if key not in FIELD_LABELS:
                continue
            a, b = prior.get(key), after.get(key)
            if a == b and a != "[REDACTED]":
                continue
            changes.append({"label": FIELD_LABELS[key], "before": value_text(a), "after": value_text(b)})
    return changes


def present_activity(row):
    r = dict(row)
    body = r.pop("payload") or {}
    if r.get("actor_id") is None and r.get("execution_kind") == "human":
        r["execution_kind"] = "unknown"
    entries = body.get("entries") or []
    code = r["action_code"]
    category = code.split(".")[0]
    label = ACTIONS.get(code, "业务操作")
    result, result_label = "success", "已完成"
    changes = body.get("changes") or _differences(entries)
    # Ignore display-equivalent optional values in older change records.
    changes = [dict(c) for c in changes if c.get("before") != c.get("after")]
    facts = []
    materials = body.get("materials") or []
    visits = body.get("visits") or []
    if visits and not r.get("customer_name"):
        r["customer_name"] = "、".join(dict.fromkeys(v["customer_name"] for v in visits if v.get("customer_name")))
    note = body.get("source_note", "")
    if code == "customer.create":
        if body.get("data_source") == "excel_import":
            label = "导入客户资料"
            r["execution_kind"] = "import"
        facts = [("公司客户编号", body.get("company_reference")), ("来源文件", body.get("source_workbook"))]
    elif code in {"opportunity.fde_members", "visit.fde_participants"}:
        facts = [("处理说明", body.get("reason") or body.get("note"))]
        note = "商机协助关系与本次实际协同人员分别记录；不改变客户归属或销售业绩归属。"
    elif code == "company_rule.change":
        version = body.get("rule_version")
        if version is None:
            version = body.get("version")  # Historical projection field.
        facts = [("规则", body.get("rule_name")), ("版本", version),
                 ("调整原因", body.get("change_reason"))]
        note = "草稿与已发布配置分别留痕；当前生效版本以公司规则配置页面为准。"
    elif code == "visit.archive":
        result_label = "已归档"
        facts = [
            ("拜访日期", body.get("visit_date")),
            ("关联商机", body.get("opportunity_name")),
            ("沟通内容", body.get("communication")),
            ("下一步计划", body.get("next_action")),
            ("AI 评分", body.get("score")),
            ("人工确认人", body.get("confirmer")),
        ]
        if not materials:
            note = "本条记录没有可核对的上传材料关联；可能为文字录入或历史资料。"
    elif code == "material.upload":
        status = body.get("processing_status")
        result = "failed" if status == "failed" else "pending" if status in ("queued", "processing") else "success"
        result_label = (
            "处理失败"
            if result == "failed"
            else "处理中"
            if result == "pending"
            else "已关联归档"
            if visits
            else "已提取文字"
        )
        facts = [
            ("文件大小", f"{body.get('file_size', 0):,} 字节"),
            ("当前处理状态", VALUES.get(status, status)),
            ("失败原因", body.get("error_message")),
        ]
        note = "上传和处理状态与人工归档分开记录；只展示数据库中已关联的拜访。"
    elif code == "material.process":
        after = entries[-1].get("after") or {}
        r["object_name"] = after.get("filename") or "上传材料"
        result = "failed" if after.get("status") == "failed" else "success"
        result_label = "处理失败" if result == "failed" else "已提取文字"
        facts = [("处理结果", result_label), ("失败原因", after.get("error_message"))]
        changes = []
        note = "系统处理结果，操作人栏为材料发起人；未代替人工确认。"
    elif category == "advice":
        result_label = "已采纳" if code == "advice.adopted" else "无需待办"
        facts = [
            ("建议内容", body.get("action")),
            ("判断依据", body.get("evidence")),
            ("确认后的任务", body.get("task_description")),
            ("处理说明", body.get("decision_note")),
        ]
        note = "由销售人工确认；保留建议来源与实际创建的任务关联。"
    elif category == "task":
        facts = [("接收人", body.get("recipient")), ("说明", body.get("note"))]
        result_label = {
            "task.created": "待接受",
            "task.accept": "已接受",
            "task.reject": "已拒绝",
            "task.complete": "已完成",
            "task.cancel": "已取消",
            "task.reassign": "等待新接收人接受",
        }.get(code, "已记录")
    elif category == "claim":
        facts = [
            ("申请人", body.get("applicant")),
            ("处理原因", body.get("reason")),
            ("原归属人", body.get("previous_owner")),
            ("新归属人", body.get("owner")),
        ]
        result_label = {
            "claim.request": "已提交申请",
            "claim.approved": "已通过",
            "claim.rejected": "已驳回",
            "claim.released": "已释放",
        }.get(code, "已确认")
    elif category == "account":
        user_entries = [e for e in entries if e.get("type") == "user_ref"]
        if any(e.get("operation", "").endswith(".insert") for e in user_entries):
            label = "开通账号"
        elif any(
            (e.get("after") or {}).get("status") == "inactive" and "status" in e.get("fields", []) for e in user_entries
        ):
            label = "停用账号"
        elif any(e.get("type") == "password_credential" for e in entries):
            label = "修改本人密码" if str(r.get("actor_id")) == str(r.get("object_id")) else "重置账号密码"
        elif any(e.get("operation") == "account.login_unlock" for e in user_entries):
            label = "解除账号登录限制"
            unlock = next(e for e in reversed(user_entries) if e.get("operation") == "account.login_unlock")
            facts = [("解除原因", (unlock.get("after") or {}).get("reason"))]
            note = "仅解除此账号的登录限制，当前网络的登录保护保持不变。"
        elif any(e.get("type") in ("role_binding", "team_membership") for e in entries):
            label = "调整账号与权限"
    elif code == "data.export":
        entry = entries[-1]
        after = entry.get("after") or {}
        endpoint = after.get("path", "").split("/")
        kind = endpoint[-2] if len(endpoint) > 1 else ""
        r["object_name"] = EXPORTS.get(kind, "业务资料")
        result = "success" if entry.get("result") == "success" else "failed"
        result_label = "已生成导出文件" if result == "success" else "导出失败"
        filter_labels = {
            "period": "周期",
            "start": "起始日期",
            "end": "结束日期",
            "actor": "操作人标识",
            "category": "业务类型",
            "action": "业务动作",
            "q": "关键词",
            "role": "角色",
            "user": "用户标识",
            "status": "状态",
            "module": "模块",
            "level": "等级",
            "industry": "行业",
            "state": "状态",
            "owner": "归属人标识",
            "customer": "客户标识",
            "department": "操作人当前部门标识",
            "outcome": "处理结果",
        }
        filters = after.get("filters")
        summary_filters = (
            "；".join(
                f"{filter_labels.get(k, k)}：{ACTIONS.get(v, CATEGORIES.get(v, VALUES.get(v, v)))}"
                for k, v in filters.items()
            )
            if filters
            else "全部符合默认周期的记录"
            if filters == {}
            else "历史记录未保存筛选条件"
        )
        facts = [("导出条数", after.get("export_count")), ("筛选条件", summary_filters)]
        changes = []
        note = "记录服务端生成导出文件的结果，不表示文件已保存到用户设备。"
    elif code == "material.transcribe":
        result_label = "已转写"
        facts = [
            (
                "用途",
                {
                    "visit_entry": "拜访录入",
                    "customer_create": "客户录入",
                    "management_task": "任务录入",
                    "chatbi": "经营问答",
                }.get(body.get("purpose"), "语音录入"),
            ),
            ("录音时长（秒）", body.get("duration_seconds")),
        ]
    summary = "；".join(
        f"{c['label']}：{value_text(c.get('before'))} → {value_text(c.get('after'))}" for c in changes[:3]
    )
    if not summary:
        summary = "；".join(f"{k}：{v}" for k, v in facts if v not in (None, ""))[:180]
    r.update(
        category=category,
        category_label=CATEGORIES.get(category, "其他业务"),
        action_label=label,
        result=result,
        result_label=result_label,
        summary=summary or label,
        details={
            "facts": [{"label": k, "value": str(v)} for k, v in facts if v not in (None, "")],
            "changes": changes,
            "materials": materials,
            "visits": visits,
            "note": note,
        },
        evidence_label={
            "business_event": "业务事件记录",
            "saved_record": "已保存业务资料",
            "row_audit": "事务变更留痕",
        }[r["evidence_kind"]],
    )
    # Keep stable identifiers and display values; raw snapshots/paths stay in technical audit.
    return r
