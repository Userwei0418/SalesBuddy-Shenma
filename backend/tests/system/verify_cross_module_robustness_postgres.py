"""Current authorization and asynchronous source checks in a disposable database."""

import asyncio
import os
import sys

from run_integration_postgres import ROOT, main


async def verify(config, name, role):
    assert name.startswith("salegent_verify_integration_")
    env = {**os.environ,
           "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
           "SALES_TEST_ROLE": role, "SALES_TEST_WORKSPACE": "demo-sales-workspace",
           "PYTHONDONTWRITEBYTECODE": "1"}
    suites = sys.argv[1:] or [
        "test_personal_risk_source_freshness", "test_personal_risk_platform",
        "test_target_current_applicant_authorization", "test_target_batches",
        "test_target_history_permissions", "test_operations_claims_sql", "test_claim_concurrency",
        "test_authorization_visits", "test_visit_candidate_context",
    ]
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", *(f"tests/integration/{suite}.py" for suite in suites),
        "-q", "-rs", "-p", "no:cacheprovider", cwd=str(ROOT / "backend"), env=env,
    )
    if await process.wait():
        raise RuntimeError("Cross-module robustness integration verification failed")


if __name__ == "__main__":
    asyncio.run(main(serve=verify))
