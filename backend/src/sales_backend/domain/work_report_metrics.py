"""Closed metric-only prose contract shared by generation and save admission."""

from sales_backend.contracts.work_report import METRIC_TOKEN
from sales_backend.domain.model_contract import ModelContractError


def metric_fragment(metric) -> str:
    token = "{{metric:" + metric.metric_id + "}}"
    return token if metric.unit in metric.display_value else token + " " + metric.unit


def metric_statement(metric) -> str:
    """A complete server-owned sentence; a count cannot support added analysis."""
    basis = {
        "occurred_in_period": "本期已发生",
        "planned_in_period": "本期计划",
        "current_at_cutoff": "截止时点状态",
        "current_at_snapshot": "取数时当前状态",
    }[metric.period_basis]
    label = metric.label.replace("{{", "｛｛").replace("}}", "｝｝")
    return f"{basis} · {label}：{metric_fragment(metric)}。"


def validate_metric_only_item(item, metrics):
    """Business prose keeps item-local sources; statistics alone are closed text.

    Several complete statements may share one item, separated only by newlines.
    Source membership/section/batch checks remain the caller's responsibility.
    No old document or manually edited content is migrated by this guard.
    """
    if item.source_refs or not item.metric_refs:
        return
    refs = METRIC_TOKEN.findall(item.text)
    if (not refs or len(refs) != len(set(refs)) or set(refs) != set(item.metric_refs)
            or any(ref not in metrics or not metrics[ref].complete or metrics[ref].value is None for ref in refs)):
        raise ModelContractError("work_report.metric_only.invalid_statistics")
    planned = any(metrics[ref].period_basis == "planned_in_period" for ref in refs)
    if item.kind not in ({"summary", "plan"} if planned else {"summary", "fact"}):
        raise ModelContractError("work_report.metric_only.unsupported_kind")
    if item.text != "\n".join(metric_statement(metrics[ref]) for ref in refs):
        raise ModelContractError("work_report.metric_only.unsupported_prose")
