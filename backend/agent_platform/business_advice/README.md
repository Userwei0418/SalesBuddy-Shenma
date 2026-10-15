# 三类经营建议 Agent

每个 JSON 对应一个独立能力，`publication.json` 是 CLI 回读的发布记录。模型为 `senseaudio-s2`，不授予工具、数据库、账号或业务写入能力。运行 API Key 只由服务器运行配置提供，禁止加入仓库、交付包或日志。

后端维护请求契约和事实范围：`domain/advice.py`、`repositories/advice_facts.py`。请求携带 `mode`、`facts` 和 `backend_prompt`，后者包含当前分析页签、公司调优提示词和允许的事实引用。单次拜访只允许引用 `subject`；客户/商机可引用实际提供的关联记录。平台提示词保留相同边界，并明确服从可信后端的契约要求。后端独立校验结果，拒绝多余字段、重复建议及越界引用。

需要调优时，更新对应 JSON，使用本人的 CLI 授权、当前 revision 检查和发布，再回读 `publication.json`。不要直接覆盖别人的修订。后端提示词更新通过现有公司规则发布；模型、权限、事实、规则或期望快照改变会产生新的缓存标识。运行 API 当前不回传实际快照，因此 `runtime_snapshot_verified=false`，不能把期望快照当作已核实执行版本。

验证命令 `backend/tests/system/verify_business_advice_live.py` 通过独立环境配置启动，创建临时数据库及非绕过 RLS 的运行角色，使用原生密码登录、队列 Worker、真实中台和原 API，然后核对建议、任务、通知和审计。测试结束清理临时库与角色。`verification-20260913.json` 仅包含证据元数据，不含密钥、提示词输入或模型生成正文；它证明隔离环境验证通过，不代表正式环境已经发布。
