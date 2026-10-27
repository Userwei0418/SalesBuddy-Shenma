"""Prepare a private, reviewable HTTP import body from the exported GTM NDJSON.

No network, database, credentials, or source writes. Output is created exclusively
with mode 0600. Root may POST it to /api/v1/knowledge/imports/feishu after review.
"""

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from sales_backend.contracts.knowledge_import import ImportRecord, KnowledgeImportInput, imported_contribution

FIELD_IDS = {
    "内容名称": "fldgDSjdyn", "产品": "fldweoDuBh", "内容类别": "fldxjiYv5A",
    "内容提供人": "fldm80h9Fk", "内容材料": "fldaVr4JXp", "附件": "fldPYUM8Tf",
    "使用范围": "fldPi1SH5w", "状态": "fldIp8GKLC", "备注": "fldo3LU4ic",
}


def read_source(input_path, manifest_path, *, base_id, table_id, source_revision):
    """Require the full export of the reviewed nine-field GTM contract."""
    raw_bytes = Path(input_path).read_bytes()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if (
        manifest.get("manifest_version") != "v1"
        or manifest.get("format") != "ndjson"
        or manifest.get("base_token") != base_id
        or manifest.get("table_id") != table_id
        or type(manifest.get("rev")) is not int
        or manifest["rev"] != source_revision
        or manifest.get("has_more") is not False
        or manifest.get("query_context", {}).get("record_scope") != "all_records"
        or type(manifest.get("records_count")) is not int
        or not 1 <= manifest["records_count"] <= 500
        or manifest.get("record_file_size_bytes") != len(raw_bytes)
    ):
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_MANIFEST_MISMATCH")
    columns = manifest.get("columns", {})
    if any(columns.get(name, {}).get("field_id") != field_id for name, field_id in FIELD_IDS.items()):
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_FIELDS_MISMATCH")
    rows = [json.loads(line) for line in raw_bytes.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != manifest["records_count"]:
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_COUNT_MISMATCH")
    if any(not isinstance(row, dict) or not set(FIELD_IDS).issubset(row) for row in rows):
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_RECORD_FIELDS_MISSING")
    return rows, hashlib.sha256(raw_bytes).hexdigest()


def source_summary(records):
    """No record titles, personal names, reference URLs or attachment tokens."""
    return {
        "records": len(records),
        "source_status_counts": dict(sorted(Counter(r.source_status for r in records).items())),
        "source_scope_counts": dict(sorted(Counter(r.scope_label for r in records).items())),
        "with_attachments": sum(bool(r.attachments) for r in records),
        "without_content_reference": sum(not r.links and not r.attachments for r in records),
        "network_requests": 0,
        "database_writes": 0,
    }


def write_private_body(path, body):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as stream:
        json.dump(body.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def normalize_record(raw):
    def single(key):
        values = raw.get(key)
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
            raise ValueError("KNOWLEDGE_IMPORT_SOURCE_ENUM_SHAPE")
        return values[0]

    content = raw.get("内容材料") or ""
    if not isinstance(content, str):
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_LINK_SHAPE")
    if content.startswith(("https://", "http://")):
        links = [content]
    elif content:
        # Exported Feishu hyperlinks use exactly Markdown [label](url).
        match = re.fullmatch(r"\[[^\]\n]*\]\((https?://[^\s]+)\)", content)
        if not match:
            raise ValueError("KNOWLEDGE_IMPORT_SOURCE_LINK_SHAPE")
        links = [match.group(1)]
    else:
        links = []
    providers = raw.get("内容提供人")
    if not isinstance(providers, list) or any(not isinstance(p, dict) or not isinstance(p.get("name"), str)
                                             for p in providers):
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_PROVIDER_SHAPE")
    attachments = raw.get("附件")
    if not isinstance(attachments, list):
        raise ValueError("KNOWLEDGE_IMPORT_SOURCE_ATTACHMENT_SHAPE")
    return ImportRecord(
        record_id=raw["record_id"], original_name=raw["内容名称"], product_label=single("产品"),
        category_label=single("内容类别"), scope_label=single("使用范围"), source_status=single("状态"),
        provider_name="、".join(p["name"] for p in providers), original_content_text=content,
        links=links, remarks=raw.get("备注") or "",
        attachments=[{"source_token": a["file_token"], "name": a["name"], "size_bytes": a.get("size"),
                      "media_type": a.get("type")} for a in attachments],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("input", "manifest", "base-id", "table-id"):
        parser.add_argument("--" + field, required=True)
    for field in ("output", "workspace-id", "owner-user-id", "grants-file"):
        parser.add_argument("--" + field)
    parser.add_argument("--source-revision", type=int, required=True)
    parser.add_argument("--apply-body", action="store_true", help="Prepare dry_run=false; does not execute it")
    parser.add_argument("--audit-only", action="store_true", help="Only validate source and print aggregate counts")
    args = parser.parse_args()
    raw, source_sha256 = read_source(args.input, args.manifest, base_id=args.base_id,
                                    table_id=args.table_id, source_revision=args.source_revision)
    records = [normalize_record(row) for row in raw]
    summary = {**source_summary(records), "source_sha256": source_sha256,
               "source_revision": args.source_revision, "mode": "audit" if args.audit_only else "prepare"}
    if args.audit_only:
        if args.apply_body or args.output or args.workspace_id or args.owner_user_id or args.grants_file:
            parser.error("--audit-only cannot be combined with destination or output arguments")
        print(json.dumps(summary, ensure_ascii=False))
        return
    if not all((args.output, args.workspace_id, args.owner_user_id, args.grants_file)):
        parser.error("preparing a body requires --output, --workspace-id, --owner-user-id and --grants-file")
    body = KnowledgeImportInput(
        workspace_id=args.workspace_id, owner_user_id=args.owner_user_id,
        base_id=args.base_id, table_id=args.table_id, source_revision=args.source_revision,
        grants=json.loads(Path(args.grants_file).read_text()),
        records=records, dry_run=not args.apply_body,
    )
    contributions = [imported_contribution(body, row) for row in body.records]
    write_private_body(args.output, body)
    print(json.dumps({**summary, "dry_run": body.dry_run,
                      "with_validation_issues": sum(bool(c.source.validation_issues) for c in contributions)},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, ValidationError):
        # Pydantic and JSON errors may embed a private record or signed URL.
        raise SystemExit(
            "KNOWLEDGE_IMPORT_PREPARATION_FAILED: check the private source/manifest and arguments"
        ) from None
