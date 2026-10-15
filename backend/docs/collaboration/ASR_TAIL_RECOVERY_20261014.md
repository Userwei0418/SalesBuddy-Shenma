# 录音极短尾片与中断恢复修复

请求：SB-ASR-TAIL-20261014-01。基于总控确认的固定 `84ee9c5361d0956b774dcf128f6035028e828a73`，相关 ASR 文件由 Backend 明确移交，Worker 仅修改 visit.import 失败分支。该修复不进入已固定 M2 包，不包含 D02/D03/M3 工作区增量。

## 问题和行为

线上已定位的一份录音被切为 240.012 秒和 0.36 秒；第二片 HTTP 200 但无有效文字，使整个任务失败，第一片文字仅在内存中而丢失。七次处理重复同一边界。该历史观察来自只读诊断，不是本修复中重跑客户录音的结果。

本修复在 FFmpeg 已解码的 PCM 分段上，将不足 2 秒的尾片无损并入前片。仍保留原始材料；不会删除尾部音频、跳过空识别的正常分段或用部分文字冒充完整成功。按现有 mono/16 kHz/16-bit 配置，合并后的约 242 秒低于 10 MB；同时检查实际文件大小、完整帧数和 60 分钟限制。

每个成功分段单独保存到 visit_import.evidence 的版本化 checkpoint。checkpoint 绑定原始内容 SHA256、全部分段 SHA256、模型/提供方/配置摘要，并只复用合法连续前缀；不保存凭据、上游原始响应或本次诊断中的客户正文。数据库权限及当前 Worker 租约仍由原 transaction/RLS 复核。ASR 调用期间不持有数据库连接。

临时数据库繁忙、连接中断、超时或事务冲突，最多尝试保存同一 checkpoint 三次（初次加两次重试），不会因此立即重调模型。撤权、状态改变、租约失效、取消不进入这个重试。若三次均未写入，保留先前已提交的分段，当前尚未保存的分段以后仍可能重新调用；不声称对所有故障都能做到零重复调用。

只有全部分段成功才写 extracted_text、succeeded 和最终 job effect；部分文字仅留在内部 checkpoint。现有重试 API 不清除 evidence，因此重新入队或 Worker 重启后可恢复。最终成功保存失败时，完整 checkpoint 可避免再次调用已处理分段。已经 succeeded 的记录保持幂等早退。

HTTP 成功与识别成功分别处理：空字符串/空白/不可见字符为 EmptyTranscriptionError；字段缺失、类型异常、非 JSON 或含 NUL 为 InvalidTranscriptionError；网络或服务失败沿用 SenseAudioError。校验在 observer 的尝试范围内完成，HTTP 200 的无效识别会记为失败且保留实际 HTTP 状态。原调用结算函数按 dispatched/http_status 结算，因此不会因语义失败将已发送请求错误视为未调用退款。

## 客户端和迁移

- 不新增 API 字段或路由，不修改 OpenAPI；一致性检查通过。
- 不新增数据库迁移，沿用 V243 的 evidence JSONB 和原 Worker UPDATE 权限；不修改 migration、授权规则、配置或生产数据。
- 原 084 小程序在 status=succeeded 时才使用 extracted_text，failed 时显示 error_message；可直接显示分段失败原因和保留进度，不需要微信改包。
- 原来七次失败所丢失的内存文字无法从旧记录补回。发布后的首次重试需要重新处理原文件；已有原始材料不需要用户重新上传。真实成功须在总控另行安排的生产验收窗口确认。

## 验证

最终针对性集合：142 passed，0 failed，2 条现有 Starlette/httpx 弃用警告。命令：

```sh
python -m pytest backend/tests/test_visit_audio_recovery.py backend/tests/test_asr_response_observation.py backend/tests/test_senseaudio.py backend/tests/test_audio_lifecycle.py backend/tests/test_model_api.py backend/tests/test_worker_scheduling.py -q
python -m ruff check backend/src/sales_backend/integrations/senseaudio.py backend/src/sales_backend/services/visit_import.py backend/src/sales_backend/worker.py backend/tests/test_visit_audio_recovery.py backend/tests/test_asr_response_observation.py --select F
PYTHONPATH=backend/src python backend/scripts/export_openapi.py --check
git diff --check
```

覆盖包括：

- 240.012 + 0.36 秒、单帧尾片、2 秒阈值、多个长分段，逐字节比较解码 PCM 的顺序和总量；正常短录音保持行为。
- 空/异常响应、不可见字符、非 JSON、503 与超时恢复、默认/自定义网关观测结果、既有模型调用计量测试。
- 空音频、截断 PCM、10 MB 合并限制、60 分钟总时长、5 万字上限。
- 中途失败的连续前缀、原始文件/分段/模型配置变化、非法缓存、最终保存失败、取消、临时 checkpoint 失败及重试耗尽、撤权、租约失效、已成功记录重入。
- 实际 Worker 失败分支的状态、用户提示、队列 error_code 和缓存保留；原音频资源取消/回收、Worker 调度回归。

证据根：`/Users/zhouwei5/Developer/SalesBuddy-deliveries/asr-tail-recovery-20261014`。保留首次测试设置缺字段造成的 15 项 fixture 失败及修正记录；最终 XML 为 `edge-cases-final.xml`。

边界：使用真实 PCM/WAV 文件、真实业务代码、模拟提供方与事务型内存数据库；当前 Mac 未安装生产 FFmpeg 可执行文件，分段输入由 fixture 生成，原有生命周期测试仍使用实际受控子进程。没有声称新做真实 FFmpeg/PG/RLS/客户录音/生产端到端验收，没有外部 ASR 调用。总控需在配套发布后安排真实链路确认；生产状态单独登记。
