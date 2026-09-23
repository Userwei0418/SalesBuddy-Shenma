import io
import zipfile
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from sales_backend.domain.visit_contract import ensure_visit_result, validate_content
from sales_backend.services.visit_import import document_text


@pytest.mark.parametrize('extension', ['md', 'txt'])
def test_plain_document_preserves_chinese(extension):
    assert document_text('拜访.'+extension, '客户要求周五演示'.encode()) == '客户要求周五演示'


@pytest.mark.parametrize('extension,path,ns', [
    ('docx', 'word/document.xml', 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'),
    ('pptx', 'ppt/slides/slide1.xml', 'http://schemas.openxmlformats.org/drawingml/2006/main'),
])
def test_office_document_text(extension, path, ns):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr(path, f'<r xmlns:a="{ns}"><a:p><a:r><a:t>拜访结果</a:t></a:r></a:p></r>')
    assert document_text('visit.'+extension, data.getvalue()) == '拜访结果'


def test_scan_pdf_is_not_fabricated_as_text():
    pdf = PdfWriter()
    pdf.add_blank_page(width=100, height=100)
    content = io.BytesIO()
    pdf.write(content)
    with pytest.raises(ValueError, match='扫描件'):
        document_text('scan.pdf', content.getvalue())


def test_optional_information_can_be_missing():
    values = dict(visit_goal='核对采购目标', follow_up_record='客户确认先试点', next_action='周五由我安排演示')
    clean = validate_content(values)
    assert clean['opportunity_id'] is None
    assert clean['interaction_at'] == ''
    assert clean['collaborator_ids'] == []
    assert validate_content({**values, 'visit_goal': ''})['visit_goal'] == ''
    with pytest.raises(ValueError, match='沟通内容'):
        validate_content({**values, 'follow_up_record': ''})
    with pytest.raises(ValueError):
        validate_content({**values, 'collaborator_ids': [str(uuid4()), 'fake']})


def test_only_trusted_total_and_grade_are_exposed():
    result = ensure_visit_result({'fields': {}, 'quality_review': {
        'follow_up_score': 69, 'dimensions': {'logic': 20}, 'grade': '优秀',
        'suggestions': ['补充客户反馈'], 'next_action': {'passed': False},
    }})
    assert result['quality_review']['grade'] == '合格'
    assert 'dimensions' not in result['quality_review']
    assert len(result['missing_fields']) == 2
    with pytest.raises(ValueError):
        ensure_visit_result({'fields': {}, 'quality_review': {'follow_up_score': True}})
