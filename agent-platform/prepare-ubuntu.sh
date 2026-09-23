#!/usr/bin/env bash
# Docker official apt repository: https://docs.docker.com/engine/install/ubuntu/
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo '请使用 sudo。'; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && ( "$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04 ) && "$(dpkg --print-architecture)" == amd64 ]] || { echo '此环境准备脚本仅支持 Ubuntu 22.04/24.04 amd64。'; exit 1; }
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
 apt-get update
 apt-get install -y python3 openssl curl ca-certificates
 echo '已检测到 Docker Compose，保留现有 Docker 安装。';exit 0
fi
for pkg in docker.io docker-compose docker-compose-v2 podman-docker; do
 if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
   echo "检测到 $pkg，请由服务器管理员按 Docker 官方说明处理现有安装后再运行。不会自动卸载。";exit 1
 fi
done
apt-get update
apt-get install -y ca-certificates curl python3 openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker compose version
echo '运行环境已准备好。下一步运行 install.sh。'
