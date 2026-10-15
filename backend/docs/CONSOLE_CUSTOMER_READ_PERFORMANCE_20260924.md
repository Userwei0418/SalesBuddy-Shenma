# 运营后台客户管理查询优化

用户确认范围：优化真实公司客户管理约 11–12 秒的加载，只改服务端，不动小程序。基准为唯一仓库干净 main `de08496ec861716a4d9ce026534abfee5d0ec099`，线上后端 `71e8f71` / V150，三个服务一致，小程序保持 1.0.9。

## 原因与修改

现有管理员只读实测：`console/summary` 11,319.1 毫秒，`console/customers` 11,508.4 毫秒。查询计划显示约 1.7 万客户在客户表及归属表分别重复执行复杂授权检查，是主要耗时；列表还在分页前为所有客户查询联系人。

- V151 将全公司客户读取授权提到每条语句的 InitPlan。公司和软删除条件独立保留，只有当前功能确实拥有 workspace 范围的读取授权才走快速分支。
- 客户归属读取仍检查有效父客户；仅有认领审批权限的账号、团队/本人/参与范围及显式禁止继续按原语义判断。不修改写入策略或授予新权限，不在请求之间缓存权限。
- 客户列表先过滤、计数并分页，再关联当前页的人员、团队、联系人。字段、排序、搜索、筛选、分页和导出契约不变。
- 小程序源码和 OpenAPI 不变；不调整或测试已冻结的质检模型及其他 Agent。

## 验证

新增真实 PostgreSQL 测试：

1. 一次性测试库中创建 17,200 个合成客户，在非绕过 RLS 的角色下验证汇总、连续分页、行业/级别/认领状态/负责人筛选，每条语句限制 3 秒。
2. 对照原权限表达式，验证全公司、团队、本人、参与、仅审批、功能投影及显式撤权；同一连接预热后切换公司，防止复用旧授权结果。外公司及软删除数据保持隔离。
3. 验证主联系人、建档编号搜索、空页与导出字段一致。

首轮本地汇总 37.1 毫秒，组合分页和筛选 212.1 毫秒；这是合成测试库结果，不作为生产耗时。最终运行以 CI 和部署后的同查询对比为准。

验证命令：

```sh
backend/.venv/bin/python backend/tests/system/run_integration_postgres.py
backend/.venv/bin/python -m pytest backend/tests --ignore=backend/tests/integration -q
backend/.venv/bin/python backend/scripts/export_openapi.py --check
backend/.venv/bin/ruff check backend/src --select F
git diff --check
```

发布采用现有受控脚本：先备份并核验 V150，安装 V151 后重复迁移确认不变，再切换三个服务。随后用原管理员只读重跑同一查询，核对结果摘要、实际页面耗时、公网版本、服务进程目录及数据库校验。源码合并、部署和线上验收分别记录。

## 生产部署与验收回执

PR42 已通过[完整 CI](https://github.com/Userwei0418/Raccoon-SalesBuddy/actions/runs/35988727375)并合并，同步 GitHub/GitLab。后端单元 2052、前端 831、PostgreSQL 712 通过（20 项条件跳过）；历史迁移、ACL、队列及数据库专项检查通过。受检头为 bf804e7，合并提交 535b139 与受检源码树一致。

部署 `535b139a06c8132ebea097adb612cc4b79607096` / `customer-read-20260924-535b139`；实际 V151、119 份校验、无待迁移，运行角色不绕过 RLS。备份 118,719,639 字节完成校验，未读取或下载备份。公网版本、RELEASES 清单与 API、普通 Worker、飞书 Worker 三个实际进程一致。previous 为 admin-read-20260924-71e8f71，仅作为核对数据库兼容性后的回滚候选。

| 原管理员相同查询 | 优化前 | 优化后 | 返回摘要 |
| --- | ---: | ---: | --- |
| 客户汇总 | 11,319.1 ms | 58.4 ms | 完全一致 |
| 客户列表第一页 | 11,508.4 ms | 64.5 ms | 完全一致 |

这是生产仓储查询耗时，未计入浏览器渲染与网络，不代表全页 P95。查询计划确认权限在当前语句计算一次、联系人只关联当前页50条。浏览器核验真实公司首页、第二页、单客户名称搜索及演示公司首页均通过；两公司对客户、审计和同步事件的交叉读取均不可见。核验只读，无生产业务写入。

小程序源码及 OpenAPI 与修复基准无差异，保持 1.0.9 体验版；未重新上传或提审。11 项 Agent 配置保持，未进行新的模型调用。部署后文档提交可领先部署提交，业务源码继续以 535b139 为准。

证据见[生产验证](../../docs/evidence/customer-read-performance-20260924/verification.json)、[三服务运行目录](../../docs/evidence/customer-read-performance-20260924/runtime-inventory.json)、[实际数据库](../../docs/evidence/customer-read-performance-20260924/database-after.json)。
