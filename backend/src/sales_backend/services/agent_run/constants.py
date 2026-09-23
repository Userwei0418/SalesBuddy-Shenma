from __future__ import annotations

from datetime import timedelta, timezone

VISIT_FIELDS = (
    "客户名称",
    "合作伙伴",
    "商机名称",
    "预计金额（元）",
    "客户/线索来源",
    "拜访对象类别",
    "客户职位",
    "客户名字",
    "记录人",
    "拜访及沟通日期",
    "拜访及沟通方式",
    "拜访地点",
    "沟通时长",
    "是/否达成预期",
    "跟进记录",
    "下一步",
)

FIRST_VISIT_FIELDS = (
    "客户主营业务",
    "客户需求",
    "客户预算",
    "联系人角色",
)

CHINA_TZ = timezone(timedelta(hours=8))
JSON_ONLY = "只输出一个JSON对象，不要前言、不要Markdown、不要解释推理过程。"
CHATBI_METRIC_GLOSSARY = (
    "数字口径必须用facts.summary：customers=客户数；open_opportunities=未关闭商机数；"
    "open_tasks=未完成任务数（status不是completed/cancelled）；completed_tasks=已完成任务数；"
    "open_risks=未解除风险数；open_pipeline_amount_cny=在推商机金额。"
    "用户问未完成任务时直接用open_tasks的数字回答，不要说口径不清。"
)
