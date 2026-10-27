#!/usr/bin/env python3
"""Plan a frozen CRM snapshot locally; no credentials, network, SQL or business writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sales_backend.services.crm_history_preflight import build_preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = build_preflight(json.loads(args.snapshot.read_text()), json.loads(args.inventory.read_text()))
    # Exclusive creation prevents accidentally replacing a reviewed plan/source.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"mode": plan["mode"], "counts": plan["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
