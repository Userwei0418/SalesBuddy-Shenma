#!/usr/bin/env python3
"""Build a local handoff from a clean, committed Git HEAD, without deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from io import BytesIO
import zipfile


EXCLUDED_PARTS = {".git", ".DS_Store", "node_modules", "miniprogram_npm", "__pycache__",
                  ".pytest_cache", ".cache", "coverage", "dist", "build"}
EXCLUDED_NAMES = {"project.private.config.json", "CHECKSUMS.sha256"}
PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3",
                    ".zip", ".tar", ".gz", ".pyc"}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def excluded(path: PurePosixPath) -> bool:
    return (any(part in EXCLUDED_PARTS for part in path.parts)
            or path.name in EXCLUDED_NAMES or path.name.startswith(".env")
            or path.suffix.lower() in PRIVATE_SUFFIXES)


def json_bytes(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def manifest(files: dict[str, bytes], prefix: str = "") -> bytes:
    return "".join(f"{sha256(content)}  {name[len(prefix):]}\n"
                   for name, content in sorted(files.items()) if name.startswith(prefix)).encode("utf-8")


def build(args: argparse.Namespace) -> dict:
    repo = Path(git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel").decode().strip())
    if git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("工作目录有未提交文件；请提交、通过检查并合并后从最终 HEAD 打包。")
    subprocess.run(["python3", str(repo / "scripts/check_isolation.py"), "--verify-remote"],
                   check=True, cwd=repo, stdout=subprocess.DEVNULL)
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", args.backend_revision):
        raise ValueError("--backend-revision 必须是已核验的完整 40 位 Git SHA。")
    if not re.fullmatch(r"V\d{3,}", args.database_version):
        raise ValueError("--database-version 必须是已核验的 Vxxx 迁移版本。")
    # Both source points must be accessible, with compatible API/DB code. A newer
    # frontend/docs commit is fine; an undeployed backend change is not.
    git(repo, "cat-file", "-e", args.backend_revision + "^{commit}")
    if git(repo, "diff", "--name-only", args.backend_revision, head, "--",
           "backend/src", "backend/pyproject.toml", "backend/uv.lock", "database").strip():
        raise ValueError("最终 HEAD 与运行后端的业务代码或数据库迁移不同，请先完成部署和版本核验。")
    output = Path(args.output_dir).expanduser().resolve()
    if output == repo or repo in output.parents:
        raise ValueError("交付目录必须在 Git 工作目录外。")
    now = datetime.now(timezone.utc)
    release_name = args.release_name or f"销售智助-前端交付包-{now.astimezone():%Y%m%d-%H%M}-{head[:7]}"
    if not re.fullmatch(r"[\w\-]+", release_name) or release_name in {".", ".."}:
        raise ValueError("交付名称只允许文字、数字、下划线和短横线。")
    targets = [output / release_name, output / (release_name + ".zip"),
               output / (release_name + ".zip.sha256")]
    if any(target.exists() for target in targets):
        raise ValueError("目标交付文件已存在，请指定一个新名称，避免覆盖既有交付。")

    docs = set(args.include_doc)
    readme = git(repo, "show", "HEAD:frontend/README.md").decode("utf-8")
    docs.update("docs/" + name for name in re.findall(r"\]\(\.\./docs/([^\s)#]+\.md)(?:#[^)]*)?\)", readme))
    for name in docs:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.parts[0] != "docs" or path.suffix != ".md":
            raise ValueError("--include-doc 仅接受仓库 docs/ 内已提交的 Markdown 文件。")
    evidence = set(args.include_evidence)
    for name in evidence:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("docs", "evidence") or len(path.parts) < 3:
            raise ValueError("--include-evidence 仅接受 docs/evidence/ 内已提交的证据目录或文件。")
    files: dict[str, bytes] = {}
    # Ship the generated public contract with the frontend, without copying
    # backend runtime configuration or source into a workstation handoff.
    contract_name = "接口说明/openapi.yaml"
    files[contract_name] = git(repo, "show", "HEAD:backend/openapi/openapi.yaml")
    archive = git(repo, "archive", "--format=tar", head, "frontend", *sorted(docs | evidence))
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        for item in tar:
            path = PurePosixPath(item.name)
            if item.isdir() or excluded(path):
                continue
            if not item.isfile() or path.is_absolute() or ".." in path.parts or re.search(r"[\x00-\x1f]", item.name):
                raise ValueError("Git 源码中包含不支持的链接或文件名，停止打包。")
            content = tar.extractfile(item).read()
            if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", content):
                raise ValueError("源码含私钥内容，停止打包。")
            files[item.name] = content

    private_name = "交付说明/账号登录说明.md"
    if args.private_accounts_file:
        source = Path(args.private_accounts_file).expanduser()
        if not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise ValueError("账号说明必须是仓库外绝对路径的普通文件。")
        source = source.resolve()
        if source == repo or repo in source.parents:
            raise ValueError("账号说明不得放在 Git 仓库内。")
        files[private_name] = source.read_bytes()

    source_version = json.loads(files["frontend/VERSION.json"])
    config = files["frontend/miniprogram/config.js"].decode("utf-8")
    api_url = re.search(r'API_BASE_URL\s*:\s*["\'](https://[^"\']+)["\']', config)
    if not api_url:
        raise ValueError("不能识别前端 HTTPS API 地址。")
    app = json.loads(files["frontend/miniprogram/app.json"])
    required = {"frontend/project.config.json", "frontend/miniprogram/app.js",
                "frontend/miniprogram/app.wxss", "frontend/miniprogram/utils/apiClient.js"}
    if not app.get("pages"):
        raise ValueError("前端未注册页面。")
    for page in app["pages"]:
        required.update(f"frontend/miniprogram/{page}{suffix}" for suffix in (".js", ".json", ".wxml", ".wxss"))
    for tab in app.get("tabBar", {}).get("list", []):
        required.update("frontend/miniprogram/" + tab[key] for key in ("iconPath", "selectedIconPath"))
    if not required.issubset(files):
        raise ValueError("交付源码缺少工程配置、页面文件或导航图标。")
    project = json.loads(files["frontend/project.config.json"])
    if project.get("miniprogramRoot") != "miniprogram/":
        raise ValueError("工程目录必须配置为 miniprogram/。")
    version = {
        "schema_version": 3, "artifact_kind": "frontend_handoff",
        "frontend_revision": head, "packaged_at": now.isoformat(),
        "backend_revision": args.backend_revision, "database_version": args.database_version,
        "deployment_verification": "Caller supplied after live API/Worker/database verification; this script does not access the server.",
        "api_base_url": api_url.group(1), "registered_pages": len(app.get("pages", [])),
        "contains_private_account_guide": bool(args.private_accounts_file),
        "api_contract_path": contract_name,
        "source_metadata": source_version,
    }
    files["frontend/VERSION.json"] = json_bytes(version)
    privacy_note = ("\n本包包含经授权提供的演示账号说明，见 [账号登录说明](交付说明/账号登录说明.md)。"
                    "交付包仅发送给指定协作人员，不上传公开仓库或公开 Release。\n"
                    if args.private_accounts_file else "\n本包不含账号密码，登录账号由运营提供。\n")
    files["打开说明.md"] = (
        f"# 销售智助前端交付\n\n前端版本：`{head}`。\n\n"
        f"后端版本：`{args.backend_revision}`；数据库迁移：`{args.database_version}`。\n\n"
        "在微信开发者工具中导入本目录下的 **frontend/**，该目录包含 project.config.json。"
        "本机无需启动后端、数据库或安装 npm 依赖。详见 [前端说明](frontend/README.md) 和"
        "[给 AI 的启动提示词](frontend/docs/给AI的启动提示词.md)。\n\n"
        "根目录 CHECKSUMS.sha256 校验全部交付文件；frontend/CHECKSUMS.sha256 可用于单独校验前端目录。"
        "运行 `shasum -a 256 -c CHECKSUMS.sha256`。先校验后修改源码；修改后的文件不再匹配发行校验值。\n\n"
        "[OpenAPI 接口契约](接口说明/openapi.yaml) 与源码一并交付；docs/ 为本次选择的当前状态与验收材料。"
        "历史证据与未随包附送的材料以仓库为准。\n"
        + privacy_note
    ).encode("utf-8")
    files["frontend/CHECKSUMS.sha256"] = manifest(files, "frontend/")
    files["CHECKSUMS.sha256"] = manifest(files)

    output.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".salegent-handoff-", dir=output))
    try:
        package_root = stage / release_name
        package_root.mkdir(mode=0o700)
        for name, content in files.items():
            destination = package_root / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(content)
            destination.chmod(0o600)
        zip_path = stage / (release_name + ".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name in sorted(files):
                zipped.write(package_root / name, str(PurePosixPath(release_name) / name))
        zip_path.chmod(0o600)
        with zipfile.ZipFile(zip_path) as zipped:
            if zipped.testzip() is not None:
                raise ValueError("交付压缩包自检失败。")
        digest = sha256(zip_path.read_bytes())
        checksum = stage / (release_name + ".zip.sha256")
        checksum.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
        checksum.chmod(0o600)
        for staged, destination in zip((package_root, zip_path, checksum), targets):
            os.rename(staged, destination)
    finally:
        shutil.rmtree(stage)
    return {"frontend_revision": head, "backend_revision": args.backend_revision,
            "database_version": args.database_version, "directory": str(targets[0]),
            "zip": str(targets[1]), "sha256": digest, "files": len(files),
            "contains_private_account_guide": bool(args.private_accounts_file)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="仓库外本地交付目录")
    parser.add_argument("--backend-revision", required=True, help="已核验运行后端的完整 Git SHA")
    parser.add_argument("--database-version", required=True, help="已核验运行数据库的迁移版本，如 V073")
    parser.add_argument("--release-name", help="可选的交付文件夹名")
    parser.add_argument("--include-doc", action="append", default=[], help="额外附送的 docs/*.md，可重复")
    parser.add_argument("--include-evidence", action="append", default=[], help="额外附送的 docs/evidence/ 证据路径，可重复；仍排除私有材料")
    parser.add_argument("--private-accounts-file", help="授权提供的仓库外账号说明绝对路径，仅写入本地交付包")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        # No subprocess stderr or private file contents are printed.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        parser.exit(1, "打包失败：" + message + "\n")


if __name__ == "__main__":
    main()
