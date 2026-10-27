"""Reviewed failures for opportunity collection edits; SQL details stay private."""

COLLECTION_ACTUAL_ERRORS = {
    "OPPORTUNITY_TRADE_RELATION_REQUIRED": (
        422, "请先在交易物料中选择交易关系，再保存实际金额"),
    "OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN": (
        422, "下游商机不能登记非零我方回款，请核对交易关系与收款归属"),
    "OPPORTUNITY_COLLECTION_VERSION_CONFLICT": (
        409, "实际回款或来源已变化，请刷新回款状态并核对后保存"),
    "OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED": (
        409, "请确认替换本季度现有回款来源后再保存"),
    "OPPORTUNITY_COLLECTION_FORBIDDEN": (
        403, "当前账号无权修改此商机的实际回款"),
    "OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED": (
        422, "本次保存标识缺失，请重新打开商机后保存"),
    "OPPORTUNITY_COLLECTION_INPUT_INVALID": (
        422, "请检查季度实际回款金额、回款依据和备注"),
    "OPPORTUNITY_COLLECTION_PERIOD_INVALID": (
        422, "请选择有效的回款年份和季度"),
}

# Only exact, reviewed V290 rejections inside collection save are translated.
# A similar sentence, an unrecognised SQLSTATE, or diagnostic DETAIL is not a
# business contract and must continue through the existing exception boundary.
COLLECTION_ACTUAL_SQL_ERRORS = {
    ("23514", "请先明确商机交易关系"): "OPPORTUNITY_TRADE_RELATION_REQUIRED",
    ("23514", "下游商机不能重复登记我方回款，请核对合同关系与收款归属"):
        "OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN",
    ("23514", "下游商机不能采用非零我方回款，请核对合同关系与收款归属"):
        "OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN",
    ("40001", "实际回款或来源已变化，请刷新后重新核对"):
        "OPPORTUNITY_COLLECTION_VERSION_CONFLICT",
    ("40001", "请确认将本季度现有来源改为本次人工登记，原始依据仍会保留"):
        "OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED",
    ("42501", "当前账号无权修改此商机的实际回款"):
        "OPPORTUNITY_COLLECTION_FORBIDDEN",
    ("22023", "请核对实际回款金额、登记依据和备注"):
        "OPPORTUNITY_COLLECTION_INPUT_INVALID",
    ("22023", "回款季度无效"): "OPPORTUNITY_COLLECTION_PERIOD_INVALID",
}


class CollectionActualError(Exception):
    def __init__(self, code: str):
        self.code = code
        self.status = COLLECTION_ACTUAL_ERRORS[code][0]
        super().__init__(code)
