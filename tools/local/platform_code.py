#!/usr/bin/env python3
"""Display a current code for the synthetic, loopback-only platform fixture."""
import base64
import json
from pathlib import Path
import sys
import time
from local_paths import state_directory, state_from_config

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'backend/src'))
from sales_backend.security.platform_mfa import authenticator

cfg=json.loads((Path(sys.argv[1]) if len(sys.argv)>1 else state_directory(root)/'settings.json').read_text())
state_from_config(cfg)
if cfg.get('kind')!='salesbuddy-local-v1' or cfg.get('api_port')!=18800:
    raise SystemExit('Only the owned loopback acceptance environment is supported')
seed=base64.b32decode(cfg['platform_totp_seed'])
print('仅用于本机 PLATFORMADMIN；同一验证码不可重复使用。')
print(authenticator(seed).generate(time.time()).decode())
