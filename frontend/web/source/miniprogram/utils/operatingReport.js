/**
 * BACKEND-CONTRACT operating_report Agent 结果映射，输入必须有 run.id/result；action 决定需读取的结果数组。
 * 需及时处理条目优先用条目内 ai_recommendation/recommendation，才按名称与 action_plan 回填；按顺序兜底可能错配，后端宜逐条内嵌建议。
 * 文案为报告展示，不会自动 POST /tasks 或解除风险；详见 docs/backend-handoff/跟进任务与智能体详解.md。
 */
const REPORT_ACTIONS = {
  report_personal_daily: {
    label: "经营即时总结", prompt: "生成经营即时总结，按当前业务授权范围分析客户、商机、拜访跟进、任务和风险",
    sections: [
      ["safe_customers", "安全客户", "当前推进健康、有明确下一步且风险可控"],
      ["attention_customers", "需及时处理", "跟进停滞、任务逾期、商机临期或存在风险"],
    ],
  },
  report_team_daily: {
    label: "经营即时总结", prompt: "生成经营即时总结，按当前业务授权范围分析客户、商机、拜访跟进、任务和风险，并分别展示本人和授权范围的结果",
    sections: [
      ["personal_safe_customers", "本人 · 安全客户", "本人负责且当前推进健康的客户"],
      ["personal_attention_customers", "本人 · 需及时处理", "本人需要立即跟进的客户"],
      ["team_safe_customers", "授权范围 · 安全客户", "授权范围内推进健康的客户"],
      ["team_attention_customers", "授权范围 · 需及时处理", "授权范围内停滞、逾期、临期或有风险的客户"],
    ],
  },
  report_department_daily: {
    label: "经营即时总结", prompt: "生成经营即时总结，按当前业务授权范围分析客户、商机、拜访跟进、任务和风险，并提供部门处置概览",
    sections: [
      ["safe_customers", "授权范围 · 安全客户", "授权范围内推进健康、下一步明确且风险可控的客户"],
      ["attention_customers", "授权范围 · 需及时处理", "授权范围内商机停滞、逾期、临期或有风险的客户"],
      ["team_comparison", "部门处置概览", "按授权范围内部门比较待处理客户与任务执行情况"],
    ],
  },
};

// Legacy action names select a result layout, never an authorization scope.
// Only an explicit user selection narrows the server's effective read scope.
function reportPrompt(action, options = {}) {
  const config = REPORT_ACTIONS[action];
  if (!config) return '';
  return options.scope === 'self' ? `${config.prompt}；范围仅本人` : config.prompt;
}

function buildReport(run, action) {
 const config=REPORT_ACTIONS[action];
 if (!config || !run || !run.id || !run.result) throw new Error("报告数据无效");
 const result=run.result;
      const reportId = run.id;
      const details = {};
      const actionPlans = Array.isArray(result.action_plan) ? result.action_plan : [];
      const usedActionPlans = {};
      let attentionIndex = 0;
      const normalizePlanText = (value) => String(value || "").replace(/\s+/g, "").toLowerCase();
      const findActionPlan = (item, fallbackIndex) => {
        const embedded = item && typeof item === "object" ? item.ai_recommendation || item.recommendation : null;
        if (embedded) {
          return typeof embedded === "string"
            ? { title: "AI 推荐行动", detail: embedded }
            : { title: embedded.title || "AI 推荐行动", detail: embedded.detail || embedded.comment || "" };
        }
        const customerTitle = normalizePlanText(item && typeof item === "object" ? item.title : item);
        let planIndex = actionPlans.findIndex((plan, index) => {
          if (usedActionPlans[index]) return false;
          const planText = normalizePlanText(typeof plan === "string" ? plan : `${plan.title || ""}${plan.detail || plan.comment || ""}`);
          return customerTitle && planText.indexOf(customerTitle) >= 0;
        });
        if (planIndex < 0 && actionPlans[fallbackIndex] && !usedActionPlans[fallbackIndex]) planIndex = fallbackIndex;
        if (planIndex < 0) planIndex = actionPlans.findIndex((plan, index) => plan && !usedActionPlans[index]);
        if (planIndex < 0) return null;
        usedActionPlans[planIndex] = true;
        const plan = actionPlans[planIndex];
        return typeof plan === "string"
          ? { title: "AI 推荐行动", detail: plan }
          : { title: plan.title || "AI 推荐行动", detail: plan.detail || plan.comment || "" };
      };
      const definitions = config.sections.map(([key, title, subtitle]) => ({ key, title, subtitle }));
      const sections = definitions.map((definition) => {
        const source = Array.isArray(result[definition.key]) ? result[definition.key] : [];
        const isAttentionSection = definition.key.indexOf("attention_customers") >= 0;
        return {
          ...definition,
          report: true,
          rows: source.map((item, rowIndex) => {
            const title = typeof item === "string" ? item : item.title || definition.subtitle;
            const meta = typeof item === "string" ? "" : item.detail || item.comment || "";
            const recommendation = isAttentionSection ? findActionPlan(item, attentionIndex++) : null;
            const reportDetailId = `${reportId}_${definition.key}_${rowIndex}`;
            details[reportDetailId] = {
              runId:run.id, action, sectionKey:definition.key, rowIndex,
              reportTitle: result.title || config.label,
              sectionTitle: definition.title,
              sectionSubtitle: definition.subtitle,
              title,
              detail: meta || "当前条目暂无补充说明。",
              isAttention: isAttentionSection,
              recommendationTitle: recommendation ? recommendation.title : "",
              recommendationDetail: recommendation ? recommendation.detail : "",
              scope: result.scope || "当前权限范围",
              period: result.period || "当前实时状态",
              generatedAt: new Date(run.completed_at || run.created_at).toLocaleString("zh-CN"),
            };
            return { title, meta, tag: isAttentionSection ? "查看 AI 建议" : definition.title, reportDetailId };
          }),
          emptyText: `本周期暂无${definition.subtitle}`,
        };
      });

 return {sections,details};
}
module.exports={REPORT_ACTIONS,reportPrompt,buildReport};
