# weekly.v2 契约来源

来自用户提供的「周报后端联调交接-weekly-v2-20260924.zip」。输入、输出 JSON Schema 与 system-prompt.txt 保留交接版原文。

后端复用交接包的 decode_response.py、schema_check.py、validate_response.py：改为包内导入，schema_check 增加 Decimal 类型支持，金额以精确 JSON 数字传输，不经过二进制浮点数。验证器校验结构、日期和来源引用，不能证明模型文字的全部语义真实性。

神码 Service API 实测中，缺少档案的两个样例出现空栏目及把拜访引用放进 entity_refs 的问题。runtime-guard.txt 仅重申原输出 Schema 的约束；发布提示词为原文加此补充，未改变业务口径。客户现有模型插件输出上限 4096；超长或不完整输出必须失败，不裁剪后作为成功草稿。

运行时仅访问神码中台。原中台的 App ID、Key、Agent ID 不作为运行配置。固定输入快照、原始生成结果与人工草稿分别保存；当前传输协议不能证明实际执行的快照 ID，因此 actual_snapshot_id=null、runtime_snapshot_verified=false。
