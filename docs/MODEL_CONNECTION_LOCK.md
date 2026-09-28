# 模型连接统一维护开关

销售端与 Agent 中台分别启用，锁定不改变现有模型、地址、凭据或正常推理路径。

## 销售后台

在销售服务受限运行配置中设置 `MODEL_CONNECTIONS_LOCKED=true`，按正常流程重启 API。原用途连接的测试、发布和旧配置保存/回滚返回 HTTP 403，即使账号拥有全部管理权限也不能通过这些接口修改连接。已有直连接口的手工连通测试也被锁定；Agent 连通测试及正常业务调用保持原有授权规则。

`/admin#modelApis` 保留连接和历史只读信息。统一 Key 框只面向统一密钥 API 返回 `can_manage=true` 的指定维护账号，提交到 `POST /api/v1/admin/unified-model-key`；该独立入口不属于旧配置锁定路由。统一维护身份、双机验证与同步由统一密钥服务负责，不通过恢复旧页面编辑权限实现。

## Agent 中台

中台使用现有 Nginx 的 `/opt/brand` 只读挂载，不需要重建 Agent API 镜像。先备份当前 `nginx/default.conf`，然后从当前代码仓库执行下面的命令；路径须替换成实际部署路径，不能覆盖客户现有 TLS 或品牌配置：

```sh
python3 deployment/configure-model-connection-lock.py \
  --nginx-conf /实际运行目录/nginx/default.conf \
  --assets-dir /实际运行目录/nginx/assets \
  --locked on
```

脚本只添加带标记的 Nginx 规则、生成无凭据的锁定配置，不连接服务器、不修改模型凭据、不自行重启服务。执行后必须在实际中台 Compose 工程中先运行 `docker compose exec nginx nginx -t`，再应用配置。`default.conf` 是单文件绑定挂载，脚本原子替换文件后应重建 Nginx 容器以采用新挂载；仅 reload 可能仍读取旧 inode。首次安装前可使用同一镜像对新文件做 `nginx -t`，再 `docker compose up -d --force-recreate nginx`，重建后复验。

启用后，公网及通过该 Nginx 的内网请求中，`/console/api/workspaces/current/model-providers/**` 和 `default-model` 的非 GET/HEAD/OPTIONS 请求返回明确的 403 JSON 维护提示。覆盖模型/Provider 凭据新增、修改、删除、测试、已有凭据切换、模型启停与负载均衡配置。读取、登录、应用编辑和 `/v1` 推理不受该规则拦截。统一轮换使用受限 SSH 命令调用 `deployment/rotate-agent-model-key.py`，只接收固定 JSON 协议，不开放通用 shell 或公网配置例外。

回退使用同一脚本 `--locked off`，再验证并应用 Nginx 配置。销售端设 `MODEL_CONNECTIONS_LOCKED=false` 并重启 API 可恢复旧接口原有权限逻辑。此开关限制正常应用入口，不提供防宿主机 root 的保证；不应直接对外暴露绕过 Nginx 的 Agent API 端口。

## 唯一的统一 Key 入口

运维在销售后台「模型服务」填写 Token Plan Key，然后选择「验证并更新统一 Key」。系统验证文字、识别和合成三项固定样本，通过后先更新中台现有 SenseAudio 凭据，再原子启用销售端加密凭据文件。所有公司、直接调用和备用调用读取同一个文件，新调用无需重启服务。停用的用途仍保持停用；模型、提示词、业务权限和 Agent 应用访问令牌不随 Key 变更。

首次部署维护配置如下；不在环境文件或本说明填写新模型 Key：

| 配置 | 用途 |
|---|---|
| `MODEL_CONNECTIONS_LOCKED=true` | 关闭原有分用途与旧配置修改入口 |
| `UNIFIED_MODEL_KEY_FILE` | 受限目录中的加密凭据状态文件，服务用户独占读写、0600；沿用私有 keyring |
| `MODEL_KEY_OPERATOR_IDS` | 可使用唯一入口的运维账号 UUID，逗号分隔；客户不能通过业务授权增加名单 |
| `MODEL_KEY_ROTATION_COMMAND_JSON` | 固定 SSH 命令数组；Key 仅通过 stdin 传递 |

实际服务目录使用 `/var/lib/shenma-model-key`，不能为原 `/etc/shenma-sales` 整个配置目录开放服务写权限。SSH 固定连接中台内网主机，校验固定 host key；远端账号的公钥使用 `restrict,command=...`，sudo 仅允许无参数执行固定的 root 维护脚本。脚本同时核验 hostname、内网 IP 和实例地址，回滚快照只含加密凭据，保存在中台 root 0600 文件中。

运维账号应有独立的 `access.console`、`ai.config_read`、`ai.config_publish` 直接授权。普通客户账号不能通过成员修改、重置密码或账号授权接管该身份。维护账号的凭据单独受限交付，普通说明不写密码。

同步失败会保留原本地凭据并尝试恢复中台；如果进程中断，受限 journal 保留恢复信息，下一次运维提交先处理未完成操作。页面只展示末四位、版本及状态，从不回显 Key。验证与同步可能需要数分钟，该 API 的 Nginx 读取超时单独设置为 600 秒。

## 50 个启用账号

V154 为整个客户部署设置一个 50 人上限，所有公司共用。active 且未软删除的业务账号计入，管理员也计入；仅平台管理镜像 `platform_managed` 不重复占席。停用释放名额，重新启用再次检查。客户不能修改上限或制造豁免身份。

数据库触发器覆盖后台、维护脚本、恢复、upsert 和并发创建，事务失败不留下错误名额；后台显示已用、上限与剩余。迁移保留现有账号，如初始人数已超限，只拒绝进一步增加，不自动停用。部署后将私有额度计数与 `platform.user_ref` 的实际业务账号数核对。

## 本地验证

```sh
cd backend
python -m pytest tests/test_model_connection_lock.py tests/test_unified_model_key_ui.py tests/test_model_api.py tests/test_connectivity.py -q
cd ..
python3 -m unittest deployment/test_model_connection_lock.py
```

部署后还需以普通客户和指定维护账号分别检查页面、旧接口 403、统一入口身份限制、统一 Key 轮换结果和正常业务推理。单元测试不替代双机实际轮换验收。
