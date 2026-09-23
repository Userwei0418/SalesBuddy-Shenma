# 神码 Agent 配置与发布

## 当前状态

客户中台已创建并发布 12 个独立 Agent：经营问答、作战地图复盘、商机草稿、个人风险、拜访录入、拜访质检、今日待办、经营报告、客户经营建议、商机建议、拜访建议、销售能力复盘。

全部通过 CLI 配置预检、发布后提示词 SHA256 回读、应用鉴权及 `/v1/parameters` 检查。已有客户经营建议实例保持原发布配置，其余为初次发布。销售业务目前仍只对部署管理员限时启用客户经营建议；发布与参数接口通过不代表各项业务输出已验收。

## 从维护代码生成配置

在仓库根目录运行：

```bash
python3 deployment/build-agent-specs.py --output ../output/shenma-agent-specs
```

输出包含 12 份 CLI spec 和来源清单，不包含 API Key、应用 ID 或公司绑定。已有输出目录会被拒绝，避免混入旧配置。可通过 `--plugin`、`--provider`、`--model` 指定客户中台已经安装并配置的模型供应商。

11 项能力使用 `backend/agent_platform/` 中维护的提示词；销售能力复盘直接提取后端 `CompetencyReviewHandler` 的 `coaching_contract`，框架与本次业务指导由后端通过信封传入。脚本发现来源缺失或不唯一时停止，不自行补写业务契约。

新 Agent 先执行 CLI `create --dry-run`，再创建、回读 revision、执行 `publish --dry-run`，最后发布。已有 Agent 必须先 get 读取当前 revision，审核差异后 configure；不要重复创建或直接覆盖已验收版本。生成配置不会自动修改客户实例。

## 实例与凭据

- 发布来源及快照记录：`docs/evidence/shenma-agent-catalog-publications.json`。
- 参数检查：`docs/evidence/shenma-agent-parameters-checks.json`。
- 客户机器上的应用运行 Key：中台 `/var/lib/shenma-provision/agent-runtime-bindings.json`，root 0600，不入库。
- 模型连接可在中台配置页面替换，业务应用 Key 与模型供应商 Key 是不同凭据。

新能力接入销售后端时，先用本能力真实输入结构做合成正向、空资料与越权内容样例，运行后端领域校验，再配置对应独立绑定和试用范围。保留已有受控后端写入，不给 Agent 数据库直连或任意业务写入工具。正式业务验收还必须覆盖登录后的任务执行、持久化和权限边界，不能用参数接口或通用 JSON 测试代替。
