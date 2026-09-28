# 小程序 UI 第三方组件说明

本次 UI 来自用户提供的 `Raccoon-SalesBuddy-小程序-v1.0.8-仅配色调整-20260928.zip`，归档 SHA-256 为 `658213b3455abbaf9341ad685700bfdd0f0b8f584338e6dba9a9673751ee9829`。其中第三方运行代码位于 `miniprogram/ui/`。本文件和 `licenses/` 中的原文通知应随源码交付保留。

| 组件 | 使用位置与版本依据 | 授权通知 |
| --- | --- | --- |
| TDesign MiniProgram | `miniprogram/ui/tdesign/`；随包 `ui/VERSION.json` 声明 1.16.1。包含经过 CommonJS 转换的组件与筛选后的图标样式。 | MIT；[原文](licenses/TDesign-MIT.txt) |
| Day.js | `miniprogram/ui/lib/dayjs/`；主文件与本机已安装的 1.11.23 发布文件逐字一致，附带 localeData 插件与 zh-cn locale。 | MIT；[原文](licenses/dayjs-MIT.txt) |
| tslib | `miniprogram/ui/lib/tslib.js`；归档未记录精确版本。保留对应上游 TypeScript helper 库的 0BSD 许可和版权通知；许可原文取自 tslib 2.8.1 的正常 npm 发布包，不据此把归档版本标为 2.8.1。 | 0BSD；[原文](licenses/tslib-0BSD.txt)、[版权通知](licenses/tslib-CopyrightNotice.txt) |

## 许可原文来源

- TDesign MiniProgram 1.16.1：[npm 发布元数据](https://registry.npmjs.org/tdesign-miniprogram/1.16.1)，发布包 `package/LICENSE`。
- Day.js 1.11.23：现有本地依赖的 `dayjs/LICENSE`，其主运行文件与归档逐字一致；[上游项目](https://github.com/iamkun/dayjs)。
- tslib 2.8.1：[npm 发布元数据](https://registry.npmjs.org/tslib/2.8.1)，发布包 `package/LICENSE.txt` 与 `package/CopyrightNotice.txt`。

获取上述通知时仅读取本机文件及正常发布包中的许可证文件，没有安装依赖、执行归档脚本或重建 vendor 产物。

## 用户提供的部门组件

`miniprogram/ui/sb/`、设计变量和桥接样式来自归档声明的 `@shandiant/ui-miniprogram` 0.11.0。归档未附该内部组件的独立开源许可证，本说明不将其声明为 MIT 或其他开源授权；其使用范围沿用用户提供该附件进行神码小程序适配的授权。后续若独立对外分发组件源码，应同时提供来源方的授权说明。
