"""Explicit demo setup through public business APIs; never writes SQL or fabricates AI output.

prepare creates marked demo customers/opportunities, imports material, and asks the real
visit agent to structure/review it. Inspect the local state before running archive.
State is deliberately outside the repository and contains no authentication material.
"""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

CORE = ("visit_goal", "follow_up_record", "next_action")
MARKER = "【演示数据，全部业务情节为虚构】"


class Demo:
    def __init__(self, args):
        self.args = args
        self.spec = json.loads(args.spec.read_text())
        self.state = (
            json.loads(args.state.read_text())
            if args.state.exists()
            else {
                "batch": self.spec["batch"],
                "base_url": args.base_url,
                "account": args.account,
                "customers": {},
                "opportunities": {},
                "visits": {},
            }
        )
        assert self.state["batch"] == self.spec["batch"]
        assert self.state["base_url"] == args.base_url and self.state["account"] == args.account
        assert all(c["name"].startswith("【演示】") for c in self.spec["customers"])
        assert all(o["name"].startswith("【演示】") for o in self.spec["opportunities"])
        assert all(v["text"].startswith(MARKER) for v in self.spec["visits"])

    def save(self):
        self.args.state.parent.mkdir(parents=True, exist_ok=True)
        self.args.state.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))
        self.args.state.chmod(0o600)

    @staticmethod
    def emit(event, **values):
        print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)

    async def req(self, method, path, **kwargs):
        r = await self.client.request(method, path, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:250]}")
        return r.json()

    async def agent(self, state, key, text, customer_id=None):
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        if key not in state or state[key]["text_hash"] != fingerprint:
            conv = await self.req(
                "POST",
                "/conversations",
                json={
                    "mode": "visit_entry",
                    "customer_id": customer_id,
                },
            )
            accepted = await self.req(
                "POST",
                f"/conversations/{conv['id']}/messages",
                json={
                    "text": text,
                    "client_message_id": str(uuid4()),
                },
            )
            state[key] = {"run_id": accepted["run_id"], "text_hash": fingerprint}
            self.save()
        run_id = state[key]["run_id"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            run = await self.req("GET", f"/agent/runs/{run_id}")
            if run["status"] in {"waiting_human", "succeeded"} and run.get("result"):
                state[key]["result"] = run["result"]
                self.save()
                return run["result"]
            if run["status"] == "failed":
                raise RuntimeError(f"Real AI failed for {run_id}: {run.get('error_detail')}")
            await asyncio.sleep(2)
        raise RuntimeError(f"Real AI still pending: {run_id}; rerun to resume")

    async def upload(self, state, scenario):
        name = scenario["key"] + ".md"
        if "import_id" not in state:
            accepted = await self.req(
                "POST",
                "/visit-imports",
                data={"original_filename": name},
                files={"file": (name, scenario["text"].encode(), "text/markdown")},
            )
            state["import_id"] = accepted["id"]
            self.save()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = await self.req("GET", f"/visit-imports/{state['import_id']}")
            if result["status"] == "succeeded":
                assert result["extracted_text"].strip() == scenario["text"].strip()
                self.emit("file_extracted", scenario=scenario["key"], characters=len(result["extracted_text"]))
                return result["extracted_text"]
            if result["status"] == "failed":
                raise RuntimeError(result["error_message"])
            await asyncio.sleep(2)
        raise RuntimeError("File still pending; rerun to resume")

    async def prepare(self):
        for customer in self.spec["customers"]:
            key = customer["key"]
            found = await self.req("GET", "/customers", params={"q": customer["name"], "page_size": 100})
            exact = [c for c in found["items"] if c["name"] == customer["name"]]
            if len(exact) > 1:
                raise RuntimeError("Duplicate demo customer names require explicit review")
            result = (
                exact[0]
                if exact
                else await self.req("POST", "/customers", json={k: v for k, v in customer.items() if k != "key"})
            )
            self.state["customers"][key] = {"id": result["id"], "name": customer["name"]}
            self.save()
            self.emit("customer_ready", key=key, id=result["id"], reused=bool(exact))

        for opportunity in self.spec["opportunities"]:
            key = opportunity["key"]
            cid = self.state["customers"][opportunity["customer"]]["id"]
            rows = await self.req("GET", "/opportunities", params={"customer_id": cid, "page_size": 100})
            exact = [o for o in rows["items"] if o["name"] == opportunity["name"]]
            if len(exact) > 1:
                raise RuntimeError("Duplicate demo opportunity names require explicit review")
            result = (
                exact[0]
                if exact
                else await self.req(
                    "POST",
                    f"/customers/{cid}/opportunities",
                    json={k: v for k, v in opportunity.items() if k not in {"key", "customer"}},
                )
            )
            self.state["opportunities"][key] = {"id": result["id"], "name": opportunity["name"], "customer_id": cid}
            self.save()
            self.emit("opportunity_ready", key=key, id=result["id"], reused=bool(exact))

        colleagues = (await self.req("GET", "/directory/colleagues"))["items"]
        by_account = {c["account_code"]: c for c in colleagues}
        for scenario in self.spec["visits"]:
            state = self.state["visits"].setdefault(scenario["key"], {})
            if state.get("archived_id"):
                continue
            cid = self.state["customers"][scenario["customer"]]["id"]
            text = await self.upload(state, scenario) if scenario["input_kind"] == "file" else scenario["text"]
            result = await self.agent(state, "structure", text)
            fields = dict(result["fields"])
            if MARKER not in fields["visit_goal"]:
                fields["visit_goal"] = MARKER + fields["visit_goal"]
            # Manual form values: source identifiers and optional information are confirmed
            # against this explicit fictional scenario, never guessed from model names.
            for key in ("interaction_at", "contact_name", "contact_title", "interaction_mode", "visit_location"):
                fields[key] = scenario[key]
            fields["opportunity_id"] = (
                self.state["opportunities"][scenario["opportunity"]]["id"] if scenario["opportunity"] else None
            )
            fields["collaborator_ids"] = [by_account[a]["id"] for a in scenario["collaborators"]]
            fields["source_import_id"] = state.get("import_id")
            state["fields"] = fields
            state["customer_id"] = cid
            self.save()
            review_text = "\n".join(
                [
                    "拜访审核 v2",
                    "拜访目标：" + fields["visit_goal"].strip(),
                    "拜访结果：" + fields["follow_up_record"].strip(),
                    "下一步行动计划：" + fields["next_action"].strip(),
                ]
            )
            review = await self.agent(state, "review", review_text, cid)
            state["review_passed"] = (
                review["quality_review"]["follow_up_score"] >= 70
                and review["quality_review"]["next_action"]["passed"] is True
            )
            self.save()
            self.emit(
                "ready_for_content_review",
                key=scenario["key"],
                score=review["quality_review"]["follow_up_score"],
                passed=state["review_passed"],
            )

    async def archive(self):
        for scenario in self.spec["visits"]:
            state = self.state["visits"][scenario["key"]]
            if not state["review_passed"]:
                raise RuntimeError("Review failed; revise actual content and re-review, never change the score")
            if not state.get("content_checked"):
                raise RuntimeError("Inspect extracted content and set content_checked before archival")
            payload = {**state["fields"], "_quality_review_run_id": state["review"]["run_id"]}
            result = await self.req("POST", "/visits", json={"customer_id": state["customer_id"], "fields": payload})
            if state.get("archived_id"):
                assert state["archived_id"] == result["id"]
            state["archived_id"] = result["id"]
            self.save()
            detail = await self.req("GET", f"/visits/{result['id']}")
            assert detail["customer_id"] == state["customer_id"]
            assert detail["opportunity_id"] == state["fields"]["opportunity_id"]
            assert all(detail[k] == state["fields"][k] for k in CORE)
            assert sorted(detail["collaborator_ids"]) == sorted(state["fields"]["collaborator_ids"])
            state["readback"] = {
                k: detail.get(k) for k in ("id", "customer_id", "opportunity_id", "interaction_at", "created_at")
            }
            state["readback"]["follow_up_score"] = detail["quality_review"]["follow_up_score"]
            assert (
                state["readback"]["follow_up_score"] == state["review"]["result"]["quality_review"]["follow_up_score"]
            )
            self.save()
            self.emit("archived_and_readback", key=scenario["key"], **state["readback"])

    async def verify(self):
        for key, customer in self.state["customers"].items():
            detail = await self.req("GET", f"/customers/{customer['id']}")
            expected_visits = [
                v["archived_id"]
                for v in self.state["visits"].values()
                if v["customer_id"] == customer["id"] and v.get("archived_id")
            ]
            assert set(expected_visits) <= {v["id"] for v in detail["visits"]}
            rows = await self.req("GET", "/opportunities", params={"customer_id": customer["id"], "page_size": 100})
            expected_opps = {
                o["id"] for o in self.state["opportunities"].values() if o["customer_id"] == customer["id"]
            }
            assert {o["id"] for o in rows["items"]} == expected_opps
            self.emit(
                "customer_verified",
                key=key,
                name=customer["name"],
                visits=len(expected_visits),
                opportunities=len(expected_opps),
            )

    async def run(self):
        async with httpx.AsyncClient(base_url=self.args.base_url, timeout=60) as self.client:
            auth = await self.req("POST", "/auth/session", json={"account_code": self.args.account})
            self.client.headers["Authorization"] = "Bearer " + auth["access_token"]
            self.emit("session_ready", account=self.args.account, name=auth["actor"]["display_name"])
            if self.args.phase == "prepare":
                await self.prepare()
            elif self.args.phase == "archive":
                await self.archive()
            await self.verify()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--account", default="XS001")
    parser.add_argument("--phase", choices=["prepare", "archive", "verify"], required=True)
    args = parser.parse_args()
    asyncio.run(Demo(args).run())
