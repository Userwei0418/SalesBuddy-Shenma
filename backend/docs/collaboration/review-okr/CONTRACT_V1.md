# SB-REVIEW-20261014 后端契约 V1

状态：首包仅行式目标与指标字典，M1 及三项后继窄修已通过隔离 PostgreSQL/ASGI 验证，待生产配套发布；概览、O/KR、任务对齐与报告接线独立交付。例子是合成数据，不是业务验收。基线 bd8156a。金额始终为十进制字符串（元），计数为整数字符串；业务时区 Asia/Shanghai。未设目标=null，明确预测0="0.00"；目标保存沿用>0，不放宽旧Mini契约。

## 首切片（优先冻结）

- `GET /api/v1/review/metrics`：`{items:[{code,name,unit,decimal_places,definition,source_kind,definition_version,active,version_no,target_kind,goal_selectable}],data_source:"database"}`。source_kind=actual/forecast/manual/category；定义版本与维护版本分开。停用保留历史，停用指标不能新设/改目标。系统指标定义不可由普通维护请求改写，租户可启停；新增自定义指标限manual，不允许自报automatic。
- `POST /api/v1/review/metrics`：`{code:"custom_pipeline",name:"有效沟通",unit:"次",decimal_places:0,definition:"经负责人确认的有效沟通",active:true}`；code须custom_前缀。仅workspace范围target.manage可维护字典。
- `PATCH /api/v1/review/metrics/{code}`：`{active:false,version_no:1}`。旧version为409；不存在404；禁止跨公司共享字典状态。
- `GET /api/v1/review/targets?period_type=quarter&anchor_date=2026-10-14&scope=team&team_id=UUID&limit=50&offset=0`；scope=self/person/team/department，也可不传scope查自己有权看的全部。输出`{items,total,limit,offset,period,scope,editable,data_source}`。items逐行：原目标字段/id/kind/amount_text/version_no等 + `metric`字典对象 + `effective_amount`十进制字符串 + `pending_change`（null或`{request_id,request_type:"batch"|"legacy",proposed_amount,base_version,status:"pending",applicant_name,created_at}`）+ `state:"effective"|"pending_change"|"pending_new"`。待审首次目标也进入同一分页items（id/version_no/effective_amount=null，state=pending_new，row_key稳定）；不依赖旧100条待审预览重建，避免刷新消失。旧接口仍保留原pending数组。目标amount和effective_amount永远生效值，pending不可替换分母。
- `GET /api/v1/review/targets/options`：`{people,teams,departments,data_source:"database"}`；仅返回目标可写与可读范围交集，每项含`id/name/can_manage`，人员另有`account_code`。供新建目标选择使用。
- `POST /api/v1/review/targets`：沿用原单行目标请求：`{scope:"team",team_id:UUID,period_type:"quarter",anchor_date:"2026-10-14",kind:"collection",amount:"1000000.01",version_no:1,reason:"季度计划调整"}`。首次不传version_no；更新必须带GET读到的version_no。成功`{status:"effective"|"pending"|"unchanged",items:[原生效目标],target:原生效目标或null,request:待审单或null}`；金额递归序列化为字符串。写入仍调用唯一`security.save_target_batch`；scope/期别/对象/kind身份不变，旧六kind及旧API保持兼容，新增collection_ppl、forecast_recognized、自定义manual kind复用原表和审批。任何客户端role/租户字段均不接受。
- 原审批不换路径：`POST /api/v1/console/target-batches/{id}/decision`，body `{decision:"approved"|"rejected",reason:"..."}`；原历史`GET /api/v1/console/targets/{id}/history`。保持原动作权限/记录范围。
- 审批读取：`GET /api/v1/console/target-batches` 的每个 `items` 子项，以及 `GET /api/v1/console/target-requests` 的每行，额外返回 `previous_amount_text` / `proposed_amount_text`。文本在数据库中从原 numeric 转出，未设置仍为 null；原数值字段保留。审批页面优先精确文本，并用完整指标字典的名称、单位与精度展示新增指标；旧 Mini 的 `legacy_only` 待审投影不追加这两个字段。
- 错误：422参数错误；403无功能或对象权限；404不存在/不可见；409旧版本、重复身份、待审冲突、审批冲突。所有写支持现有Idempotency-Key；行内取消不发送写入。
- 唯一键：workspace_id + period_type + period_start + scope_type + user_ref_id + team_id + department_code + kind；NULL参与唯一性。不同O/KR不能另存同一目标值。

## 首包边界修复

- 新 Review 三条写路径在读取幂等回执前重新校验当前精确授权范围。权限仍有效时允许原请求重放，不重复校验已过期业务版本；撤销范围后返回403，即使用户仍有其他对象的管理权限，也不能返回原回执。
- 自定义指标按字典 `decimal_places` 校验，名称以 `_count` 结尾不额外推断为整数；原六种指标的精度、目标归属和日期规则保持。
- 旧 `GET /api/v1/targets` 的目标、待审申请、批次和最近历史均先按原六种 kind 过滤，再计数和分页。混合批次只投影旧种类，原 console 审批列表仍返回完整批次；新 Review 列表保留全部指标。新指标大量增长不会挤掉旧小程序首屏的回款/确收目标。
- 验证：新增2项模型验证单测；独立 PostgreSQL + 受限 API 角色执行4组、24次HTTP，覆盖同身份权限撤销、字典精度、110种新指标及110条新待审/混合批次。迁移V240与ACL字节未改，沿用已固定迁移证据。此处不代表生产登录、页面或线上业务验收。

## 指标身份（Data已确认部分）

既有collection/recognized/acv/opportunity_count/visit_count/demo_count保留。新增daily_routine仅任务类别，无财务目标。新增三卡forecast_collection（target_kind=collection）、collection_ppl（独立同名目标）、forecast_recognized（target_kind=forecast_recognized，含税预测目标独立于旧不含税实际recognized目标）。预测与实际分开，forecast_*不是新的金额副本。回款预测为季度collection_amount + collection_confidence=high；PPL为概率>=30且非lost、直接SUM；预测确收为季度recognized_amount + 概率>=70且非lost，用户已确认首版预测与目标均含税；旧recognized不自动改税基或复用作新预测分母。

## 下一切片契约草案（尚未冻结，不作为已可用接口）

`/api/v1/review/objectives`、`/api/v1/review/key-results`：O/KR UUID + version_no；owner_user_id唯一，collaborator_user_ids去重，上级parent_id做租户/权限/周期/循环校验。KR以metric_code及target_id引用生效目标，不另写目标金额；非财务手工进展有值、证据和确认人。上下级对齐不自动分摊或汇总金额。

任务沿原创建/派发/完成接口追加可选`alignment`对象：`{metric_codes:["daily_routine"],key_result_ids:[],expected_contribution:null}`；新Web显式传，旧Mini不传保持原请求有效，不隐式归到财务指标。KR带出指标并去重；任务全局计数按task_id去重。具体贡献结构、完成证据及管理权限在M2冻结前另发版本。

概览另发冻结契约：共用授权scope、metric_code/definition_version/unit/period/timezone/as_of/effective_target和source_refs；无目标no_target，无昨日基线no_baseline，缺源missing，明确零value=0；税/口径未确认返回unavailable及reason，不返回猜测金额或达成率。没有数据的demo样例不能作为运行验收。
