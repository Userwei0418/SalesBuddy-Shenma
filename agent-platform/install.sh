#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [[ "${1:-}" == --help ]]; then
  echo '用法: sudo bash install.sh --public-url https://你的服务器IP:8443 [--install-dir /opt/raccoon-agent] [--resume] [--cert fullchain.pem --key privkey.pem]'
  exit 0
fi
[[ $EUID == 0 ]] || { echo '请使用 sudo 运行。'; exit 1; }
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo '此包支持 Linux x86_64，请使用 Ubuntu 22.04/24.04 服务器。'; exit 1; }
for tool in docker python3 openssl curl sha256sum; do command -v "$tool" >/dev/null || { echo "缺少 $tool，请先按安装说明准备环境。"; exit 1; }; done
docker info >/dev/null
docker compose version >/dev/null
sha256sum -c --quiet SHA256SUMS || { echo '安装包校验失败，停止安装。'; exit 1; }
TARGET=/opt/raccoon-agent
ARGS=()
while (($#)); do
 case "$1" in
 --install-dir) TARGET="${2:?缺少目录}"; ARGS+=("$1" "$2"); shift 2 ;;
 --public-url|--cert|--key|--bind-address) ARGS+=("$1" "${2:?缺少参数值}"); shift 2 ;;
 --resume) ARGS+=("$1"); shift ;;
 *) echo "未知参数: $1"; exit 2 ;;
 esac
done
[[ "$TARGET" == /* ]] || { echo '安装目录必须为绝对路径。'; exit 2; }
if [[ ! " ${ARGS[*]} " == *" --install-dir "* ]]; then ARGS+=(--install-dir "$TARGET"); fi
# x86_64 bundle; fail early on insufficient installation disk rather than partially unpacking.
NEAREST="$TARGET"
while [[ ! -d "$NEAREST" ]]; do NEAREST="$(dirname -- "$NEAREST")"; done
FREE=$(df -Pk "$NEAREST" | awk 'NR==2 {print $4}')
(( FREE >= 12582912 )) || { echo '至少需要 12GB 可用空间，建议预留 25GB。'; exit 1; }
python3 configure.py "${ARGS[@]}"
trap 'echo "安装未完成。保留目录便于排查；修正原因后使用相同命令加 --resume。" >&2' ERR
while IFS= read -r IMAGE; do
 if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then docker pull "$IMAGE"; fi
done < <(python3 -c 'import json; print("\n".join(sorted(set(json.load(open("BASE-IMAGES.json")).values()))))')
cd "$TARGET/runtime"
docker compose -f compose.json config --quiet
docker compose -f compose.json build api web
docker compose -f compose.json up -d --no-build
python3 "$TARGET/healthcheck.py" --wait 300
trap - ERR
echo "安装已启动并通过就绪检查。查看首次登录方式：sudo cat '$TARGET/首次登录.txt'"
echo '随后在页面创建管理员、配置模型供应商，再创建智能体。'
