# cab0dee 失败项基线对比

基线 commit: cab0deec2fea053ea46e7bebfba658eba1316589。临时目录：/tmp/salegent-cab0dee-baseline-dgwnns2v。
使用当前 backend/.venv/bin/python 及 /opt/homebrew/bin/node，仅重跑原日志失败文件。未修改业务代码、测试或生产。

| 范围 | 当前原始全量日志 | cab0dee 失败文件重跑 | 结论 |
| --- | --- | --- | --- |
| Backend | 1402 passed / 6 failed | 67 passed / 5 failed（3 文件，72 项） | 5 项为既有失败；OpenAPI 为本轮新增但已修复 |
| Frontend | 537 passed / 27 failed（564 项） | 44 passed / 27 failed（8 文件，71 项） | 27 个失败名称与位置相同，无本轮新增失败项 |

当前 OpenAPI 已单项重跑：test_committed_spec_matches_the_application，1 passed。不能将失败文件子集结果表述成全量重跑通过。

## Backend 既有失败（5 项）

- tests/test_business_time_and_follow_up_schedule.py::test_advice_repository_serializes_correct_date_and_stable_fingerprint
- tests/test_fde_identity_capabilities.py::test_fde_agent_modes_block_sales_materialization_and_changed_snapshots
- tests/test_fde_identity_capabilities.py::test_fde_role_defaults_deny_commercial_and_recording_writes[fde]
- tests/test_fde_identity_capabilities.py::test_fde_role_defaults_deny_commercial_and_recording_writes[fde_lead]
- tests/test_fde_identity_capabilities.py::test_file_processing_rechecks_policy_before_persisting_text

## Backend 本轮新增但已修复（1 项）

- tests/test_openapi_contract.py::test_committed_spec_matches_the_application：当前单项 1 passed。

## Frontend 既有失败（27 项）

- frontend/tests/data-integrity.test.js:198 — 拒绝后重发草稿按账号隔离，并按用户ID选回同名接收人
- frontend/tests/data-integrity.test.js:214 — 岗位选择来自后台有效名单，空岗位不可选，切换接收方式保留表单
- frontend/tests/data-integrity.test.js:239 — 采纳建议从后台回查并允许人工修改，原生保存带建议版本和业务关联
- frontend/tests/fde-efficiency.test.js:4 — 团队排名按当前指标排序，同值并列且未知不当作零
- frontend/tests/fde-identity.test.js:32 — 24页中的业务页均在入口读取集中能力，旧深链不能绕过页面守卫
- frontend/tests/fde-identity.test.js:39 — FDE看板和我的不请求销售画像或排名，负责人个人页仍是本人
- frontend/tests/fde-identity.test.js:97 — 拜访实际参与与长期名单分开，改商机清掉未保存参与人，普通协同人去重
- frontend/tests/fde-identity.test.js:171 — 负责人钻取其他成员的商机固定成员上下文，不能切成本人加其他成员请求
- frontend/tests/fde-progress.test.js:9 — 周进展先聚合全部日数据再截取展示，不受日视图14条限制
- frontend/tests/fde-projects-loading.test.js:42 — 项目先显示，迟到的成员目录失败不清空项目、数量或金额，下一次加载可恢复目录
- frontend/tests/fde-projects-loading.test.js:69 — 目录先失败仍可等待并显示主列表；目录成功也不会掩盖主列表失败
- frontend/tests/fde-projects-loading.test.js:89 — 切换本人和团队后，旧目录的成功或失败都不能覆盖当前目录状态
- frontend/tests/fde-projects-loading.test.js:109 — 快速切成员保持精确请求范围，目录重排按成员ID恢复选择
- frontend/tests/fde-projects-loading.test.js:128 — 最新目录缺少当前筛选成员时不将现有项目误标为全部成员
- frontend/tests/fde-projects-loading.test.js:140 — 换账号、权限或重新登录后，未启动新加载也拒绝旧请求的成功与失败
- frontend/tests/fde-projects-loading.test.js:156 — 新身份加载立即清掉旧目录选项，不把旧成员ID带进新身份查询
- frontend/tests/fde-projects-loading.test.js:174 — 组件离页后目录与项目均不回填，普通FDE不读取团队目录
- frontend/tests/fde-projects-loading.test.js:188 — 连续五次输入只发最终关键词一次查询，目录在途结果可正常完成而不重读
- frontend/tests/fde-projects-loading.test.js:211 — 搜索防抖遇到显式筛选或卸载会清理，不能迟发重复或离页查询
- frontend/tests/fde-projects-loading.test.js:224 — 搜索等待时权限变化不发送旧范围查询，返回组件会恢复新身份加载
- frontend/tests/fde-projects-loading.test.js:237 — FDE卡片与销售共享字段格式，实绩零值和未登记值不会被项目金额替代
- frontend/tests/fde-projects-loading.test.js:255 — FDE总览与列表季度独立，切成员后迟到的旧总览不能覆盖新范围
- frontend/tests/followup-scope.test.js:72 — 团队多季度金额与倒计时保留所选季度，活跃不由在推数量代替
- frontend/tests/followup-scope.test.js:80 — 排名独立失败不阻断部门金额，金额失败不借用个人值
- frontend/tests/native-login.test.js:18 — 账号与密码交给服务端验证，任意真实密码不会被本地样例口令拒绝
- frontend/tests/native-login.test.js:40 — 重复点击只发一次登录请求，角色不匹配不会留下可用会话
- frontend/tests/opportunity-linked-boards.test.js:12 — 商机经营首页整合总览、筛选和商机卡片

## 日志

- /tmp/profile-all-backend-unit.log
- /tmp/profile-all-frontend.log
- /tmp/profile-baseline-cab0dee-backend-failed-files.log
- /tmp/profile-baseline-cab0dee-frontend-failed-files.log
