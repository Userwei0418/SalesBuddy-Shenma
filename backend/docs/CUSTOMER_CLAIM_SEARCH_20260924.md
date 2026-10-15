# 客户认领筛选与拼音搜索

用户确认第 10 项：行业、认领状态、客户名中文/全拼/首字母搜索；地区不纳入。基于 PR35 的 `91ae2c2` 继续，旧目录与历史版本不作为开发来源。

## 接口与数据边界

- `GET /api/v1/customers/claim-pool/options` 返回 `industries`、`claim_statuses`，各项为 `{value,label}`；空值表示全部。
- 行业来自当前有权查看的公司客户目录去重值，不补造未知行业。未填行业仍在“全部行业”中；不会从当前 50 条前端列表推导选项。
- 原 `GET /api/v1/customers/claim-pool` 增加可选 `industry`、`claim_status`。行业精确匹配；状态非法返回 422；`q` 上限 100 字符，单页 1–100（前端 50）。省略新参数保留全目录语义。
- `unclaimed` / `claimed` / `legacy_review` 按正式归属状态；`mine` 为本人正式认领；`pending` 为本人最新申请待审批。未认领客户可以存在待审批申请，两种筛选有交集；申请成功不会变成已认领。
- 中文名称子串匹配；全拼/首字母不区分大小写，接受空格、连字符、全角字母。`%`、`_` 按字面查找。多音字采用词典默认读音，不声称所有读音或错别字均可匹配。
- 目录基础字段、客户详情权限、认领审批流程均保留。选项和结果都要求 `customer.claim_directory`，SQL 再按当前工作区与授权范围检查；名字匹配本身不授予客户详情权限。

## 拼音实现与迁移

使用固定版本 `pypinyin==0.55.0` 的全拼和 `FIRST_LETTER` 转换，[官方用法](https://pypinyin.readthedocs.io/zh-cn/master/usage.html)。不改写客户名称或推断业务字段。

只有拉丁字母或数字查询才额外读取服务端最小名称投影，在线程中计算匹配；50,000 项 LRU 仅缓存“名称→拼音”，不缓存租户目录、成员资格或授权结果。每次查询重新读取当前授权名称，最终 SQL 以当前名称匹配并重新校验权限，客户改名/删除/撤权后旧缓存不能恢复其访问。转换结果不下发小程序。纯数字/英文混合客户名也保留字面搜索。

V147 只新增三个只读 `SECURITY DEFINER` 函数；不改业务数据。继承既有目录函数所有者、EXECUTE 授权和 grant option，移除部署账号的额外默认授权，固定 `search_path=pg_catalog`。旧 SQL 函数签名与旧 company/department 调用不变。总数和当前页在同一条最终 SQL 中计算；名称候选和最终页是两个读取快照，期间新增/改名的客户可在下一次搜索出现。前端若检测跨页总数变化或重复 ID，会要求刷新，不拼接不一致的页。

发布顺序：数据库负责人审核 V147 → 迁移及后端部署（安装新增依赖）→ 验证两个接口 → 小程序验收/上传。旧后端无法提供新筛选接口，不应拿该组合做新功能验收。旧前端可继续使用升级后的服务；回滚应用可保留新增只读函数。

## 验证记录与边界

- 前端全量 826 项通过，含组合条件翻页、旧响应、选项故障重试、换号、删除行业后刷新；后端单元 2045 项通过。
- 全新隔离 PostgreSQL、非绕过 RLS 运行角色：客户目录与授权 7 项通过；另跑实际 owner/ACL/default grant/重复迁移专项通过。
- 合成 17,000 客户规模脚本：首次全拼约 3173ms、缓存后首字母 2578ms、行业+首字母 1260ms、行业+中文 638ms；每次返回 50 条、约 15KB。是本地合成测试，不代表线上延迟；更大客户库须复测，此实现每次拼音查询扫描服务端可见名称。
- OpenAPI、结构/语法检查和 diff 检查通过。三档宽度浏览器布局检查不代替微信真机验收。
- 当前仅源码验证；未合并、迁移生产、部署、上传或替换体验版。

可复制验证命令（仓库根目录）：

```sh
node --test frontend/tests/*.test.js
PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend/tests --ignore=backend/tests/integration -q
PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/export_openapi.py --check
python3 frontend/scripts/verify_frontend.py
# 以下脚本自行创建并删除隔离数据库，仅连接本地测试 PostgreSQL：
PGHOST=/tmp PGUSER=postgres backend/.venv/bin/python database/tests/verify_customer_directory_acl_postgres.py
PYTHONPATH=backend/src PGHOST=/tmp PGUSER=postgres backend/.venv/bin/python backend/tests/system/verify_customer_claim_search_scale_postgres.py
```
