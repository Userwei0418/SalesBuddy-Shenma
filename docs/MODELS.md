# 客户模型配置

## 当前调试配置

用户已授权临时使用 SenseAudio 调试 Key。Key 只保存在客户机受限配置和中台加密凭据中，不写入仓库、前端或源码包，也不从商汤运行环境复制。

| 用途 | 当前选择 | 接口 |
|---|---|---|
| 文字模型 | senseaudio-s2-lite | https://api.senseaudio.cn/v1/chat/completions |
| 录音转写默认项 | senseaudio-asr-lite-1.5-260319 | https://api.senseaudio.cn/v1/audio/transcriptions |
| 语音合成预留项 | senseaudio-tts-1.5-260319 | https://api.senseaudio.cn/v1/t2a_v2 |

文字模型已经完成客户服务器真实合成请求及销售 SenseAudioClient 调用。ASR/TTS 模型名称已在该 Key 的可用列表中核实；录音、语音合成实测分别验收，不以文字请求代替。

## 后续替换

销售管理后台的“模型接口配置”（`/admin#modelApis`）已有按文字、转写、合成用途配置服务商名称、完整接口地址、模型和 Key 的能力。修改先测试，再发布生效；密钥不在响应和配置快照中明文展示。当前环境默认值来自 `/etc/shenma-sales/runtime.env`，属于调试初始配置；通过管理后台发布的公司配置可覆盖它。

中台已安装经官方市场校验的 OpenAI-API-compatible 0.0.67 插件，并已保存和验证 SenseAudio-S2-Lite 连接。后续通过模型供应商配置页面更换接口地址、模型和 Key。当前中台调试配置的上下文窗口为 32,768、输出上限 4,096，属于本实例的配置值，不作为供应商模型最大容量承诺。销售与中台分别维护各自的连接配置，替换供应商时需核对两边，不能只改其中一处。模型切换不需要改小程序源码。

正式交接时由客户配置自己的 Key；本次调试 Key 不表示正式额度或长期供应商承诺。更换后须重新测试文字、转写和已发布 Agent 的真实调用。

依据：https://docs.senseaudio.cn/api-reference/introduction
