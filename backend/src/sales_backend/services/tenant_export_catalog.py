"""Versioned data-portability projection, never SELECT * or an instance backup.

Identifiers and predicates below are source constants, not client input. Linked
rows are restricted through their visible parent and the authenticated workspace.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Table:
    name: str
    columns: str
    predicate: str = 'true'


@dataclass(frozen=True)
class Section:
    label: str
    permissions: tuple[str, ...]
    tables: tuple[Table, ...]


def parent(table, field):
    return f'{field} IN (SELECT id FROM {table} WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)'


SECTIONS = {
    'organization': Section('公司、成员与组织', ('organization.read',), (
        Table('platform.workspace', 'id,external_workspace_id,name,status,default_timezone,version_no,created_at,updated_at',
              'id=common.current_workspace_id()'),
        Table('platform.user_ref', 'id,workspace_id,account_code,display_name,phone_number,email,status,version_no,created_at,updated_at,deleted_at'),
        Table('platform.team', 'id,workspace_id,parent_team_id,code,name,region_code,status,valid_from,valid_to,version_no,created_at,updated_at,deleted_at'),
        Table('platform.team_membership', 'id,workspace_id,team_id,user_ref_id,membership_role,is_primary,valid_from,valid_to,created_at,updated_at'),
        Table('platform.role_binding', 'id,workspace_id,user_ref_id,role_code,data_scope_code,team_id,valid_from,valid_to,created_at,updated_at'),
    )),
    'customers': Section('客户、联系人与归属', ('customer.read', 'customer.export'), (
        Table('crm.customer', 'id,workspace_id,external_customer_id,customer_code,name,industry_code,customer_type_code,source_code,lifecycle_status,level_code,owner_user_ref_id,owner_team_id,primary_partner_name,demand_summary,data_source,created_by_user_ref_id,version_no,created_at,updated_at,next_action,operation_type,cooperation_years,main_business,customer_budget,data_kind,company_reference,company_verified_at,company_verified_by', 'deleted_at IS NULL'),
        Table('crm.contact', 'id,workspace_id,customer_id,name,title,department,contact_category_code,relationship_role_code,phone,email,is_primary,version_no,created_at,updated_at', 'deleted_at IS NULL AND '+parent('crm.customer', 'customer_id')),
        Table('crm.customer_ownership', 'customer_id,workspace_id,owner_user_ref_id,state,version_no,updated_at', parent('crm.customer', 'customer_id')),
        Table('crm.customer_sales_member', 'workspace_id,customer_id,user_ref_id,added_by_user_ref_id,joined_at', parent('crm.customer', 'customer_id')),
    )),
    'opportunities': Section('商机、季度计划与协作', ('opportunity.read', 'opportunity.export'), (
        Table('crm.opportunity', 'id,workspace_id,customer_id,external_opportunity_id,name,stage_code,amount,currency,probability,expected_close_date,owner_user_ref_id,owner_team_id,status,source_code,created_by_user_ref_id,version_no,created_at,updated_at,partner_name,product_line,closed_at,follow_up_plan,sales_channel,partner_id,original_owner_name,ownership_resolution,expected_close_year,expected_close_quarter,trade_relation,upstream_opportunity_id,customer_source,priority,is_key,trial_status,summary,note,ppl_status,external_system_status,primary_fde_id,fde_support_stage,fde_progress', 'deleted_at IS NULL'),
        Table('crm.opportunity_material', 'id,workspace_id,opportunity_id,material_id,line_no,quantity,unit_price,line_amount,usage,expected_delivery_on,expected_start_on,expected_oa_on,service_months,expected_account_count,expected_consumption_amount,expected_consumption_year,expected_consumption_quarter,note,catalog_snapshot,is_active,version_no,updated_by_user_ref_id,created_at,updated_at', parent('crm.opportunity','opportunity_id')),
        Table('crm.opportunity_forecast', 'id,workspace_id,opportunity_id,year,quarter,recognized_amount,collection_amount,updated_by_user_ref_id,updated_at,collection_confidence', parent('crm.opportunity', 'opportunity_id')),
        Table('crm.opportunity_participant', 'opportunity_id,workspace_id,user_ref_id,participant_role,valid_from,valid_to,created_at,assigned_by_user_ref_id,ended_by_user_ref_id,source_code,source_visit_id,end_reason', parent('crm.opportunity', 'opportunity_id')),
        Table('crm.opportunity_project_participant', 'id,workspace_id,opportunity_id,user_ref_id,participant_role,created_by_user_ref_id,created_at', parent('crm.opportunity', 'opportunity_id')),
        Table('crm.opportunity_related_partner', 'workspace_id,opportunity_id,partner_id,created_by_user_ref_id,created_at', parent('crm.opportunity', 'opportunity_id')),
        Table('crm.opportunity_quote_reference', 'id,workspace_id,opportunity_id,reference_no,title,url,amount,created_by_user_ref_id,created_at', parent('crm.opportunity', 'opportunity_id')),
    )),
    'visits': Section('已确认跟进与字段', ('visit.read',), (
        Table('activity.visit', 'id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,form_version_id,status,interaction_at,interaction_mode_code,visit_location,duration_minutes,expectation_code,follow_up_record,next_action,partner_name_snapshot,lead_source_snapshot,contact_category_snapshot,contact_title_snapshot,contact_name_snapshot,confirmed_by_user_ref_id,confirmed_at,archived_at,version_no,created_at,updated_at,is_first_visit,follow_up_score,first_visit_profile,visit_goal,source_import_id,customer_type_code_snapshot,created_by_user_ref_id,recorded_on,archived_fields,recording_role_code_snapshot,recording_team_id_snapshot,follow_up_task_mode,partner_id,original_recorder_name,manager_user_ref_id', "deleted_at IS NULL AND status IN ('confirmed','archived')"),
        Table('activity.visit_field_value', 'visit_id,workspace_id,field_definition_id,value_json,value_text,source_type,confidence,is_confirmed,evidence,edited_at,created_at,updated_at', "visit_id IN (SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND status IN ('confirmed','archived'))"),
        Table('activity.visit_participant', 'visit_id,workspace_id,user_ref_id,participant_role,team_id_at_event,role_code_at_event,created_by_user_ref_id,created_at,updated_at', "visit_id IN (SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND status IN ('confirmed','archived'))"),
        Table('activity.visit_opportunity', 'visit_id,workspace_id,opportunity_id,created_by_user_ref_id,created_at', "visit_id IN (SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND status IN ('confirmed','archived'))"),
    )),
    'tasks': Section('待办、负责人和处理历史', ('task.read',), (
        Table('workflow.task', 'id,workspace_id,task_type,title,description,customer_id,opportunity_id,source_visit_id,creator_user_ref_id,creator_team_id,priority_code,status,due_at,accepted_at,completed_at,completion_note,verification_status,version_no,created_at,updated_at,rejection_comment,source_code,agent_reason,source_follow_up_record,source_interaction_at,target_position,source_suggestion_id,association_kind,completion_submitted_at,completion_review_note', 'deleted_at IS NULL'),
        Table('workflow.task_assignee', 'task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility,assigned_at,accepted_at', parent('workflow.task', 'task_id')),
        Table('workflow.task_event', 'id,workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,note,occurred_at', parent('workflow.task', 'task_id')),
    )),
    'actuals': Section('有效确收回款与原始核对证据（证据不可再次加总）', ('actual.read',), (
        Table('crm.effective_actual', 'id,workspace_id,customer_id,opportunity_id,material_line_id,year,quarter,kind,amount,raw_amount,tax_basis,tax_unresolved,source,status,occurred_on,period_start,period_end,period_precision,owner_user_ref_id,owner_team_id,expected_material_count,confirmed_material_count,missing_material_count,complete,reference_changed,source_fingerprint,version_no'),
        Table('crm.opportunity_actual_confirmation', 'id,workspace_id,opportunity_id,material_line_id,year,quarter,kind,amount,currency,tax_basis,status,source_ref,note,reason,material_snapshot,confirmed_by_user_ref_id,confirmed_at,created_at,version_no', parent('crm.opportunity','opportunity_id')),
        Table('crm.opportunity_actual_basis', 'id,workspace_id,opportunity_id,year,quarter,kind,source,selected_history_id,source_fingerprint,reason,decided_by_user_ref_id,created_at,updated_at,version_no,adopted_snapshot,operational_snapshot,expected_material_line_ids,empty_confirmation', parent('crm.opportunity','opportunity_id')),
        Table('crm.opportunity_period_actual_snapshot', 'id,workspace_id,opportunity_id,year,quarter,kind,raw_amount,source_unit,tax_basis,source_field,source_record_id,import_batch_id', parent('crm.opportunity','opportunity_id')),
        Table('crm.customer_actual', 'id,workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,note,confirmed_by_user_ref_id,created_at,voided_at,voided_by_user_ref_id,void_reason'),
    )),
    'materials': Section('物料目录', ('material.read',), (
        Table('crm.material', 'id,workspace_id,code,name,product_line_id,unit,catalog_price,currency,tax_rate,recognition_rule,rule_parameters,is_active,version_no,reason,updated_by_user_ref_id,created_at,updated_at'),
    )),
    'targets': Section('销售目标', ('target.read',), (
        Table('crm.sales_target', 'id,workspace_id,target_year,scope_type,user_ref_id,team_id,kind,amount,updated_by_user_ref_id,created_at,updated_at,version_no,period_type,period_start,period_end,department_code,change_reason,source'),
    )),
    'partners': Section('伙伴目录', ('partner.read',), (
        Table('crm.partner', 'id,workspace_id,name,status,version_no,created_by_user_ref_id,updated_by_user_ref_id,created_at,updated_at,short_name,principal_name,channel_manager_user_ref_id,original_channel_manager_name,priority,progress,grade,signed_on,partner_type,region,province,note'),
    )),
}
EXCLUSIONS = [
    '未选择或未授权的业务分类；已删除业务记录；未确认跟进草稿及未关联归档记录的上传材料',
    '密码、登录会话、令牌、MFA、模型/飞书/存储密钥与集成配置',
    'AI 会话与推理快照、系统日志、通知投递、队列和内部幂等记录',
    '规则/权限配置、派生排名/画像/风险建议、演示方案及历史导入元数据',
    '个人/共享保存视图与默认视图偏好；上传意图、尝试及额度恢复记录',
    '周会原文、解析与编辑草稿及导入批次元数据；已确认生成的正式任务、现有负责人和任务事件仍按任务分类导出',
]


def required_permissions(sections, originals):
    codes = {'company.export', 'access.console'}
    for section in sections:
        if section not in SECTIONS:
            raise ValueError('未知导出分类')
        codes.update(SECTIONS[section].permissions)
    if originals:
        if 'visits' not in sections:
            raise ValueError('导出原件时必须选择跟进记录')
        codes.add('visit.download_original')
    return sorted(codes)


def require_permissions(effective, sections, originals):
    missing = [code for code in required_permissions(sections, originals)
               if not any(g.scope == 'workspace' for g in effective.for_permission(code))]
    if missing:
        raise PermissionError('数据包要求所选分类的公司级权限，缺少：' + '、'.join(missing))
