"""Explicit HTTP coverage. New endpoints must be classified before they can run.

This is the action gate, not the record filter. Repositories and services enforce
scoped grants again at object access and mutation boundaries.
"""

# module -> handler -> required action. '*' declares intentionally public/auth-only
# endpoints. '$...' denotes a body-dependent action checked by the dispatcher.
ROUTES = {
 'work_report_feishu': {'publish':'work_report.share','status':'work_report.read'},
 'unified_model_key': {'key_status':'ai.config_read','rotate_key':'ai.config_publish'},
 'visit_range': {'list_visits':'visit.read'},
 'web_auth': {name:'*' for name in ('login','refresh','me','change_password','logout')},
 'weekly_reports': {'generate':'weekly_report.generate','list_reports':'weekly_report.read',
  'detail':'weekly_report.read','sources':'weekly_report.generate','report_sources':'weekly_report.read',
  'save_draft':'weekly_report.edit','publish_feishu':'weekly_report.edit','feishu_status':'weekly_report.read',
  'cancel':'weekly_report.cancel'},
 'review': {'metrics':'*','create_metric':'target.manage','update_metric':'target.manage','targets':'target.read','save_target':'$review_target','target_options':'$review_target'},
 'review_overview': {name:'work_report.read' for name in ('overview','activities','opportunities')},
 'review_okr': {'objectives':'target.read','objective':'target.read','key_results':'target.read','key_result':'target.read',
  'create_objective':'$review_target','update_objective':'$review_target','create_key_result':'$review_target',
  'update_key_result':'$review_target','progress':'$review_target','mention_objective':'$review_target','mention_key_result':'$review_target'},
 'material_reference_explanation': {'explain_reference':'agent.finance_assistant'},
 'work_reports': {
  'snapshot_cancellation_state':'work_report.write','cancel_snapshot':'work_report.write',
  **{name:'work_report.read' for name in ('options','overview_options','overview_read','meeting_options','meeting_snapshot','schemes','scheme','snapshot','sources','run','documents','document','revisions','revision')},
  **{name:'work_report.write' for name in ('meeting_prepare','meeting_save','create_scheme','update_scheme','create_snapshot','cancel_run','create_revision')},
  'create_run':'agent.work_report','share':'work_report.share','unshare':'work_report.share','export':'report.export'},
 'support_access': {**{n:'support.manage' for n in ('support_directory','support_grants','approve_support','revoke_support','support_audit')},'support_assignments':'*','support_records':'*'},
 'tenant_export': {'export_options':'company.export','export_business_data':'company.export'},
 # These routes authenticate only a purpose-bound closed-export cookie in their
 # own gate. Normal company/platform tokens confer no authority here.
 'closed_company_export': {name:'*' for name in ('closed_export_page','closed_export_login',
    'closed_export_session','closed_export_logout','closed_export_options','closed_export_download')},
 'platform_control': {name:'*' for name in ('platform_page','company_activation_page','platform_start','finish_platform_login','tenant_entry','tenant_ai_service','save_tenant_ai_service','tenant_configuration','platform_runtime','platform_login','platform_me','platform_logout',
    'platform_tenants','provision_tenant','transition_tenant','reissue_tenant_activation','activate_tenant','platform_audit','begin_mfa', 'confirm_mfa', 'verify_sensitive', 'tenant_modules','save_modules','tenant_job_settings','save_tenant_job_settings','account_quota','save_account_quota','storage_quota','save_storage_quota','http_limits','save_http_limit','call_quotas','save_call_quota')},
 'admin': {'admin_page':'*','get_agent_config':'ai.config_read','get_agent_config_releases':'ai.config_read',
           'rollback_agent_config':'ai.config_rollback','update_agent_config':'ai.config_publish'},
 'advice': {'request_advice':'advice.request','advice_statistics':'advice.read','latest_advice':'advice.read','advice_detail':'advice.read','decide_suggestion':'advice.decide'},
 'agent_audit': {'runs':'ai.run_read'},
 'assistant': {'assistant_home':'overview.read','create_conversation':'$agent','send_message':'agent.history',
               'list_messages':'agent.history','get_run':'agent.history'},
 'audio': {'transcribe_audio':'visit.transcribe'},
 'auth': {name:'*' for name in ('company_entry','password_login','password_change','create_session','refresh_session','get_me','logout')},
 'console_auth': {name:'*' for name in ('login','refresh','me','change_password','logout')},
 'authorization': {name:'*' for name in ('own_permissions','catalog','roles','options','console_permissions','create_role','update_role','account','update_account','audit','available_modules','delegations','save_delegation')},
 'business': {'opportunity_create_options':'opportunity.create','business_options':'directory.read','directory_teams':'$team_directory','directory_members':'directory.read',
  'task_assignees':'directory.read','task_positions':'directory.read','workbench':'overview.read',
  'dashboard_ranking_view':'dashboard.ranking','dashboard_options':'dashboard.read','dashboard':'dashboard.read',
  'opportunities_overview':'opportunity.read','opportunity_detail':'opportunity.read','opportunities':'opportunity.read',
  'visit_form_schema':'visit.create','create_visit':'visit.create','visit_range':'visit.read','get_visit':'visit.read',
  'supplement_visit':'visit.supplement','visit_colleagues':'directory.read'},
 'business_activity': {'activities':'audit.business_read','activity_detail':'audit.business_read'},
 'collaboration': {'directory_fde':'directory.read','update_members':'opportunity.fde_members',
  'profile_scope_options':'profile.fde_read','dashboard':'profile.fde_read','activity':'profile.fde_activity','visit_opportunities':'visit.create'},
 'companies': {'directory':'access.console','select_company':'company.read','rename_company':'company.update',
               'get_role_labels':'company.read','set_role_labels':'company.update'},
 'company_rules': {'catalog':'rule.read','execution_catalog':'ai.execution_read','save_draft':'rule.draft',
  'publish':'rule.publish','restore':'rule.restore','preview':'rule.preview','presentation':'directory.read'},
 'connectivity': {'test_current_connection':'ai.config_test','read_connection_test':'ai.config_test'},
 'customer_assets': {'map_customers':'battle_map.read','refresh_map':'battle_map.read','read_assets':'actual.read','create_actual':'actual.create','void_actual':'actual.void'},
 'customers': {'customer_portfolio':'customer.read','customer_claim_pool':'customer.claim_directory','customer_claim_pool_options':'customer.claim_directory','reference':'customer.reference','list_customers':'customer.read',
  'opportunity_save_receipt':'customer.read','cancel_customer_opportunity_save':'customer.read',
  'submit_customer_creation_request':'customer.create_request','my_customer_creation_requests':'customer.create_request','customer_request_history':'customer.read',
  'claim_existing_customer':'customer.claim','customer_detail':'customer.read','create_customer':'customer.create',
  'update_customer':'customer.update','assign_customer':'*','check_opportunity_name':'$opportunity_name',
  'create_customer_opportunity':'$opportunity'},
 'demo_scenes': {'scenes':'demo_scene.read','create':'demo_scene.create','detail':'demo_scene.read',
  'history':'demo_scene.read','update':'demo_scene.update','delete':'demo_scene.delete'},
 'detail_reads': {'customer_overview':'customer.read','customer_header':'customer.read','opportunity_header':'opportunity.read',
  'customer_opportunity_header':'opportunity.read','opportunity_overview':'opportunity.read','customer_opportunities':'opportunity.read',
  'customer_opportunity_overview':'opportunity.read','visit_history':'visit.read','contact_history':'customer.read',
  'opportunity_timeline':'opportunity.read','actual_quarters':'actual.read'},
 'fde_profile': {'profile':'profile.fde_read','review':'profile.fde_review','profile_review_detail':'profile.fde_read'},
 'feishu_sync': {'read':'feishu.read','save':'feishu.configure','recover':'feishu.recover','act':'feishu.control','catalog':'feishu.read','sync_status':'feishu.read'},
 'model_api': {'list_connections':'ai.config_read','list_releases':'ai.config_read','test_connection':'ai.config_test',
  'get_test':'ai.config_test','publish_connection':'ai.config_publish'},
 'notifications': {'list_notifications':'notification.read','mark_notification_read':'notification.mark_read'},
 'operations_accounts': {'password_policy':'organization.read','update_password_policy':'account.password_policy','organization':'organization.read',
  'create_account':'account.create','update_account':'account.update','reset_password':'account.reset_password',
  'unlock_account_login':'account.unlock','create_department':'organization.manage','update_department':'organization.manage'},
 'operations_customers': {'summary':'customer.read','customers':'customer.read','create_options':'customer.create','customer':'customer.read','customer_overview':'customer.read',
  'customer_creation_requests':'customer.create_review','review_customer_creation_request':'customer.create_review',
  'update_profile':'customer.update','customer_history':'customer.read','claims':'customer.claim_review',
  'review':'customer.claim_review','release':'customer.release','resolve_owner':'customer.resolve_owner'},
 'operations_opportunities': {'opportunities':'opportunity.read','amount_policy':'$opportunity_name','detail':'opportunity.read','save':'$opportunity','quote':'opportunity.quote_create'},
 'opportunity_finance': {'read_financial_overview':'actual.read','management_financial_overview':'actual.read','management_finance_workspace':'actual.read','confirm_financial_source':'actual.create',
                         'confirm_operational_actuals':'actual.create'},
 'materials': {'materials':'material.read','management_materials':'material.read','product_lines':'material.read',
               'management_product_lines':'material.read','create_material':'material.manage','update_material':'material.manage',
               'update_product_lines':'material.manage','opportunity_materials':'opportunity.read','material_forecast_preview':'opportunity.read',
               'update_opportunity_materials':'opportunity.update','material_draft_forecast_preview':'material.read'},
 'opportunity_views': {name:'opportunity.read' for name in ('options','query','preference','set_preference','list_views','detail','create','update','delete')},
 'opportunity_creation_history': {'history':'opportunity.read','options':'opportunity.read'},
 'opportunity_fields': {'options':'opportunity.read','directory':'opportunity.read','detail':'opportunity.read',
                        'intent_history':'opportunity.read',
                        'update_fields':'opportunity.update','update_people':'opportunity.update'},
 'opportunity_initialization': {'options':'opportunity.update','preview':'opportunity.update','apply':'opportunity.update'},
 'operations_visits': {'visits':'visit.read','recorders':'visit.read','detail':'visit.read'},
 'operations_reports': {'ai_overview':'ai.usage_read','ai_calls':'ai.usage_read','rules':'ai.usage_read',
  'create_rule':'ai.usage_rules_manage','update_rule':'ai.usage_rules_manage','audit':'audit.read',
  'system_events':'audit.events_read','export_role_usage':'ai.usage_export'},
 'operations_targets': {'targets':'target.read','options':'target.manage','save':'target.manage','requests':'target.approve','save_batch':'target.manage',
  'batches':'target.approve','decide_batch':'target.approve','decide':'target.approve','history':'target.history'},
 'partners': {'directory':'partner.read','list_partners':'partner.read','save_partner':'partner.manage'},
 'profile': {'profile_scope_options':'profile.sales_read','scoped_performance':'profile.sales_read','update_sales_target':'target.submit',
  'sales_growth_review_detail':'profile.sales_read','ensure_sales_growth_review':'profile.sales_review','sales_growth':'profile.sales_read','scoped_sales_growth':'profile.sales_read',
  'evaluation_summary':'profile.sales_read','team_member_sales_growth':'profile.sales_read'},
 'risks': {'decide_risk':'risk.decide','list_risks':'risk.read','risk_detail':'risk.read','resolve_risk':'risk.resolve'},
 'targets': {'targets':'target.read','save':'target.submit','save_batch':'target.submit','history':'target.history'},
 'task_imports': {'preview_task_import':'$task_choices', 'save_task_import_draft':'$task_choices',
  'confirm_task_import':'$task_choices', 'reparse_task_import':'$task_choices',
  'list_task_imports':'task.read', 'task_import_detail':'task.read'},
 'tasks': {'alignment_capabilities':'*','task_customer_choices':'task.create_customer','task_opportunity_choices':'task.create_customer',
  'management_tasks':'task.read','management_task_owners':'task.read',
  'task_recipients':'$task_choices','list_tasks':'task.read','task_overview':'task.read','create_task':'$task','create_task_batch':'$task_batch',
  'task_detail':'task.read','task_reassignment_options':'task.coordinate','create_task_event':'$task_event','update_task_alignment':'$task_alignment'},
 'visit_flow': {'structure':'visit.structure','quality':'visit.quality_review'},
 # Draft handlers use the same action as their repository; the router remains
 # unmounted until source confirmation and archive integration are verified.
 'visit_imports': {'upload_visit_file':'visit.upload','upload_request_status':'visit.upload','import_status':'visit.read','retry_import':'visit.retry_import','download_original':'visit.download_original'},
 'main': {'connection_status':'*','web_capabilities':'*','health_live':'*','health_version':'*','health_ready':'*'},
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
