"""Distinct authorized subject targets; ratios are calculated after union."""
from decimal import Decimal, localcontext

TARGET_KINDS = ("collection", "collection_ppl", "forecast_recognized")


def aggregate_targets(subjects):
    """Accept server-authorized subjects, never client-supplied target values.

    Each entry has scope_type, subject_id, allowed and targets keyed by kind.
    Identical repeated subjects are harmless. Conflicting duplicate identities
    are refused instead of silently choosing one denominator.
    """
    distinct = {}
    scope_types = set()
    for subject in subjects:
        identity = (subject["scope_type"], str(subject["subject_id"]))
        if identity[0] not in {"person", "team"}:
            raise ValueError("目标聚合只支持人员或团队")
        if identity in distinct and distinct[identity] != subject:
            raise ValueError("同一主体的目标事实不一致")
        distinct[identity] = subject
        scope_types.add(identity[0])
    if not distinct or len(scope_types) != 1:
        raise ValueError("目标聚合必须选择同类且非空的主体")

    result = {}
    with localcontext() as context:
        context.prec = 80
        for kind in TARGET_KINDS:
            components, values, target_ids = [], [], set()
            for (scope_type, subject_id), subject in sorted(distinct.items()):
                component = dict(scope_type=scope_type, subject_id=subject_id)
                # Do not even inspect a denied target payload: its presence,
                # identity, version and amount are not authorized output.
                target = subject["targets"].get(kind) if subject["allowed"] else None
                if not subject["allowed"]:
                    component.update(status="permission_denied", target_id=None, version_no=None, value=None)
                elif target is None:
                    component.update(status="no_target", target_id=None, version_no=None, value=None)
                else:
                    identity = str(target["id"])
                    if identity in target_ids:
                        raise ValueError("不同主体不能复用同一生效目标")
                    target_ids.add(identity)
                    value = Decimal(str(target["value"]))
                    if not value.is_finite() or value < 0:
                        raise ValueError("生效目标金额无效")
                    values.append(value)
                    component.update(status="effective", target_id=identity,
                                     version_no=target["version_no"], value=format(value, ".2f"))
                components.append(component)
            complete = all(item["status"] == "effective" for item in components)
            allowed = all(item["status"] != "permission_denied" for item in components)
            known = format(sum(values, Decimal(0)), ".2f") if values else None
            result[kind] = dict(
                kind=kind, id=components[0]["target_id"] if len(components) == 1 else None,
                version_no=components[0]["version_no"] if len(components) == 1 else None,
                value=known if complete else None, known_subtotal=known,
                status="effective" if complete else "no_target" if allowed else "permission_denied",
                coverage="complete" if complete else "partial" if values else "unavailable",
                aggregation="sum_distinct_subject_targets", components=components,
            )
    return result
