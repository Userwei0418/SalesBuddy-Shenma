#!/bin/zsh
set -eu
cd "${0:A:h}"

fail() {
  print -u2 -- "$1"
  if [[ -t 0 ]]; then read '?按回车关闭'; fi
  exit 1
}

command -v node >/dev/null 2>&1 || fail '请先安装 Node.js 22 或更新版本。'
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' || fail '当前 Node.js 版本过低，请使用 Node.js 22 或更新版本。'
[[ -f dist/index.html && -f dist/bundle.js && -f dist/department-ui/app.js && -f dist/department-ui/app.css ]] || fail '缺少已构建页面。开发者请先执行 npm ci --ignore-scripts，再执行 npm run build；也可重新解压完整交付包。'

export PORT="${PORT:-5188}"
print -- "企业账号登录：http://127.0.0.1:${PORT}/?mode=live#/pages/login/index"
print -- '在本终端按 Ctrl+C 停止当前服务。端口已被占用时，请设置其他 PORT 后重新启动。'
node server.mjs
