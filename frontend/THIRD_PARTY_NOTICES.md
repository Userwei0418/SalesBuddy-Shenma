# 神码 SalesBuddy 1.2.1 前端第三方说明

本客户版本以已核对的内部 Web 1.2.1 和小程序发布源码为基础，保留神码品牌与环境配置。许可证通知随源码和构建产物交付；旧 UI 归档来源保留于历史交付记录，不将其作为整套新版的唯一来源。

- TDesign MiniProgram：运行目录 `miniprogram/ui/tdesign/`；`ui/VERSION.json` 声明 1.16.1。保留 [MIT 许可原文](licenses/TDesign-MIT.txt)。
- Day.js：小程序运行目录 `miniprogram/ui/lib/dayjs/`；Web 锁文件声明 1.11.23。保留 [MIT 许可原文](licenses/dayjs-MIT.txt)。
- tslib：小程序运行文件 `miniprogram/ui/lib/tslib.js`。归档未记录该副本精确版本；保留 [0BSD 许可](licenses/tslib-0BSD.txt) 和 [版权通知](licenses/tslib-CopyrightNotice.txt)，不据许可来源推定运行副本版本。
- 部门 UI：`miniprogram/ui/sb/` 的版本清单声明 `@shandiant/ui-miniprogram` 0.11.0；Web 的 `@shandiant/ui-react` 与 tokens 来源见 `web/package-lock.json`。沿用来源方和用户授权，不将这些内部组件擅自声明为开源软件。
- Web React、Ant Design、Day.js 等依赖的精确版本由 `web/package-lock.json` 固定。构建使用 `legalComments: 'linked'`，随 `web/dist/department-ui/app.js` 保留 `app.js.LEGAL.txt`，发布时不可单独丢弃此文件。
- Web 静态 ECharts、Flatpickr、Tom Select 的原文通知位于 `web/assets/vendor/`，构建复制到 `web/dist/assets/vendor/`。ECharts 同时保留 LICENSE 与 NOTICE。

原四份小程序许可证从神码上一交付版本的物理文件原样保留；本次没有为补通知重装或替换 vendor 运行代码。
