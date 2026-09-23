#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 0
case "$(hostname)" in
  salesbuddy) domain=salesbuddy.shenzhoukuntai.com ;;
  opsbuddy) domain=ops-salesbuddy.shenzhoukuntai.com ;;
  *) echo 'Unexpected customer host' >&2; exit 1 ;;
esac
if test "${1:-}" = --dry-run; then
  test -f "/etc/letsencrypt/renewal/$domain.conf"
  # Do not deploy staging certificates during this test.
  exec certbot renew --cert-name "$domain" --dry-run
fi
if test "$#" != 0; then echo 'Usage: shenma-issue-certificate [--dry-run]' >&2; exit 1; fi
# Run only after external port-80 challenge reachability has been checked.
certbot certonly --webroot -w /var/lib/letsencrypt --preferred-challenges http \
  --non-interactive --agree-tos --register-unsafely-without-email \
  --cert-name "$domain" -d "$domain"
# Reconcile an existing certificate too, e.g. when issuance preceded platform startup.
RENEWED_LINEAGE="/etc/letsencrypt/live/$domain" \
  /etc/letsencrypt/renewal-hooks/deploy/50-shenma-https
echo 'Production certificate applied; next run shenma-issue-certificate --dry-run.'
