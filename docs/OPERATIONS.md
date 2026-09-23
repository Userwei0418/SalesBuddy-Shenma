# 神码运行与维护

| 服务器 | 内容 | 监听 | 入口 / 数据 |
|---|---|---|---|
| salesbuddy，172.22.9.234，SSH 32222 | 销售 API、管理端、Worker、PostgreSQL | HTTPS 443、28899；API 仅 127.0.0.1:8080；数据库仅本机 5432 | salesbuddy.shenzhoukuntai.com；销售库及上传文件 |
| opsbuddy，172.22.9.233，SSH 12222 | Agent 中台及自有数据库、Redis、向量库、沙箱 | 主机 HTTPS 18899 → 容器 443；存储服务仅容器网络 | ops-salesbuddy.shenzhoukuntai.com:18899；中台账号、Agent、知识库及运行记录 |

用户已确认使用带端口的公网地址：销售 `https://salesbuddy.shenzhoukuntai.com:28899`，中台 `https://ops-salesbuddy.shenzhoukuntai.com:18899`。正式公网入口以现场验收为准。

证书采用 Let's Encrypt DNS-01，服务器上的 `certbot.timer` 每天自动检查两次，成功续期后重载 HTTPS。首次签发、对方运维分工与验收步骤见 [证书与自动续期](TLS.md)。当前已启用定时器，DNS 验证客户端待对方运维配置，正式证书未签发。

已通过短时 TCP 监听和外部连接核验现有 NAT：公网 `223.76.131.120:28899` 到销售机 `172.22.9.234:28899`，公网 `:18899` 到中台机 `172.22.9.233:18899`。当前直接使用这些映射，无需额外 443 分流。不要沿用职责互换前的反向对应表。

## 销售主机

代码发行目录 `/opt/shenma-sales/releases/<提交SHA>/`，当前版本软链接 `/opt/shenma-sales/current`。配置 `/etc/shenma-sales/runtime.env`；加密钥匙 `/etc/shenma-sales/keyring.json`；文件 `/var/lib/sales-backend`。

```bash
sudo systemctl status shenma-api shenma-worker
curl -fsS http://127.0.0.1:8080/api/v1/health/version
curl -sS http://127.0.0.1:8080/api/v1/health/ready
sudo journalctl -u shenma-api -u shenma-worker --since '10 minutes ago'
```

`health/version` 返回加载版本，不代表数据库迁移或模型调用完成。未配置客户模型时 `health/ready` 的模型配置项为 false，接口会返回 503；不能把这一项改成成功来代替模型接入。

## 中台主机

原始安装包 `/opt/shenma-installer-source/Raccoon-Agent-Installer-20260923`；运行实例 `/opt/raccoon-agent`；容器配置和数据在其 `runtime/` 下。不要执行 `docker compose down -v` 或删除卷目录。

```bash
cd /opt/raccoon-agent/runtime
sudo docker compose -f compose.json ps
sudo python3 /opt/raccoon-agent/healthcheck.py --wait 30
```

中台首次初始化口令只保存在服务器 `/opt/raccoon-agent/首次登录.txt`。本次已创建管理员 `admin@example.com`，随机密码记录在 `/var/lib/shenma-provision/agent-admin.json`（root 0600）；正式交接后由管理员改密。模型供应商和销售 Agent 需使用客户独立账号、发布 ID 与 Key；原环境的 publication.json 不随源码迁移。

## 备份与升级

销售快照脚本 `deployment/backup-sales.sh` 会在维护窗口短暂停止 API/Worker，备份数据库、文件、密钥与版本，再恢复原先运行的服务。快照只保留在 root 可访问的 `/var/backups/shenma-sales/`；含实际数据和秘密，不能放进源码仓库或普通交付包。首次上线前演练备份及恢复，后续频率和保留周期由客户运维确定。

销售快照另含 `provision-secrets.tar.gz`，保存当前管理员交接凭据、Agent 接入密钥映射和部署记录；中台实例归档也包含管理员、Agent 接入密钥映射及发布清单。恢复时与数据库、运行配置和加密密钥配套使用，不能沿用首次初始化时的旧密码或旧映射。归档中不包含操作员的 CLI 登录会话，恢复后重新登录 CLI。

2026-09-24 新销售快照：`/var/backups/shenma-sales/20260923T185704Z`（目录时间为 UTC）。已恢复到独立临时数据库，103 张表恢复成功，102 张表行数与重新启动后的运行库一致；`ops.system_event` 为快照 40 条、运行库 42 条，单独记录该时间差异，未认定所有表完全一致。接入映射、当前管理员凭据及中台 CA 文件归档存在且校验通过，临时库已删除。详细脱敏记录位于快照内 `restore-check.json`。

同轮中台快照：`/var/backups/shenma-agent/20260923T185654Z`。已覆盖模型配置与 12 个 Agent 发布后的实例，两套数据库均完成临时恢复与逐表行数核对，随后恢复服务并通过健康检查。完整记录为快照内 `verification.json`，校验清单为 `SHA256SUMS`；整机替换恢复尚未演练。

恢复时先校验 SHA256SUMS，在另建的恢复数据库和目录中验证 `pg_restore`、文件和密钥配套，再安排服务切换。不得直接覆盖运行库，也不能仅回退代码而忽略已执行的数据库迁移。中台可运行 `sudo python3 deployment/backup-agent.py`：会暂停中台服务，备份两套数据库、实例配置、文件/向量卷和 TLS/登录凭据，验证临时数据库恢复并恢复原服务。请在维护窗口执行。快照在 `/var/backups/shenma-agent/`，含秘密，只能由客户运维保管；物理卷归档可读性与逻辑数据库恢复分别登记。

后续定开从本仓库 main 新建 `codex/` 分支，经检查后合并。商汤的新功能按明确提交评估后移植；不自动同步其环境配置、数据或发布绑定。

## 源码交付

从干净、已提交并通过检查的代码生成完整源码包：

```bash
python3 scripts/package_source.py --output-dir ../output/shenma-delivery
```

包内包含前端、销售后端、数据库结构/迁移、中台现有源码、部署脚本和说明；不包含数据库业务数据、运行配置、账号口令或私钥。根目录 `REVISION` 和 `DELIVERY.json` 标记源码版本，`SHA256SUMS` 校验全部文件。中台完整运行安装包从公开 Release 单独下载；完整 Web 开发源码尚未提供。AppID 未配置时，包会明确记录 pending，不能视为可上传的小程序体验版。

只交付小程序源码时，使用 `frontend/scripts/package_frontend.py`；传入实测后端完整版本和数据库版本，详情见 `frontend/README.md`。源码包和服务器数据备份分别保管，不能把备份加入源码包。
