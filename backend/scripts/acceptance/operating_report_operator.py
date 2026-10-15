"""Install only authorized report rollout flags; never read or write API secrets."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from operating_report_business_live import WORKSPACE, validate_plan

PRIOR = ("battle_map_review", "opportunity_draft", "personal_risks", "visit_entry", "today_tasks")


def configuration(plan, mode, prior):
    account = validate_plan(plan, mode, live=True)
    value = json.loads(json.dumps(prior))
    assert set(value) == {WORKSPACE}
    caps = value[WORKSPACE]["capabilities"]
    assert set(caps) <= set(PRIOR) | {"operating_report"}
    assert all(caps.get(key) == {"enabled": True, "rollout": "production"} for key in PRIOR)
    if mode == "production":
        report = {"enabled": True, "rollout": "production"}
    else:
        report = {
            "enabled": mode != "off",
            "user_ids": [plan["actors"][account]["user_id"]],
            "expires_at": plan["expires_at"],
            "block_platform_requests": mode == "blocked",
        }
    caps["operating_report"] = report
    return value


def main(args):
    import grp

    assert os.geteuid() == 0
    plan = json.loads(Path(args.plan).read_text())
    path = Path("/etc/sales-fde-pilot.json")
    value = configuration(plan, args.mode, json.loads(path.read_text()))
    temp = path.with_name(path.name + ".report-" + uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 0, grp.getgrnam("sales-backend").gr_gid)
    os.replace(temp, path)
    print("OPERATING_REPORT_MODE=" + args.mode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--mode", required=True)
    main(parser.parse_args())
