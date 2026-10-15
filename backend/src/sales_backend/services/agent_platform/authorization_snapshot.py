"""Backend-owned fact permissions; execution grants never define business reads."""

from copy import deepcopy

from sales_backend.integrations.model_observer import finish_observed, prepare_observed
from sales_backend.repositories.authorization import AuthorizationRepository

READ_ACTIONS = ("customer.read", "opportunity.read", "visit.read", "task.read", "risk.read")


def read_scope(snapshot, actor, *, personal=False, customer_id=None):
    if (snapshot.get("workspace_id"), snapshot.get("user_id")) != (actor.workspace_id, actor.user_id):
        raise PermissionError("分析权限快照不属于当前身份")
    version = snapshot.get("permission_version")
    if not isinstance(version, str) or not version:
        raise PermissionError("分析缺少有效权限版本")
    authorization = AuthorizationRepository.from_snapshot(snapshot)
    reads = {
        action: {
            "allowed": authorization.allows(action),
            "grants": [
                {"scope": grant.scope, "team_ids": sorted(grant.team_ids)}
                for grant in authorization.for_permission(action)
            ],
        }
        for action in READ_ACTIONS
    }
    label = "按各业务授权范围"
    signatures = {tuple(sorted((g["scope"], tuple(g["team_ids"])) for g in item["grants"]))
                  for item in reads.values()}
    if len(signatures) == 1:
        common = next(iter(signatures))
        scopes = {scope for scope, _ in common}
        if "workspace" in scopes:
            label = "全公司授权范围"
        elif scopes == {"teams"}:
            label = "已授权部门"
        elif scopes == {"self"}:
            label = "本人"
        elif scopes == {"assigned"}:
            label = "本人参与业务"
        elif not scopes:
            label = "无业务读取授权"
    if customer_id:
        label = "当前客户，各业务按授权读取"
    if personal:
        label = "本人，各业务按授权读取"
    return {
        # Retained identity fields are not a business authorization decision.
        "role": actor.role.value, "scope_type": actor.data_scope.value,
        "team_ids": list(actor.team_ids), "user_id": actor.user_id,
        "workspace_id": actor.workspace_id, "permission_version": version,
        "business_read": reads, "scope_label": label,
        "selection": {"personal": personal, "customer_id": customer_id},
    }


async def capture(connection, actor, *, personal=False, customer_id=None):
    snapshot = await AuthorizationRepository().snapshot(connection)
    return read_scope(snapshot, actor, personal=personal, customer_id=customer_id)


def attach(facts, scope):
    return {**facts, "scope": {**(facts.get("scope") or {}), **deepcopy(scope)}}


def from_facts(facts):
    scope = facts.get("scope") or {}
    return deepcopy(scope) if "permission_version" in scope else None


async def recheck_connection(connection, actor, expected):
    if not isinstance(expected, dict) or not expected.get("permission_version"):
        raise PermissionError("分析缺少加载时的权限快照")
    if (expected.get("workspace_id"), expected.get("user_id"), expected.get("role")) != (
        actor.workspace_id, actor.user_id, actor.role.value
    ):
        raise PermissionError("分析权限快照不属于当前身份")
    # A long-lived transaction must not reuse its cached authorization context.
    await connection.execute("SELECT security.authorization_refresh()")
    current = await AuthorizationRepository().snapshot(connection)
    if current.get("permission_version") != expected["permission_version"]:
        raise PermissionError("业务读取权限已变化，请重新发起分析")


async def recheck(database, actor, expected):
    if database is None:  # Explicit in-memory routing tests have no persisted facts.
        return
    async with database.transaction(actor, readonly=True) as connection:
        await recheck_connection(connection, actor, expected)


async def for_inference(database, actor, facts):
    """Use the load-time scope; legacy callers capture at dispatch until adapted."""
    expected = from_facts(facts)
    if expected is None and database is not None:
        async with database.transaction(actor, readonly=True) as connection:
            expected = await capture(connection, actor)
    return expected


class PermissionCheckedObserver:
    """Check every provider attempt, including retries inside the direct client."""

    def __init__(self, observer, check):
        self.observer, self.check = observer, check

    async def start(self, endpoint, model, metadata, attempt):
        await self.check()
        return await self.observer.start(endpoint, model, metadata, attempt)

    async def prepare_dispatch(self, invocation_id):
        await self.check()
        await prepare_observed(self.observer, invocation_id)

    async def finish(self, invocation_id, response, error, *, network_sent=True, response_metadata=None):
        # Accounting must still finish after permissions change; it is not a
        # business result or a fresh dispatch and carries no source facts.
        await finish_observed(self.observer, invocation_id, response, error,
                              network_sent=network_sent, response_metadata=response_metadata)
