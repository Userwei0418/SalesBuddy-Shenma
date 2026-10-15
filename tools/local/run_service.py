"""Start one service with local-only, allowlisted configuration."""
import json
import os
from pathlib import Path
import runpy
import sys
from local_paths import state_from_config

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend/src"))
service = sys.argv[1]
cfg = json.loads(Path(sys.argv[2]).read_text())
if cfg.get("kind") != "salesbuddy-local-v1" or service not in {"api", "worker"}:
    raise SystemExit("Invalid local service configuration")
os.environ.update(APP_ENV="development", AUTH_MODE="password",
                  PUBLIC_WEB_ORIGIN=f"http://127.0.0.1:{cfg['api_port']}",
                  DATABASE_URL=f"postgresql://salesbuddy_local_{service}:{cfg[service + '_password']}@127.0.0.1:{cfg['db_port']}/salesbuddy_local",
                  ACCESS_TOKEN_SECRET=cfg["token_secret"], ACCESS_TOKEN_ISSUER="salesbuddy-local",
                  ACCESS_TOKEN_AUDIENCE="salesbuddy-local-client", PYTHONUNBUFFERED="1",
                  VISIT_IMPORT_ROOT=str(state_from_config(cfg) / "materials"), WORKER_ID="local-worker")
if service == "api":
    os.environ['PLATFORM_MFA_KEYRING_FILE'] = str(state_from_config(cfg) / "platform-mfa-keys.json")
    os.environ['PLATFORM_MFA_KEY_ID'] = "local-v1"
    os.environ['PLATFORM_DATABASE_URL'] = (f"postgresql://salesbuddy_local_platform:{cfg['platform_password']}"
                                         f"@127.0.0.1:{cfg['db_port']}/salesbuddy_local")
    import uvicorn
    uvicorn.run("sales_backend.main:app", host="127.0.0.1", port=cfg["api_port"], proxy_headers=False)
else:
    runpy.run_module("sales_backend.worker", run_name="__main__")
