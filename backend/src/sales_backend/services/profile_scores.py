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
    # Kept as the API's compatibility hook. No current rule lookup or historical
    # recomputation: reads expose only the score and rule frozen with the result.
    from copy import deepcopy

    latest = result.get("latest")
    result["score_policy"] = deepcopy((latest.get("score_basis") or {}).get("score_policy")) if latest else None
    return result


async def projection_provenance(connection, inputs, context):
    # This identifies exact calculation inputs, not a claim to hash every underlying DB row.
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()
    return {
        **context,
        "calculated_at": await connection.fetchval("SELECT clock_timestamp()"),
        "aggregate_input_fingerprint": fingerprint,
    }
