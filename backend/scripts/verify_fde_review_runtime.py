"""Read existing FDE accounts and team ranking counts; no sessions or business writes."""

import asyncio
import json
from collections import Counter

from verify_profile_runtime import Checks, load_actors

from sales_backend.config import get_settings
from sales_backend.db import Database, set_request_context
from sales_backend.repositories.collaboration import fde_scope_options
from sales_backend.repositories.fde_dashboard import fde_dashboard


async def main():
    settings = get_settings()
    database = Database(settings)
    await database.connect()
    checks = Checks()
    roles = Counter()
    try:
        async with database.connection() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                actors = await load_actors(connection, checks)
                for (_, role), who in actors.items():
                    if role not in {"fde", "fde_lead"}:
                        continue
                    roles[role] += 1
                    await set_request_context(connection, who)
                    own = await fde_dashboard(connection, who, scope="self", period="month")
                    peers = own["company_rankings"]
                    checks.require(
                        "personal_cohort", peers["scope"] == ("all_fde_leads" if role == "fde_lead" else "all_fde")
                    )
                    checks.require("personal_complete", peers["complete"] is True)
                    if role != "fde_lead":
                        continue
                    directory = await fde_scope_options(connection, who)
                    for team in directory["teams"]:
                        for period in ("week", "month", "quarter", "year"):
                            data = await fde_dashboard(connection, who, scope="team", team_id=team["id"], period=period)
                            company = data["company_rankings"]
                            checks.require(
                                "team_complete",
                                company["scope"] == "company_fde_teams"
                                and company["complete"] is True
                                and company["total"] == len(company["items"]),
                            )
                            checks.require("selected_team", team["id"] in company["selection"]["team_ids"])
                            for row in company["items"]:
                                checks.require(
                                    "team_project_count",
                                    type(row.get("opportunity_count")) is int
                                    and 0 <= row["opportunity_count"] <= row["followup_count"],
                                )
                            selected = next(row for row in company["items"] if row["user_id"] == team["id"])
                            checks.require(
                                "summary_matches_team_projects",
                                selected["opportunity_count"] == data["summary"]["period_opportunities"],
                            )
                            checks.require(
                                "summary_matches_team_visits",
                                selected["followup_count"] == data["summary"]["period_visits"],
                            )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "roles": dict(roles),
                    "checks": dict(checks.counts),
                    "total_checks": sum(checks.counts.values()),
                    "readonly": True,
                    "business_writes": False,
                    "model_calls": False,
                    "sessions_created": False,
                }
            )
        )
    finally:
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        raise SystemExit("FDE verification failed (" + type(error).__name__ + ")") from None
