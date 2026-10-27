"""Run personnel-picker SQL and permission regressions in a disposable RLS database."""

import asyncio
import os
import sys

from run_integration_postgres import ROOT, main


async def verify(config, name, role):
    assert name.startswith('salegent_verify_integration_')
    env = {**os.environ,
           'SALES_TEST_DATABASE_URL': f"postgresql://{config['user']}@/{name}?host={config['host']}",
           'SALES_TEST_ROLE': role, 'SALES_TEST_WORKSPACE': 'demo-sales-workspace',
           'PYTHONDONTWRITEBYTECODE': '1'}
    paths = [
        'tests/integration/test_personnel_picker_directory.py',
        'tests/integration/test_team_directory.py',
        'tests/integration/test_task_reassignment_options.py',
        'tests/integration/test_opportunity_browse.py',
        'tests/integration/test_dashboard_selection.py',
        'tests/integration/test_profile_subject_metrics.py',
        'tests/integration/test_fde_profile_team_selection.py',
    ]
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'pytest', *paths, '-q', '-rs', '-p', 'no:cacheprovider',
        cwd=str(ROOT / 'backend'), env=env)
    if await process.wait():
        raise RuntimeError('Personnel picker integration verification failed')


if __name__ == '__main__':
    asyncio.run(main(serve=verify))
