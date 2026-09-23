#!/usr/bin/env bash
# Run on either authorized customer server after installing certbot and nginx.
set -euo pipefail
test "$(id -u)" = 0
case "$(hostname)" in
  salesbuddy) domain=salesbuddy.shenzhoukuntai.com; https_port=28899 ;;
  opsbuddy) domain=ops-salesbuddy.shenzhoukuntai.com; https_port=18899 ;;
  *) echo 'Unexpected customer host' >&2; exit 1 ;;
esac
command -v certbot >/dev/null
command -v nginx >/dev/null
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
install -d -m 755 /var/lib/letsencrypt/.well-known/acme-challenge
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
install -m 755 "$source_dir/deploy-certificate.py" /etc/letsencrypt/renewal-hooks/deploy/50-shenma-https
install -m 755 "$source_dir/issue-certificate.sh" /usr/local/sbin/shenma-issue-certificate
site=/etc/nginx/sites-available/shenma-acme
if test -f "$site" && ! test -f "$site.before-renewal"; then
  cp -p "$site" "$site.before-renewal"
fi
cat > "$site" <<EOF
# Managed by SalesBuddy-Shenma/deployment/tls/install-renewal.sh
server {
    listen 80;
    server_name $domain;
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/letsencrypt;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 301 https://$domain:$https_port\$request_uri; }
}
EOF
ln -sfn "$site" /etc/nginx/sites-enabled/shenma-acme
nginx -t
systemctl enable --now nginx
systemctl reload nginx
systemctl enable --now certbot.timer
systemctl is-enabled certbot.timer
systemctl list-timers --all certbot.timer --no-pager
echo 'Renewal infrastructure ready. Initial issuance and a real renewal dry-run remain required.'
