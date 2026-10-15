"""Whole 1.2.1 customer bridge: V159 and V161 preservation and fresh provisioning.

CI: python deployment/check-whole-upgrade-ci.py (CI=true, PGHOST=127.0.0.1).
Local: add --socket /private/socket --port N. Creates/drops only random test DBs.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "database/tests"))
from verify_shenma_whole_121_postgres import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--ci")
    asyncio.run(main())
