# 神码运行与维护

| 服务器 | 内容 | 监听 | 入口 / 数据 |
|---|---|---|---|
| salesbuddy，172.22.9.234，SSH 32222 | 销售 API、管理端、Worker、PostgreSQL | HTTPS 443、28899；API 仅 127.0.0.1:8080；数据库仅本机 5432 | salesbuddy.shenzhoukuntai.com；销售库及上传文件 |
| opsbuddy，172.22.9.233，SSH 12222 | Agent 中台及自有数据库、Redis、向量库、沙箱 | HTTPS 443、18899；存储服务仅容器网络 | ops-salesbuddy.shenzhoukuntai.com；中台账号、Agent、知识库及运行记录 |

表中为本项目安装目标。公网 443 的域名分流由客户网关配置，两个域名解析到同一公网 IP 不能代替转发配置。正式公网入口以现场验收为准。

已通过短时 TCP 监听和外部连接核验现有 NAT：公网 `223.76.131.120:28899` 到销售机 `172.22.9.234:28899`，公网 `:18899` 到中台机 `172.22.9.233:18899`。客户网关若通过公网预留端口回源，应将销售域名接 28899、中台域名接 18899；若内网直连，则分别接两台机器的 443。不要沿用职责互换前的反向对应表。

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

中台首次初始化口令只保存在服务器 `/opt/raccoon-agent/首次登录.txt`。模型供应商和销售 Agent 需使用客户独立账号、发布 ID 与 Key；原环境的 publication.json 不随源码迁移。

## 备份与升级

销售快照脚本 `deployment/backup-sales.sh` 会在维护窗口短暂停止 API/Worker，备份数据库、文件、密钥与版本，再恢复原先运行的服务。快照只保留在 root 可访问的 `/var/backups/shenma-sales/`；含实际数据和秘密，不能放进源码仓库或普通交付包。首次上线前演练备份及恢复，后续频率和保留周期由客户运维确定。

恢复时先校验 SHA256SUMS，在另建的恢复数据库和目录中验证 `pg_restore`、文件和密钥配套，再安排服务切换。不得直接覆盖运行库，也不能仅回退代码而忽略已执行的数据库迁移。中台备份须同时包含它自己的数据库、文件/向量存储和 `.env`、TLS 密钥，并记录镜像与源码版本。

后续定开从本仓库 main 新建 `codex/` 分支，经检查后合并。商汤的新功能按明确提交评估后移植；不自动同步其环境配置、数据或发布绑定。
