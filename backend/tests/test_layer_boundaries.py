"""Guard dependency direction and prevent transport routes accumulating inline SQL again."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src/sales_backend'


def test_dependency_direction():
    for layer, forbidden in {
        'domain': ('api', 'services', 'repositories'),
        'repositories': ('api', 'services'),
        'services': ('api',),
    }.items():
        for path in (ROOT/layer).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not any(node.module.startswith('sales_backend.'+item) for item in forbidden), (path,node.module)


def test_api_has_no_database_statements():
    for path in (ROOT/'api').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {'execute','executemany','fetch','fetchval','fetchrow'}, (path,node.lineno)


def test_visit_orchestration_delegates_sql_to_repositories():
    for name in ('visit_archive.py', 'visit_review_gate.py'):
        for node in ast.walk(ast.parse((ROOT/'services'/name).read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {'execute','fetch','fetchrow','fetchval'}, (name,node.lineno)
