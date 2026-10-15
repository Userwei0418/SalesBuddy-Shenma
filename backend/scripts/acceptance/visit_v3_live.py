"""Opt-in live visit v3, document import, and speech API acceptance; no fake model results."""

import argparse
import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import httpx


def office_file(text, powerpoint=False):
    buffer = io.BytesIO()
    prefix = "a" if powerpoint else "w"
    uri = (
        "http://schemas.openxmlformats.org/drawingml/2006/main"
        if powerpoint
        else "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    )
    xml = (f'<{prefix}:document xmlns:{prefix}="{uri}"><{prefix}:p><{prefix}:r>'
           f'<{prefix}:t>{escape(text)}</{prefix}:t></{prefix}:r></{prefix}:p></{prefix}:document>')
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("ppt/slides/slide1.xml" if powerpoint else "word/document.xml", xml)
    return buffer.getvalue()


def text_pdf():
    content = (
        b"BT /F1 12 Tf 50 740 Td (Demo visit: customer requested a pilot. Sales will deliver the plan next week.) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    result, offsets = b"%PDF-1.4\n", [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += str(n).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return result + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()


async def main(args):
    prior = json.loads(args.business.read_text())
    cid, name = prior["ids"]["customer"], prior["prefix"]
    state = {"checks": [], "ids": {}, "customer_id": cid}
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    next_day = (today + timedelta(days=3)).isoformat()

    def check(label, passed, **detail):
        state["checks"].append(dict(name=label, passed=bool(passed), **detail))
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(json.dumps(state["checks"][-1], ensure_ascii=False), flush=True)
        assert passed, label

    async with httpx.AsyncClient(base_url=args.base_url, timeout=150) as client:

        async def req(method, path, expected=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            if response.status_code != expected:
                check(f"HTTP {path}", False, status=response.status_code, expected=expected, error=response.text[:400])
            return response.json()

        auth = await req("POST", "/auth/session", json={"account_code": "XS001"})
        client.headers["Authorization"] = "Bearer " + auth["access_token"]

        async def run(text, label):
            conv = await req("POST", "/conversations", 201, json={"mode": "visit_entry"})
            accepted = await req(
                "POST",
                f"/conversations/{conv['id']}/messages",
                202,
                json={"text": text, "client_message_id": str(uuid4())},
            )
            state["ids"][label + "_run"] = accepted["run_id"]
            for _ in range(120):
                result = await req("GET", f"/agent/runs/{accepted['run_id']}")
                if result["status"] in {"waiting_human", "succeeded"}:
                    check(
                        label + "真实模型完成",
                        True,
                        run_id=result["id"],
                        status=result["status"],
                        prompt_version=(result.get("result") or {}).get("prompt_version"),
                    )
                    return result
                if result["status"] in {"failed", "cancelled"}:
                    check(
                        label + "真实模型完成", False, code=result.get("error_code"), error=result.get("error_detail")
                    )
                await asyncio.sleep(2)
            raise AssertionError("Model job did not finish in time")

        text = (
            f"{today}，刘志德通过视频拜访{name}客服经理林悦。这不是首次拜访。"
            "林悦介绍12名客服每日处理80条咨询，退款规则分散在3个知识库。双方演示5个问题，"
            "4个找到准确说明，1个缺最新退款版本。林悦认可检索方向但要求补齐版本信息，尚未接受报价或决定采购。"
            f"下一步刘志德在{next_day}前提交试点计划与5条验收标准；林悦在同一天提供20条脱敏咨询样本。"
            "本次不关联商机，没有新的采购项目。"
        )
        for filename, content in (
            ("visit.md", text.encode()),
            ("visit.txt", text.encode()),
            ("visit.docx", office_file(text)),
            ("visit.pptx", office_file(text, True)),
            ("visit.pdf", text_pdf()),
        ):
            imported = await req("POST", "/visit-imports", 202, files={"file": (filename, content)})
            state["ids"][filename] = imported["id"]
            for _ in range(90):
                row = await req("GET", f"/visit-imports/{imported['id']}")
                if row["status"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(1)
            check(
                filename + "服务器文件提取",
                row["status"] == "succeeded" and len(row["extracted_text"] or "") > 20,
                import_id=imported["id"],
                status=row["status"],
            )
            if filename == "visit.md":
                text = row["extracted_text"]
        await req("POST", "/visit-imports", 422, files={"file": ("scan.png", b"unsupported")})
        check("扫描图片不进入OCR流程", True)
        regular = await run(text, "普通拜访")
        result = regular["result"]
        score = result["quality_review"]["follow_up_score"]
        check("普通拜访两个正文审核通过", score > 60, score=score)
        fields = {
            **result["fields"],
            "visit_goal": "",
            "interaction_at": today.isoformat(),
            "created_date": today.isoformat(),
            "contact_name": "林悦",
            "opportunity_id": None,
            "_quality_review_run_id": regular["id"],
            "_follow_up_quality_score": 100,
        }
        idem = {"Idempotency-Key": str(uuid4())}
        archived = await req("POST", "/visits", 201, json={"customer_id": cid, "fields": fields}, headers=idem)
        state["ids"]["regular_visit"] = archived["id"]
        again = await req("POST", "/visits", 201, json={"customer_id": cid, "fields": fields}, headers=idem)
        detail = await req("GET", f"/visits/{archived['id']}")
        check(
            "归档入库、可选目标、无商机及客户端分数不可信",
            detail["quality_review"]["follow_up_score"] == score
            and detail["opportunity_id"] is None
            and not detail["visit_goal"]
            and again["id"] == archived["id"],
        )
        home = await req("GET", "/assistant/home")
        check("真实拜访归档卡片进入总览", archived["id"] in json.dumps(home))
        first_text = (
            f"{today}首次拜访{name}采购负责人林悦。客户主营跨境电商客服服务，"
            "客户需求是统一退款规则知识检索，客户预算尚未确定。林悦是选型影响者，由总经理决策。"
            "林悦说目前12名客服每天处理80条咨询，规则散落三处，培训需要两周。她认可试点方向，"
            "希望先解决资料版本差异，但尚未批准采购。"
            f"下一步刘志德于{next_day}前提交试点方案和样本清单，林悦于{next_day}前提供20条脱敏样本。"
        )
        first = await run(first_text, "首次拜访")
        result = first["result"]
        fields = {
            **result["fields"],
            "interaction_at": today.isoformat(),
            "created_date": today.isoformat(),
            "contact_name": "林悦",
            "opportunity_id": prior["ids"]["opportunity_a"],
            "_quality_review_run_id": first["id"],
        }
        check(
            "首次拜访识别四项辅助信息及未知预算",
            fields.get("is_first_visit") is True
            and all(
                fields.get(k) for k in ("customer_main_business", "customer_needs", "customer_budget", "contact_role")
            ),
            score=result["quality_review"]["follow_up_score"],
        )
        archived = await req(
            "POST",
            "/visits",
            201,
            json={"customer_id": cid, "fields": fields},
            headers={"Idempotency-Key": str(uuid4())},
        )
        state["ids"]["first_visit"] = archived["id"]
        detail = await req("GET", f"/visits/{archived['id']}")
        check(
            "首次拜访字段及已有商机关联真实保存",
            detail["is_first_visit"] is True
            and detail["opportunity_id"] == prior["ids"]["opportunity_a"]
            and bool(detail["archived_fields"]),
        )
        low = await run("今天联系了客户，聊得还可以，以后再说。", "低质量拜访")
        quality = low["result"]["quality_review"]
        check(
            "低质量真实评分与修改建议",
            quality["follow_up_score"] <= 60 and bool(quality.get("suggestions")),
            score=quality["follow_up_score"],
        )
        response = await client.post(
            "/visits",
            json={
                "customer_id": cid,
                "fields": {
                    **low["result"]["fields"],
                    "_quality_review_run_id": low["id"],
                    "_follow_up_quality_score": 100,
                    "interaction_at": today.isoformat(),
                    "created_date": today.isoformat(),
                    "contact_name": "林悦",
                },
            },
        )
        check("低质量或缺失正文禁止归档，不能伪造100分绕过", response.status_code in {409, 422})
        if args.audio:
            content = args.audio.read_bytes()
            transcription = await req(
                "POST",
                "/audio/transcriptions",
                files={"file": (args.audio.name, content)},
                data={"purpose": "visit_entry", "language": "zh"},
            )
            state["ids"]["audio_artifact"] = transcription["artifact_id"]
            check(
                "直接录音接口真实语音识别",
                len(transcription["text"]) > 10,
                artifact_id=transcription["artifact_id"],
                trace_present=bool(transcription.get("trace_id")),
            )
            imported = await req("POST", "/visit-imports", 202, files={"file": (args.audio.name, content)})
            state["ids"]["audio_import"] = imported["id"]
            for _ in range(100):
                row = await req("GET", f"/visit-imports/{imported['id']}")
                if row["status"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(2)
            check("上传录音文件真实异步转写", row["status"] == "succeeded" and len(row["extracted_text"] or "") > 10)
        state["finished_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--business", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audio", type=Path)
    asyncio.run(main(parser.parse_args()))
