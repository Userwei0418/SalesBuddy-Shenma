"""Role capabilities describe actions; object permissions remain independently enforced."""

import hashlib
import json

FDE_ROLES = frozenset({"fde", "fde_lead"})
BUSINESS_ROLES = frozenset({"sales", "supervisor", "manager"})
CONSOLE_ROLES = frozenset({"operations", "administrator"})


def role_capabilities(role: str, *, visit_entry_enabled: bool = False) -> dict[str, bool]:
    fde = role in FDE_ROLES
    business = role in BUSINESS_ROLES
    console = role in CONSOLE_ROLES
    return {
        "customer.read": business or fde or console,
        "customer.create": console,
        "customer.edit": business or console,
        "customer.claim": business,
        "opportunity.read": business or fde or console,
        "opportunity.edit": business or console,
        "fde.members.manage": business or role == "fde_lead" or console,
        "visit.create": business or (fde and visit_entry_enabled),
        "visit.supplement": business or (fde and visit_entry_enabled),
        "task.create": business or fde or console,
        "task.respond": business or fde or console,
        "task.coordinate": role in {"supervisor", "manager", "fde_lead"} or console,
        "risk.resolve": business or console,
        "advice.decide": business or fde,
        "actual.manage": role in {"supervisor", "manager"} or console,
        "console.access": console,
        "team.view": role in {"supervisor", "manager", "fde_lead"} or console,
    }


def permission_version(actor, capabilities: dict[str, bool], relation_version: str = "") -> str:
    payload = [
        actor.workspace_id,
        actor.user_id,
        actor.role.value,
        actor.data_scope.value,
        sorted(actor.team_ids),
        capabilities,
        relation_version,
    ]
    return "cap-v1:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
