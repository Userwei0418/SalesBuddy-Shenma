#!/usr/bin/env python3
"""Install/toggle the Agent console connection lock without touching credentials.

Run against the deployed nginx/default.conf and its existing assets directory.
Preserves customer TLS, branding and all proxy routes. Run nginx -t and reload
only after reviewing the resulting files; this command never runs Docker.
"""

import argparse
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "agent-platform/runtime/nginx"


def block(text, kind):
    pattern = rf"(?m)^[ \t]*# BEGIN MODEL CONNECTION LOCK {kind}\n.*?^[ \t]*# END MODEL CONNECTION LOCK {kind}\n"
    match = re.search(pattern, text, flags=re.S)
    if not match:
        raise ValueError(f"Missing {kind} lock template")
    return match.group(0)


def render_config(current, template):
    for kind in ("HTTP", "SERVER"):
        content = block(template, kind)
        pattern = rf"(?m)^[ \t]*# BEGIN MODEL CONNECTION LOCK {kind}\n.*?^[ \t]*# END MODEL CONNECTION LOCK {kind}\n"
        if re.search(pattern, current, flags=re.S):
            current = re.sub(pattern, lambda _: content, current, flags=re.S)
        elif kind == "HTTP":
            current = content + current
        else:
            current, count = re.subn(r"(?m)^server\s*\{\s*\n", lambda m: m.group(0) + content, current, count=1)
            if count != 1:
                raise ValueError("Expected an existing nginx server block; refused to replace config")
    return current


def atomic_write(path, content):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=".model-lock-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(nginx_conf, assets_dir, locked):
    current = nginx_conf.read_text()
    rendered = render_config(current, (SOURCE / "default.conf").read_text())
    policy = (SOURCE / "assets/model-connection-lock.conf").read_text()
    policy, count = re.subn(
        r"(map \$host \$model_connections_locked \{\s*default )[01];",
        lambda match: match.group(1) + str(int(locked)) + ";", policy, count=1,
    )
    if count != 1:
        raise ValueError("Expected deployment lock map in template")
    if not assets_dir.is_dir():
        raise ValueError("Expected existing nginx assets directory")
    atomic_write(assets_dir / "model-connection-lock.conf", policy)
    atomic_write(nginx_conf, rendered)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx-conf", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--locked", choices=("on", "off"), required=True)
    args = parser.parse_args()
    configure(args.nginx_conf, args.assets_dir, args.locked == "on")
    print(f"Model connection lock: {args.locked}. Validate nginx configuration, then reload/recreate nginx.")


if __name__ == "__main__":
    main()
