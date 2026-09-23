"""Attach reproducible score projections to already permission-scoped business facts."""

import hashlib
import json

from sales_backend.domain.profile_scores import attainment, weighted_score
from sales_backend.repositories.company_rules import CompanyRulesRepository


async def performance_scores(connection, result):
    repository = CompanyRulesRepository()
    actuals, targets = result["actuals"], result["targets"]
    maturity = {key: attainment(actuals.get(key), targets.get(key)) for key in ("collection", "recognized")}
    maturity["retention"] = result["retention"]["rate"]
    result["scores"] = {
        "maturity": weighted_score(maturity, await repository.active(connection, "score.maturity")),
        "efficiency": weighted_score(result["supplementals"], await repository.active(connection, "score.efficiency")),
    }
    for score in result["scores"].values():
        score["provenance"] = await projection_provenance(connection, score["inputs"], result.get("provenance", {}))
    return result


async def competency_score(connection, result):
    rule = await CompanyRulesRepository().active(connection, "score.competency")
    if result.get("latest"):
        latest = result["latest"]
        values = {
            key: value.get("score")
            for key, value in (latest.get("dimension_scores") or {}).items()
            if isinstance(value, dict)
        }
        # Historical overall_score stays intact. Current presentation carries its own rule snapshot.
        latest["score_summary"] = weighted_score(values, rule)
        latest["score_summary"]["provenance"] = await projection_provenance(
            connection,
            values,
            {
                **result.get("provenance", {}),
                "review_id": latest.get("id"),
                "reviewed_at": latest.get("reviewed_at"),
                "framework_version": (result.get("framework") or {}).get("version_no"),
            },
        )
    result["score_policy"] = rule
    return result


async def projection_provenance(connection, inputs, context):
    # This identifies exact calculation inputs, not a claim to hash every underlying DB row.
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()
    return {
        **context,
        "calculated_at": await connection.fetchval("SELECT clock_timestamp()"),
        "aggregate_input_fingerprint": fingerprint,
    }
