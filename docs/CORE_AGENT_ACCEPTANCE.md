# 核心 Agent 合成业务验证

在销售服务器以 `shenma-sales` 实际服务用户调用客户中台，使用已部署后端的 FdeClient、事件流解析器和领域校验函数；不连接数据库、不创建客户或正式业务记录。

| 样例 | 验证内容 | 结果 |
|---|---|---|
| 明确新建商机 | 金额 80,000 元、概率 50%、预计日期 2026-12-20；不冒用已有商机 ID | 通过 |
| 不关联商机 | 只维护日常关系时返回 action=none | 通过 |
| 再次拜访结构化 | 18 项字段严格校验、首访标记、联系人、正文与下一步计划；采用后端可信字段 | 通过 |
| 后续质检 | 接收结构化快照，fields 和 summary 原样保留，分数和下一步审核满足当前契约 | 通过 |

证据：`docs/evidence/core-agent-business-20260924-v2.json`。四次调用分别约 2.3、1.5、2.6、3.2 秒。提示词未在本轮修改。

管理员经正常 HTTP 登录提交后台测试后，三项能力均由真实 Worker 调用成功，没有使用备用模型；耗时分别为商机草稿 1,827 毫秒、结构化 1,536 毫秒、质检 2,332 毫秒。回执见 `docs/evidence/core-agent-worker-probes.json`。这类通用探针仍标记 business_acceptance=false，业务样例校验以上述独立证据为准。

首轮拜访测试误用了历史一步式结果校验，因没有 quality_review 被拒绝；当前产品要求结构化与质检分离。修正测试信封的 visit_stage，并按结构化输出接续质检后通过。首轮记录保留在客户机 `/var/lib/shenma-provision/core-agent-business-20260924-v1.json`，不能把测试入口修正记作产品缺陷修复。

可重跑脚本为 `deployment/verify-agent-business.py`，只允许 root 在 salesbuddy 上启动，读取 root 受限应用凭据后降权到服务用户运行；输出路径必须不存在。脚本仅包含合成资料，不提交 CRM 写入。

```bash
sudo /opt/shenma-sales/current/backend/.venv/bin/python deployment/verify-agent-business.py \
  --output /var/lib/shenma-provision/core-agent-business-new-run.json
```

已将商机草稿、拜访结构化和拜访质检加入部署管理员的 48 小时试用范围，其他用户未启用。合成业务结果校验与 Worker 连通性回执分别记录；正式业务闭环仍需覆盖真实登录操作、用户确认、任务持久化与权限边界。测试通过不等于小程序真机或客户业务验收完成。
