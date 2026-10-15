"""Server-only acceptance: isolated database, real HTTP, real SenseAudio, no credential output."""

import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx

BASE = Path(__file__).resolve().parents[2]
TARGET_DB = "sales_visit_acceptance_20260910"
url = urlsplit(os.environ["DATABASE_URL"])
os.environ["DATABASE_URL"] = urlunsplit(url._replace(path="/" + TARGET_DB))
os.environ["SALES_TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]
os.environ["VISIT_IMPORT_ROOT"] = "/var/lib/sales-backend/visit-imports-acceptance"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def worker():
    from sales_backend.config import get_settings
    from sales_backend.db import Database
    from sales_backend.worker import Worker

    db = Database(get_settings())
    await db.connect()
    w = Worker(db)
    w._next_daily_schedule_at = w._next_priority_monitor_at = datetime.now(UTC) + timedelta(days=30)
    try:
        await w.run_forever()
    finally:
        await db.close()


async def check():
    from sales_backend.config import get_settings
    from sales_backend.db import Database
    from sales_backend.integrations.senseaudio import SenseAudioClient
    from sales_backend.repositories.identity import IdentityRepository
    from sales_backend.services.runtime_config import load_runtime_configuration
    from sales_backend.domain.visit_review import review_text

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8081/api/v1", timeout=45) as c:
        for _ in range(50):
            try:
                if (await c.get("/health/ready")).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.4)

        async def req(method, path, **kwargs):
            r = await c.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path}: {r.status_code}: {r.text[:350]}")
            return r.json()

        auth = await req("POST", "/auth/session", json={"account_code": "XS001"})
        actor_info = auth["actor"]
        c.headers["Authorization"] = "Bearer " + auth["access_token"]
        db = Database(get_settings())
        await db.connect()
        async with db.connection() as conn:
            identity = await IdentityRepository().find_actor_by_account(
                conn, workspace_external_id=get_settings().demo_workspace, account_code="XS001"
            )
        actor = identity.context
        customer = await req(
            "POST",
            "/customers",
            json={
                "name": f"闭环验收-{int(time.time())}",
                "industry": "",
                "customer_type": "潜在客户",
                "source": "销售线索",
                "target_team": actor_info["team_names"][0],
                "partner_name": "",
                "contact_name": "王经理",
                "contact_title": "产品经理",
                "contact_role": "使用者",
            },
        )
        cid = customer["id"]
        cols = await req("GET", "/directory/colleagues")
        assert any(x["id"] == actor_info["user_id"] for x in cols["items"])
        assert not (await req("GET", f"/opportunities?customer_id={cid}"))["items"]
        emit({"check": "customer-created-no-opportunity", "customer_id": cid, "colleagues": len(cols["items"])})

        async def agent(text, customer_id=None):
            conv = await req("POST", "/conversations", json={"mode": "visit_entry", "customer_id": customer_id})
            accepted = await req(
                "POST", f"/conversations/{conv['id']}/messages", json={"text": text, "client_message_id": str(uuid4())}
            )
            for _ in range(180):
                r = await req("GET", f"/agent/runs/{accepted['run_id']}")
                if r["status"] in {"succeeded", "waiting_human"}:
                    emit(
                        {
                            "check": "real-model-run",
                            "run_id": r["id"],
                            "score": r["result"].get("quality_review", {}).get("follow_up_score"),
                            "prompt_version": r["result"].get("prompt_version"),
                        }
                    )
                    return r
                if r["status"] in {"failed", "cancelled"}:
                    raise RuntimeError("Agent failed: " + str(r.get("error_detail")))
                await asyncio.sleep(1)
            raise RuntimeError("Agent timeout")

        async def upload(filename, content):
            accepted = await req(
                "POST",
                "/visit-imports",
                files={"file": (filename, content, "application/octet-stream")},
                data={"original_filename": filename},
            )
            for _ in range(180):
                r = await req("GET", f"/visit-imports/{accepted['id']}")
                if r["status"] == "succeeded":
                    assert r["extracted_text"].strip()
                    emit(
                        {
                            "check": "real-file-import",
                            "filename": filename,
                            "characters": len(r["extracted_text"]),
                            "id": r["id"],
                        }
                    )
                    return r
                if r["status"] == "failed":
                    raise RuntimeError("File failed: " + str(r["error_message"]))
                await asyncio.sleep(1)
            raise RuntimeError("File timeout")

        poor = {"visit_goal": "聊聊需求", "follow_up_record": "客户说以后再说", "next_action": "继续跟进"}
        low = await agent(review_text(poor), cid)
        assert low["result"]["quality_review"]["follow_up_score"] < 70
        assert low["result"]["quality_review"]["suggestions"]
        blocked = await c.post(
            "/visits",
            json={
                "customer_id": cid,
                "fields": {**poor, "_quality_review_run_id": low["id"], "_follow_up_quality_score": 100},
            },
        )
        assert blocked.status_code == 422, blocked.text
        emit({"check": "server-rejects-low-score-and-forged-100", "status": blocked.status_code})

        text = (
            f"2026年9月10日上午拜访{customer['name']}，与产品经理王经理沟通。"
            "拜访目标是确认企业知识检索试点范围和验收方式。"
            "王经理明确现有资料分散在三个知识库，员工每天找资料约需半小时，要求先验证权限隔离和检索准确率。"
            "本次双方确认先用销售部脱敏资料做两周试点，验收要求检索正确率达到百分之九十且不能跨部门看到数据。"
            "下一步计划：9月11日下午由刘志德提交试点方案和权限清单给王经理，"
            "王经理在9月14日前确认测试人员，9月15日安排一次演示。"
        )
        md = await upload("拜访.md", text.encode())
        for ext, path, ns in [
            ("docx", "word/document.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
            ("pptx", "ppt/slides/slide1.xml", "http://schemas.openxmlformats.org/drawingml/2006/main"),
        ]:
            content = io.BytesIO()
            with zipfile.ZipFile(content, "w") as z:
                z.writestr(path, f'<r xmlns:a="{ns}"><a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></r>')
            doc = await upload("拜访." + ext, content.getvalue())
            assert text in doc["extracted_text"]
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        pdf = PdfWriter()
        page = pdf.add_blank_page(width=400, height=500)
        font = pdf._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 450 Td (Visit result: customer confirmed a two-week pilot.) Tj ET")
        page[NameObject("/Contents")] = pdf._add_object(stream)
        pdfbytes = io.BytesIO()
        pdf.write(pdfbytes)
        await upload("visit.pdf", pdfbytes.getvalue())
        draft = await agent(md["extracted_text"])
        fields = draft["result"]["fields"]
        assert all(fields[k] for k in ("visit_goal", "follow_up_record", "next_action"))
        review = await agent(review_text(fields), cid)
        assert review["result"]["quality_review"]["follow_up_score"] >= 70
        assert review["result"]["quality_review"]["next_action"]["passed"]
        payload = {
            **fields,
            "opportunity_id": None,
            "collaborator_ids": [actor.user_id],
            "source_import_id": md["id"],
            "_quality_review_run_id": review["id"],
        }
        changed = await c.post("/visits", json={"customer_id": cid, "fields": {**payload, "visit_goal": "替换目标"}})
        assert changed.status_code == 422, changed.text
        archived = await req("POST", "/visits", json={"customer_id": cid, "fields": payload})
        duplicate = await req("POST", "/visits", json={"customer_id": cid, "fields": payload})
        assert duplicate["id"] == archived["id"]
        detail = await req("GET", f"/visits/{archived['id']}")
        assert detail["visit_goal"] == fields["visit_goal"]
        assert detail["opportunity_id"] is None
        saved = await req(
            "PATCH",
            f"/visits/{archived['id']}",
            json={"version_no": detail["version_no"], "partner_name": "人工补充伙伴"},
        )
        assert saved["partner_name"] == "人工补充伙伴"
        customer_detail = await req("GET", f"/customers/{cid}")
        assert any(v["id"] == archived["id"] for v in customer_detail["visits"])
        assert not (await req("GET", f"/opportunities?customer_id={cid}"))["items"]
        emit({"check": "archive-real-db-reload-idempotent-supplement-no-opportunity", "visit_id": archived["id"]})
        runtime = await load_runtime_configuration(db, actor, get_settings())
        sc = SenseAudioClient(runtime.settings)
        try:
            audio = await sc.synthesize(
                text="今天拜访客户，与王经理确认知识检索试点。客户要求验证权限隔离和检索准确率，同意先做两周试点。下一步由刘志德在九月十一日下午提交试点方案。",
                voice_id="female_0033_b",
            )
            transcribed = await upload("拜访录音.mp3", audio.audio_bytes)
            audio_draft = await agent(transcribed["extracted_text"])
            assert audio_draft["result"]["fields"]["follow_up_record"]
            emit(
                {
                    "check": "real-tts-fixture-asr-structure",
                    "asr_model": runtime.settings.asr_model,
                    "llm_model": runtime.settings.llm_model,
                }
            )
        finally:
            await sc.close()
            await db.close()
        emit({"acceptance": "PASSED", "database": TARGET_DB})


if __name__ == "__main__":
    if "--worker" in sys.argv:
        asyncio.run(worker())
    elif "--integration" in sys.argv:
        import pytest

        raise SystemExit(pytest.main(["-q", str(BASE / "tests/integration")]))
    else:
        log = tempfile.TemporaryFile(mode="w+")
        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sales_backend.main:app", "--host", "127.0.0.1", "--port", "8081"],
            stdout=log,
            stderr=log,
        )
        # Fixed local interpreter and this acceptance script; no shell or user arguments.
        w = subprocess.Popen([sys.executable, __file__, "--worker"], stdout=log, stderr=log)  # noqa: S603
        try:
            asyncio.run(check())
        finally:
            w.terminate()
            api.terminate()
            w.wait(timeout=10)
            api.wait(timeout=10)
            log.close()
