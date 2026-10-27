"""Minimal structural fixtures, not real provider or Office rendering acceptance."""

import hashlib
import io
import stat
import warnings
import zipfile
from dataclasses import replace
from xml.sax.saxutils import escape

import pytest
from sales_backend.services.knowledge_artifacts import (
    CHART_TYPE,
    CONTENT_TYPES,
    MAIN_TYPES,
    MIME_TYPES,
    OFFICE_REL,
    PACKAGE_REL,
    RELS_TYPE,
    SLIDE_TYPE,
    TABLE_TYPE,
    WORKBOOK_MAIN_TYPE,
    WORKBOOK_TYPE,
    WORKSHEET_TYPE,
    ArtifactLimits,
    KnowledgeArtifactError,
    validate_knowledge_artifact,
)

R = OFFICE_REL[0]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def rels(*entries):
    return (
        f'<Relationships xmlns="{PACKAGE_REL}">'
        + "".join(
            f'<Relationship Id="{i}" Type="{R}/{kind}" Target="{target}" {extra}/>'
            for i, kind, target, extra in entries
        )
        + "</Relationships>"
    )


def types(overrides):
    return (
        f'<Types xmlns="{CONTENT_TYPES}"><Default Extension="rels" ContentType="{RELS_TYPE}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        + "".join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in overrides.items())
        + "</Types>"
    )


def docx(body="<w:p><w:r><w:t>可编辑正文</w:t></w:r></w:p>"):
    return {
        "[Content_Types].xml": types({"word/document.xml": MAIN_TYPES["docx"]}),
        "_rels/.rels": rels(("rId1", "officeDocument", "word/document.xml", "")),
        "word/document.xml": f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}</w:body></w:document>',
    }


def slide(body):
    return (
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:c="{C}">'
        f"<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
    )


def text_shape(text="可编辑幻灯片文字"):
    return f"<p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"


def pptx(*bodies):
    bodies = bodies or (text_shape(),)
    slides = {f"ppt/slides/slide{i}.xml": slide(body) for i, body in enumerate(bodies, 1)}
    return {
        "[Content_Types].xml": types({"ppt/presentation.xml": MAIN_TYPES["pptx"], **dict.fromkeys(slides, SLIDE_TYPE)}),
        "_rels/.rels": rels(("rId1", "officeDocument", "ppt/presentation.xml", "")),
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
        + "".join(f'<p:sldId id="{255 + i}" r:id="s{i}"/>' for i in range(1, len(bodies) + 1))
        + "</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": rels(
            *((f"s{i}", "slide", f"slides/slide{i}.xml", "") for i in range(1, len(bodies) + 1))
        ),
        **slides,
    }


def package(parts, *, extra=(), compression=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(out, "w", compression=compression) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, data in [*parts.items(), *extra]:
            archive.writestr(name, data)
    return out.getvalue()


def check(parts, fmt="docx", **kwargs):
    return validate_knowledge_artifact(
        package(parts), filename=f"真实产物.{fmt}", media_type=MIME_TYPES[fmt], expected_format=fmt, **kwargs
    )


def rejects(parts, suffix, fmt="docx", **kwargs):
    with pytest.raises(KnowledgeArtifactError) as error:
        check(parts, fmt, **kwargs)
    assert error.value.code == "KNOWLEDGE_ARTIFACT_" + suffix
    assert error.value.message


@pytest.mark.parametrize("fmt,builder", [("docx", docx), ("pptx", pptx)])
def test_accepts_real_zip_structure_and_preserves_original_bytes(fmt, builder):
    blob = package(builder())
    result = validate_knowledge_artifact(
        blob, filename=f"生成资料.{fmt}", media_type=MIME_TYPES[fmt], expected_format=fmt
    )
    assert result.content is blob
    assert result.sha256 == hashlib.sha256(blob).hexdigest()
    assert result.size_bytes == len(blob) and result.uncompressed_bytes > 0
    assert result.editable_text_count == 1
    assert result.slide_count == (1 if fmt == "pptx" else 0)


def test_docx_table_text_and_normal_page_toc_hyperlink_fields_remain_allowed():
    parts = docx(
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格文字</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        '<w:p><w:fldSimple w:instr="PAGE"/><w:r><w:instrText> TOC \\o 1-3 </w:instrText></w:r>'
        '<w:hyperlink r:id="safe"><w:r><w:t>查看参考</w:t></w:r></w:hyperlink></w:p>'
    )
    parts["word/_rels/document.xml.rels"] = rels(
        ("safe", "hyperlink", "https://example.test/reference#section", 'TargetMode="External"')
    )
    assert check(parts).editable_text_count == 2


def test_relative_and_absolute_internal_relationships_resolve_without_extraction():
    parts = docx()
    parts["_rels/.rels"] = rels(("rId1", "officeDocument", "/word/document.xml", ""))
    parts["word/_rels/document.xml.rels"] = rels(("img", "image", "../media/image.png", ""))
    parts["media/image.png"] = b"\x89PNG\r\n\x1a\nfixture"
    assert check(parts).format == "docx"


def test_pptx_allows_image_cover_but_requires_editable_content_somewhere():
    parts = pptx("<p:pic/>", text_shape())
    assert check(parts, "pptx").slide_count == 2
    rejects(pptx("<p:pic/>", "<p:pic/>"), "NOT_EDITABLE", "pptx")


def test_pptx_notes_and_master_text_do_not_disguise_screenshot_only_slides():
    parts = pptx("<p:pic/>")
    parts["ppt/notesSlides/notesSlide1.xml"] = slide(text_shape("只在备注中的文字"))
    rejects(parts, "NOT_EDITABLE", "pptx")


def test_pptx_native_table_is_editable_without_text():
    body = (
        "<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody/>"
        "</a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    result = check(pptx(body), "pptx")
    assert result.editable_object_count == 1 and result.editable_text_count == 0


def test_pptx_native_chart_requires_actual_series_and_matching_chart_relationship():
    parts = pptx(
        '<p:graphicFrame><a:graphic><a:graphicData><c:chart r:id="chart"/></a:graphicData></a:graphic></p:graphicFrame>'
    )
    parts["ppt/slides/_rels/slide1.xml.rels"] = rels(("chart", "chart", "../charts/chart1.xml", ""))
    parts["ppt/charts/chart1.xml"] = (
        f'<c:chartSpace xmlns:c="{C}"><c:chart><c:plotArea><c:barChart><c:ser>'
        '<c:val><c:numLit><c:pt idx="0"><c:v>10</c:v></c:pt></c:numLit></c:val>'
        "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
    )
    parts["[Content_Types].xml"] = types(
        {
            "ppt/presentation.xml": MAIN_TYPES["pptx"],
            "ppt/slides/slide1.xml": SLIDE_TYPE,
            "ppt/charts/chart1.xml": CHART_TYPE,
        }
    )
    assert check(parts, "pptx").editable_object_count == 1
    parts["ppt/charts/chart1.xml"] = f'<c:chartSpace xmlns:c="{C}"/>'
    rejects(parts, "NOT_EDITABLE", "pptx")


def test_safe_https_click_and_internal_slide_navigation_preserved():
    parts = pptx(
        text_shape()
        + '<p:sp><p:nvSpPr><p:cNvPr><a:hlinkClick r:id="next" action="ppaction://hlinksldjump"/></p:cNvPr></p:nvSpPr></p:sp>',
        text_shape(),
    )
    parts["ppt/slides/_rels/slide1.xml.rels"] = rels(("next", "slide", "slide2.xml", ""))
    assert check(parts, "pptx").slide_count == 2


@pytest.mark.parametrize("tag", ["hlinkClick", "hlinkMouseOver"])
@pytest.mark.parametrize(
    "destination", ["nextslide", "previousslide", "firstslide", "lastslide", "lastslideviewed", "endshow"]
)
def test_standard_show_navigation_uses_empty_relationship_id(tag, destination):
    parts = pptx(
        text_shape() + "<p:sp><p:nvSpPr><p:cNvPr>"
        f'<a:{tag} r:id="" action="ppaction://hlinkshowjump?jump={destination}"/>'
        "</p:cNvPr></p:nvSpPr></p:sp>",
        text_shape(),
    )
    assert check(parts, "pptx").slide_count == 2


@pytest.mark.parametrize(
    "node",
    [
        '<a:hlinkClick r:id=""/>',
        '<a:hlinkClick r:id="" action="ppaction://hlinksldjump"/>',
        '<a:hlinkClick r:id="missing" action="ppaction://hlinkshowjump?jump=nextslide"/>',
        '<a:blip r:embed="" action="ppaction://hlinkshowjump?jump=nextslide"/>',
        '<a:hlinkClick r:id="" action="ppaction://hlinkshowjump?jump=nextslide&amp;url=https://example.test"/>',
    ],
)
def test_empty_id_exception_does_not_hide_missing_targets_or_external_content(node):
    rejects(pptx(text_shape() + node), "INVALID_RELATIONSHIP", "pptx")


@pytest.mark.parametrize(
    "filename,mime,fmt",
    [
        ("fake.pptx", MIME_TYPES["docx"], "docx"),
        ("fake.docx", MIME_TYPES["pptx"], "docx"),
        ("fake.docx", "application/octet-stream", "docx"),
        ("../fake.docx", MIME_TYPES["docx"], "docx"),
    ],
)
def test_filename_mime_and_requested_format_must_agree(filename, mime, fmt):
    with pytest.raises(KnowledgeArtifactError, match="FORMAT_MISMATCH"):
        validate_knowledge_artifact(package(docx()), filename=filename, media_type=mime, expected_format=fmt)


def test_renaming_real_docx_to_pptx_does_not_make_presentation():
    rejects(docx(), "FORMAT_MISMATCH", "pptx")


@pytest.mark.parametrize("blob", [b"<html>error</html>", b"plain model response", b"PK\x03\x04broken"])
def test_non_office_or_broken_zip_rejected(blob):
    with pytest.raises(KnowledgeArtifactError, match="INVALID_PACKAGE"):
        validate_knowledge_artifact(blob, filename="file.docx", media_type=MIME_TYPES["docx"], expected_format="docx")


@pytest.mark.parametrize("name", ["[Content_Types].xml", "_rels/.rels", "word/document.xml"])
def test_required_part_missing_rejected(name):
    parts = docx()
    del parts[name]
    with pytest.raises(KnowledgeArtifactError):
        check(parts)


def test_main_part_must_be_real_document_not_arbitrary_xml():
    parts = docx()
    parts["word/document.xml"] = "<fake><body>text</body></fake>"
    rejects(parts, "INVALID_STRUCTURE")
    rejects(docx(""), "NOT_EDITABLE")


def test_broken_or_entity_expanding_xml_rejected():
    for xml in ["<broken>", '<!DOCTYPE x [<!ENTITY a "expanded">]><x>&a;</x>']:
        parts = docx()
        parts["word/document.xml"] = xml
        rejects(parts, "INVALID_XML")


@pytest.mark.parametrize("filename", ["../outside.xml", "/absolute.xml", "word\\evil.xml", "a/%2e%2e/b.xml"])
def test_unsafe_archive_paths_rejected(filename):
    parts = {**docx(), filename: "<x/>"}
    rejects(parts, "UNSAFE_PACKAGE")


def test_duplicate_case_collision_and_symlink_zip_members_rejected():
    for extra in [[("word/document.xml", "<x/>")], [("WORD/document.xml", "<x/>")]]:
        with pytest.raises(KnowledgeArtifactError, match="UNSAFE_PACKAGE"):
            validate_knowledge_artifact(
                package(docx(), extra=extra), filename="a.docx", media_type=MIME_TYPES["docx"], expected_format="docx"
            )
    link = zipfile.ZipInfo("link.xml")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(KnowledgeArtifactError, match="UNSAFE_PACKAGE"):
        validate_knowledge_artifact(
            package(docx(), extra=[(link, "target")]),
            filename="a.docx",
            media_type=MIME_TYPES["docx"],
            expected_format="docx",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_bytes": 10},
        {"max_entries": 2},
        {"max_uncompressed_bytes": 20},
        {"max_entry_bytes": 20},
        {"max_compression_ratio": 1},
        {"max_xml_bytes": 10},
        {"max_total_xml_bytes": 20},
        {"max_xml_elements": 2},
    ],
)
def test_bounded_zip_and_xml_limits(overrides):
    rejects(docx(), "LIMIT_EXCEEDED", limits=replace(ArtifactLimits(), **overrides))


def test_compression_bomb_is_rejected_before_unbounded_decompression():
    parts = {**docx(), "padding.xml": "<a>" + "x" * 2_000_000 + "</a>"}
    rejects(parts, "LIMIT_EXCEEDED", limits=replace(ArtifactLimits(), max_compression_ratio=100))


@pytest.mark.parametrize(
    "kind,target",
    [
        ("image", "https://example.test/auto.png"),
        ("attachedTemplate", "https://example.test/template.dotx"),
        ("hyperlink", "file:///etc/passwd"),
        ("hyperlink", "javascript:alert(1)"),
        ("hyperlink", "http://example.test"),
        ("hyperlink", "https://user:pass@example.test"),
    ],
)
def test_external_auto_load_and_unsafe_links_rejected(kind, target):
    parts = docx()
    parts["word/_rels/document.xml.rels"] = rels(("remote", kind, target, 'TargetMode="External"'))
    with pytest.raises(KnowledgeArtifactError):
        check(parts)


def test_hyperlink_relationship_cannot_be_reused_as_external_image():
    parts = docx(f'<w:p><w:r><w:t>正文</w:t></w:r></w:p><a:blip xmlns:a="{A}" r:link="remote"/>')
    parts["word/_rels/document.xml.rels"] = rels(
        ("remote", "hyperlink", "https://example.test/auto.png", 'TargetMode="External"')
    )
    rejects(parts, "UNSAFE_RELATIONSHIP")


@pytest.mark.parametrize(
    "kind,target", [("image", "../../outside.png"), ("image", "missing.png"), ("image", "https://example.test")]
)
def test_internal_relations_cannot_escape_or_refer_to_missing_parts(kind, target):
    parts = docx()
    parts["word/_rels/document.xml.rels"] = rels(("r", kind, target, ""))
    with pytest.raises(KnowledgeArtifactError):
        check(parts)


def test_missing_and_duplicate_relation_ids_rejected():
    rejects(docx('<w:hyperlink r:id="absent"><w:r><w:t>text</w:t></w:r></w:hyperlink>'), "INVALID_RELATIONSHIP")
    parts = docx()
    parts["_rels/.rels"] = rels(
        ("same", "officeDocument", "word/document.xml", ""), ("same", "officeDocument", "word/document.xml", "")
    )
    rejects(parts, "INVALID_RELATIONSHIP")


@pytest.mark.parametrize(
    "name,data",
    [
        ("word/vbaProject.bin", b"macro"),
        ("word/embeddings/object.bin", b"object"),
        ("word/activeX/control.xml", "<x/>"),
        ("word/payload.dat", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload"),
    ],
)
def test_macros_active_x_and_embedded_objects_rejected(name, data):
    rejects({**docx(), name: data}, "UNSAFE_CONTENT")


def test_macro_content_type_rejected_even_if_extension_is_docx():
    parts = docx()
    parts["[Content_Types].xml"] = types(
        {"word/document.xml": "application/vnd.ms-word.document.macroEnabled.main+xml"}
    )
    rejects(parts, "UNSAFE_CONTENT")


@pytest.mark.parametrize("field", ["DDEAUTO", "INCLUDEPICTURE", "INCLUDETEXT", "LINK", "DATABASE"])
def test_auto_external_loading_fields_rejected_including_split_runs(field):
    body = (
        f"<w:p><w:r><w:t>正文</w:t></w:r><w:r><w:instrText>{field[:3]}</w:instrText></w:r>"
        f"<w:r><w:instrText>{field[3:]} https://example.test </w:instrText></w:r></w:p>"
    )
    rejects(docx(body), "UNSAFE_CONTENT")


def test_content_type_xml_is_inspected_even_if_part_renamed_dat():
    parts = docx()
    parts["word/payload.dat"] = '<!DOCTYPE x [<!ENTITY a "expanded">]><x>&a;</x>'
    parts["[Content_Types].xml"] = types(
        {"word/document.xml": MAIN_TYPES["docx"], "word/payload.dat": "application/xml"}
    )
    rejects(parts, "INVALID_XML")


def test_external_svg_resource_is_not_silently_accepted():
    parts = docx()
    parts["word/media/diagram.svg"] = (
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.test/a.png"/></svg>'
    )
    parts["[Content_Types].xml"] = types(
        {"word/document.xml": MAIN_TYPES["docx"], "word/media/diagram.svg": "image/svg+xml"}
    )
    rejects(parts, "UNSAFE_CONTENT")


def workbook():
    return {
        "[Content_Types].xml": types(
            {"xl/workbook.xml": WORKBOOK_MAIN_TYPE, "xl/worksheets/sheet1.xml": WORKSHEET_TYPE}
        ),
        "_rels/.rels": rels(("main", "officeDocument", "xl/workbook.xml", "")),
        "xl/workbook.xml": (
            f'<s:workbook xmlns:s="{S}" xmlns:r="{R}"><s:sheets>'
            '<s:sheet name="Data" sheetId="1" r:id="sheet"/></s:sheets></s:workbook>'
        ),
        "xl/_rels/workbook.xml.rels": rels(("sheet", "worksheet", "worksheets/sheet1.xml", "")),
        "xl/worksheets/sheet1.xml": (
            f'<s:worksheet xmlns:s="{S}"><s:sheetData><s:row r="1">'
            '<s:c r="A1"><s:v>10</s:v></s:c></s:row></s:sheetData></s:worksheet>'
        ),
    }


def embedded_chart(book=None):
    parts = pptx(
        '<p:graphicFrame><a:graphic><a:graphicData><c:chart r:id="chart"/></a:graphicData></a:graphic></p:graphicFrame>'
    )
    parts["ppt/slides/_rels/slide1.xml.rels"] = rels(("chart", "chart", "../charts/chart1.xml", ""))
    parts["ppt/charts/chart1.xml"] = (
        f'<c:chartSpace xmlns:c="{C}" xmlns:r="{R}"><c:chart><c:plotArea><c:barChart><c:ser>'
        '<c:val><c:numRef><c:f>Data!$A$1</c:f><c:numCache><c:pt idx="0"><c:v>10</c:v></c:pt>'
        "</c:numCache></c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart>"
        '<c:externalData r:id="data"><c:autoUpdate val="0"/></c:externalData></c:chartSpace>'
    )
    parts["ppt/charts/_rels/chart1.xml.rels"] = rels(("data", "package", "../embeddings/data.xlsx", ""))
    parts["ppt/embeddings/data.xlsx"] = package(workbook() if book is None else book)
    parts["[Content_Types].xml"] = types(
        {
            "ppt/presentation.xml": MAIN_TYPES["pptx"],
            "ppt/slides/slide1.xml": SLIDE_TYPE,
            "ppt/charts/chart1.xml": CHART_TYPE,
            "ppt/embeddings/data.xlsx": WORKBOOK_TYPE,
        }
    )
    return parts


def test_standard_native_chart_with_local_embedded_xlsx_can_be_edited():
    parts = embedded_chart()
    result = check(parts, "pptx")
    assert result.editable_object_count == 1 and result.embedded_workbook_count == 1
    assert result.entry_count == len(parts) + len(workbook())
    assert result.uncompressed_bytes > sum(len(v.encode() if isinstance(v, str) else v) for v in parts.values())


@pytest.mark.parametrize("unsafe", ["macro", "nested", "external", "formula", "dde", "external_reference", "renamed"])
def test_chart_workbooks_reject_macros_nested_archives_external_data_and_active_formulas(unsafe):
    book = workbook()
    if unsafe == "macro":
        book["xl/vbaProject.bin"] = b"macro"
    elif unsafe == "nested":
        book["xl/embedded.xlsx"] = package(workbook())
    elif unsafe == "external":
        book["xl/_rels/workbook.xml.rels"] = rels(
            ("sheet", "worksheet", "https://example.test/data.xml", 'TargetMode="External"')
        )
    elif unsafe in {"formula", "dde", "external_reference"}:
        formula = {
            "formula": 'WEBSERVICE("https://example.test")',
            "dde": "cmd|'/C calc'!A0",
            "external_reference": "[external.xlsx]Sheet1!A1",
        }[unsafe]
        book["xl/worksheets/sheet1.xml"] = (
            f'<s:worksheet xmlns:s="{S}"><s:sheetData><s:row><s:c><s:f>{formula}</s:f>'
            "</s:c></s:row></s:sheetData></s:worksheet>"
        )
    else:
        book = docx()
    with pytest.raises(KnowledgeArtifactError):
        check(embedded_chart(book), "pptx")


def test_embedded_workbook_budget_is_added_to_outer_archive_budget():
    parts = embedded_chart()
    outer_size = sum(len(v.encode() if isinstance(v, str) else v) for v in parts.values())
    rejects(parts, "LIMIT_EXCEEDED", "pptx", limits=replace(ArtifactLimits(), max_uncompressed_bytes=outer_size + 10))
    rejects(parts, "LIMIT_EXCEEDED", "pptx", limits=replace(ArtifactLimits(), max_entries=len(parts) + 1))


def test_xlsx_package_cannot_be_attached_as_an_ole_object_or_unreferenced_payload():
    parts = embedded_chart()
    parts["ppt/charts/_rels/chart1.xml.rels"] = rels(("data", "oleObject", "../embeddings/data.xlsx", ""))
    rejects(parts, "UNSAFE_CONTENT", "pptx")
    parts = pptx()
    parts["unused.xlsx"] = package(workbook())
    parts["[Content_Types].xml"] = types(
        {
            "ppt/presentation.xml": MAIN_TYPES["pptx"],
            "ppt/slides/slide1.xml": SLIDE_TYPE,
            "unused.xlsx": WORKBOOK_TYPE,
        }
    )
    rejects(parts, "UNSAFE_CONTENT", "pptx")


def test_internal_bookmark_hyperlink_is_preserved():
    parts = docx('<w:p><w:hyperlink r:id="bookmark"><w:r><w:t>跳转正文</w:t></w:r></w:hyperlink></w:p>')
    parts["word/_rels/document.xml.rels"] = rels(("bookmark", "hyperlink", "#section1", ""))
    assert check(parts).editable_text_count == 1


def test_arbitrary_extension_text_cannot_fake_editable_pptx_text():
    rejects(pptx("<p:pic/><p:extLst><a:t>不是实际文本对象</a:t></p:extLst>"), "NOT_EDITABLE", "pptx")


@pytest.mark.parametrize("fmt,builder", [("docx", docx), ("pptx", pptx)])
def test_strict_ooxml_namespaces_remain_supported(fmt, builder):
    mappings = {
        R: OFFICE_REL[1],
        W: "http://purl.oclc.org/ooxml/wordprocessingml/main",
        P: "http://purl.oclc.org/ooxml/presentationml/main",
        A: "http://purl.oclc.org/ooxml/drawingml/main",
    }
    parts = builder()
    for old, new in mappings.items():
        parts = {name: data.replace(old, new) for name, data in parts.items()}
    assert check(parts, fmt).editable_text_count == 1


def test_ordinary_local_workbook_formula_remains_supported():
    book = workbook()
    book["xl/worksheets/sheet1.xml"] = (
        f'<s:worksheet xmlns:s="{S}"><s:sheetData><s:row><s:c>'
        "<s:f>SUM(A1:A3)</s:f><s:v>10</s:v></s:c></s:row></s:sheetData></s:worksheet>"
    )
    assert check(embedded_chart(book), "pptx").embedded_workbook_count == 1


def table_workbook(formula):
    book = workbook()
    book["[Content_Types].xml"] = types(
        {
            "xl/workbook.xml": WORKBOOK_MAIN_TYPE,
            "xl/worksheets/sheet1.xml": WORKSHEET_TYPE,
            "xl/tables/table1.xml": TABLE_TYPE,
        }
    )
    book["xl/worksheets/sheet1.xml"] = (
        f'<s:worksheet xmlns:s="{S}" xmlns:r="{R}"><s:sheetData>'
        '<s:row r="1"><s:c r="A1" t="inlineStr"><s:is><s:t>Revenue</s:t></s:is></s:c></s:row>'
        '<s:row r="2"><s:c r="A2"><s:v>10</s:v></s:c>'
        f'<s:c r="B2"><s:f>{escape(formula)}</s:f><s:v>10</s:v></s:c></s:row></s:sheetData>'
        '<s:tableParts count="1"><s:tablePart r:id="table"/></s:tableParts></s:worksheet>'
    )
    book["xl/worksheets/_rels/sheet1.xml.rels"] = rels(("table", "table", "../tables/table1.xml", ""))
    book["xl/tables/table1.xml"] = (
        f'<s:table xmlns:s="{S}" id="1" name="Table1" displayName="Table1" ref="A1:A2" totalsRowShown="0">'
        '<s:autoFilter ref="A1:A2"/><s:tableColumns count="1"><s:tableColumn id="1" name="Revenue"/>'
        "</s:tableColumns></s:table>"
    )
    return book


@pytest.mark.parametrize(
    "formula",
    [
        "SUM(Table1[Revenue])",
        "SUM(table1[Revenue])",
        "SUM(Table1[[#Data],[Revenue]])",
        "Table1[@Revenue]+SUM(A1:A3)",
        "SUM(Table1[Revenue])+Data!A2",
    ],
)
def test_local_structured_table_references_in_embedded_chart_workbook(formula):
    assert check(embedded_chart(table_workbook(formula)), "pptx").embedded_workbook_count == 1


@pytest.mark.parametrize(
    "formula",
    [
        "SUM([1]Data!A2)",
        "SUM('[external.xlsx]Data'!A2)",
        "SUM([external.xlsx]Data!A2)",
        "SUM('C:\\Table1[external.xlsx]Data'!A2)",
        "SUM(Table1[external.xlsx]Data!A2)",
        'SUM(Table1[Revenue])+WEBSERVICE("https://example.test")',
        "SUM([1]Table1[Revenue])",
        "SUM(Table1[Revenue])+cmd|'/C calc'!A0",
        "SUM(UnknownTable[Revenue])",
        "SUM(Table1[Revenue)",
    ],
)
def test_local_table_allowance_does_not_allow_external_or_active_formula(formula):
    rejects(embedded_chart(table_workbook(formula)), "UNSAFE_CONTENT", "pptx")


@pytest.mark.parametrize("tag", ["calculatedColumnFormula", "totalsRowFormula"])
def test_table_formula_nodes_cannot_load_external_workbooks(tag):
    book = table_workbook("SUM(Table1[Revenue])")
    book["xl/tables/table1.xml"] = book["xl/tables/table1.xml"].replace(
        '<s:tableColumn id="1" name="Revenue"/>',
        f'<s:tableColumn id="1" name="Revenue"><s:{tag}>[external.xlsx]Data!A1</s:{tag}></s:tableColumn>',
    )
    rejects(embedded_chart(book), "UNSAFE_CONTENT", "pptx")


def relative_table_workbook(formula, tag="calculatedColumnFormula"):
    book = table_workbook("SUM(Table1[Revenue])")
    book["xl/worksheets/sheet1.xml"] = (
        f'<s:worksheet xmlns:s="{S}" xmlns:r="{R}"><s:sheetData><s:row r="1">'
        + "".join(
            f'<s:c r="{column}1" t="inlineStr"><s:is><s:t>{name}</s:t></s:is></s:c>'
            for column, name in (("A", "Revenue"), ("B", "Units"), ("C", "Price"))
        )
        + '</s:row><s:row r="2"><s:c r="A2"><s:v>10</s:v></s:c><s:c r="B2"><s:v>2</s:v></s:c>'
        '<s:c r="C2"><s:v>5</s:v></s:c></s:row></s:sheetData>'
        '<s:tableParts count="1"><s:tablePart r:id="table"/></s:tableParts></s:worksheet>'
    )
    book["xl/tables/table1.xml"] = (
        f'<s:table xmlns:s="{S}" id="1" name="Table1" displayName="Table1" ref="A1:C2" totalsRowShown="0">'
        '<s:autoFilter ref="A1:C2"/><s:tableColumns count="3"><s:tableColumn id="1" name="Revenue">'
        f'<s:{tag}>{escape(formula)}</s:{tag}></s:tableColumn><s:tableColumn id="2" name="Units"/>'
        '<s:tableColumn id="3" name="Price"/></s:tableColumns></s:table>'
    )
    return book


@pytest.mark.parametrize(
    "tag,formula",
    [
        ("calculatedColumnFormula", "[@Units]*[@Price]"),
        ("calculatedColumnFormula", "[@[Units]]*[@[Price]]"),
        ("calculatedColumnFormula", "[[#This Row],[Units]]*[[#This Row],[Price]]"),
        ("calculatedColumnFormula", "[@units]*[@price]"),
        ("totalsRowFormula", "SUBTOTAL(109,[Revenue])"),
        ("totalsRowFormula", "SUM([[#Data],[Revenue]])"),
    ],
)
def test_relative_columns_resolve_only_within_their_validated_table_formula(tag, formula):
    assert check(embedded_chart(relative_table_workbook(formula, tag)), "pptx").embedded_workbook_count == 1


@pytest.mark.parametrize(
    "formula",
    [
        "SUM([Revenue]Data!A1)",
        "SUM([1]Data!A1)",
        "SUM('[Revenue]Data'!A1)",
        "SUM([external.xlsx]Data!A1)",
        "[@Missing]*[@Price]",
        "UnknownTable[Revenue]",
        '[@Units]+WEBSERVICE("https://example.test")',
        "[@Units]+cmd|'/C calc'!A0",
    ],
)
def test_relative_column_allowance_still_rejects_external_unknown_and_active_data(formula):
    rejects(embedded_chart(relative_table_workbook(formula)), "UNSAFE_CONTENT", "pptx")


@pytest.mark.parametrize("scope", ["worksheet_cell", "worksheet_fake_table_formula", "unreferenced_table"])
def test_relative_columns_do_not_gain_trust_from_an_unrelated_table(scope):
    book = relative_table_workbook("[@Units]*[@Price]")
    if scope == "worksheet_cell":
        book["xl/worksheets/sheet1.xml"] = book["xl/worksheets/sheet1.xml"].replace(
            '<s:c r="A2"><s:v>10</s:v>', '<s:c r="A2"><s:f>[@Units]*[@Price]</s:f><s:v>10</s:v>'
        )
    elif scope == "worksheet_fake_table_formula":
        book["xl/worksheets/sheet1.xml"] = book["xl/worksheets/sheet1.xml"].replace(
            "</s:worksheet>", "<s:calculatedColumnFormula>[@Units]*[@Price]</s:calculatedColumnFormula></s:worksheet>"
        )
    else:
        book["xl/worksheets/sheet1.xml"] = book["xl/worksheets/sheet1.xml"].replace(
            '<s:tableParts count="1"><s:tablePart r:id="table"/></s:tableParts>', ""
        )
    rejects(embedded_chart(book), "UNSAFE_CONTENT", "pptx")
