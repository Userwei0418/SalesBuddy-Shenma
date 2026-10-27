"""Stable, public failures for direct net recognition; never SQL diagnostics."""

RECOGNIZED_ACTUAL_ERRORS = {
    "OPPORTUNITY_RECOGNIZED_FORBIDDEN": (403, "当前账号无权修改此商机的实际确收"),
    "OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT": (409, "实际确收或来源已变化，请刷新并核对后保存"),
    "OPPORTUNITY_RECOGNIZED_SOURCE_CONFIRMATION_REQUIRED": (409, "请确认替换本季度现有确收来源后再保存，旧依据会保留"),
    "OPPORTUNITY_RECOGNIZED_INPUT_INVALID": (422, "请检查物料累计不含税确收金额、登记依据和备注"),
    "OPPORTUNITY_RECOGNIZED_MATERIALS_CHANGED": (409, "请先保存交易关系和物料，再刷新本季度实际确收"),
    "OPPORTUNITY_RECOGNIZED_MATERIAL_REQUIRED": (422, "请先保存本商机的交易物料；历史总额仍保留，不会自动分摊"),
    "OPPORTUNITY_RECOGNIZED_MATERIAL_FORBIDDEN": (422, "请选择本商机可确收的物料，总代转售物料应在下游商机登记"),
    "OPPORTUNITY_RECOGNIZED_PERIOD_INVALID": (422, "请选择有效的确收年份和季度"),
    "OPPORTUNITY_RECOGNIZED_IDEMPOTENCY_KEY_REQUIRED": (422, "本次保存标识缺失，请重新打开商机后保存"),
}


class RecognizedActualError(Exception):
    def __init__(self, code):
        self.code = code
        self.status = RECOGNIZED_ACTUAL_ERRORS[code][0]
        super().__init__(code)
