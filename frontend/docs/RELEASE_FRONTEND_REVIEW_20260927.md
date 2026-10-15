# 前端上线准备复查（2026-09-27）

## 版本与边界

- 唯一源码为 Raccoon SalesBuddy 仓库；在仓库根目录执行本文命令，本地生成目录不作为源码。
- 复查分支：`codex/local-browser-acceptance`，HEAD `7c6e0b9a3810883c751ffdf47964a48a38ac1f0c`，包含本轮未提交增量；保留各并行任务的改动。
- 生产对照基线由发布负责人核验为 `5d55f49` / V152；本记录不把本地代码、定向测试或历史体验版 1.0.9 当成新版本已发布。
- 本次仅修改 `frontend/`，不修改接口、数据库、权限授予规则、任务状态机、每人一条任务或任务查看范围。

## 审查与修复

| 范围 | 结论 |
| --- | --- |
| SaaS 登录、公司码、记住账号、请求会话身份 | 核对 `app.js`、登录页、`rememberedLogin`、`apiClient`、`requestIdentity`；新公司码、权限版本与会话更新需要配套服务端，不能靠旧账号缓存推断公司。保留原有唯一账号的服务端兼容规则。 |
| 地图、人员目录、本人/团队范围 | 核对年度活跃口径、缺坐标补评、稳定人员 ID、授权目录与 TabBar 恢复。目录不完整时明确失败，不从客户或商机数据反推候选人员。 |
| 任务转交、取消、负责人及事件展示 | 核对原有 `task-detail`、取消弹层和列表、转交人员目录、任务展示工具的新增保护；后端当前负责人及权限仍是依据。 |
| 创建待办页 | 修复隐藏后的确认回调仍发 POST、成功返回计时器影响其他页面、旧身份失败回调影响新提交。复用 `pageWriteContext`，已发请求保留锁，结果未知仍由用户重试原批次，不自动重试 POST。 |
| 创建待办的 AI 建议与录音 | 绑定公司、账号、角色、权限版本及登录会话；旧建议/转写不能覆盖新身份数据。录音启动至停止回调固定归属，切身份时停止旧录音，在旧录音结算前不启动新录音。离页后迟到授权不开始新录音，既有录音后台结算不因此改变。 |
| 客户创建动作检查 | `customer-create` 的真实方法为 `submitCustomer`，`customer-assign-confirm` 为 `confirmArchive`；两者均补上已有 `customer.create` 的点击时检查，不新增或扩大权限。 |
| “我的”团队选择 | 团队分支补上与人员分支一致的打开状态、会话身份及目录状态检查；关闭或换身份后的旧选择不能触发新的范围查询。 |
| 已完成的页面写入/读取保护 | 复查 `pageWriteContext` 与前轮客户、商机、拜访及详情页保护；原有专项说明见 `PAGE_WRITE_ROBUSTNESS_20260927.md`、`PAGE_ASYNC_ROBUSTNESS_20260927.md`。 |

成功任务返回使用一次性回执：当前页可见时才返回；隐藏期间成功则在回到该页后返回。卸载、切身份、`onShow` 与旧计时器竞争均不能多返回一层。

## 发布顺序与兼容要求

必须先发布并验证配套后端，再上传/开放新小程序。新前端不支持直接配 V152：

1. 地图使用当年 1 月 1 日至当前日期的活跃口径及缺坐标补评接口，不能把旧半年口径当成年口径显示。
2. 地图、看板、“我的”、FDE 与任务人员目录依赖 `teams`、`defaults.team_id`、成员 `team_ids`；新商机负责人使用稳定 ID 及后端选项。
3. 任务转交候选接口与取消能力依赖后端当前权限、状态、版本字段。
4. SaaS 公司码、角色并集及账号覆盖权限由后端确认，前端不自行猜测租户或补权限。

发布负责人还须验证“旧小程序 + 新后端”的兼容窗口，以及对应的线上版本/数据库状态。本轮静态与 VM 回归不能代替该运行检查，也未执行正式发布或微信真机录音测试。

## 定向验证

以下命令通过：300 条，失败 0、跳过 0；包含隐藏/返回、重复确认、账号/租户/权限版本切换、旧写 finally、未知结果重试、AI/录音晚到、导航计时器、目录缺字段、任务转交取消及地图恢复回归。

```bash
node --test \
  frontend/tests/task-recipient-picker.test.js \
  frontend/tests/configurable-access.test.js \
  frontend/tests/task-type-links.test.js \
  frontend/tests/task-recipient-directory.test.js \
  frontend/tests/person-picker-pages.test.js \
  frontend/tests/person-picker.test.js \
  frontend/tests/auth-session-races.test.js \
  frontend/tests/native-login.test.js \
  frontend/tests/request-idempotency.test.js \
  frontend/tests/task-detail-robustness.test.js \
  frontend/tests/task-reassignment-picker.test.js \
  frontend/tests/task-cancel-dialog.test.js \
  frontend/tests/task-cancellation-detail.test.js \
  frontend/tests/task-cancellation-list.test.js \
  frontend/tests/task-notification.test.js \
  frontend/tests/customer-map.test.js \
  frontend/tests/map-tabbar-recovery.test.js \
  frontend/tests/business-write-robustness.test.js \
  frontend/tests/page-write-context.test.js \
  frontend/tests/detail-page-lifecycle.test.js
git diff --check -- frontend
```

发布负责人首次全量发现一条遗留生命周期回归：建议读取的测试以 `isUnloading` 模拟离页，而新上下文只检查 `unloaded`。页面真实 `onUnload` 同时设置两者；仍保留原有 `isUnloading` 检查，未删除断言，并在同一用例补上真实 `onUnload` 后的迟到建议返回。修复仅限该页建议读取条件。

随后执行 `node --test frontend/tests/*.test.js`：1172 条全部通过，失败 0、跳过 0；输出保存在 `/tmp/salesbuddy-readiness-frontend-retest.log`。Linux CI、配套本地环境检查由发布负责人统一执行和记录。
