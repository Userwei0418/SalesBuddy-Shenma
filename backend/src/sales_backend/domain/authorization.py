"""Union of scoped grants, with explicit account denies taking precedence.

Only the repository resolves active, same-company role assignments. This pure
evaluator preserves each action/scope pair instead of multiplying all actions by
the widest scope of any role. It never modifies organization appointments.
"""

from dataclasses import dataclass
from typing import Iterable

from sales_backend.domain.permission_catalog import CATALOG


@dataclass(frozen=True, slots=True)
class Grant:
    permission: str
    scope: str
    team_ids: frozenset[str] = frozenset()
    source: str = ""

    def __post_init__(self):
        definition = CATALOG.get(self.permission)
        if definition is None or self.scope not in definition.scopes:
            raise ValueError("未知权限或不支持的数据范围")
        if (self.scope == "teams") != bool(self.team_ids):
            raise ValueError("指定团队范围必须有团队，其他范围不能附带团队")


@dataclass(frozen=True, slots=True)
class ObjectScope:
    workspace_id: str
    owner_user_id: str | None = None
    team_id: str | None = None
    participant_user_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Authorization:
    workspace_id: str
    user_id: str
    grants: tuple[Grant, ...]
    denied: frozenset[str] = frozenset()

    @classmethod
    def combine(cls, workspace_id, user_id, role_grants: Iterable[Grant],
                account_grants: Iterable[Grant] = (), account_denies: Iterable[str] = ()):
        denied = frozenset(account_denies)
        if denied - CATALOG.keys():
            raise ValueError("未知的账号禁用权限")
        # Keep provenance for the effective-permission explanation in the console.
        grants = tuple(dict.fromkeys((*role_grants, *account_grants)))
        return cls(workspace_id, user_id, grants, denied)

    def for_permission(self, permission: str) -> tuple[Grant, ...]:
        if permission not in CATALOG or permission in self.denied:
            return ()
        return tuple(grant for grant in self.grants if grant.permission == permission)

    def allows(self, permission: str, target: ObjectScope | None = None) -> bool:
        grants = self.for_permission(permission)
        if target is None:
            return bool(grants)
        if target.workspace_id != self.workspace_id:
            return False
        return any(
            grant.scope == "workspace"
            or (grant.scope == "self" and target.owner_user_id == self.user_id)
            or (grant.scope == "assigned" and self.user_id in target.participant_user_ids)
            or (grant.scope == "teams" and target.team_id in grant.team_ids)
            for grant in grants
        )

    def require(self, permission: str, target: ObjectScope | None = None) -> None:
        if not self.allows(permission, target):
            raise PermissionError("当前账号未获此操作或数据范围的授权")

    def capabilities(self) -> dict[str, bool]:
        return {code: self.allows(code) for code in CATALOG}
