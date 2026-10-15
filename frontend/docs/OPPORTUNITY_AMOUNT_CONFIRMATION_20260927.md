# 商机金额单位与大额核对（2026-09-27）

## 范围与行为

本轮以前端 `98d53b7` 为源码基准，在唯一仓库 `frontend/` 内修复小程序商机录入、编辑和拜访归档的金额防误填。未部署或上传小程序；不能把本地测试通过写成正式上线。

- 共享商机表单的 ACV 输入单位仍是**万元**，最多六位小数，精确到分。输入框内固定显示单位，并实时显示完整元金额及量级，例如 `100000 万元 → 1,000,000,000 元（10 亿元）`。
- 默认提醒值由后端规则提供，为 **1000 万元（含）**；各公司可配置。达到提醒值不是禁止保存，用户确认金额后可以保存。
- 新增商机或实际修改金额时，每次保存前读取当前公司的规则。规则读取失败、缺字段或格式错误时保留输入，显示“金额核对规则加载失败，表单已保留，请重试保存”；不静默使用本地默认阈值。
- 历史金额未改变时不弹金额提醒，也不依赖规则读取。历史未知金额 `null` 不当成零或已确认金额。
- 独立商机页面在正式保存前弹金额核对窗。拜访的 AI 质检不弹金额窗；最终归档将金额核对合并到原有归档确认窗，确认后才携带本次金额确认值。
- 取消或配置失败都不写入正式数据。确认值只在本次正式请求中生成，不写入 AI 结果、草稿或已质检内容；换账号、租户、客户、商机、金额或记录版本后，旧确认不能再提交。
- 万元和元转换用十进制移位，兼容 API／数据库的分精度。超过六位万元小数明确拒绝，不静默舍入；历史 `12345.67 元` 回填 `1.234567 万元` 后仅改其它字段可以保存，原金额不变。超过 JavaScript 安全整数范围的元金额明确阻止，避免显示一个数而提交另一个；这是小程序精确表示限制，不是新增业务金额上限。不新增季度预测的大额提醒规则。

## 接口契约

`GET /api/v1/company-rules/presentation` 增加：

```json
{
  "opportunity_amount": {
    "id": null,
    "version": null,
    "definition": {
      "schema_version": 1,
      "warning_threshold_wan": 1000
    },
    "source": "baseline"
  }
}
```

前端要求 `schema_version=1`、`warning_threshold_wan` 为正整数；`id/version` 可以为空，不能据此判配置失败。`apiClient.getCompanyPresentation()` 每次发起新的 GET，仍使用现有会话切换、认证刷新保护，不共享旧的在途规则读取。

正式商机保存 `POST /customers/{id}/opportunities` 和拜访 `POST /visits` 中的 `fields._opportunity_mutation`，在需要提醒且用户确认后增加：

```json
{"amount": 10000000, "amount_confirmed_value": 10000000}
```

两项均为**元**，确认值必须等于最终写入金额。后端复核新建/实际金额变化与当前公司阈值，不满足时返回 422 中文金额核对提示。该字段不能当成一般的 `true/false` 豁免。

超时后的幂等重试仅在 `POST /customers/{id}/opportunities` 顶层和 `POST /visits` 的 `fields._opportunity_mutation` 中从请求指纹排除 `amount_confirmed_value`，后端对应操作同步处理。公司阈值调整导致重试新增/去掉确认值时，仍复用未确定提交的 key；真实金额、商机版本、客户、质检记录与账号保持参与指纹。正式请求仍完整发送本次确认值，不改变后端校验。

拜访质检匹配时仅忽略 `amount_confirmed_value`，原有 `amount` 等业务字段仍严格匹配；最终确认无需重跑质检，原有商机 `version_no` 冲突控制不变。

## AI 预填修复

`visit-confirm` 原来调用 `visitFlow` 中重复的旧金额转换，按数值大小推断单位，导致 `amount:1200` 被误当作 `1200 万元`。现统一到 `visitOpportunityAI`，`visitFlow` 仅兼容导出同一适配器，不再维护第二份转换。

- 标准 API `amount` 始终为元，不按数值大小猜单位：`1200 → 0.12 万元 → 保存 1200 元`。
- 显式 `amount_wan`、`acv_wan`、`1.2万元` 等保持原有万元语义；显式 `1200元` 按元转换。
- `action:none` 不创建商机关联；AI 内容不能携带金额确认。

## 验证与上线边界

新增行为回归覆盖阈值下方/等值、公司不同阈值、配置缺失/失败、取消/重试、旧确认剥离、历史金额不变、重复点击、账号和记录变化、精度边界，以及真实 `visit-confirm` 页面与共享组件的 AI 预填→质检→归档→请求金额全链。

测试命令：

```bash
node --test frontend/tests/opportunity-amount-confirmation.test.js frontend/tests/auth-session-races.test.js frontend/tests/opportunity-names.test.js frontend/tests/request-idempotency.test.js
node --test frontend/tests/*.test.js
```

本轮结果：定向回归 **78/78**、前端全量 **1211/1211** 通过，`git diff --check -- frontend` 通过。原始输出保存在本机 `/tmp/salesbuddy-opportunity-amount-focused.log` 和 `/tmp/salesbuddy-opportunity-amount-frontend.log`；临时日志不是持久交付或线上证据。本页测试为本地模拟接口与页面行为，不代表真机、线上 Agent 或生产写入验收。

需后端与 V187 规则配套：先部署支持金额规则和确认值的后端，再发布小程序。新前端连接旧后端时会明确提示规则不可用，不会猜阈值继续提交。旧小程序连接新后端时，大额新建/修改会收到金额核对提示但旧版无确认入口，因此后端与小程序应安排同一发布窗口。已存在的异常金额不自动修正，需要业务人员核对后修改。
