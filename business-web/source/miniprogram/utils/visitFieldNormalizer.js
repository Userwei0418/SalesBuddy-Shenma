const EMPTY_VALUES = ["", "—", "待补充", "待确认", "未知", "不详"];

const CHOICE_ALIASES = {
  lead_source: [
    [/^(自拓|自主拓展|销售自拓|自行开发|陌生开发)$/i, "自拓"],
    [/(inbound|公司分配|公司线索|市场线索|官网线索|线上线索)/i, "公司线索"],
    [/(转介绍|客户介绍|推荐线索)/i, "转介绍"],
    [/(合作伙伴|伙伴|渠道|\bsi\b|俏皮|信息侠|联邦云|连邦云|道汇)/i, "合作伙伴"],
  ],
  contact_category: [
    [/^(最终客户|终端客户|最终用户|客户|客户高层|客户管理层|决策者)$/i, "最终客户"],
    [/(白金伙伴)/i, "白金伙伴"],
    [/(金牌伙伴)/i, "金牌伙伴"],
    [/(商机伙伴|合作伙伴|渠道伙伴|伙伴|\bisv\b|\bsi\b)/i, "商机伙伴"],
  ],
  interaction_mode: [
    [/(线下会议|线下拜访|现场拜访|上门拜访|线下沟通|面谈)/i, "线下会议"],
    [/(线上会议|线上沟通|视频会议|视频沟通|腾讯会议|钉钉会议|^线上$)/i, "线上会议"],
    [/(电话|语音|电访)/i, "电话/语音"],
    [/(饭局|聚会|宴请|会餐)/i, "饭局/聚会"],
  ],
  expectation_met: [
    [/(超出|超额|超预期|超过\s*100)/i, "超出100%"],
    [/^(是|达成|已达成|达成预期|符合预期|达成100%|100%达成)$/i, "达成100%"],
    [/(50\s*[-—~至到]\s*100|达成一半以上)/i, "达成50-100%"],
    [/(30\s*[-—~至到]\s*50)/i, "达成30-50%"],
    [/(10\s*[-—~至到]\s*30)/i, "达成10-30%"],
    [/^(否|未达成|没有达成|不达预期|未达到预期)$/i, "未达成"],
  ],
  contact_role: [
    [/(决策者|拍板人|最终决策|董事长|总裁|ceo|coo|cio|cto|cfo|cxo)/i, "决策者"],
    [/(影响者|总经理|\bgm\b|总监|部门负责人|架构师|专家)/i, "影响者"],
    [/(使用者|一线|工程师|业务|开发|运维|数据人员|用户)/i, "使用者"],
  ],
};

function pad(value) {
  return String(value).padStart(2, "0");
}

function cleanText(value) {
  return String(value == null ? "" : value).trim().replace(/[ \t]+/g, " ");
}

function normalizeChoice(key, value) {
  const raw = cleanText(value);
  if (EMPTY_VALUES.includes(raw)) return "";
  const alias = (CHOICE_ALIASES[key] || []).find(([pattern]) => pattern.test(raw));
  return alias ? alias[1] : raw;
}

function parseAmountToYuan(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : NaN;
  const raw = cleanText(value).replace(/[，,￥¥\s]/g, "");
  const match = raw.match(/^([0-9]+(?:\.[0-9]+)?)(亿元|亿|万元|万|元)?$/);
  if (!match) return NaN;
  const amount = Number(match[1]);
  const unit = match[2] || "元";
  if (unit === "亿元" || unit === "亿") return amount * 100000000;
  if (unit === "万元" || unit === "万") return amount * 10000;
  return amount;
}

function formatWanAmount(value) {
  const yuan = parseAmountToYuan(value);
  if (!Number.isFinite(yuan) || yuan <= 0) return cleanText(value);
  const wan = yuan / 10000;
  const digits = Number.isInteger(wan) ? 0 : 2;
  const formatted = wan.toLocaleString("zh-CN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
  return `${formatted} 万元`;
}

function normalizeDuration(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${Math.max(1, Math.min(1440, Math.round(value)))} 分钟`;
  }
  const raw = cleanText(value);
  const hourMatches = Array.from(raw.matchAll(/([0-9]+(?:\.[0-9]+)?)\s*(?:小时|h)/gi));
  const minuteMatches = Array.from(raw.matchAll(/([0-9]+(?:\.[0-9]+)?)\s*(?:分钟|min)/gi));
  let minutes = 0;
  hourMatches.forEach((item) => { minutes += Number(item[1]) * 60; });
  minuteMatches.forEach((item) => { minutes += Number(item[1]); });
  if (!minutes) {
    const numbers = raw.match(/[0-9]+(?:\.[0-9]+)?/g) || [];
    if (numbers.length) minutes = Math.max(...numbers.map(Number));
  }
  if (!Number.isFinite(minutes) || minutes <= 0) return raw;
  return `${Math.max(1, Math.min(1440, Math.round(minutes)))} 分钟`;
}

function normalizeDateTimeDisplay(value) {
  const raw = cleanText(value);
  if (!raw) return "";
  const chinese = raw.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日?(?:\s*(上午|下午|晚上|中午)?\s*(\d{1,2})(?:[:点时](\d{1,2}))?分?)?$/);
  if (chinese) {
    let hour = Number(chinese[5] || 0);
    if (["下午", "晚上"].includes(chinese[4]) && hour < 12) hour += 12;
    if (chinese[4] === "中午" && hour < 11) hour += 12;
    if (chinese[4] === "上午" && hour === 12) hour = 0;
    return `${chinese[1]}-${pad(chinese[2])}-${pad(chinese[3])} ${pad(hour)}:${pad(chinese[6] || 0)}`;
  }
  const standard = raw.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2})(?::(\d{1,2}))?)?/);
  if (standard) {
    return `${standard[1]}-${pad(standard[2])}-${pad(standard[3])} ${pad(standard[4] || 0)}:${pad(standard[5] || 0)}`;
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function normalizeVisitFieldValue(key, value) {
  if (["lead_source", "contact_category", "interaction_mode", "expectation_met", "contact_role"].includes(key)) {
    return normalizeChoice(key, value);
  }
  if (["estimated_amount", "customer_budget"].includes(key)) return formatWanAmount(value);
  if (key === "interaction_at") return normalizeDateTimeDisplay(value);
  if (key === "duration_minutes") return normalizeDuration(value);
  if (key === "partner_name" && /^(无|没有|不涉及|直销|无合作伙伴)$/i.test(cleanText(value))) return "无";
  return cleanText(value);
}

function normalizeVisitFields(fields) {
  return (fields || []).map((field) => {
    const original = field.value;
    const value = normalizeVisitFieldValue(field.key, original);
    const changed = cleanText(original) !== cleanText(value) && Boolean(cleanText(original));
    return {
      ...field,
      value,
      normalized: Boolean(field.normalized || changed),
      missing: Boolean(field.required) && EMPTY_VALUES.includes(cleanText(value)),
    };
  });
}

module.exports = {
  formatWanAmount,
  normalizeDateTimeDisplay,
  normalizeDuration,
  normalizeVisitFields,
  normalizeVisitFieldValue,
  parseAmountToYuan,
};
