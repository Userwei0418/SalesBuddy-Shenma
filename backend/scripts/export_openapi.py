"""把应用真实的 OpenAPI 契约导出到 openapi/openapi.yaml。

手写 spec 会随迭代腐化（这份文件曾一度声明了 14 条并不存在的路径），
所以契约改为从 FastAPI 应用生成，由 tests/test_openapi_contract.py 守住不漂移。

改动接口后执行：
    uv run --extra dev python scripts/export_openapi.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi" / "openapi.yaml"
HEADER = "# 本文件由 scripts/export_openapi.py 生成，请勿手工编辑。\n"


def render() -> str:
    from sales_backend.main import app

    body = yaml.safe_dump(app.openapi(), allow_unicode=True, sort_keys=True, width=100)
    return HEADER + body


def main() -> int:
    rendered = render()
    if "--check" in sys.argv:
        current = SPEC_PATH.read_text() if SPEC_PATH.exists() else ""
        if current != rendered:
            print(f"{SPEC_PATH.name} 与应用不一致，请运行 scripts/export_openapi.py 重新生成")
            return 1
        print(f"{SPEC_PATH.name} 与应用一致")
        return 0
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(rendered)
    print(f"已写入 {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
