#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MINIPROGRAM = ROOT / "miniprogram"


def fail(message: str) -> None:
    raise RuntimeError(message)


# Reject accidental page markup in stylesheets before WeChat compilation.
for stylesheet in MINIPROGRAM.rglob("*.wxss"):
    css = re.sub(r"/\*.*?\*/", "", stylesheet.read_text(), flags=re.S)
    if re.search(r"<\s*/?\s*(view|block|text|button|scroll-view)\b", css):
        fail(f"WXML markup in stylesheet: {stylesheet.relative_to(ROOT)}")

for relative in (
    "project.config.json",
    "miniprogram/app.js",
    "miniprogram/app.json",
    "miniprogram/app.wxss",
    "miniprogram/config.js",
    "miniprogram/utils/apiClient.js",
):
    if not (ROOT / relative).is_file():
        fail(f"missing required file: {relative}")

for path in ROOT.rglob("*"):
    if path.name in {"project.private.config.json", ".DS_Store"}:
        # WeChat generates this local file when importing the formal development project.
        continue
    if path.name in {".DS_Store", ".env"}:
        fail(f"private/local file included: {path.relative_to(ROOT)}")
    if path.is_dir() and path.name in {"node_modules", "miniprogram_npm", "__pycache__"}:
        fail(f"generated directory included: {path.relative_to(ROOT)}")

json_files = sorted(p for p in ROOT.rglob("*.json") if p.name != "project.private.config.json")
for path in json_files:
    json.loads(path.read_text(encoding="utf-8"))

project_config_path = ROOT / "project.config.json"
project = json.loads(project_config_path.read_text(encoding="utf-8"))
if project.get("miniprogramRoot") != "miniprogram/":
    fail("project.config.json miniprogramRoot must be miniprogram/")
if not re.fullmatch(r"(?:touristappid|wx[0-9a-f]{16})", str(project.get("appid", ""))):
    fail("project.config.json appid must be touristappid or a valid WeChat AppID")

app = json.loads((MINIPROGRAM / "app.json").read_text(encoding="utf-8"))
pages = app.get("pages", [])
for page in pages:
    for suffix in (".js", ".json", ".wxml", ".wxss"):
        path = MINIPROGRAM / f"{page}{suffix}"
        if not path.is_file():
            fail(f"incomplete registered page: {path.relative_to(ROOT)}")

for item in app.get("tabBar", {}).get("list", []):
    for key in ("iconPath", "selectedIconPath"):
        path = MINIPROGRAM / item[key]
        if not path.is_file():
            fail(f"missing tab icon: {path.relative_to(ROOT)}")

node = shutil.which("node")
if node:
    for path in sorted(MINIPROGRAM.rglob("*.js")):
        subprocess.run([node, "--check", str(path)], check=True, capture_output=True, text=True)

banned_patterns = {
    "owner AppID": re.compile(r"wx[0-9a-f]{16}"),
    "plaintext IPv4 API URL": re.compile(r"https?://(?:\d{1,3}\.){3}\d{1,3}"),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "developer home path": re.compile(r"/Users/[^/\s]+"),
}
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in {".js", ".json", ".wxml", ".wxss", ".md"}:
        continue
    if path.name == "project.private.config.json" or path.resolve() == Path(__file__).resolve():
        continue
    content = path.read_text(encoding="utf-8", errors="replace")
    for label, pattern in banned_patterns.items():
        # AppIDs identify the application; they are not credentials. Historical
        # release receipts may record the same ID as project.config.json.
        if label == "owner AppID" and (path.resolve() == project_config_path.resolve()
                                      or path.relative_to(ROOT).parts[0] == "docs"):
            continue
        # Isolated test fixtures are outside miniprogramRoot and excluded from
        # uploads. They may exercise IPv4 rejection; all other scans still apply.
        if label == "plaintext IPv4 API URL" and path.relative_to(ROOT).parts[0] == "tests":
            continue
        if pattern.search(content):
            fail(f"{label} found in {path.relative_to(ROOT)}")

print("FRONTEND_VERIFY_OK")
print(f"registered_pages={len(pages)}")
print(f"json_files={len(json_files)}")
print(f"files={sum(1 for path in ROOT.rglob('*') if path.is_file())}")
print(f"node_syntax_check={'enabled' if node else 'skipped (node not installed)'}")
