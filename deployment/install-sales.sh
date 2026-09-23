#!/usr/bin/env bash
# Run only after supported dependencies are ready. Each phase is explicit.
set -Eeuo pipefail
[[ $EUID == 0 && $(hostname) == salesbuddy ]] || { echo 'Wrong user/host'; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
[[ $(/usr/lib/postgresql/16/bin/psql --version) == *' 16.'* ]] || { echo 'PostgreSQL 16 required'; exit 1; }
getent passwd shenma-sales >/dev/null || useradd --system --home /var/lib/sales-backend --shell /usr/sbin/nologin shenma-sales
install -d -o shenma-sales -g shenma-sales /var/lib/sales-backend/visit-imports
install -d -o postgres -g postgres -m 700 /var/lib/shenma-provision
# uv must already be available to root; installs a dedicated managed Python.
export UV_PYTHON_INSTALL_DIR=/opt/shenma-python
uv python install 3.12
(cd backend && uv sync --frozen --no-dev --python 3.12)
[[ -e backend/database ]] || ln -s ../database backend/database
cp REVISION backend/REVISION
# First installation only: refuse accidental reuse of an existing database.
if sudo -u postgres psql -X -At -c "SELECT 1 FROM pg_database WHERE datname='shenma_sales'" | grep -qx 1; then
 echo 'Database exists; use the documented resume phases, do not recreate'; exit 1
fi
sudo -u postgres createdb --template=template0 shenma_sales
sudo -u postgres env DATABASE_URL='postgresql:///shenma_sales?host=/var/run/postgresql' "$ROOT/backend/.venv/bin/python" database/scripts/migrate.py
"$ROOT/backend/.venv/bin/python" deployment/provision-runtime.py
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d shenma_sales -f deployment/runtime-grants.sql
sudo -u postgres "$ROOT/backend/.venv/bin/python" deployment/bootstrap-customer.py
chown root:root /var/lib/shenma-provision/initial-admin.json
chmod 600 /var/lib/shenma-provision/initial-admin.json
install -d /opt/shenma-sales
[[ ! -e /opt/shenma-sales/current ]] || { echo 'current already exists; inspect first'; exit 1; }
ln -s "$ROOT" /opt/shenma-sales/current
cp deployment/shenma-api.service deployment/shenma-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shenma-api shenma-worker
curl --fail --silent http://127.0.0.1:8080/api/v1/health/version
