import shutil
import subprocess
from pathlib import Path


def test_finance_console_behaviors():
    node = shutil.which("node")
    assert node, "Node is required for the admin finance interaction proof"
    result = subprocess.run(  # noqa: S603 - trusted test fixture and discovered Node executable
        [node, "--test", str(Path(__file__).with_suffix(".cjs"))], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
