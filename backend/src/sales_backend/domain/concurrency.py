"""乐观锁：调用方带来的 version_no 必须与当前行一致。

未传 expected 时不做校验，兼容尚未带版本号的旧客户端；传入后冲突抛 VersionConflict，
由 API 层映射为 409。
"""

from __future__ import annotations

from typing import Any


class VersionConflict(RuntimeError):
    """当前行的 version_no 与调用方持有的不一致。"""


def require_version(actual: Any, expected: Any) -> None:
    """expected 为 None 时跳过；否则必须与 actual 同为整数且相等。"""
    if expected is None:
        return
    try:
        if int(actual) == int(expected):
            return
    except (TypeError, ValueError) as exc:
        raise VersionConflict("记录已被他人更新，请刷新后再试") from exc
    raise VersionConflict("记录已被他人更新，请刷新后再试")
