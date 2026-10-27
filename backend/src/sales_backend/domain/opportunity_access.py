"""Pure access-change rules, fed only by trusted same-transaction repository facts.

These plans do not authorize database writes by themselves. The mutation adapter
must validate current identity/appointments, acquire locks, and retain DB guards.
No role-name shortcut, customer inheritance, or implicit participant conversion.
"""

from dataclasses import dataclass

from sales_backend.domain.authorization import Authorization, ObjectScope
from sales_backend.domain.concurrency import require_version


class OpportunityAccessError(Exception):
    def __init__(self, code: str, detail: str, *, status: int):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class OwnerTarget:
    workspace_id: str
    user_id: str
    team_id: str
    active_sales_appointment: bool
    active_team_membership: bool


@dataclass(frozen=True)
class TransferPlan:
    changed: bool
    owner_user_id: str
    owner_team_id: str
    collaborator_ids: frozenset[str]


def transfer_block_reason(
    authorization: Authorization, source: ObjectScope, *, status: str,
    historical_attribution_safe: bool,
) -> str | None:
    """Source-side availability only; writes also check the chosen destination.

    The repository must supply a complete attribution proof under the finance
    lock. RLS-filtered absence of financial rows is not such a proof.
    """
    if not authorization.allows("opportunity.transfer", source):
        return "OPPORTUNITY_ACCESS_DENIED"
    if status != "open":
        return "TRANSFER_STATE_NOT_ALLOWED"
    if not source.owner_user_id or not source.team_id or historical_attribution_safe is not True:
        return "TRANSFER_ATTRIBUTION_UNRESOLVED"
    return None


def require_access_version(actual: int, expected: int) -> None:
    # This new API must not inherit the legacy None-means-unchecked behavior.
    if type(expected) is not int or expected < 1:
        raise OpportunityAccessError("INVALID_ACCESS_VERSION", "必须提交正整数商机版本", status=422)
    require_version(actual, expected)


def require_transfer_scope(authorization: Authorization, source: ObjectScope, target: OwnerTarget) -> None:
    destination = ObjectScope(target.workspace_id, target.user_id, target.team_id)
    if not authorization.allows("opportunity.transfer", source) or not authorization.allows(
        "opportunity.transfer", destination
    ):
        raise OpportunityAccessError("OPPORTUNITY_ACCESS_DENIED", "来源商机或目标团队不在转交授权范围内", status=403)
    if not target.active_sales_appointment or not target.active_team_membership:
        raise OpportunityAccessError("INVALID_ACCESS_TARGET", "请选择有效销售及其有效任职团队", status=422)


def plan_transfer(
    authorization: Authorization, source: ObjectScope, target: OwnerTarget, *,
    status: str, version_no: int, expected_version: int, collaborator_ids: frozenset[str],
    historical_attribution_safe: bool,
) -> TransferPlan:
    """A false/unknown attribution proof blocks changes, not harmless no-ops."""
    require_transfer_scope(authorization, source, target)
    require_access_version(version_no, expected_version)
    if status != "open":
        raise OpportunityAccessError("TRANSFER_STATE_NOT_ALLOWED", "本阶段仅允许转交进行中的商机", status=409)
    changed = (source.owner_user_id, source.team_id) != (target.user_id, target.team_id)
    if changed and (not source.owner_user_id or not source.team_id or historical_attribution_safe is not True):
        raise OpportunityAccessError(
            "TRANSFER_ATTRIBUTION_UNRESOLVED", "历史业绩归属尚未确认，不能执行本次转交", status=409
        )
    return TransferPlan(
        changed, target.user_id, target.team_id,
        collaborator_ids - {target.user_id} if changed else collaborator_ids,
    )


def plan_collaborators(
    authorization: Authorization, source: ObjectScope, *, version_no: int, expected_version: int,
    requested_ids: frozenset[str], eligible_ids: frozenset[str], existing_ids: frozenset[str],
) -> tuple[frozenset[str], bool]:
    """Only an owner with edit authority may change this explicit relation.

    eligible_ids must come from the current tenant directory, not the request.
    Removing a stale/inactive member is allowed; retaining one is rejected.
    """
    if authorization.user_id != source.owner_user_id or not authorization.allows("opportunity.update", source):
        raise OpportunityAccessError("OPPORTUNITY_ACCESS_DENIED", "仅当前商机主责可维护协作人", status=403)
    require_access_version(version_no, expected_version)
    if source.owner_user_id in requested_ids or not requested_ids <= eligible_ids:
        raise OpportunityAccessError("INVALID_ACCESS_TARGET", "协作人须为有效企业成员且不能重复主责", status=422)
    return requested_ids, requested_ids != existing_ids
