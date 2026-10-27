"""Real worker source labels; synthetic files and tenants, no provider requests."""

import json
from uuid import uuid4

import pytest

from sales_backend.api.knowledge import CatalogWrite
from sales_backend.db import json_value
from sales_backend.domain.feishu_sync.cards import knowledge_publication_card
from sales_backend.domain.feishu_sync.projection import project
from sales_backend.domain.knowledge import CATEGORY_LABELS
from sales_backend.services.feishu_sync import knowledge_notification_snapshot
from tests.test_knowledge_distribution_feishu import kb_config

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import catalog_values, selection
from .test_knowledge_distribution_feishu import configure, source
from .test_knowledge_notification_intent import publish

pytestmark = pytest.mark.asyncio


def displayed(raw, snapshot):
    values = project("knowledge", raw, {"product", "category", "content_format"})
    card = json.dumps(knowledge_publication_card(snapshot, kb_config(), "recSynthetic"), ensure_ascii=False)
    return values, card


async def publication_snapshot(kb, publication):
    event = dict(await kb.admin.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", publication["publication_id"]))
    event["notification_snapshot"] = json_value(event["notification_snapshot"])
    return event


async def test_native_labels_reach_both_directory_and_immutable_formal_card(kb):
    cid, material, mid = await configure(kb)
    pdf = await kb.service.upload(kb.actors["owner"], uuid4(), "qa.pdf", "application/pdf", b"%PDF-1.4\nsynthetic\n%%EOF")
    material = await kb.service.save(
        kb.actors["owner"], uuid4(), selection(files=[pdf["file_id"]], content_category="conference_speech"), mid, 1
    )
    p = await publish(kb, mid, material["version_id"], 0)
    raw = await source(kb, cid, mid)
    event = await publication_snapshot(kb, p)
    values, card = displayed(raw, event["notification_snapshot"])
    assert (values["product"], values["category"], values["content_format"]) == ("通用", "会议演讲", "PDF文档")
    assert "产品：通用" in card and "类别：会议演讲" in card
    assert "general" not in card and "conference_speech" not in card
    # A later catalog edit can refresh the current row, but cannot rewrite a
    # formal publication's already captured business label or notification intent.
    body = catalog_values()
    body.update(expected_revision=1, products=[{"code": "general", "label": "后续通用名称", "active": True}])
    await kb.service.save_catalog(kb.actors["owner"], uuid4(), CatalogWrite(**body))
    later = await source(kb, cid, mid)
    assert later["product"] == "后续通用名称"
    assert knowledge_notification_snapshot(event, later) == event["notification_snapshot"]
    assert event["notification_snapshot"]["product"] == "通用"
    assert "后续通用名称" not in displayed(later, event["notification_snapshot"])[1]


async def test_unknown_codes_do_not_leak_or_borrow_another_tenants_labels(kb):
    cid, material, mid = await configure(kb)
    unknown = "gtm_unknown_product_code"
    other = catalog_values()
    other["products"].append({"code": unknown, "label": "其他公司保密产品", "active": True})
    await kb.service.save_catalog(kb.actors["other"], uuid4(), CatalogWrite(**other))
    # Owner fixture models an old/unrecognized stored code, not a writable API.
    await kb.admin.execute(
        "UPDATE knowledge.version SET contribution=jsonb_set(jsonb_set(contribution,"
        "'{selection,product_code}',to_jsonb($2::text)),'{selection,content_category}',to_jsonb($3::text)) WHERE id=$1",
        material["version_id"], unknown, "future_internal_category",
    )
    p = await publish(kb, mid, material["version_id"], 0)
    raw = await source(kb, cid, mid)
    event = await publication_snapshot(kb, p)
    values, card = displayed(raw, event["notification_snapshot"])
    assert values["product"] == "未配置产品" and values["category"] == "未分类"
    encoded = json.dumps(values, ensure_ascii=False) + card
    assert not any(v in encoded for v in [unknown, "future_internal_category", "其他公司保密产品"])


async def test_legacy_import_labels_are_preserved_in_directory_and_card(kb):
    cid, material, mid = await configure(kb)
    # Model the already validated source-import contribution directly. The
    # source-import HTTP contract is covered by its own suite; this test keeps
    # the label assertion focused on the Feishu source boundary.
    publication = await publish(kb, mid, material["version_id"], 0)
    await kb.admin.execute(
        """UPDATE knowledge.version
           SET contribution=jsonb_set(
                 jsonb_set(
                   jsonb_set(
                     jsonb_set(contribution,'{kind}',to_jsonb('legacy_import'::text)),
                     '{source}',jsonb_build_object('product_label','归档产品','category_label','客户方案','source_status','可用')),
                   '{selection,product_code}',to_jsonb('legacy_archive_product'::text)),
                 '{selection,content_category}',to_jsonb('customer_solution'::text))
         WHERE id=$1""",
        material["version_id"],
    )
    raw = await source(kb, cid, mid)
    event = await publication_snapshot(kb, publication)
    # The source row is the current trusted projection; the publication event
    # remains immutable. Merge only the tested display labels into the card
    # input to assert both consumers render business names without rewriting
    # the historical snapshot.
    values, card = displayed(
        raw,
        {
            **event["notification_snapshot"],
            "product": raw["product"],
            "category": raw["category"],
            "content_format": raw["content_format"],
        },
    )
    assert values["product"] == "归档产品"
    assert values["category"] == "客户方案"
    assert values["content_format"] == "文本文档"
    assert "legacy_" not in json.dumps(values, ensure_ascii=False) + card


async def test_all_existing_category_codes_use_the_shared_business_labels(kb):
    cid, material, mid = await configure(kb)
    await publish(kb, mid, material["version_id"], 0)
    for code, label in CATEGORY_LABELS.items():
        # Exercise the database projection's fixed dictionary against the
        # application's existing contract, without inventing CRM references.
        await kb.admin.execute(
            "UPDATE knowledge.version SET contribution=jsonb_set(contribution,"
            "'{selection,content_category}',to_jsonb($2::text)) WHERE id=$1", material["version_id"], code
        )
        assert (await source(kb, cid, mid))["category"] == label


@pytest.mark.parametrize(
    ("media_type", "label"),
    [
        ("text/plain", "文本文档"),
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "PPT演示文稿"),
        ("video/mp4", "视频"),
        ("application/x-unknown-internal", "其他文件"),
    ],
)
async def test_file_format_is_readable_without_claiming_new_preview_support(kb, media_type, label):
    cid, material, mid = await configure(kb)
    file = await kb.service.upload(kb.actors["owner"], uuid4(), "synthetic.bin", media_type, b"synthetic bytes")
    material = await kb.service.save(kb.actors["owner"], uuid4(), selection(files=[file["file_id"]]), mid, 1)
    await publish(kb, mid, material["version_id"], 0, allow_preview=False, allow_download=True)
    raw = await source(kb, cid, mid)
    assert raw["content_format"] == label
    assert project("knowledge", raw, {"content_format"})["content_format"] == label
