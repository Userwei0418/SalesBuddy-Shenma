#!/usr/bin/env bash
# Consistent database/files snapshot. Run as root during a maintenance window.
set -Eeuo pipefail
[[ $EUID == 0 && $(hostname) == salesbuddy ]] || { echo 'Wrong user/host'; exit 1; }
[[ -L /opt/shenma-sales/current && -f /etc/shenma-sales/runtime.env ]] || { echo 'Installation is incomplete'; exit 1; }
umask 077
DEST="/var/backups/shenma-sales/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$DEST"
API_ACTIVE=$(systemctl is-active shenma-api || true)
WORKER_ACTIVE=$(systemctl is-active shenma-worker || true)
resume_services() {
 [[ "$API_ACTIVE" != active ]] || systemctl start shenma-api
 [[ "$WORKER_ACTIVE" != active ]] || systemctl start shenma-worker
}
trap resume_services EXIT
systemctl stop shenma-api shenma-worker
sudo -u postgres pg_dump -Fc shenma_sales > "$DEST/database.dump"
tar -czf "$DEST/uploads.tar.gz" -C /var/lib/sales-backend .
tar -czf "$DEST/runtime-secrets.tar.gz" -C /etc shenma-sales
PROVISION_FILES=()
for name in initial-admin.json agent-runtime-bindings.json activated-sales-release.json; do
 [[ ! -f "/var/lib/shenma-provision/$name" ]] || PROVISION_FILES+=("$name")
done
[[ ${#PROVISION_FILES[@]} -gt 0 ]] || { echo 'Missing provisioning handover files'; exit 1; }
tar -czf "$DEST/provision-secrets.tar.gz" -C /var/lib/shenma-provision "${PROVISION_FILES[@]}"
readlink -f /opt/shenma-sales/current > "$DEST/release-path.txt"
cp /opt/shenma-sales/current/REVISION "$DEST/REVISION"
pg_restore --list "$DEST/database.dump" >/dev/null
(cd "$DEST" && sha256sum database.dump uploads.tar.gz runtime-secrets.tar.gz provision-secrets.tar.gz release-path.txt REVISION > SHA256SUMS)
echo "Backup verified: $DEST (contains private data; never commit or attach to a public artifact)"
