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

# 演示工作区：只用合成数据，关闭真实中台代理，不连接任何业务后端
export PORT="${PORT:-5189}"
url="http://127.0.0.1:${PORT}/?mode=preview#/pages/index/index"
print -- "演示工作区（合成数据）：${url}"
print -- '顶栏「演示身份」可切换一线销售、销售主管、销售总经理、FDE、FDE 主管。在本终端按 Ctrl+C 停止。'
( sleep 1 && command -v open >/dev/null 2>&1 && open "$url" ) &
node scripts/start_department_preview.mjs
