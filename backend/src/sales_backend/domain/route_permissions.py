"""Explicit HTTP coverage. New endpoints must be classified before they can run.

This is the action gate, not the record filter. Repositories and services enforce
scoped grants again at object access and mutation boundaries.
"""

# module -> handler -> required action. '*' declares intentionally public/auth-only
# endpoints. '$...' denotes a body-dependent action checked by the dispatcher.
ROUTES = {
 'admin': {'admin_page':'*','get_agent_config':'ai.config_read','get_agent_config_releases':'ai.config_read',
           'rollback_agent_config':'ai.config_rollback','update_agent_config':'ai.config_publish'},
 'advice': {'request_advice':'advice.request','advice_statistics':'advice.read','advice_detail':'advice.read','decide_suggestion':'advice.decide'},
 'agent_audit': {'runs':'ai.run_read'},
 'assistant': {'assistant_home':'overview.read','create_conversation':'$agent','send_message':'agent.history',
               'list_messages':'agent.history','get_run':'agent.history'},
 'audio': {'transcribe_audio':'visit.transcribe'},
 'auth': {name:'*' for name in ('password_login','password_change','create_session','refresh_session','get_me','logout')},
 'console_auth': {name:'*' for name in ('login','refresh','me','change_password','logout')},
 'authorization': {name:'*' for name in ('own_permissions','catalog','roles','options','console_permissions','create_role','update_role','account','update_account','audit')},
 'business': {'opportunity_create_options':'opportunity.create','business_options':'directory.read','directory_teams':'directory.read','directory_members':'directory.read',
  'task_assignees':'directory.read','task_positions':'directory.read','workbench':'overview.read',
  'dashboard_ranking_view':'dashboard.ranking','dashboard_options':'dashboard.read','dashboard':'dashboard.read',
  'opportunities_overview':'opportunity.read','opportunity_detail':'opportunity.read','opportunities':'opportunity.read',
  'visit_form_schema':'visit.create','create_visit':'visit.create','get_visit':'visit.read',
  'supplement_visit':'visit.supplement','visit_colleagues':'directory.read'},
 'business_activity': {'activities':'audit.business_read','activity_detail':'audit.business_read'},
 'collaboration': {'directory_fde':'directory.read','update_members':'opportunity.fde_members',
  'profile_scope_options':'profile.fde_read','dashboard':'profile.fde_read','activity':'profile.fde_activity','visit_opportunities':'visit.create'},
 'companies': {'directory':'access.console','select_company':'company.read','rename_company':'company.update'},
 'company_rules': {'catalog':'rule.read','execution_catalog':'ai.execution_read','save_draft':'rule.draft',
  'publish':'rule.publish','restore':'rule.restore','preview':'rule.preview','presentation':'directory.read'},
 'connectivity': {'test_current_connection':'ai.config_test','read_connection_test':'ai.config_test'},
 'customer_assets': {'map_customers':'battle_map.read','read_assets':'actual.read','create_actual':'actual.create','void_actual':'actual.void'},
 'customers': {'customer_claim_pool':'customer.claim_directory','customer_claim_pool_options':'customer.claim_directory','reference':'customer.reference','list_customers':'customer.read',
  'claim_existing_customer':'customer.claim','customer_detail':'customer.read','create_customer':'customer.create',
  'update_customer':'customer.update','assign_customer':'*','check_opportunity_name':'$opportunity_name',
  'create_customer_opportunity':'$opportunity'},
 'demo_scenes': {'scenes':'demo_scene.read','create':'demo_scene.create','detail':'demo_scene.read',
  'history':'demo_scene.read','update':'demo_scene.update','delete':'demo_scene.delete'},
 'detail_reads': {'customer_overview':'customer.read','customer_header':'customer.read','opportunity_header':'opportunity.read',
  'customer_opportunity_header':'opportunity.read','opportunity_overview':'opportunity.read','customer_opportunities':'opportunity.read',
  'customer_opportunity_overview':'opportunity.read','visit_history':'visit.read','contact_history':'customer.read',
  'opportunity_timeline':'opportunity.read','actual_quarters':'actual.read'},
 'fde_profile': {'profile':'profile.fde_read','review':'profile.fde_review'},
 'feishu_sync': {'read':'feishu.read','save':'feishu.configure','recover':'feishu.recover','act':'feishu.control','catalog':'feishu.read','sync_status':'feishu.read'},
 'model_api': {'list_connections':'ai.config_read','list_releases':'ai.config_read','test_connection':'ai.config_test',
  'get_test':'ai.config_test','publish_connection':'ai.config_publish'},
 'notifications': {'list_notifications':'notification.read','mark_notification_read':'notification.mark_read'},
 'operations_accounts': {'password_policy':'organization.read','update_password_policy':'account.password_policy','organization':'organization.read',
  'create_account':'account.create','update_account':'account.update','reset_password':'account.reset_password',
  'unlock_account_login':'account.unlock','create_department':'organization.manage','update_department':'organization.manage'},
 'operations_customers': {'summary':'customer.read','customers':'customer.read','customer':'customer.read','customer_overview':'customer.read',
  'update_profile':'customer.update','customer_history':'customer.read','claims':'customer.claim_review',
  'review':'customer.claim_review','release':'customer.release','resolve_owner':'customer.resolve_owner'},
 'operations_opportunities': {'opportunities':'opportunity.read','detail':'opportunity.read','save':'$opportunity','quote':'opportunity.quote_create'},
 'operations_reports': {'ai_overview':'ai.usage_read','ai_calls':'ai.usage_read','rules':'ai.usage_read',
  'create_rule':'ai.usage_rules_manage','update_rule':'ai.usage_rules_manage','audit':'audit.read',
  'system_events':'audit.events_read','export_role_usage':'ai.usage_export'},
 'operations_targets': {'targets':'target.read','save':'target.manage','requests':'target.approve','save_batch':'target.manage',
  'batches':'target.approve','decide_batch':'target.approve','decide':'target.approve','history':'target.history'},
 'partners': {'directory':'partner.read','list_partners':'partner.read','save_partner':'partner.manage'},
 'profile': {'profile_scope_options':'profile.sales_read','scoped_performance':'profile.sales_read','update_sales_target':'target.submit',
  'ensure_sales_growth_review':'profile.sales_review','sales_growth':'profile.sales_read','scoped_sales_growth':'profile.sales_read',
  'evaluation_summary':'profile.sales_read','team_member_sales_growth':'profile.sales_read'},
 'risks': {'list_risks':'risk.read','risk_detail':'risk.read','resolve_risk':'risk.resolve'},
 'targets': {'targets':'target.read','save':'target.submit','save_batch':'target.submit'},
 'tasks': {'task_customer_choices':'task.create_customer','task_opportunity_choices':'task.create_customer',
  'task_recipients':'$task_choices','list_tasks':'task.read','task_overview':'task.read','create_task':'$task','create_task_batch':'$task_batch',
  'task_detail':'task.read','create_task_event':'$task_event'},
 'visit_flow': {'structure':'visit.structure','quality':'visit.quality_review'},
 'visit_imports': {'upload_visit_file':'visit.upload','import_status':'visit.read','retry_import':'visit.retry_import','download_original':'visit.download_original'},
 'main': {'health_live':'*','health_version':'*','health_ready':'*'},
}
EXPORTS = {'customer.read':'customer.export','opportunity.read':'opportunity.export','ai.run_read':'ai.run_export',
 'ai.usage_read':'ai.usage_export','audit.business_read':'audit.business_export','audit.read':'audit.export','audit.events_read':'audit.events_export'}
AGENT_PERMISSIONS = {mode:'agent.'+mode for mode in ('chatbi','customer_chatbi','today_tasks','personal_risks','operating_report','opportunity_draft','management_task')}
AGENT_PERMISSIONS.update(visit_entry='visit.structure',customer_create='customer.create')
TASK_EVENTS = {'accept':'task.accept','reject':'task.decline','complete':'task.complete',
 'approve_completion':'task.review','reject_completion':'task.review','cancel':'task.cancel','reassign':'task.coordinate'}


def route_permission(module, handler, *, exporting=False):
    permission = ROUTES.get(module,{}).get(handler)
    if permission is None:
        raise PermissionError('此接口尚未配置功能权限')
    if exporting:
        return EXPORTS.get(permission,permission)
    return permission
