// 原生接口（wx.showModal 的 confirmColor 等）不认 var()，只能传十六进制。
// 这里的值与部门设计变量 1.5.0-draft.1（小程序端）一致（shandianT/desgin specs/salesbuddy/02-设计变量与同步链路/dist/design-tokens.wxss），变量改了要同步改这里。
module.exports = {
  primary: "#2863CD", // --ui-primary
  danger: "#AC2F28",  // --ui-danger（飞书红 7 档）
  success: "#237B19", // --ui-success（飞书绿 7 档）
};
