"""Plain-text cards matching the useful fields of CRM-V3 follow-up notifications."""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from sales_backend.domain.knowledge import CATEGORY_LABELS
from sales_backend.domain.opportunities import STAGE_BY_PROBABILITY, STAGES, stage_label


def _decimal_text(number):
    text = format(number, ',f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _opportunity_amount(value, currency):
    if value is None or value == '':
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return '金额格式异常'
    if not amount.is_finite():
        return '金额格式异常'
    # Bound malformed exponents before expanding fixed-point text. This is a
    # display guard, not a business amount limit or a conversion of source data.
    if amount.adjusted() >= 60 or amount.as_tuple().exponent < -60:
        return '金额超出显示范围'
    if not amount:
        amount = Decimal(0)
    text = _decimal_text(amount)
    code = str(currency or '').strip().upper()
    if code != 'CNY':
        return f'{text} {code}' if code else f'{text}（币种未填写）'
    text += '元'
    magnitude = amount.copy_abs()
    if magnitude >= 10_000:
        places, unit = (8, '亿元') if magnitude >= 100_000_000 else (4, '万元')
        sign, digits, exponent = amount.as_tuple()
        # Shift the decimal point exactly, without context-dependent rounding.
        scaled = Decimal((sign, digits, exponent - places))
        text += f'（{_decimal_text(scaled)}{unit}）'
    return text


def _opportunity_stage(values):
    stage = values.get('stage')
    if values.get('status') == 'lost' or stage == 'lost':
        return stage_label({'status': 'lost'})
    probability = values.get('probability')
    if isinstance(probability, Decimal) and not probability.is_finite():
        probability = None
    if isinstance(probability, (int, float, Decimal)) and probability in STAGES:
        return stage_label(values)
    # Older projections may carry only the stage code. Reuse the same Chinese
    # labels rather than maintaining a second translation of the stage enum.
    for probability, code in STAGE_BY_PROBABILITY.items():
        if stage == code:
            return stage_label({'probability': probability})
    if stage == 'won':
        return stage_label({'probability': 100})
    return stage


def notification_card(kind, values, config, record_id):
    title = {'customer': '新增客户', 'opportunity': '新增商机', 'visit': '新增跟进记录',
             'task': '新增任务', 'weekly_report': '销售工作报告已确认'}.get(kind, '业务更新')
    definitions = {
        "weekly_report": [("title", "报告"), ("report_week", "报告周期开始"), ("author_name", "提交人"),
                          ("stats_summary", "统计摘要"), ("body_markdown", "报告正文")],
        'customer': [('name', '客户'), ('owner_name', '负责人'), ('department_name', '部门'),
                     ('industry', '行业'), ('needs', '客户需求'), ('next_action', '下一步计划')],
        'opportunity': [('name', '商机'), ('customer_name', '客户'), ('owner_name', '负责人'),
                        ('amount', '金额'), ('currency', '币种'), ('stage', '阶段'),
                        ('expected_close_date', '预计关单')],
        'visit': [('customer_name', '客户'), ('opportunity_name', '商机'), ('partner_name', '伙伴'),
                  ('owner_name', '填写人'), ('interaction_at', '跟进日期'), ('contact_name', '对接人'),
                  ('content', '沟通内容'), ('next_action', '下一步计划')],
        'task': [('title', '任务'), ('description', '任务内容'), ('owner_name', '负责人'),
                 ('original_dispatcher_name', '下发人'), ('due_at', '截止时间'), ('status', '当前状态'),
                 ('source_code', '任务来源'), ('batch_name', '来源批次')],
    }
    elements = []
    for field, label in definitions.get(kind, []):
        value = values.get(field)
        if kind == 'opportunity' and field == 'amount':
            value = _opportunity_amount(value, values.get('currency'))
        elif kind == 'opportunity' and field == 'stage':
            value = _opportunity_stage(values)
        elif kind == 'task':
            if field == 'due_at':
                value = _update_time(value)
            elif field == 'status':
                value = {'pending_confirm': '待接收', 'pending_execution': '待执行',
                         'pending_review': '待验收', 'pending_handover': '待交接',
                         'completed': '已完成', 'cancelled': '已取消'}.get(value, '待核对')
            elif field == 'source_code':
                value = {'mini_program': '手动创建', 'business_advice': '业务建议采纳',
                         'meeting_import': '周会导入'}.get(value, '任务下发')
            elif field == 'owner_name' and not values.get('owner_id'):
                names = '、'.join(values.get('notification_recipient_names') or [])
                value = f'待领取（可接收人：{names}）' if names else '待领取'
        if value is None or value == '':
            continue
        if field == 'interaction_at':
            try:
                parsed = datetime.fromisoformat(str(value))
                if parsed.tzinfo:
                    parsed = parsed.astimezone(ZoneInfo('Asia/Shanghai'))
                value = parsed.strftime('%Y-%m-%d %H:%M')
            except ValueError:
                pass
        limit = 1600 if field == 'body_markdown' else 1200 if field in {'content', 'needs', 'description'} else 800 if field == 'next_action' else 120
        if kind == 'opportunity' and field == 'amount':
            limit = 320  # Keep both complete bounded amounts; never shorten significant digits.
        value = str(value)
        value = value if len(value) <= limit else value[:limit] + '…（完整内容请查看详情）'
        # User text cannot become mentions, Markdown links or executable card actions.
        elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': f'{label}：{value}'}})
    if record_id and config.base_url and kind in config.mappings:
        url = config.base_url + '?' + urlencode({'table': config.mappings[kind].table_id, 'record': record_id})
        elements.append({'tag': 'action', 'actions': [{'tag': 'button', 'type': 'primary', 'url': url,
                        'text': {'tag': 'plain_text', 'content': '查看详情'}}]})
    if not elements:
        elements = [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': title}}]
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue', 'title': {'tag': 'plain_text', 'content': title}},
            'elements': elements}


def _update_text(value, limit=240):
    if value is None or value == '':
        return '未填写'
    text = str(value)
    return text if len(text) <= limit else text[:limit] + '…（完整内容见系统商机变更记录）'


def _audit_amount(value, currency):
    """Audited amount changes are explicitly wan, unlike projection yuan values."""
    if value is None or value == '' or value == '未填写':
        return '未填写'
    try:
        wan = Decimal(str(value))
        if not wan.is_finite():
            return '金额格式异常'
        sign, digits, exponent = wan.as_tuple()
        # Exact decimal shift, not float multiplication or context-rounded math.
        yuan = Decimal((sign, digits, exponent + 4))
    except (InvalidOperation, ValueError):
        return '金额格式异常'
    return _opportunity_amount(yuan, currency)


def _update_time(value):
    if value is None or value == '':
        return '未填写'
    try:
        timestamp = datetime.fromisoformat(str(value))
        china = ZoneInfo('Asia/Shanghai')
        timestamp = timestamp.astimezone(china) if timestamp.tzinfo else timestamp.replace(tzinfo=china)
        return timestamp.strftime('%Y-%m-%d %H:%M') + '（北京时间）'
    except ValueError:
        return _update_text(value, 80) + '（时间格式异常）'


_GENERAL_PRODUCT_LABEL = '通用'
_UNKNOWN_PRODUCT_LABEL = '未配置产品'


def _knowledge_label(field, value):
    """Keep cards readable when a frozen event still carries legacy codes."""
    if value is None or value == '':
        return value
    text = str(value)
    if field == 'category':
        return CATEGORY_LABELS.get(text, '未分类')
    if field == 'product':
        if text == 'general':
            return _GENERAL_PRODUCT_LABEL
        if text == _UNKNOWN_PRODUCT_LABEL:
            return text
        # Catalog snapshots normally contain a display label. A lower-case
        # token with a separator is a legacy/system code, not a label to show.
        if text == text.lower() and any(char in text for char in '_-'):
            return _UNKNOWN_PRODUCT_LABEL
    return value


def opportunity_update_card(snapshot, config, record_id):
    """Render only frozen business-change facts, never current mutable values."""
    elements = []
    for label, value in [
        ('商机', snapshot.get('opportunity_name')), ('客户', snapshot.get('customer_name')),
        ('负责人（修改时）', snapshot.get('owner_name')), ('修改人', snapshot.get('actor_name')),
        ('修改时间', _update_time(snapshot.get('changed_at'))),
    ]:
        elements.append({'tag': 'div', 'text': {
            'tag': 'plain_text', 'content': f'{label}：{_update_text(value, 120)}'}})
    card = {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue', 'title': {'tag': 'plain_text', 'content': '商机更新'}},
            'elements': elements}
    detail = []
    if record_id and config.base_url and 'opportunity' in config.mappings:
        url = config.base_url + '?' + urlencode({'table': config.mappings['opportunity'].table_id, 'record': record_id})
        detail = [{'tag': 'action', 'actions': [{'tag': 'button', 'type': 'primary', 'url': url,
                   'text': {'tag': 'plain_text', 'content': '查看当前商机'}}]}]
    changes = snapshot.get('changes') or []
    rendered = 0
    for change in changes[:30]:
        label = str(change.get('label') or '变更字段')
        if label.endswith('（万元）'):
            label = label.removesuffix('（万元）')
            before = _audit_amount(change.get('before'), snapshot.get('currency'))
            after = _audit_amount(change.get('after'), snapshot.get('currency'))
        else:
            before, after = change.get('before'), change.get('after')
            if label == '销售渠道':
                channels = {'direct': '直销', 'partner': '合作伙伴'}
                before, after = channels.get(before, before), channels.get(after, after)
            before, after = _update_text(before), _update_text(after)
        row = {'tag': 'div', 'text': {'tag': 'plain_text', 'content':
               f'{_update_text(label, 80)}\n修改前：{before}\n修改后：{after}'}}
        candidate = {**card, 'elements': [*elements, row, *detail]}
        # Reserve room for an omitted-row notice. Omit whole rows on overflow;
        # never shorten a monetary value's significant digits to fit the card.
        if len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > 23000:
            break
        elements.append(row)
        rendered += 1
    if rendered < len(changes):
        elements.append({'tag': 'div', 'text': {'tag': 'plain_text',
                        'content': f'另有 {len(changes) - rendered} 项未展开，完整变更见系统商机变更记录。'}})
    elements.extend(detail)
    return card


def knowledge_publication_card(snapshot, config, record_id):
    """Internal directory card; never exposes a customer token or source file URL."""
    title = "知识资料发布" if snapshot["event_kind"] == "published" else "知识资料版本更新"
    labels = [("content_name", "资料"), ("product", "产品"), ("category", "类别"),
              ("version_no", "资料版本"), ("description", "更新摘要"),
              ("provider_name", "提供人"), ("published_at", "发布时间")]
    elements = []
    for field, label in labels:
        value = snapshot.get(field)
        if value is None or value == "":
            continue
        if field in {'product', 'category'}:
            value = _knowledge_label(field, value)
        if field == "published_at":
            value = _update_time(value)
        text = str(value)
        if len(text) > 300:
            text = text[:300] + "…"
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": f"{label}：{text}"}})
    if config.knowledge_web_url:
        material_id = str(UUID(str(snapshot["material_id"])))
        base = config.knowledge_web_url + "#/pages/knowledge/index?" + urlencode({"material_id": material_id})
        elements.append({"tag": "action", "actions": [
            {"tag": "button", "type": "primary", "url": base,
             "text": {"tag": "plain_text", "content": "查看资料"}},
            {"tag": "button", "url": base + "&distribution=1",
             "text": {"tag": "plain_text", "content": "获取分享链接"}}]})
    if record_id and config.base_url and "knowledge" in config.mappings:
        url = config.base_url + "?" + urlencode({"table": config.mappings["knowledge"].table_id, "record": record_id})
        elements.append({"tag": "action", "actions": [{"tag": "button", "type": "primary", "url": url,
            "text": {"tag": "plain_text", "content": "查看资料目录"}}]})
    return {"config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}}, "elements": elements}
