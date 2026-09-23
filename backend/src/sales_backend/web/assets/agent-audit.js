import {state, api, query, head, filters, options, people, table, pager, esc, date, num,
  dialog, details, bindCommon, download, roles, stats} from "./core.js";
import {capabilityLabels, capabilityLabel} from "./ai-capabilities.js";
import {providers, label, agentId, agentName, modelName, calledAgents, errorName, requestStates, stopStates} from "./ai-audit-labels.js";
const outcomes = {accepted:"结果校验通过", failed:"未取得合格结果", running:"推理中",
  cancelled:"已取消", reconciliation_required:"需要核对写入回执", unknown:"证据不完整"};
const business = {queued:"等待处理", running:"处理中", waiting_human:"等待人工确认",
  succeeded:"业务已完成", failed:"业务失败", cancelled:"已取消", superseded:"资料已变化，建议已过期", dead_letter:"后台任务失败"};
const phases = {route_started:"开始推理",output_received:"收到结构化输出",contract_accepted:"业务契约校验通过",
  fallback_requested:"准备兜底接续",route_failed:"本次路径未通过"};
const select = (name, label, values) => '<select name="'+name+'" aria-label="'+label+'">'+options(values,state.filters[name],label)+'</select>';
const small = value => '<small>'+esc(value)+'</small>';

function source(row) {
  const trace = row.trace || {};
  if (!trace.provider) return "未确认采用来源";
  return label(providers, trace.provider, "未记录提供方") +
    (trace.fallback_reason ? "接续 · " + errorName(trace.fallback_reason) : "");
}
function businessState(row) {
  if (row.business_status) return label(business, row.business_status, "业务状态待核实");
  if (row.job_effect_recorded) return "已记录业务保存回执";
  if (row.job_status) return label(business, row.job_status, "后台状态待核实");
  return "暂无关联业务终态";
}
function runDetail(row) {
  const sections = (row.operations || []).map((o,index) => {
    const config = o.configuration || {}, platform = o.trace?.platform_run || {}, attempts = o.attempts || [];
    return '<section class="activity-section"><h3>推理 '+(index+1)+' · '+esc(label(outcomes, o.inference_status, "推理状态待核实"))+'</h3>' +
      details([["推理编号",o.id],["开始时间",date(o.started_at)],["完成时间",date(o.completed_at)],
        ["采用来源",source(o)],["实际调用智能体",calledAgents(o)],
        ["配置的智能体",agentName(config.agent_id)],["配置的智能体编号",config.agent_id],
        ["兜底方式",row.capability === "opportunity_change" ? "原有商机变化规则" : "原模型接口"],
        ["中台 / 总预算",config.platform_seconds == null ? "历史未记录" : config.platform_seconds+" / "+config.total_seconds+" 秒"],
        ["期望发布快照",config.expected_snapshot_id],
        ["公司规则",config.company_policy ? "公司规则第 "+config.company_policy.version+" 版" : "历史未记录"],
        ["规则编号",config.company_policy?.id],
        ["业务指引版本",config.agent_business_policy ?
          (config.agent_business_policy.source === "baseline" ? "能力基线" : "公司业务指引")+"第 "+config.agent_business_policy.version+" 版" : "历史未记录"],
        ["业务指引编号",config.agent_business_policy?.id],
        ["指引版本记录来源",config.agent_business_policy ? "后端本次加载记录，不代表中台执行证明" : "历史未记录"],
        ["运行配置版本",config.execution_policy?.id || (config.execution_policy?.source === "unavailable" ? "配置不可用，已选择原模型接口" : "历史未记录")],
        ["本次执行快照",platform.runtime_snapshot_verified ? platform.actual_snapshot_id : "未能核实"],
        ["中台会话编号",platform.ids?.conversation_id],["中台消息编号",platform.ids?.message_id],["中台任务编号",platform.ids?.task_id],
        ["停止请求回执",label(stopStates,platform.stop_state)]]) +
      (o.receipt_warning ? '<p class="notice">'+esc(o.receipt_warning)+'</p>' : '') +
      '<h4>处理过程</h4>' + ((o.events || []).map(e =>
        '<div class="barline"><span>'+esc(label(phases,e.phase,"处理阶段未记录"))+' · '+esc(label(providers,e.provider,"提供方未记录"))+'</span><strong>'+num(e.elapsed_ms)+' 毫秒</strong></div>'+
        (e.error_code ? '<p class="help">'+esc(errorName(e.error_code))+'</p>' : "")+
        (e.contract_code ? '<p class="help">'+esc(errorName(e.contract_code))+'</p>' : "")
      ).join("") || '<p class="help">该历史记录未保存阶段事件，不补造过程。</p>') +
      '<h4>提供方请求</h4>' + table(["提供方 / 智能体","发送情况","网络状态码","请求结果","耗时","输入 / 输出用量"],attempts,a =>
        '<tr><td>'+esc(label(providers,a.provider,"提供方未记录"))+small(modelName(a.model))+
        (agentId(a.model) ? small("智能体编号："+agentId(a.model)) : "")+'</td><td>'+
        (a.network_dispatch_suppressed ? "测试拦截，未发送" : "已记录请求尝试")+'</td><td>'+num(a.http_status)+
        '</td><td>'+esc(label(requestStates,a.status,"请求状态待核实"))+(a.error_code ? small(errorName(a.error_code)) : "")+'</td><td>'+num(a.latency_ms)+' 毫秒</td><td>'+
        num(a.input_tokens)+' / '+num(a.output_tokens)+'</td></tr>') +
      '<h4>请求关联编号</h4>'+attempts.map(a => details([
        ["本地请求记录编号",a.id],["请求编号",a.request_id],["上游链路编号",a.upstream_trace_id],
      ])).join("")+'</section>';
  }).join("");
  dialog("智能体业务运行详情",details([["业务能力",capabilityLabel(row.capability)],
    ["发起人",row.actor_name],["调用时角色",label(roles,row.actor_role,"历史未记录角色")],
    ["业务关联编号",row.case_id],["关联客户",row.customer_name || "未关联单一客户"],
    ["业务状态",businessState(row)],["保存的助手结果数",row.assistant_results],
    ["关联任务",row.job_id],["测试标记",row.test_injected ? "包含受控故障测试" : "未标记；不能据此判断为正式业务"]]) +
    '<p class="notice">请求返回、结果校验通过、人工确认和业务保存分别记录。历史缺失证据保持未知。</p>'+
    (row.capability === "visit_quality" ? '<p class="help">返回校验通过表示格式与字段符合要求。拜访能否保存，仍取决于质量评分和下一步审核；低分结果也会正常采用。</p>' : '')+
    '<p class="help">可复制中台会话编号，在中台按同一会话核对；完整技术记录保留在导出审计清单中。</p>'+sections,{wide:true});
}

export async function agentRuns() {
  if (!state.filters.period) state.filters.period = "month";
  const data = await api("/ai/runs" + query({limit:50,offset:state.offset}));
  const summary = data.statistics || {};
  const rate = (accepted, attempted) => attempted ? Math.round(100 * accepted / attempted) + "%" : "暂无样本";
  return {
    html:head("智能体运行审计","从一次业务操作，核对中台、原模型接口、结果校验与最终业务状态。",
      '<button data-action="export">导出审计清单</button>') +
      '<div class="notice">中台结果通过、原模型接口接续和业务完成分别判断。测试仅按已记录标记显示；未标记不代表正式业务。</div>' +
      stats([
        ["参与统计的业务",num(summary.eligible),"已排除 "+num(summary.excluded_fault_tests)+" 条故障测试"],
        ["中台结果合格",rate(summary.platform_accepted,summary.platform_attempted),num(summary.platform_accepted)+" / "+num(summary.platform_attempted)+" 条含中台请求的业务"],
        ["原模型接口接续合格",rate(summary.fallback_accepted,summary.fallback_requested),num(summary.fallback_accepted)+" / "+num(summary.fallback_requested)+" 条请求接续的业务"],
        ["原规则兜底",num(summary.rule_fallback_accepted),"商机变化评估；不计为原模型接口 调用"],
        ["业务完成 / 待确认",num(summary.business_succeeded)+" / "+num(summary.waiting_human),"直接原模型接口 合格 "+num(summary.direct_accepted)+" · 证据不完整 "+num(summary.unknown)],
      ]) + '<p class="help">统计覆盖当前筛选范围的全部业务，每条业务以最后一次推理结果计数。请求尝试不代表平台收到或完成；合格结果不等于业务写入。超过15分钟未有结束回执标为证据不完整。</p>' +
      '<div class="card">' + filters(
        select("period","时间范围",[["day","今日"],["week","本周"],["month","本月"],["custom","指定日期"]]) +
        '<input type="date" name="start" aria-label="起始日期" value="'+esc(state.filters.start || "")+'">'+
        '<input type="date" name="end" aria-label="结束日期" value="'+esc(state.filters.end || "")+'">' +
        select("capability","全部能力",Object.entries(capabilityLabels)) + select("provider","全部采用来源",Object.entries(providers)) +
        select("outcome","全部推理状态",Object.entries(outcomes)) +
        '<select name="actor" aria-label="发起人">'+people(state.filters.actor)+'</select>' +
        select("test_only","全部测试标记",[["true","含受控故障测试"],["false","未标记故障测试"]])) +
      table(["发起时间","能力 / 发起人","采用来源","推理结果","业务结果",""],data.items,r=>
        '<tr><td>'+date(r.started_at)+'</td><td><strong>'+esc(capabilityLabel(r.capability))+'</strong>'+
        small("实际调用："+calledAgents(r))+small(r.actor_name+" · "+(label(roles,r.actor_role,"历史未记录角色")))+'</td><td>'+esc(source(r))+
        (r.test_injected ? small("受控故障测试") : "")+'</td><td>'+esc(label(outcomes, r.inference_status, "推理状态待核实"))+
        '</td><td>'+esc(businessState(r))+'</td><td><button class="link" data-action="detail" data-id="'+esc(r.case_id)+
        '">查看链路</button></td></tr>') + pager(data.total) + '</div>',
    bind(root) {bindCommon(root,{export:()=>download("/ai/runs/export"+query(),"智能体运行审计"),
      detail:id=>runDetail(data.items.find(r=>r.case_id===id))});},
  };
}
