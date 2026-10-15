# 公司后台正式跟进查询 API

三个接口均要求正式密码登录、`access.console` 和 `visit.read`，并复用现有 PostgreSQL 行级权限。管理员名称不构成越权条件，`all` 代表该账号的授权范围。未确认、撤回或软删除记录不进入本模块。每次读取使用只读 `repeatable_read` 事务，分页和统计共享一致快照。

## 列表与统计

`GET /api/v1/console/visits`

| 参数 | 约束与语义 |
| --- | --- |
| `scope` | `all`（默认）或 `mine`；mine 限实际跟进人为当前账号，不含仅作为创建人或管理人的记录 |
| `q` | 最长 100 字符的字面子串；搜索客户、伙伴、当前/历史实际跟进人、正文、下一步及可见关联商机名称 |
| `recorder_id` | 实际跟进人 UUID，支持筛选已有可见记录中的停用账号 |
| `date_basis` | `interaction`（默认实际沟通时间）或 `created`（系统创建时间） |
| `date_from` / `date_to` | 北京日期闭区间；缺实际时间的记录不会使用创建时间冒充，时间区间筛选时自然排除 |
| `score_min` / `score_max` | 0–100；以保存的 `follow_up_score` 筛选，空评分不当作零分，提供任一边界时排除空评分 |
| `page_size` / `offset` | 页大小 1–100，默认 50；offset 0–100000，默认 0 |

返回 `{items, summary, has_more, next_offset, as_of}`。排序为选定时间倒序、系统创建时间倒序和 ID 倒序，空选定时间最后。

`summary` 对完整筛选范围统计，独立于分页：

- `total`：正式记录数；同一条记录关联多个商机仍计一次。
- `subject_count`：可见关联客户和伙伴按类型及 ID 去重；包含可见关联商机的客户，同一客户不重复；没有身份 ID 的历史名称不猜为新客户。
- `scored_count`：保存评分非空的记录数，0 分仍计入。

`items` 明确提供主体、客户/伙伴、主商机及多商机关联、当前账号匹配的跟进人及活动状态、不可变的原跟进人姓名、管理人、创建人、实际沟通时间、人工填写日期、系统创建时间、沟通方式、对接人、首访标识、保存评分与脱敏质检摘要。正文和下一步各最多 600 字符，`is_summary=true`。

`linked_opportunities` 每项为 `{id,name,customer_id,customer_name,can_open}`。主商机与桥表关联均纳入并去重，但商机元数据仍受商机 RLS 约束。拥有跟进读取权限不自动获得商机详情权限；原有主商机关联额外权限限制保持不变。`can_open_customer` / `can_open_opportunity` 用于决定是否显示可点击档案链接。

`visit_date` 为实际跟进的北京日期；`recorded_on` 是人工填写日期；`created_at` 是系统创建时间；`created_date` 仅用于兼容旧表单（优先人工填写日期，否则系统创建日期），不可作为系统录入时间。

## 可见记录的跟进人目录

`GET /api/v1/console/visits/recorders`

支持上述全部筛选、分页参数，另提供 `recorder_q` 搜跟进人姓名/账号。`q` 始终筛记录，`recorder_q` 只筛目录候选项。

返回 `{items:[{id,name,account_code,active,record_count}], unmapped_count, has_more, next_offset, as_of}`。不要求额外组织目录权限；目录仅来自当前筛选范围内可见记录，不返回公司全部人员。历史停用人员保留；无法匹配账号的历史记录保留原姓名，可用列表 `q` 查询，计入 `unmapped_count`。

## 单条详情与可执行操作

`GET /api/v1/console/visits/{visit_id}`

记录不可见或非正式状态返回 404。详情返回完整正文/下一步、拜访目标、方式地点、对接人/职务、预期、时长、客户类型、首访四项、协同人/FDE参与人，以及 `version_no`。质检仅公开 `follow_up_score`、`grade`、`next_action_passed`；不返回公司策略快照、原始识别结果、存储路径或原始 JSON 数据袋。

- `partner_name` 用于展示关联伙伴当前名称；辅助补充表单须从 `partner_name_snapshot` 初始化原始文本，避免一次保存无意用当前目录名称覆盖历史快照。
- `can_supplement` / `supplement_fields` / `supplement_unavailable_reason` 表达精确操作能力。实际跟进人本人且具备现有 `visit.supplement` 对象权限与写入权限才可补充；当前未关联客户的历史记录不提供补充按钮。
- 首版允许的辅助字段：伙伴文本、对接人及职务、实际跟进时间、方式、地点、人工填写日期；已为首访的记录可补首访四项。不提供正文、下一步、跟进归属、首访类型、协同人整体重置或删除入口。
- 保存继续调用原 `PATCH /api/v1/visits/{id}` 并传 `version_no`。原接口再次鉴权，过期版本和禁止字段沿用 422；不能根据一个较早的 `can_supplement` 绕过实时检查。
- `original_materials` 为 `{id,filename,file_size,can_download,download_url}`；只通过数据库 `source_import_id` 与同租户导入记录关联获得。URL 仅在具有原件下载权限时给出，形式为原有 `/api/v1/visit-imports/{id}/original`，下载接口再次检查实时身份/对象权限。`original_material_status` 为 `none`、`unavailable` 或 `available`，其中 available 表示存在可授权入口，不代表已读取文件或验证存储介质。

本次没有数据库迁移、扩权、AI重跑、外部消息发送。旧客户内历史查询、小程序、原详情和原补充接口契约保持不变。
