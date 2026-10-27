"""Run only follow-up management and existing quality/access regressions in an isolated DB."""
import asyncio
import os
import sys

from run_integration_postgres import ROOT, main


async def verify(config, name, role):
    assert name.startswith('salegent_verify_integration_')
    env = {**os.environ, 'SALES_TEST_DATABASE_URL': f"postgresql://{config['user']}@/{name}?host={config['host']}",
           'SALES_TEST_ROLE': role, 'SALES_TEST_WORKSPACE': 'demo-sales-workspace', 'PYTHONDONTWRITEBYTECODE': '1'}
    suites = ['test_visit_management', 'test_visit_quality_summary', 'test_authorization_visits']
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pytest',
        *(f'tests/integration/{name}.py' for name in suites), '-q', '-rs', '-p', 'no:cacheprovider',
        cwd=str(ROOT / 'backend'), env=env)
    if await process.wait():
        raise RuntimeError('Follow-up management integration failed')


if __name__ == '__main__':
    asyncio.run(main(serve=verify))
