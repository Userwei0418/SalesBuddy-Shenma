# 第 1 项：跟进日期默认值修复

## 已确认范围

用户于 2026-09-24 确认：首次新建跟进缺少日期时默认北京时间当天，显示、质检和提交一致；保留识别日期、草稿、手改与主动清空；同样检查创建时间；历史记录不补造日期。本项只修改 frontend，沿用 interaction_at、created_date 接口字段，无后端或数据库变更。

## 开始前核验

- 唯一仓库 Raccoon SalesBuddy，GitHub origin `Userwei0418/Raccoon-SalesBuddy`，GitLab backup `zhouwei5/raccoon-salesbuddy`。
- 干净 main `3e5b904645fdcafda46e177eedae7f9e2dd1d957`，新分支 `codex/visit-default-dates`。
- 微信开发者工具原生窗口确认工程为唯一仓库 `frontend`，AppID `wx08463b79dbeb2ba7`，配置 API 为 `https://www.ericepc.com/api/v1`。
- 公网 version 与 RELEASES.json 一致：`7a414b4c128c5443f71b9c6ac80a0bef3cd911f0`，release `feishu-events-20260923-7a414b4`，expected_schema V125。本轮未执行数据库迁移。
- 通过服务器 `runtime_inventory.py` 读取 API、普通 Worker、飞书 Worker 的实际进程目录及 REVISION，三个服务均 active，与上述 release 一致；源码预期 V125 不作为本轮数据库迁移状态的验证证据。
- 体验版保持用户此前确认的 1.0.8；此次修复尚未上传。

## 原因与实施规则

日期选择器的 fallback 是今天，但 values 中的真实字段可以为空。校验和提交读取 values，所以默认停留日期不等于已填写。

只在无匹配确认草稿的新建分支初始化空日期，取一次北京时间当天；不覆盖非空原值。初始化后立即持久化，避免跨日重进变成另一天。已有草稿即使日期为空也保留原样，避免把主动清空误认为新建缺省。已保存记录的编辑分支完全跳过初始化。

首次草稿同时保存尚未应用的 AI 商机建议，避免商机目录加载完毕前离开后丢失建议。已消费/取消的建议不从原文恢复，旧草稿仍沿用原恢复规则。此处只保护提前持久化引入的恢复边界，不更改商机关联规则。

验证计划：真实页面逻辑的隔离测试覆盖默认日期进入质检、识别/手改/跨日草稿保留、清空拦截、异常日期保留给校验、历史编辑不补日期。运行 `node --test frontend/tests/*.test.js` 与 `python3 frontend/scripts/verify_frontend.py`。

## 验证与交付

- 修复前，隔离测试复现默认日期为空：9 项中 5 项失败；补丁后增加商机建议恢复保护，共 11 项本次测试通过。
- 全量前端回归：`node --test frontend/tests/*.test.js`，784 项通过，0 失败、0 跳过。
- 前端结构及语法：`python3 frontend/scripts/verify_frontend.py` → `FRONTEND_VERIFY_OK`，26 个页面、48 个 JSON、386 个文件。
- `git diff --check` 通过。源码通过短期分支 PR 交付，仓库 CI 通过后再合并；最终提交与远程同步回执记入本轮需求交接清单。
- 验证使用隔离的页面逻辑、固定北京时间时钟、模拟存储与质量接口，没有调用付费 AI 或向真实客户写入测试记录。
- 未执行本项真机验收，也未上传小程序或替换体验版；体验版仍为用户此前确认的 1.0.8。本项无后端、数据库、接口变更或服务部署。

人工验收：新建缺日期的跟进，确认页无需再操作日期选择器即可进行质检；分别修改/清空日期、返回后重进，检查保留原值/必填拦截；历史记录的空日期仍保持原样。
