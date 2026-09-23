# 神码首次安装指引

本页用于空环境首次安装。现有神码服务器的实施进度见 [部署记录](DEPLOYMENT.md)；已有数据库或实例时先按该记录续接，不重复初始化。

| 主机 | 部署内容 | HTTPS 监听 | 公网预留端口 |
|---|---|---|---|
| salesbuddy，172.22.9.234，SSH 32222 | API、管理端、Worker、销售 PostgreSQL、上传文件 | 443、28899 | 28899 |
| opsbuddy，172.22.9.233，SSH 12222 | Agent 中台及其数据库、缓存、向量库和沙箱 | 443、18899 | 18899 |

两机均为 Ubuntu 22.04 amd64。以下安装命令使用 root；实际磁盘空间、已运行服务和目标主机必须先核对。源码包不含业务数据；初次安装只创建结构、系统规则和最小管理账号。

## 1. 准备文件与依赖

下载本私有仓库的完整源码包及 `.sha256`，在同一目录校验外层哈希，解压后再执行包内 `sha256sum -c SHA256SUMS`。根目录 `REVISION` 是源码版本；`DELIVERY.json` 标记 AppID 和中台 Web 源码状态。

销售机需要 PostgreSQL **16**、Nginx、ffmpeg、curl、CA 证书及 uv。按 [PostgreSQL 官方 Ubuntu 指南](https://www.postgresql.org/download/linux/ubuntu/)启用 PGDG 仓库后，安装命令为：

```bash
apt-get update
apt-get install -y --no-install-recommends postgresql-16 postgresql-client-16 nginx ffmpeg curl ca-certificates
```

按照 [uv 官方安装指南](https://docs.astral.sh/uv/getting-started/installation/)安装 uv，确保 root 的 `command -v uv` 有输出。本次实际使用 uv 0.12.5；销售安装器会准备独立 Python 3.12 和锁定依赖。Python/依赖下载受限时，先通过可信渠道准备相同版本和锁定依赖，再继续安装，不跳过锁文件。

中台机本次使用 Ubuntu 已签名仓库提供的 Docker/Compose：

```bash
apt-get update
apt-get install -y --no-install-recommends docker.io docker-compose-v2 docker-buildx python3 openssl curl ca-certificates
systemctl enable --now docker
docker compose version
docker buildx version
```

## 2. 安装销售系统

将完整源码放到 `/opt/shenma-sales/releases/源码提交SHA/`，其中目录名取自根目录 `REVISION`，目录及其父目录需允许服务账号读取和进入。进入该源码根目录后执行：

```bash
sha256sum -c SHA256SUMS
bash deployment/install-sales.sh
systemctl is-active shenma-api shenma-worker
curl -fsS http://127.0.0.1:8080/api/v1/health/version
python3_bin=/opt/shenma-sales/current/backend/.venv/bin/python
"$python3_bin" deployment/verify-live-sales.py
```

安装器发现已有 `shenma_sales` 数据库或 `current` 目录会停止；保留现场，检查已完成阶段后续接。不要删除数据库来规避检查。管理员 `CUSTOMERADMIN` 的初始口令仅保存在 `/var/lib/shenma-provision/initial-admin.json`（root 0600），首次登录要求改密；改密后不要再用初始化验收脚本尝试旧密码。

## 3. 配置销售 HTTPS

本项目正式证书使用 Let's Encrypt；自动申请、续期定时器和重载配置见 [证书与自动续期](TLS.md)。下列路径也是自动续期后写入的位置。

准备匹配 `salesbuddy.shenzhoukuntai.com` 的完整证书链和私钥，将下例 `/path/to/` 替换成实际安全交付路径：

```bash
install -d -m 700 /etc/shenma-sales/tls
install -m 644 /path/to/fullchain.pem /etc/shenma-sales/tls/server.crt
install -m 600 /path/to/privkey.pem /etc/shenma-sales/tls/server.key
install -m 644 deployment/sales-nginx.conf /etc/nginx/sites-available/shenma-sales
ln -s /etc/nginx/sites-available/shenma-sales /etc/nginx/sites-enabled/shenma-sales
nginx -t
systemctl reload nginx
```

上述链接已存在时保留并核对目标，不用重复创建。临时自签证书可用于指定 CA 的本机验收，不能算浏览器/小程序的可信 HTTPS 已完成。

## 4. 安装 Agent 中台

从 [中台原始发行包](https://github.com/Userwei0418/SalesBuddy-Shenma/releases/tag/agent-platform-20260923)下载完整安装包和校验文件，校验后解压到 `/opt/shenma-installer-source/Raccoon-Agent-Installer-20260923`。源码仓库中的 `agent-platform/` 不包含 127MB Web 编译包，不能直接替代完整发行包。

本次采用离线镜像安装：12 个官方固定摘要镜像及 `transfer-manifest.json` 放在 `/home/opsbuddy/shenma-images/`。清单和镜像必须成套使用；安装器校验全部压缩包后才会导入，不自动换镜像源。

在本项目源码根目录运行：

```bash
python3 deployment/install-agent-offline.py \
  --bundle /opt/shenma-installer-source/Raccoon-Agent-Installer-20260923 \
  --images /home/opsbuddy/shenma-images \
  --public-url https://ops-salesbuddy.shenzhoukuntai.com
python3 /opt/raccoon-agent/healthcheck.py --wait 300
```

首次失败后先检查日志和现有实例；确认为同一实例、同一参数续接时加 `--resume`。不要删除 `runtime/volumes` 或执行 `down -v`。安装器保留原包，并用 `offline-provenance.json` 记录客户运行配置及镜像来源。

初次 HTTPS 使用实例独立生成的临时证书。将正式中台证书和私钥分别替换到 `/opt/raccoon-agent/runtime/tls/server.crt`、`server.key`（私钥 root 0600）后，在 `runtime/` 内执行 `docker compose -f compose.json exec nginx nginx -t`，通过后执行 `docker compose -f compose.json exec nginx nginx -s reload`。

初始化口令只保存在 `/opt/raccoon-agent/首次登录.txt`。通过页面创建客户独立管理员和工作空间，配置客户模型，再逐项导入/创建 `backend/agent_platform/` 的销售 Agent 提示词。使用新实例的 Agent ID、发布版本和 Key；原环境的绑定和 Key 不迁入。

## 5. 网关、小程序与验收

公网两个域名均需提供可信的标准 HTTPS 443 入口。若网关通过预留公网端口回源，销售域名接 `223.76.131.120:28899`，中台域名接 `223.76.131.120:18899`；若内网回源，分别接两台机器的 443。TLS 回源使用匹配的域名/SNI 与可信证书。

客户 AppID 填入 `frontend/project.config.json`；前端 API 固定指向销售域名 `/api/v1`。在客户小程序后台配置实际所用的合法请求/上传/下载域名，再使用客户工程做真机登录、录音上传与业务操作验收。正式微信审核发布另行安排。

上线验收需覆盖：两机进程及版本、真实登录权限、持久化/备份恢复、可信公网 HTTPS、客户模型真实调用、中台与销售业务输出、小程序真机主链。模型未配置时 `health/ready` 返回 degraded，属于尚未完成接入。当前已验证项见 [部署记录](DEPLOYMENT.md)，备份和后续定开见 [运行维护](OPERATIONS.md)。
