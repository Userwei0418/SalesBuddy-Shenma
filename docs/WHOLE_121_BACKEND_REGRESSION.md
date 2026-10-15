# 神码完整 1.2.1 后端回归证据

记录日期：2026-10-15。内部源码基准：`e40a8029d94a03fc45448e00d536e5a5b9120831`。此文件不含账号密码、Token、Key、DSN、真实业务记录或运行配置。

## 当前判定

- 后端源码候选冻结用于服务器备份克隆验证；不等于已经上线或业务验收完成。
- 完整 R3 退出码非零：123 failed / 4826 passed / 1263 skipped，耗时119.09秒。整体测试仍红，不能写成“后端全套通过”。
- 123项由86项固定 e40 同依赖纯源码可复现失败、下列36项逐项列明的非同基线合同/资料问题、1项安全字段新增导致的旧精确响应断言组成。最后1项已修fixture并定向9/9通过；未再运行完整R4，不能把122/4827等推算值冒充实测结果。
- 纯 e40 复现证明“并非神码覆盖层新引入”，不证明功能正确或可忽略。涉及登录、权限、报告、推送的实际能力仍要由服务器克隆/API/受限PG验收关闭。
- 1263项 skipped 没有计入通过；本机离线测试不替代外部AI、飞书发送、微信真机、服务器数据库权限或正式上线证据。

## 固定运行方法与原始记录

以下均在神码目标仓库 backend 目录执行，`SHENMA_TEST_PYTHON` 指向任务固定锁文件安装的独立 venv Python3.12，变量本身不是业务运行配置。

```
"$SHENMA_TEST_PYTHON" -m pytest tests -q -p no:cacheprovider --tb=short
```

- R2：141 failed / 4800 passed / 1264 skipped。修复前日志保留，不覆盖。
- R3：123 failed / 4826 passed / 1263 skipped。没有通过跳过或删除测试消除失败。
- 纯 e40 unit 基线：16个报告/Agent/评估等失败文件，同任务 venv，`PYTHONPATH=/tmp/shenma-e40-backend-source/backend/src`，79 failed /311 passed。79个失败nodeid与R3逐一相同。
- 纯 e40 UI/治理基线：company_login_ui、opportunity_finance_console、layer_boundaries、prepare_agent_metadata，7 failed /107 passed，7个失败nodeid与R3逐一相同。
- 纯基线使用从固定Git对象提取到/tmp的backend，不修改内部共享工作区，不连接任何线上环境。
- `shenma-whole-backend-full-pytest-r2.log`：SHA-256 `fa96ae169d842eecfe8500542d046b9a4131a7a57bd06d771710982ff44f3133`；原始本机路径 `/tmp/shenma-whole-backend-full-pytest-r2.log`。
- `shenma-whole-backend-full-pytest-r3.log`：SHA-256 `b644fb86ccfa54ee97fbf38c2e712a4fc02dfa2df9de6c69eeaf1287d2d6d4ea`；原始本机路径 `/tmp/shenma-whole-backend-full-pytest-r3.log`。
- `shenma-whole-pure-e40-unit-baseline.log`：SHA-256 `3544ca95f4453ac384a051f646c5fe6e6ded3ca8c4971cf672c919a1b3d39cf1`；原始本机路径 `/tmp/shenma-whole-pure-e40-unit-baseline.log`。
- `shenma-whole-pure-e40-ui-baseline.log`：SHA-256 `a97d6e02c2e8a90ccb1e8463134578c717ead5374d887e2e0628471abe229427`；原始本机路径 `/tmp/shenma-whole-pure-e40-ui-baseline.log`。

## 发布所需关键回归

以下命令保留所有选中测试，不用xfail或新skip隐藏失败。该固定命令实测830 passed /0 failed，耗时18.27秒。

```
"$SHENMA_TEST_PYTHON" -m pytest tests/test_weekly*.py tests/test_feishu*.py tests/test_unified_model_key*.py tests/test_model_connection_lock.py tests/test_fde_private_tls.py tests/test_shenma_whole_contract.py tests/test_private_request_validation.py tests/test_customer_creation_request_contract.py tests/test_opportunity_view_contract.py tests/test_business_web_static.py tests/test_openapi_contract.py tests/test_route_permissions.py tests/test_work_report_contract.py tests/test_work_report_generation.py tests/test_work_report_providers.py tests/test_work_report_review_scope_v2.py -q -p no:cacheprovider --tb=short
```

- 已单独实测78/78：统一Key旧密文旁路保护、共享服务拒绝、单入口UI、真实本机CA/hostname TLS、私有请求不回显、exactjob clone/root-peer安全边界、旧v1视图显式阻断、金额表单边界等。
- S2/S3现行合同21/21：公司编号允许缺省、审批版本/同事务reviewed_payload、历史summary；新版视图Workspace v2 typed列/筛选/排序/分组、拒绝SQL/身份覆盖。旧持久配置保持原样并显式migration_blocked，不能静默覆盖。
- 锁定配置页9/9：GET新增统一Key的静态状态元数据后，验证完整安全字段白名单，所有修改入口仍由部署锁阻止。
- `python -m compileall -q backend/src`通过；全src `ruff check --select F`通过；本次新增/定向修改文件完整Ruff通过；OpenAPI重新导出成功。
- runtime源码不import tools。`tools/local`与`tools/installation`只为离线历史测试import补齐，不是上线包依赖；未运行其中部署/重置工具。

## 86项纯 e40 复现清单

以下是完整nodeid；同一文件仅合并展示，不减少分母。文件内可能有旧mock、提示词/合同版本、时间预算假设及新增来源注释差异；不统一断言“全部只是旧fixture”。例如报告audit mock缺新预算字段、PPL预期未包含来源说明、prompt断言仍为sales_week.v2而产品为overview_sections.v4；路由预算/回退测试也确实失败。具体异常保留在基线日志中。

### tests/test_agent_business_rules.py（2项）

- `tests/test_agent_business_rules.py::test_advice_does_not_adopt_old_policy_after_new_publication[False]`
- `tests/test_agent_business_rules.py::test_advice_does_not_adopt_old_policy_after_new_publication[True]`

### tests/test_agent_platform_inference.py（1项）

- `tests/test_agent_platform_inference.py::test_snapshot_metadata_never_appears_in_business_result`

### tests/test_agent_platform_pilot.py（2项）

- `tests/test_agent_platform_pilot.py::test_actual_handler_selects_pilot_or_original_and_persists_one_validated_result[True-False-agent_platform]`
- `tests/test_agent_platform_pilot.py::test_actual_handler_selects_pilot_or_original_and_persists_one_validated_result[True-True-senseaudio]`

### tests/test_agent_run_contract.py（2项）

- `tests/test_agent_run_contract.py::test_contract_keeps_model_sections_including_empty_lists`
- `tests/test_agent_run_contract.py::test_supervisor_and_manager_contracts_require_their_own_sections`

### tests/test_battle_map_platform.py（7项）

- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[blocked]`
- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[both_failed]`
- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[empty]`
- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[invalid_score]`
- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[normal]`
- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[off]`
- `tests/test_battle_map_platform.py::test_business_handler_persists_once_after_platform_or_original[timeout]`

### tests/test_business_time_and_follow_up_schedule.py（1项）

- `tests/test_business_time_and_follow_up_schedule.py::test_advice_repository_serializes_correct_date_and_stable_fingerprint`

### tests/test_company_login_ui.py（1项）

- `tests/test_company_login_ui.py::test_console_company_entry_workflow`

### tests/test_competency_platform.py（16项）

- `tests/test_competency_platform.py::test_another_subject_cannot_start_review_even_when_row_is_returned`
- `tests/test_competency_platform.py::test_competency_facts_keep_midnight_visit_on_its_beijing_business_date`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[bad_contract]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[blocked]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[both_failed]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[direct_only]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[missing_adapter]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[normal]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[off]`
- `tests/test_competency_platform.py::test_competency_uses_one_audited_operation_and_only_persists_a_valid_result[timeout]`
- `tests/test_competency_platform.py::test_fact_query_receives_only_subject_and_explicit_beijing_window`
- `tests/test_competency_platform.py::test_framework_controls_dimension_order_weights_and_total_despite_model_fields`
- `tests/test_competency_platform.py::test_no_records_can_have_explicit_insufficient_evidence_without_fabricated_sources`
- `tests/test_competency_platform.py::test_replaced_review_cannot_record_a_completion_effect`
- `tests/test_competency_platform.py::test_review_storage_links_actual_provider_and_server_scores_without_overwriting_settings`
- `tests/test_competency_platform.py::test_terminal_or_missing_review_is_not_reopened`

### tests/test_customer_profile.py（1项）

- `tests/test_customer_profile.py::test_registered_risks_keep_precedence_over_clear_assessment[risks6-100]`

### tests/test_fde_facts_runtime.py（2项）

- `tests/test_fde_facts_runtime.py::test_filtered_facts_uses_real_parser_and_common_business_contract[chatbi]`
- `tests/test_fde_facts_runtime.py::test_filtered_facts_uses_real_parser_and_common_business_contract[customer_chatbi]`

### tests/test_fde_profile.py（2项）

- `tests/test_fde_profile.py::test_same_success_is_reused_and_empty_profile_does_not_enqueue`
- `tests/test_fde_profile.py::test_summary_uses_database_counts_even_when_model_invents_perfect_scores`

### tests/test_layer_boundaries.py（3项）

- `tests/test_layer_boundaries.py::test_api_has_no_database_statements`
- `tests/test_layer_boundaries.py::test_dependency_direction`
- `tests/test_layer_boundaries.py::test_visit_orchestration_delegates_sql_to_repositories`

### tests/test_opportunity_finance_console.py（1项）

- `tests/test_opportunity_finance_console.py::test_finance_console_behaviors`

### tests/test_prepare_agent_metadata.py（2项）

- `tests/test_prepare_agent_metadata.py::test_explicit_company_onboarding_selects_all_business_agents_only`
- `tests/test_prepare_agent_metadata.py::test_new_contract_requires_publication_for_exact_template[None-work_report]`

### tests/test_visit_entry_platform.py（1项）

- `tests/test_visit_entry_platform.py::test_visit_facts_do_not_query_or_disclose_business_aggregates`

### tests/test_work_report_attempt_routing.py（3项）

- `tests/test_work_report_attempt_routing.py::test_late_platform_output_after_safe_fallback_is_discarded`
- `tests/test_work_report_attempt_routing.py::test_safely_failed_platform_is_skipped_for_remaining_batches_and_combine[ProviderUnavailable]`
- `tests/test_work_report_attempt_routing.py::test_safely_failed_platform_is_skipped_for_remaining_batches_and_combine[TimeoutError]`

### tests/test_work_report_audit_hotfix.py（12项）

- `tests/test_work_report_audit_hotfix.py::test_existing_validation_still_rejects_with_fixed_noncontent_codes[batch-work_report.source_outside_batch]`
- `tests/test_work_report_audit_hotfix.py::test_existing_validation_still_rejects_with_fixed_noncontent_codes[empty-work_report.empty_or_oversized]`
- `tests/test_work_report_audit_hotfix.py::test_existing_validation_still_rejects_with_fixed_noncontent_codes[gaps-work_report.coverage_gaps]`
- `tests/test_work_report_audit_hotfix.py::test_existing_validation_still_rejects_with_fixed_noncontent_codes[rules-work_report.content_rules]`
- `tests/test_work_report_audit_hotfix.py::test_existing_validation_still_rejects_with_fixed_noncontent_codes[schema-work_report.content_schema]`
- `tests/test_work_report_audit_hotfix.py::test_original_router_persists_contract_code_without_model_content`
- `tests/test_work_report_audit_hotfix.py::test_valid_content_is_identical_after_diagnostic_only_change`
- `tests/test_work_report_audit_hotfix.py::test_validated_result_uses_existing_terminal_enum_and_preserves_fallback_trace[basic_summary]`
- `tests/test_work_report_audit_hotfix.py::test_validated_result_uses_existing_terminal_enum_and_preserves_fallback_trace[cancelled]`
- `tests/test_work_report_audit_hotfix.py::test_validated_result_uses_existing_terminal_enum_and_preserves_fallback_trace[failure]`
- `tests/test_work_report_audit_hotfix.py::test_validated_result_uses_existing_terminal_enum_and_preserves_fallback_trace[generated]`
- `tests/test_work_report_audit_hotfix.py::test_validated_result_uses_existing_terminal_enum_and_preserves_fallback_trace[validation_failure]`

### tests/test_work_report_ppl_projection.py（16项）

- `tests/test_work_report_ppl_projection.py::test_complete_ppl_projection_preserves_exact_frozen_values_and_next_action_limit`
- `tests/test_work_report_ppl_projection.py::test_dates_in_arbitrary_prose_are_not_extracted_or_certified[\u5f53\u524d\u72b6\u6001\u3002\n\u4e0b\u4e00\u6b65\uff1a2026-12-01\u5b8c\u6210\u3002]`
- `tests/test_work_report_ppl_projection.py::test_dates_in_arbitrary_prose_are_not_extracted_or_certified[\u660e\u592914\u70b9\u9a8c\u6536]`
- `tests/test_work_report_ppl_projection.py::test_empty_contributors_and_missing_state_do_not_invent_people_or_status`
- `tests/test_work_report_ppl_projection.py::test_full_projection_is_assembled_once_after_the_only_model_call`
- `tests/test_work_report_ppl_projection.py::test_long_next_action_keeps_date_disclaimer_without_cutting_resolution`
- `tests/test_work_report_ppl_projection.py::test_next_action_absence_and_unavailability_remain_distinct[not_recorded-\u672a\u767b\u8bb0\u4e0b\u4e00\u6b65\uff1b\u4e0d\u80fd\u636e\u6b64\u63a8\u65ad\u6ca1\u6709\u540e\u7eed\u5de5\u4f5c\u3002]`
- `tests/test_work_report_ppl_projection.py::test_next_action_absence_and_unavailability_remain_distinct[unavailable-\u4e0b\u4e00\u6b65\u6750\u6599\u4e0d\u53ef\u5f97\uff0c\u4e0d\u80fd\u636e\u6b64\u8ba4\u5b9a\u672a\u767b\u8bb0\u3002]`
- `tests/test_work_report_ppl_projection.py::test_only_typed_planned_due_time_can_be_presented_as_registered_deadline[False-True]`
- `tests/test_work_report_ppl_projection.py::test_only_typed_planned_due_time_can_be_presented_as_registered_deadline[True-False]`
- `tests/test_work_report_ppl_projection.py::test_only_typed_planned_due_time_can_be_presented_as_registered_deadline[True-True]`
- `tests/test_work_report_ppl_projection.py::test_probability_is_never_filled_in_from_threshold_or_converted_to_zero[0-0%]`
- `tests/test_work_report_ppl_projection.py::test_probability_is_never_filled_in_from_threshold_or_converted_to_zero[75-75%]`
- `tests/test_work_report_ppl_projection.py::test_probability_is_never_filled_in_from_threshold_or_converted_to_zero[None-\u672a\u77e5\uff08\u672a\u6838\u5b9a\uff0c\u4e0d\u63090\u5904\u7406\uff09]`
- `tests/test_work_report_ppl_projection.py::test_referenced_action_must_also_be_allowed_in_that_section`
- `tests/test_work_report_ppl_projection.py::test_unreferenced_next_action_is_not_read_or_added_to_section`

### tests/test_work_report_retained_facts.py（3项）

- `tests/test_work_report_retained_facts.py::test_ppl_progress_is_rendered_from_frozen_enum_not_model_wording[no_recorded_progress_in_week-\u672c\u5468\u672a\u8bb0\u5f55\u65b0\u8fdb\u5c55\u3002]`
- `tests/test_work_report_retained_facts.py::test_ppl_progress_is_rendered_from_frozen_enum_not_model_wording[recorded_in_week-\u672c\u5468\u6709\u5df2\u767b\u8bb0\u7684\u8fdb\u5c55\u6750\u6599]`
- `tests/test_work_report_retained_facts.py::test_ppl_progress_is_rendered_from_frozen_enum_not_model_wording[unavailable-\u672c\u5468\u8fdb\u5c55\u6750\u6599\u4e0d\u53ef\u5f97\uff0c\u4e0d\u80fd\u636e\u6b64\u8ba4\u5b9a\u6ca1\u6709\u8fdb\u5c55\u3002]`

### tests/test_work_report_source_qualifier_wiring.py（8项）

- `tests/test_work_report_source_qualifier_wiring.py::test_prompt_update_keeps_item_and_batch_length_rejection[False]`
- `tests/test_work_report_source_qualifier_wiring.py::test_prompt_update_keeps_item_and_batch_length_rejection[True]`
- `tests/test_work_report_source_qualifier_wiring.py::test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase[False-direct]`
- `tests/test_work_report_source_qualifier_wiring.py::test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase[False-fallback]`
- `tests/test_work_report_source_qualifier_wiring.py::test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase[False-platform]`
- `tests/test_work_report_source_qualifier_wiring.py::test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase[True-direct]`
- `tests/test_work_report_source_qualifier_wiring.py::test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase[True-fallback]`
- `tests/test_work_report_source_qualifier_wiring.py::test_shared_prompt_version_and_source_scope_reach_each_actual_route_and_phase[True-platform]`

## 其余36项逐项说明

每项均给出实际失败依据和处置边界，不以“旧fixture”一词概括。

1. `tests/test_business_options_release.py::test_frontend_fixture_is_the_actual_server_contract_and_stages_are_accepted`

   - 前端测试用 business-options.json.customer_creation 缺 business_identities，与真实 business_options() 不等。已从真实后端函数同步 fixture，精确后端合同 2/2、前端 options 与完整性 26/26 通过；Mini/Web 实际 catalog VM 安装真实 JSON 均通过，两个运行端均动态 GET/install。该前端合同门禁已关闭，不据此改写完整 R3 历史结果。

2. `tests/test_credential_deployment.py::test_required_ci_checks_continue_after_an_independent_permission_failure`

   - 测试第175行读取 .github workflow 的 jobs.backend-and-database，但神码 CI 没有该 job 名，KeyError；属于内部 CI 编排合同。不能宣称当前整体 CI 已绿；发布由神码独立 clone/备份/迁移验收门禁执行。

3. `tests/test_demo_reset.py::test_catalog_preserves_originals_product_configuration_and_never_disables_fk`

   - 第42行把可清理业务表数量写死45；完整新版工具 catalog 实为50，断言50!=45。该重置工具不参与本轮安装/运行/升级，未在任何数据库执行。此结果不授权重置正式数据，也不证明重置工具可用。

4. `tests/test_identity_production_release.py::test_exact_candidate_and_normal_acl_admitted`

   - 内部 identity_release_contract.py:56 固定 V239/API三函数权限差异，fixture normal_policy 已为 V250，因精确 schema/fingerprint/三函数范围不符拒绝。不是登录路由返回失败，但涉及权限部署，须以神码 V399 克隆真实 runtime grants/NOBYPASSRLS/角色请求验收替代；该真实验收未完成前仍是发布门禁。

5. `tests/test_identity_production_release.py::test_policy_is_only_api_three_grants_and_schema_fingerprint`

   - 内部 identity_release_contract.py:56 固定 V239/API三函数权限差异，fixture normal_policy 已为 V250，因精确 schema/fingerprint/三函数范围不符拒绝。不是登录路由返回失败，但涉及权限部署，须以神码 V399 克隆真实 runtime grants/NOBYPASSRLS/角色请求验收替代；该真实验收未完成前仍是发布门禁。

6. `tests/test_installation_roles.py::test_permission_contract_is_bound_to_complete_product_ledger`

   - 第86行比较内部 product-policy V250 fingerprint 与神码实际完整桥接 ledger，二者必不同（新链保留V152–161并映射V300–398/V399）。该内部安装器不可拿来部署神码；权限安全仍须通过神码维护迁移与受限角色实际验收，不能仅靠本解释放行。

7. `tests/test_online_r4_hotfix.py::test_report_policy_delta_is_only_two_worker_selects_and_forward_ledger`

   - 第97行断言内部历史 prior policy 的固定 canonical hash；当前 copied policy 已继续更新，hash不等。只说明旧R4补丁差异合同失效；本轮不调用该内部热修部署流程。

8. `tests/test_product_bundle.py::test_packaged_current_manuals_include_their_linked_instructions`

   - 第25行按 tools/installation/release_bundle.py 内部文档白名单筛选神码 docs，manuals为空。神码使用独立打包脚本与客户指南，不调用该内部 product-bundle 打包器。标记：交付文档打包验收阻断，最终包必须另查实际神码指南及其链接存在；此项不能当已通过。

9. `tests/test_release120_production_acl.py::test_apply_has_one_commit_after_full_four_role_verify`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

10. `tests/test_release120_production_acl.py::test_changed_plan_after_before_persist_prevents_any_acl`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

11. `tests/test_release120_production_acl.py::test_exact_four_production_services_and_source_bindings`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

12. `tests/test_release120_production_acl.py::test_existing_transaction_rejected_without_rollback_of_caller`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

13. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[before_persist]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

14. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[cluster]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

15. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[commit]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

16. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[committed_persist]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

17. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[identity]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

18. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[intent_persist]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

19. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[ledger]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

20. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[reconcile]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

21. `tests/test_release120_production_acl.py::test_failure_never_erases_commit_uncertainty[rollback]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

22. `tests/test_release120_production_acl.py::test_full_ledger_preserves_historical_registration_independent_of_checksum_count[None]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

23. `tests/test_release120_production_acl.py::test_full_ledger_preserves_historical_registration_independent_of_checksum_count[checksum]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

24. `tests/test_release120_production_acl.py::test_full_ledger_preserves_historical_registration_independent_of_checksum_count[duplicate]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

25. `tests/test_release120_production_acl.py::test_full_ledger_preserves_historical_registration_independent_of_checksum_count[missing_historical_version]`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

26. `tests/test_release120_production_acl.py::test_plan_change_during_commit_intent_prevents_commit`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

27. `tests/test_release120_production_acl.py::test_verify_only_never_reconciles_or_commits`

   - 全部在 deploy/release120_production_acl.py:126 schema_contract 前置拒绝 EXACT_REGISTERED_VERSIONS_REQUIRED；尚未进入模拟apply/commit/rollback分支。测试绑定内部历史四服务/V250完整注册版本集合，而神码使用保留+桥接的独立ledger及运行角色。本轮不能调用此内部ACL发布器；需另以V399真实克隆权限与回退证据放行。

28. `tests/test_review_production_release.py::test_actual_stage_sql_policy_and_preserved_production_history[V240]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

29. `tests/test_review_production_release.py::test_actual_stage_sql_policy_and_preserved_production_history[V241]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

30. `tests/test_review_production_release.py::test_actual_stage_sql_policy_and_preserved_production_history[V242]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

31. `tests/test_review_production_release.py::test_actual_stage_sql_policy_and_preserved_production_history[V243]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

32. `tests/test_review_production_release.py::test_actual_stage_sql_policy_and_preserved_production_history[V244]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

33. `tests/test_review_production_release.py::test_candidate_stages_are_frozen[V245-migrations/V245__work_report_review_freeze.sql-36cab1055618b765dc822bdd69fa15f8b73e262316d31481969728adea06d19b-209-240]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

34. `tests/test_review_production_release.py::test_candidate_stages_are_frozen[V246-migrations/V246__review_worker_fact_reads.sql-947f157f37bf721bef5b0e8bbae5e28cb223a4a82b07fb6db81c3a3f07611d33-210-241]`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

35. `tests/test_review_production_release.py::test_frozen_v240_uses_actual_sql_ledger_and_exact_api_policy`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

36. `tests/test_review_production_release.py::test_product_policy_binds_complete_v250_source_ledger`

   - 内部冻结V240–246阶段合同直接找原迁移文件名；神码真实有效文件按 manifest 映射为 V388–394，旧路径不存在。末项另固定内部211条ledger，而神码桥接为223条。没有复制假V240文件或改写历史校验来迁就。应核查 database/shenma_whole_121_manifest.json 的 source/target/checksum及真实PG迁移；本轮不调用内部Review阶段发布器。

## 第123项修复状态

- `tests/test_model_connection_lock.py::test_locked_page_can_read_safe_config`：R3期望精确三键dict，而GET实际增加 `unified_model_key` 安全元数据。产品行为正确，没有恢复任何改Key入口。测试改为检查三键原响应及新增字段白名单：managed/can_manage/configured/key_hint/revision/updated_at/rotation_status/message，不允许密钥/密文混入。该文件定向9/9通过，源码未为迁就测试删掉字段。

## 仍须独立关闭的发布门禁

- 前端业务options fixture与真实合同对齐；实际建档/审批、页面下拉选择和错误提示核对。
- 使用神码实际V159备份副本升级到V399，核对旧数据摘要、角色NOSUPERUSER/NOBYPASSRLS、公司/本人/团队隔离及统一Key单入口，不能使用内部ACL部署器代替。
- 真实HTTP普通单人周报：options→snapshot→仅执行精确snapshot job→builtin run→仅执行精确generate job→generated且provider为direct/agent_platform→保存/重开；basic_summary不能算AI成功。
- 真实Web与管理后台登录/权限回归；纯e40 company_login测试失败虽定位在素材页旧mock而非密码提交成功，但不能据此省略真实登录验收。
- 新报告包含synthetic来源时允许本地生成，但飞书冻结发布检查全manifest/父链并拒绝synthetic；旧weekly素材过滤保持原逻辑。不能写成“新版采集已自动忽略47组体验数据”。
- 飞书实际表/群绑定保留和写入/发送验收单独记录；克隆exactjob不启动队列/通知维护、不消费旧outbox、不发真实群消息。
- 最终客户使用指南、管理员入口、密码附件范围及链接必须在神码实际发行包核对；内部product_bundle文档白名单失败不证明客户资料已交付。

本文件冻结的是回归事实和剩余验收门禁，不提供对整体CI的绿色结论。

## 关键回归最终实测补记

- 上述完整固定命令实际退出码0：830 passed，2条第三方库deprecation warning，18.27秒。没有新skip。
- 原始日志：`/tmp/shenma-whole-backend-release-critical.log`；SHA-256 `bcb32daef29d0769af178e8d51d7c74fc6150a0b3be3fae4ef2d51fbb2699233`。
- 该830项是发布关键离线回归的明确子集，不能替代R3全量结果或服务器克隆验收。

## 服务器 root 身份切换修复与实测补记

- 服务器预备阶段在 `sudo -u shenma-sales` 导入校验被 sudoers 拒绝；当时尚未进行数据库备份或迁移。没有调整 sudoers，没有新增权限角色。
- 最小修复：`prepare-whole-upgrade.py` 6处、`upgrade-sales.py` 8处、`preflight-runtime.py` 1处统一改为固定 `/usr/sbin/runuser -u <固定用户> -- <argv>`。私有 `clone_acceptance.py` 另1处做同样替换，不进仓。没有使用shell，也不改变备份、迁移、保留摘要、切换和回退顺序。
- 单job helper没有嵌套sudo；保留临时postgres euid握手、恢复root及随后永久降权为业务OS用户的既有受限实现。
- 根代理在销售服务器实际root执行并回读：业务用户 `service_uid=998`，PostgreSQL本机peer身份 `pg_peer_identity=postgres`，exec36218退出码0。此项是真实主机身份和peer验证；不等于数据库升级或上线已完成。
- 本地固定回归：`python -m pytest deployment/test_upgrade_sales.py deployment/test_root_runuser.py backend/tests/test_shenma_whole_contract.py -q -p no:cacheprovider --tb=short`，54 passed，0.65秒。覆盖原升级/回退、15处固定runuser argv/分隔符、无shell/无sudo、PostgreSQL查询合同及exactjob身份边界。
- 三个部署脚本和私有clone脚本py_compile通过；新增边界测试及私有脚本Ruff通过。候选重新冻结；没有将该定向通过冒充完整R4或正式发布结果。

## 继承umask与离线依赖cache修复补记

- 根代理随后真实回读（exec54534）：旧候选根、backend、.venv/bin及依赖目录均为root:0700，系统Python为0755。runuser身份切换成功后仍Permission denied，原因转为调用者继承umask077，不是sudoers问题。
- 根代理追加回读（exec8425）：旧候选已装fastapi源码和editable/virtualenv pth文件为0600，pydantic核心扩展为0711。仅设置新mask仍可能复用先前私有权限的UV cache，所以不能沿用该缓存作为修复。
- 两个部署main在root/主机校验后明确设置umask022用于新source/wheels/venv；备份创建前切换077。两脚本各两次安装均采用`uv --no-cache pip install`，仍只读取已校验离线wheel，不清cache、不chmod已有secret或cache整树。既有root-only wheels目录保持原样，由root安装过程读取。
- 新增真实临时tar/目录回归：继承077运行两个脚本的候选准备阶段，实际检查新候选、backend、嵌套源码、.venv/bin及wheel目录0755，源码/wheel文件0644；UV执行边界用桩，不宣称本机已真实执行服务器依赖安装。另静态检查077先于backup.mkdir及两条安装均带全局no-cache参数。
- 最终组合回归：`python -m pytest deployment/test_root_runuser.py deployment/test_upgrade_sales.py backend/tests/test_shenma_whole_contract.py -q -p no:cacheprovider --tb=short`，58 passed，0.59秒。新增边界测试Ruff通过。新候选仍须服务器实际runuser导入及克隆迁移验证；此补记不把该待办冒充已完成。
