"""Verify deployed per-company model bindings; optional fixed-sample tests never publish.

Run under the service identity/environment. Never print credentials or business facts.
"""

import argparse
import asyncio
import json
from uuid import uuid4

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.domain.model_api import ConnectionTest
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.model_api import ModelApiService


async def main(args):
    settings = get_settings()
    database = Database(settings)
    await database.connect()
    try:
        async with database.connection() as conn:
            record = await IdentityRepository().find_maintenance_administrator(
                conn, workspace_external_id=args.workspace, account_code=args.administrator
            )
        if not record or record.context.role.value != "administrator":
            raise RuntimeError("需要当前公司有效系统管理员")
        actor = record.context
        service = ModelApiService(database, settings)
        catalog = await service.list(actor)
        result = {
            "workspace": args.workspace,
            "bindings": [
                {
                    "purpose": i["purpose"],
                    "version": i["version"],
                    "mode": i["configuration"]["mode"],
                    "model": i["configuration"]["model"],
                    "has_key": i["has_api_key"],
                }
                for i in catalog["items"]
            ],
            "tests": [],
        }
        for purpose in args.test:
            item = next(i for i in catalog["items"] if i["purpose"] == purpose)
            cfg = {**item["configuration"], "mode": "custom"}
            if purpose == "tts":
                raise RuntimeError("预留音色需人工指定，不进行自动验收")
            body = ConnectionTest(request_id=uuid4(), expected_version=item["version"], configuration=cfg)
            tested = await service.test(actor, purpose, body)
            result["tests"].append(tested)
        after = await service.list(actor)
        assert [(i["purpose"], i["version"]) for i in catalog["items"]] == [
            (i["purpose"], i["version"]) for i in after["items"]
        ]
        result["bindings_unchanged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--administrator", required=True)
    parser.add_argument("--test", action="append", choices=["text", "asr"], default=[])
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        raise SystemExit("核验未完成：" + type(exc).__name__) from None
