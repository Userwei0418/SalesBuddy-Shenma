"""Validate downloaded Office artifacts without rewriting or executing them.

This is a bounded structural/security check, not Office rendering acceptance.
Package relationships, not guessed filenames, identify the actual main part and
slides. No URL is fetched and no ZIP member is extracted onto the filesystem.
"""

import hashlib
import io
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

ArtifactFormat = Literal["docx", "pptx"]
MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://purl.oclc.org/ooxml/officeDocument/relationships",
)
WORD = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
)
PRESENTATION = (
    "http://schemas.openxmlformats.org/presentationml/2006/main",
    "http://purl.oclc.org/ooxml/presentationml/main",
)
DRAWING = (
    "http://schemas.openxmlformats.org/drawingml/2006/main",
    "http://purl.oclc.org/ooxml/drawingml/main",
)
CHART = (
    "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "http://purl.oclc.org/ooxml/drawingml/chart",
)
SHEET = (
    "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "http://purl.oclc.org/ooxml/spreadsheetml/main",
)
WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WORKBOOK_MAIN_TYPE = WORKBOOK_TYPE + ".main+xml"
WORKSHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
TABLE_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"
MAIN_TYPES = {
    "docx": MIME_TYPES["docx"] + ".main+xml",
    "pptx": MIME_TYPES["pptx"] + ".main+xml",
}
SLIDE_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
CHART_TYPE = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
RELS_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
_UNSAFE_TYPE = re.compile(
    r"macroenabled|macrosheet|dialogsheet|vbaproject|vba[.+/]|activex|oleobject|embeddedpackage|externalLink|connections|queryTable",
    re.I,
)
_UNSAFE_REL = {
    "vbaproject",
    "oleobject",
    "control",
    "activexcontrol",
    "attachedtemplate",
    "afchunk",
    "externallink",
    "externallinkpath",
    "connections",
    "querytable",
}
_UNSAFE_FIELD = re.compile(r"\b(?:DDEAUTO|DDE|INCLUDEPICTURE|INCLUDETEXT|LINK|DATABASE|MACROBUTTON)\b", re.I)
_UNSAFE_FORMULA = re.compile(
    r"\b(?:WEBSERVICE|RTD|CALL|REGISTER(?:\.ID)?|EXEC|EVALUATE|HYPERLINK|IMAGE|DDE|IMPORTXML|IMPORTHTML|IMPORTDATA)\s*\("
    r"|\[|\||(?:https?|file)://",
    re.I,
)
_FORMULA_TOKEN = re.compile(
    r""""(?:[^"]|"")*"|'(?:[^']|'')*'|(?<![\w.\\])(?:(?P<table>[^\W\d][\w.]*|_[\w.]*)\[|(?P<relative>\[))"""
)
_RELATIONSHIPLESS_SLIDE_ACTIONS = {
    "ppaction://noaction",
    *(
        "ppaction://hlinkshowjump?jump=" + target
        for target in ("nextslide", "previousslide", "firstslide", "lastslide", "lastslideviewed", "endshow")
    ),
}


class KnowledgeArtifactError(ValueError):
    """Stable business code; no provider response or filesystem path is exposed."""

    def __init__(self, code: str, message: str):
        self.code = "KNOWLEDGE_ARTIFACT_" + code
        self.message = message
        super().__init__(self.code)


@dataclass(frozen=True)
class ArtifactLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_uncompressed_bytes: int = 80 * 1024 * 1024
    max_entry_bytes: int = 25 * 1024 * 1024
    max_entries: int = 3000
    max_compression_ratio: int = 1000
    max_xml_bytes: int = 8 * 1024 * 1024
    max_total_xml_bytes: int = 32 * 1024 * 1024
    max_xml_elements: int = 250_000
    max_embedded_workbooks: int = 20

    def __post_init__(self):
        if any(not isinstance(value, int) or value < 1 for value in vars(self).values()):
            raise ValueError("Artifact limits must be positive integers")


@dataclass(frozen=True)
class ValidatedKnowledgeArtifact:
    content: bytes
    filename: str
    format: ArtifactFormat
    media_type: str
    sha256: str
    size_bytes: int
    entry_count: int
    uncompressed_bytes: int
    slide_count: int
    editable_text_count: int
    editable_object_count: int
    embedded_workbook_count: int


@dataclass(frozen=True)
class _Relationship:
    kind: str
    target: str
    external: bool


def _fail(code: str, message: str):
    raise KnowledgeArtifactError(code, message)


def _tag(element: Element, namespaces: tuple[str, ...], name: str) -> bool:
    return element.tag in {f"{{{ns}}}{name}" for ns in namespaces}


def _rel_kind(kind: str, suffix: str) -> bool:
    return kind in {ns + "/" + suffix for ns in OFFICE_REL}


def _is_relative_table_reference(reference: str, columns: set[str]) -> bool:
    """Recognize current-table columns only, never a workbook or sheet target."""
    if not columns:
        return False
    inner = reference[1:-1]
    if inner.startswith("@[") and inner.endswith("]"):
        inner = inner[2:-1]
    elif inner.startswith("@"):
        inner = inner[1:]
    else:
        selector = re.fullmatch(r"\[(?:#All|#Data|#Headers|#Totals|#This Row)\],\[(.*)\]", inner, re.I)
        if selector:
            inner = selector.group(1)
    # Excel escapes special header characters with an apostrophe.
    inner = re.sub(r"'([\[\]#@'])", r"\1", inner)
    return inner.casefold() in columns


def _without_local_table_references(
    formula: str, table_names: set[str], *, relative_columns: set[str] | None = None
) -> str:
    """Mask genuine local structured references, retaining external brackets.

    Quoted sheet/workbook names are consumed separately. A bracket in a local
    table's column name is not an external workbook reference. Unknown tables,
    malformed references and suffixes that denote external sheets stay intact
    for the conservative external-data check below.
    """
    output, cursor = [], 0
    for match in _FORMULA_TOKEN.finditer(formula):
        is_relative = match.group("relative") is not None
        if match.start() < cursor:
            continue
        if is_relative and not relative_columns:
            continue
        if not is_relative and (match.group("table") or "").casefold() not in table_names:
            continue
        end, depth = match.end() - 1, 0
        while end < len(formula):
            char = formula[end]
            # Excel uses apostrophes to escape special characters in headers.
            if char == "'" and end + 1 < len(formula) and formula[end + 1] in "[]#@'":
                end += 2
                continue
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        if depth or (end < len(formula) and (formula[end].isalnum() or formula[end] in "_'!\\")):
            continue
        if is_relative and not _is_relative_table_reference(formula[match.start() : end], relative_columns):
            continue
        output.extend((formula[cursor : match.start()], "LOCAL_TABLE_REFERENCE"))
        cursor = end
    output.append(formula[cursor:])
    return "".join(output)


def _member_name(name: str) -> str:
    decoded = unquote(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in decoded
        or ":" in decoded
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        or any(p in {"", ".", ".."} for p in decoded.rstrip("/").split("/"))
        or re.search(r"%[0-9a-f]{2}", decoded, re.I)
    ):
        _fail("UNSAFE_PACKAGE", "文件包包含不安全或有歧义的路径")
    return decoded


def _internal_target(source: str, target: str, *, hyperlink=False) -> str:
    decoded = unquote(target)
    try:
        parsed = urlsplit(decoded)
    except ValueError as exc:
        raise KnowledgeArtifactError("UNSAFE_RELATIONSHIP", "文件关系地址无效") from exc
    if (
        not decoded
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or (parsed.fragment and not hyperlink)
        or "\\" in decoded
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        or re.search(r"%[0-9a-f]{2}", decoded, re.I)
    ):
        _fail("UNSAFE_RELATIONSHIP", "文件包含不安全的内部关系")
    path = parsed.path
    resolved = (
        source
        if not path and parsed.fragment
        else posixpath.normpath(posixpath.join(posixpath.dirname(source), path.lstrip("/")))
    )
    if path.startswith("/"):
        resolved = posixpath.normpath(path.lstrip("/"))
    if resolved == ".." or resolved.startswith("../"):
        _fail("UNSAFE_RELATIONSHIP", "文件关系越出当前文件包")
    return _member_name(resolved)


def _read_package(
    content: bytes, limits: ArtifactLimits, *, allow_workbooks=False
) -> tuple[dict[str, bytes], int, int]:
    if not content.startswith(b"PK\x03\x04"):
        _fail("INVALID_PACKAGE", "下载内容不是有效的 Office 文件包")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            entries = package.infolist()
            if len(entries) > limits.max_entries:
                _fail("LIMIT_EXCEEDED", "文件包条目过多")
            if sum(entry.file_size for entry in entries) > limits.max_uncompressed_bytes:
                _fail("LIMIT_EXCEEDED", "文件解压总大小超过限制")
            parts, seen, expanded = {}, set(), 0
            for entry in entries:
                name = _member_name(entry.orig_filename)
                if name.casefold() in seen or entry.filename != entry.orig_filename:
                    _fail("UNSAFE_PACKAGE", "文件包包含重复或有歧义的条目")
                seen.add(name.casefold())
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                    _fail("UNSAFE_PACKAGE", "文件包不能包含链接、设备或加密条目")
                if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    _fail("UNSAFE_PACKAGE", "文件包使用了不支持的压缩方式")
                if entry.file_size > limits.max_entry_bytes or (
                    entry.file_size > max(1, entry.compress_size) * limits.max_compression_ratio
                ):
                    _fail("LIMIT_EXCEEDED", "文件包单项大小或压缩比例超过限制")
                if entry.is_dir():
                    continue
                lower = name.lower()
                workbook_candidate = allow_workbooks and lower.endswith(".xlsx")
                if (
                    any(p in {"embeddings", "activex"} for p in lower.split("/")) and not workbook_candidate
                ) or re.search(r"(?:vbaproject|vbasignature)|\.(?:exe|dll|js|vbs|hta|bat|cmd|ps1|com|scr)$", lower):
                    _fail("UNSAFE_CONTENT", "生成文件不能包含宏、程序或嵌入对象")
                with package.open(entry) as stream:
                    data = stream.read(limits.max_entry_bytes + 1)
                expanded += len(data)
                if len(data) != entry.file_size or expanded > limits.max_uncompressed_bytes:
                    _fail("LIMIT_EXCEEDED", "文件包解压大小不可信或超过限制")
                if data.startswith((b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"MZ")):
                    _fail("UNSAFE_CONTENT", "生成文件不能包含嵌入的可执行或 OLE 对象")
                if data.startswith(b"PK") and not workbook_candidate:
                    _fail("UNSAFE_CONTENT", "文件中不能包含未经验证的嵌套文件包")
                parts[name] = data
            return parts, len(entries), expanded
    except KnowledgeArtifactError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, NotImplementedError) as exc:
        raise KnowledgeArtifactError("INVALID_PACKAGE", "文件包损坏或无法完整读取") from exc


def _content_types(parts: dict[str, bytes], roots: dict[str, Element]) -> dict[str, str]:
    root = roots.get("[Content_Types].xml")
    if root is None or root.tag != f"{{{CONTENT_TYPES}}}Types":
        _fail("INVALID_STRUCTURE", "缺少有效的 Office 内容类型声明")
    defaults, overrides = {}, {}
    for child in root:
        content_type = child.get("ContentType", "")
        if not content_type or _UNSAFE_TYPE.search(content_type):
            _fail("UNSAFE_CONTENT", "文件包含宏或嵌入对象的内容类型")
        if child.tag == f"{{{CONTENT_TYPES}}}Default":
            extension = child.get("Extension", "").lower()
            if not extension or "/" in extension or extension in defaults:
                _fail("INVALID_STRUCTURE", "文件内容类型声明重复或不完整")
            defaults[extension] = content_type
        elif child.tag == f"{{{CONTENT_TYPES}}}Override":
            name = child.get("PartName", "")
            if not name.startswith("/"):
                _fail("INVALID_STRUCTURE", "文件内容类型路径无效")
            name = _member_name(name[1:])
            if name not in parts or name in overrides:
                _fail("INVALID_STRUCTURE", "文件内容类型指向缺失或重复的部件")
            overrides[name] = content_type
        else:
            _fail("INVALID_STRUCTURE", "文件内容类型声明无效")
    types = {}
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        content_type = overrides.get(name, defaults.get(name.rsplit(".", 1)[-1].lower()))
        if not content_type:
            _fail("INVALID_STRUCTURE", "文件部件缺少内容类型声明")
        types[name] = content_type
    return types


def _xml_roots(parts: dict[str, bytes], limits: ArtifactLimits, types=None) -> dict[str, Element]:
    roots, size, elements = {}, 0, 0
    for name, data in parts.items():
        content_type = (types or {}).get(name, "").lower()
        if not name.lower().endswith((".xml", ".rels")) and not content_type.endswith(("+xml", "/xml")):
            continue
        size += len(data)
        if len(data) > limits.max_xml_bytes or size > limits.max_total_xml_bytes:
            _fail("LIMIT_EXCEEDED", "文件 XML 内容超过限制")
        try:
            root = ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        except (ParseError, DefusedXmlException, ValueError) as exc:
            raise KnowledgeArtifactError("INVALID_XML", "文件 XML 损坏或包含不安全的实体声明") from exc
        instructions = []
        for node in root.iter():
            elements += 1
            if elements > limits.max_xml_elements:
                _fail("LIMIT_EXCEEDED", "文件 XML 节点过多")
            if node.tag.rsplit("}", 1)[-1].lower() in {"oleobject", "altchunk", "control", "script", "foreignobject"}:
                _fail("UNSAFE_CONTENT", "文件包含主动内容或嵌入对象")
            if _tag(node, WORD, "instrText"):
                instructions.append(node.text or "")
            if _tag(node, CHART, "f") and _UNSAFE_FORMULA.search(node.text or ""):
                _fail("UNSAFE_CONTENT", "图表不能引用外部数据或主动公式")
            for key, value in node.attrib.items():
                if key in {f"{{{ns}}}instr" for ns in WORD}:
                    instructions.append(value)
                if (
                    key == "action"
                    and value
                    and not re.fullmatch(
                        r"ppaction://(?:hlinksldjump|hlinkshowjump|customshow|noaction)(?:\?[^\r\n]*)?", value, re.I
                    )
                ):
                    _fail("UNSAFE_CONTENT", "幻灯片包含执行动作")
                if node.tag.startswith("{http://www.w3.org/2000/svg}") and key.rsplit("}", 1)[-1] == "href":
                    if not value.startswith("#"):
                        _fail("UNSAFE_CONTENT", "文件中的矢量图不能加载外部资源")
            if node.tag.startswith("{http://www.w3.org/2000/svg}"):
                css = " ".join([node.text or "", *node.attrib.values()])
                if "@import" in css.lower() or any(
                    not url.strip().strip("\"'").startswith("#") for url in re.findall(r"url\((.*?)\)", css, re.I)
                ):
                    _fail("UNSAFE_CONTENT", "文件中的矢量图样式不能加载外部资源")
        # Instructions may be split across multiple runs; inspect the combined
        # text as well as fields, while allowing PAGE, TOC and navigation fields.
        if _UNSAFE_FIELD.search("".join(instructions)) or any(_UNSAFE_FIELD.search(s) for s in instructions):
            _fail("UNSAFE_CONTENT", "文件包含会自动加载外部内容的域")
        roots[name] = root
    return roots


def _relationships(parts, roots, types, *, allow_workbooks=False) -> dict[str, dict[str, _Relationship]]:
    relations = {}
    for name, root in roots.items():
        if not name.endswith(".rels"):
            continue
        if types.get(name) != RELS_TYPE or root.tag != f"{{{PACKAGE_REL}}}Relationships":
            _fail("INVALID_RELATIONSHIP", "文件关系声明无效")
        if name == "_rels/.rels":
            source = ""
        elif posixpath.basename(posixpath.dirname(name)) == "_rels":
            source = posixpath.join(posixpath.dirname(posixpath.dirname(name)), posixpath.basename(name)[:-5])
            if source not in parts:
                _fail("INVALID_RELATIONSHIP", "文件关系所属部件不存在")
        else:
            _fail("INVALID_RELATIONSHIP", "文件关系部件位置无效")
        rows = {}
        for node in root:
            identifier, kind, target = node.get("Id", ""), node.get("Type", ""), node.get("Target", "")
            mode = node.get("TargetMode", "Internal")
            if node.tag != f"{{{PACKAGE_REL}}}Relationship" or not identifier or identifier in rows or not kind:
                _fail("INVALID_RELATIONSHIP", "文件关系标识缺失或重复")
            if mode not in {"Internal", "External"}:
                _fail("INVALID_RELATIONSHIP", "文件关系模式无效")
            if kind.rsplit("/", 1)[-1].lower() in _UNSAFE_REL:
                _fail("UNSAFE_CONTENT", "文件包含宏、嵌入对象或自动加载关系")
            if mode == "External":
                try:
                    url = urlsplit(target)
                except ValueError as exc:
                    raise KnowledgeArtifactError("UNSAFE_RELATIONSHIP", "文件外部链接地址无效") from exc
                if (
                    not _rel_kind(kind, "hyperlink")
                    or url.scheme.lower() != "https"
                    or not url.hostname
                    or url.username
                    or url.password
                    or any(c.isspace() or ord(c) < 32 for c in target)
                ):
                    _fail("UNSAFE_RELATIONSHIP", "只允许需要用户点击的 HTTPS 超链接")
            else:
                target = _internal_target(source, target, hyperlink=_rel_kind(kind, "hyperlink"))
                if target not in parts:
                    _fail("INVALID_RELATIONSHIP", "文件关系指向不存在的部件")
            if kind.rsplit("/", 1)[-1].lower() == "package" and not (
                allow_workbooks
                and _rel_kind(kind, "package")
                and mode == "Internal"
                and types.get(source) == CHART_TYPE
                and types.get(target) == WORKBOOK_TYPE
                and target.lower().endswith(".xlsx")
                and any(
                    _tag(node, CHART, "externalData") and node.get(f"{{{ns}}}id") == identifier
                    for node in roots[source].iter()
                    for ns in OFFICE_REL
                )
            ):
                _fail("UNSAFE_CONTENT", "仅允许原生图表引用经过验证的 XLSX 数据工作簿")
            rows[identifier] = _Relationship(kind, target, mode == "External")
        relations[source] = rows
    for name, root in roots.items():
        for node in root.iter():
            for key, identifier in node.attrib.items():
                if any(key in {f"{{{ns}}}id", f"{{{ns}}}embed", f"{{{ns}}}link"} for ns in OFFICE_REL):
                    if (
                        identifier == ""
                        and key in {f"{{{ns}}}id" for ns in OFFICE_REL}
                        and (_tag(node, DRAWING, "hlinkClick") or _tag(node, DRAWING, "hlinkMouseOver"))
                        and node.get("action", "").lower() in _RELATIONSHIPLESS_SLIDE_ACTIONS
                    ):
                        # Standard next/previous slide actions deliberately use
                        # an empty r:id: the target is the slideshow, not a part.
                        continue
                    relation = relations.get(name, {}).get(identifier)
                    if relation is None:
                        _fail("INVALID_RELATIONSHIP", "文件内容引用了不存在的关系")
                    if relation.external and not (
                        key in {f"{{{ns}}}id" for ns in OFFICE_REL}
                        and (_tag(node, WORD, "hyperlink") or _tag(node, DRAWING, "hlinkClick"))
                    ):
                        _fail("UNSAFE_RELATIONSHIP", "外部超链接不能用作自动加载的图片或内容")
                    if _rel_kind(relation.kind, "package") and not _tag(node, CHART, "externalData"):
                        _fail("UNSAFE_CONTENT", "嵌入工作簿只能用作原生图表数据")
    return relations


def _validate_embedded_workbook(data: bytes, limits: ArtifactLimits):
    """Accept only non-macro, local-data XLSX; recursively embedded packages fail."""
    parts, entries, expanded = _read_package(data, limits)
    types = _content_types(parts, _xml_roots({k: v for k, v in parts.items() if k == "[Content_Types].xml"}, limits))
    roots = _xml_roots(parts, limits, types)
    relationships = _relationships(parts, roots, types)
    mains = [r for r in relationships.get("", {}).values() if _rel_kind(r.kind, "officeDocument")]
    if len(mains) != 1 or mains[0].external or types.get(mains[0].target) != WORKBOOK_MAIN_TYPE:
        _fail("INVALID_STRUCTURE", "图表数据不是有效的 XLSX 工作簿")
    main = mains[0].target
    workbook = roots.get(main)
    if workbook is None or not _tag(workbook, SHEET, "workbook"):
        _fail("INVALID_STRUCTURE", "图表数据缺少有效的工作簿部件")
    sheets = [sheet for group in workbook if _tag(group, SHEET, "sheets") for sheet in group]
    if not sheets:
        _fail("INVALID_STRUCTURE", "图表数据工作簿缺少工作表")
    for sheet in sheets:
        ref = next((sheet.get(f"{{{ns}}}id") for ns in OFFICE_REL if sheet.get(f"{{{ns}}}id")), None)
        relation = relationships.get(main, {}).get(ref)
        root = roots.get(relation.target) if relation else None
        if (
            not _tag(sheet, SHEET, "sheet")
            or relation is None
            or relation.external
            or not _rel_kind(relation.kind, "worksheet")
            or types.get(relation.target) != WORKSHEET_TYPE
            or root is None
            or not _tag(root, SHEET, "worksheet")
        ):
            _fail("INVALID_STRUCTURE", "图表数据工作表关系无效")
    local_tables = {
        reference.target: table
        for source, references in relationships.items()
        if source in roots and _tag(roots[source], SHEET, "worksheet")
        for identifier, reference in references.items()
        if not reference.external and _rel_kind(reference.kind, "table")
        if types.get(reference.target) == TABLE_TYPE
        if (table := roots.get(reference.target)) is not None and _tag(table, SHEET, "table")
        if any(
            _tag(node, SHEET, "tablePart") and any(node.get(f"{{{ns}}}id") == identifier for ns in OFFICE_REL)
            for node in roots[source].iter()
        )
    }
    table_names = {
        table.get(attribute, "").casefold()
        for table in local_tables.values()
        for attribute in ("name", "displayName")
        if table.get(attribute)
    }
    for part_name, root in roots.items():
        columns = (
            {
                node.get("name").casefold()
                for group in root
                if _tag(group, SHEET, "tableColumns")
                for node in group
                if _tag(node, SHEET, "tableColumn") and node.get("name")
            }
            if part_name in local_tables
            else set()
        )
        for node in root.iter():
            table_formula = any(_tag(node, SHEET, name) for name in ("calculatedColumnFormula", "totalsRowFormula"))
            if _tag(node, SHEET, "externalReference") or (
                (_tag(node, SHEET, "f") or _tag(node, SHEET, "definedName") or table_formula)
                and _UNSAFE_FORMULA.search(
                    _without_local_table_references(
                        "".join(node.itertext()), table_names, relative_columns=columns if table_formula else None
                    )
                )
            ):
                _fail("UNSAFE_CONTENT", "图表工作簿不能加载外部数据或执行主动公式")
            if _tag(node, SHEET, "definedName") and node.get("name", "").lower().split(".")[-1] in {
                "auto_open",
                "auto_close",
                "auto_activate",
                "auto_deactivate",
            }:
                _fail("UNSAFE_CONTENT", "图表工作簿不能包含自动执行名称")
    return entries, expanded, sum(len(parts[name]) for name in roots), sum(1 for r in roots.values() for _ in r.iter())


def _embedded_workbooks(parts, roots, types, relations, main, limits, entries, expanded):
    reachable, pending = set(), [main]
    while pending:
        source = pending.pop()
        if source in reachable:
            continue
        reachable.add(source)
        pending.extend(r.target for r in relations.get(source, {}).values() if not r.external)
    allowed = {
        r.target for source in reachable for r in relations.get(source, {}).values() if _rel_kind(r.kind, "package")
    }
    candidates = {name for name in parts if name.lower().endswith(".xlsx")}
    if candidates != allowed:
        _fail("UNSAFE_CONTENT", "文件包含未由实际图表引用的嵌入工作簿")
    if len(allowed) > limits.max_embedded_workbooks:
        _fail("LIMIT_EXCEEDED", "图表数据工作簿数量超过限制")
    xml_bytes = sum(len(parts[name]) for name in roots)
    xml_elements = sum(1 for root in roots.values() for _ in root.iter())
    for name in sorted(allowed):
        if (
            entries >= limits.max_entries
            or expanded >= limits.max_uncompressed_bytes
            or xml_bytes >= limits.max_total_xml_bytes
            or xml_elements >= limits.max_xml_elements
        ):
            _fail("LIMIT_EXCEEDED", "包括图表工作簿的文件总量超过限制")
        remaining = replace(
            limits,
            max_entries=limits.max_entries - entries,
            max_uncompressed_bytes=limits.max_uncompressed_bytes - expanded,
            max_total_xml_bytes=limits.max_total_xml_bytes - xml_bytes,
            max_xml_elements=limits.max_xml_elements - xml_elements,
        )
        more_entries, more_bytes, more_xml, more_elements = _validate_embedded_workbook(parts[name], remaining)
        entries += more_entries
        expanded += more_bytes
        xml_bytes += more_xml
        xml_elements += more_elements
    return len(allowed), entries, expanded


def _editable_content(main, roots, types, relations, expected_format):
    if expected_format == "docx":
        root = roots.get(main)
        if root is None or not _tag(root, WORD, "document"):
            _fail("INVALID_STRUCTURE", "DOCX 缺少有效的正文部件")
        bodies = [n for n in root if _tag(n, WORD, "body")]
        if len(bodies) != 1:
            _fail("INVALID_STRUCTURE", "DOCX 缺少唯一的正文区域")
        text_count = sum(bool((n.text or "").strip()) for n in bodies[0].iter() if _tag(n, WORD, "t"))
        if not text_count:
            _fail("NOT_EDITABLE", "DOCX 没有可编辑的正文文字")
        return 0, text_count, 0
    root = roots.get(main)
    if root is None or not _tag(root, PRESENTATION, "presentation"):
        _fail("INVALID_STRUCTURE", "PPTX 缺少有效的演示文稿部件")
    identifiers = [n for group in root if _tag(group, PRESENTATION, "sldIdLst") for n in group]
    seen, numeric_ids, slides, text_count, object_count = set(), set(), [], 0, 0
    for node in identifiers:
        identifier = next((node.get(f"{{{ns}}}id") for ns in OFFICE_REL if node.get(f"{{{ns}}}id")), None)
        rel = relations.get(main, {}).get(identifier)
        if (
            not _tag(node, PRESENTATION, "sldId")
            or rel is None
            or rel.external
            or not _rel_kind(rel.kind, "slide")
            or rel.target in seen
            or types.get(rel.target) != SLIDE_TYPE
            or not node.get("id", "").isdigit()
            or not 256 <= int(node.get("id")) <= 2147483647
            or node.get("id") in numeric_ids
        ):
            _fail("INVALID_STRUCTURE", "PPTX 幻灯片清单或关系无效")
        slide = roots.get(rel.target)
        if slide is None or not _tag(slide, PRESENTATION, "sld"):
            _fail("INVALID_STRUCTURE", "PPTX 幻灯片部件无效")
        seen.add(rel.target)
        numeric_ids.add(node.get("id"))
        slides.append(rel.target)
        # Only slide shapes/tables contribute; notes/master text cannot turn
        # a screenshot-only presentation into an editable result.
        trees = [n for n in slide.iter() if _tag(n, PRESENTATION, "spTree")]
        if len(trees) != 1:
            _fail("INVALID_STRUCTURE", "PPTX 幻灯片缺少有效的对象树")
        for item in trees[0].iter():
            if _tag(item, PRESENTATION, "sp"):
                text_count += sum(
                    bool((text.text or "").strip())
                    for body in item
                    if _tag(body, PRESENTATION, "txBody")
                    for text in body.iter()
                    if _tag(text, DRAWING, "t")
                )
            if _tag(item, DRAWING, "tbl") and any(_tag(cell, DRAWING, "tc") for cell in item.iter()):
                object_count += 1
                text_count += sum(bool((text.text or "").strip()) for text in item.iter() if _tag(text, DRAWING, "t"))
            if _tag(item, CHART, "chart"):
                ref = next((item.get(f"{{{ns}}}id") for ns in OFFICE_REL if item.get(f"{{{ns}}}id")), None)
                chart_rel = relations.get(rel.target, {}).get(ref)
                chart = roots.get(chart_rel.target) if chart_rel else None
                if (
                    chart_rel is None
                    or chart_rel.external
                    or not _rel_kind(chart_rel.kind, "chart")
                    or types.get(chart_rel.target) != CHART_TYPE
                    or chart is None
                    or not _tag(chart, CHART, "chartSpace")
                ):
                    _fail("INVALID_STRUCTURE", "PPTX 图表没有有效的原生图表部件")
                local_workbook = any(_tag(node, CHART, "externalData") for node in chart.iter())
                if any(
                    (value.text or "").strip()
                    for series in chart.iter()
                    if _tag(series, CHART, "ser")
                    for value in series.iter()
                    if _tag(value, CHART, "v") or (local_workbook and _tag(value, CHART, "f"))
                ):
                    object_count += 1
    if not slides:
        _fail("INVALID_STRUCTURE", "PPTX 不包含幻灯片")
    if not text_count and not object_count:
        _fail("NOT_EDITABLE", "PPTX 只有图片或空白内容，没有可编辑文字、表格或图表")
    return len(slides), text_count, object_count


def validate_knowledge_artifact(
    content: bytes,
    *,
    filename: str,
    media_type: str,
    expected_format: ArtifactFormat,
    limits: ArtifactLimits = ArtifactLimits(),
) -> ValidatedKnowledgeArtifact:
    """Return original validated bytes, or a stable ``KNOWLEDGE_ARTIFACT_*`` error.

    The MIME must be the concrete DOCX/PPTX type, not a guessed generic binary
    type. A successful result proves structure and editable objects, not that
    Word/PowerPoint/LibreOffice actually opened or rendered this external file.
    """
    if expected_format not in MIME_TYPES:
        _fail("FORMAT_UNSUPPORTED", "仅支持 DOCX 和 PPTX 文件")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 300
        or "/" in filename
        or "\\" in filename
        or any(ord(c) < 32 or ord(c) == 127 for c in filename)
        or not filename.lower().endswith("." + expected_format)
        or not isinstance(media_type, str)
        or media_type.strip().lower() != MIME_TYPES[expected_format]
    ):
        _fail("FORMAT_MISMATCH", "文件名、声明格式与 MIME 类型不一致")
    if not isinstance(content, bytes) or not content or len(content) > limits.max_bytes:
        _fail("LIMIT_EXCEEDED", "文件为空或下载大小超过限制")
    parts, count, expanded = _read_package(content, limits, allow_workbooks=expected_format == "pptx")
    content_types_part = {name: data for name, data in parts.items() if name == "[Content_Types].xml"}
    types = _content_types(parts, _xml_roots(content_types_part, limits))
    roots = _xml_roots(parts, limits, types)
    relations = _relationships(parts, roots, types, allow_workbooks=expected_format == "pptx")
    main_rels = [r for r in relations.get("", {}).values() if _rel_kind(r.kind, "officeDocument")]
    if len(main_rels) != 1 or main_rels[0].external:
        _fail("INVALID_STRUCTURE", "缺少唯一的 Office 主文档关系")
    main = main_rels[0].target
    if types.get(main) != MAIN_TYPES[expected_format]:
        _fail("FORMAT_MISMATCH", "文件实际内容与请求的 Office 格式不一致")
    slides, texts, objects = _editable_content(main, roots, types, relations, expected_format)
    workbooks, count, expanded = _embedded_workbooks(parts, roots, types, relations, main, limits, count, expanded)
    return ValidatedKnowledgeArtifact(
        content=content,
        filename=filename,
        format=expected_format,
        media_type=MIME_TYPES[expected_format],
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        entry_count=count,
        uncompressed_bytes=expanded,
        slide_count=slides,
        editable_text_count=texts,
        editable_object_count=objects,
        embedded_workbook_count=workbooks,
    )
