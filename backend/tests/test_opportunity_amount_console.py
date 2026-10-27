"""Execute the real admin form submit handler with isolated browser fixtures."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_console_opportunity_amount_workflow():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser form behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_suffix(".cjs"))],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
