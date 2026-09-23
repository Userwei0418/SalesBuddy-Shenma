#!/usr/bin/env bash
set -Eeuo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$BASE/instance.json" ]] || { echo '请在已安装目录中运行。'; exit 1; }
cd "$BASE/runtime"
case "${1:-status}" in
 status) docker compose -f compose.json ps -a ;;
 check) python3 "$BASE/healthcheck.py" ;;
 start) docker compose -f compose.json up -d --no-build ;;
 stop) docker compose -f compose.json stop ;;
 logs) docker compose -f compose.json logs --tail 100 "${2:-api}" ;;
 grant-cli) shift; python3 "$BASE/access.py" cli "$@" ;;
 enable-workspaces) shift; python3 "$BASE/access.py" workspace "$@" ;;
 *) echo '用法: manage.sh status|check|start|stop|logs [服务名]|grant-cli 邮箱 工作空间名称|enable-workspaces 管理员邮箱'; exit 2 ;;
esac
