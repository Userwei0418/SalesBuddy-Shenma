/* Render-only review polish. No Page setData, API calls, role inference or writes. */
(function (global) {
  'use strict';
  const has = (node, name) => String(node?.attrs?.class || '').split(/\s+/).includes(name);
  const plain = node => node?.tag === '#text' ? node.text : (node?.children || []).map(plain).join('');
  const text = (value, key) => ({tag: '#text', key, text: value});
  const walk = (node, fn) => {fn(node); for (const child of node.children || []) walk(child, fn);};
  // Only known presentation copy is removed. The same class names also occur
  // on API errors, evidence, values and permission messages in other pages.
  const quietClasses = {
    'index': ['web-home-metric-hint', 'web-review-metric-note', 'greeting-card-note', 'business-subtitle'],
    'customers': ['quadrant-note', 'radar-note', 'customer-task-scope-note'],
    'customer-claim': ['claim-subtitle'],
    'customer-create': ['hero-eyebrow', 'hero-subtitle'],
    'customer-assign-confirm': ['ai-copy'],
    'customer-edit': ['eyebrow', 'subtitle'],
    'customer-assets': ['actual-subtitle', 'fde-quick-description', 'linked-demo-note'],
    'opportunity-create': ['hero-subtitle', 'card-description', 'footer-hint', 'review-op-preview-note'],
    'opportunities': ['subtitle', 'opportunity-order-note'],
    'workbench': ['opportunity-order-note', 'quarter-filter-foot', 'web-list-scope'],
    'management-task-create': ['hero-subtitle', 'task-type-note', 'task-link-note'],
    'task-detail': ['coordination-entry-note'],
    'visit-entry': ['hero-eyebrow', 'hero-copy', 'process-card'],
    'visit-confirm': ['eyebrow', 'seven-help'],
    'demo-create': ['demo-eyebrow', 'demo-scene-caption', 'demo-voice-caption', 'demo-note', 'demo-submit-note'],
    'risks': ['hero-subtitle'],
    'risk-detail': ['section-eyebrow', 'resolution-help'],
    'report-detail': ['eyebrow'],
    'bi': ['timeline-scroll-hint'],
  };
  const quietText = {
    'login': ['按账号自动识别身份'],
    'index': ['点击指标查看任务；按当前账号授权范围统计', '按当前账号授权范围统计', '最近更新在前'],
    'customers': ['ACV 为当前在推商机总额；本年／历年仅切换确收和回款', 'CUSTOMER BATTLE CARD'],
    'customer-detail': ['CUSTOMER POSITION'],
    'customer-claim': ['请尝试其他客户名称关键词', '运营完成客户建档后，可在这里申请认领', '请选择一家可申请认领的客户'],
    'customer-create': ['直接填写内容，选择项支持搜索', '点击任一字段手动编辑', '点击开始，描述客户、行业、来源和联系人', '再次点击即可结束录入'],
    'customer-edit': ['修改后将成为 Agent 的新分析依据', '角色仅限决策者 / 影响者 / 使用者'],
    'customer-assets': ['确认已有实绩后，可从上方登记。', '已确认的经营实绩会显示在这里。', '仅展示明确关联此商机的任务', '从上方“创建 Demo”添加该商机的演示场景'],
    'opportunity-create': ['选择左侧客户后开始填写', '当前可见客户，可继续搜索'],
    'management-task-create': ['先确定任务范围，再填写任务内容', '说明交付物、完成标准和业务背景', '接收人确认后开始执行，也可说明原因拒绝', '点击开始，说清任务要求、完成标准和时间'],
    'task-detail': ['确认后进入执行；岗位任务只需一人领取，拒绝仅表示本人不领取。拒绝时必须填写原因，系统会立即通知发起人。', '确认完成后任务闭环；驳回时请说明需要补充或改进的内容。', '填写完成说明后可直接完成。', '请填写完成说明，提交后由发起人验收，通过才算完成。', '原任务和拒绝说明会被保留，新任务可重新修改'],
    'visit-entry': ['可搜索公司全部客户', '支持键盘输入与语音转写', '结束录音后自动转写', '支持音频与文档文件', '语音识别失败不影响手工录入；只有点击“提交结构化”后才会进入字段确认页。', '请按字段标签填写，确认页会提示补充', 'AI 将识别必填信息，并在确认页提示补充', '先记录事实，稍后确认客户与商机'],
    'visit-confirm': ['核对内容 · AI 质检 · 确认保存', '评估本次拜访录入质量，并给出评分与改进建议', '硬性检查下一步计划是否可执行', '已在上一页确认关联', '点击匹配结果，确认本次跟进的客户', '拜访已保存；采纳后还需确认负责人、商机和截止时间。', '下一步行动已保存在拜访记录中。获取建议后，可逐条确认是否创建待办。'],
    'visit-detail': ['已归档正文只读；建议由你确认后形成待办。'],
    'risk-detail': ['解除记录会保留处理人、时间和依据'],
    'risks': ['新识别的风险会自动进入这里'],
    'bi': ['按 90% / 70% / 50% / 30% / 10% 阶段降序展示', '已确认和已归档的拜访记录', '按预计成交月份分布'],
    'profile': ['围绕六项能力给出提升方法'],
    'member-growth': ['围绕六项能力给出提升方法', '综合评分与单项能力变化'],
  };
  // Component rules are keyed by the actual component tag, not generic .hint.
  const quietComponentText = {
    'fde-dashboard': ['项目进展与工作记录', '当前参与项目', '所选周期 · 协助项目', '所选周期 · 场景登记', '当前参与商机 · 按数量', '周一至周日 · 仅统计所选周期内记录', '所选周期内的已归档记录会显示在这里'],
    'fde-profile': ['本人接收的日常工作任务与客户任务', '本人填写并确认归档，新老客户均计入'],
    'opportunity-form': ['预测金额 = 填写金额 × 商机阶段百分比'],
  };
  const passive = node => {
    let result = true;
    walk(node, child => {
      if (Object.keys(child.events || {}).length || child.component ||
          ['button', 'input', 'textarea', 'select', 'picker', 'a', 'canvas'].includes(child.tag) ||
          ['alert', 'dialog'].includes(child.attrs?.role)) result = false;
    });
    return result;
  };
  function quietCopy(tree, page) {
    const route = page.route?.split('/')[1];
    const classes = quietClasses[route] || [];
    const strings = quietText[route] || [];
    function visit(nodes, components = []) {
      return nodes.filter(node => {
        if (node.tag === '#text') return true;
        if (['alert', 'dialog'].includes(node.attrs?.role) || node.attrs?.['aria-modal']) return true;
        if (route === 'visit-entry' && ['可以说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……', '可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……'].includes(node.attrs?.placeholder)) {
          node.attrs.placeholder = '输入拜访内容';
        }
        if (['opportunity-create', 'visit-confirm'].includes(route) && node.attrs?.placeholder === '填写项目名，可用部门、场景区分') {
          node.attrs.placeholder = '输入商机名称';
        }
        const value = plain(node).trim();
        const componentNames = node.component ? [...components, node.tag] : components;
        if (route === 'bi' && has(node, 'data-note') &&
            value.startsWith('数据口径经营数据按所选成员或团队汇总；') && passive(node)) {
          node.tag = 'details';
          const heading = (node.children || []).find(child => plain(child).trim() === '数据口径');
          if (heading) heading.tag = 'summary';
          return true;
        }
        // Keep permission state and the factual part of mixed captions, while
        // dropping their accompanying tutorial sentence.
        let compact = value;
        if (route === 'customer-assets' && has(node, 'opportunity-readonly-note')) {
          compact = value.replace('可通过右上方「编辑商机」修改商业信息。', '')
            .replace('当前账号可查看商业信息。', '商机信息只读 · ')
            .replace('协助名单可在此调整，调整后请保存。', '')
            .replace('协助名单由有权限的人员维护。', '协助名单只读')
            .replace(/\s*·\s*$/, '');
        } else if (route === 'customer-assets' && has(node, 'op-section-caption')) {
          compact = value.replace(/\s*·\s*(新老客户均计入|最新录入在前)$/, '');
        } else if (componentNames.includes('fde-profile') && has(node, 'records-note') && value === '本人填写并确认归档的拜访记录 · 全部历史') {
          compact = '全部历史';
        } else if (route === 'visit-entry') {
          const states = {
            '请先从匹配结果选择客户，再提交拜访内容。': '待选择客户',
            '请选择匹配结果后再填写拜访内容': '待选择客户',
            '还需选择本人参与的商机，才能提交拜访。': '待选择本人参与的商机',
            '选择客户后，仅可录入本人参与的商机': '仅本人参与的商机',
          };
          compact = states[value] || value;
        } else if (route === 'customer-detail' && has(node, 'rule-note') && value === '象限由事实数据自动计算；修改客户事实后，系统将重新评估。') {
          compact = '象限由系统评估';
        }
        if (compact !== value && passive(node)) {
          if (!compact) return false;
          node.children = [text(compact, node.key + '.quiet-copy')];
        }
        const matchesComponent = componentNames.some(name => (quietComponentText[name] || []).includes(value));
        // Never remove an actionable container just because its label happens
        // to match; its original bindings and runtime component remain intact.
        if (passive(node) && (classes.some(name => has(node, name)) || strings.includes(value) || matchesComponent)) return false;
        const before = node.children || [];
        node.children = visit(before, componentNames);
        // Remove wrappers emptied by this pass, not decorative icons/spacers
        // which were already empty in the original rendered tree.
        if (before.length && before.some(child => child.tag !== '#text' || String(child.text || '').trim()) &&
            !node.children.some(child => child.tag !== '#text' || String(child.text || '').trim()) && passive(node)) return false;
        return true;
      });
    }
    return visit(tree);
  }
  function initial(value) {
    const name = String(value || '').replace(/^\s*(?:【示例】|\[示例\])\s*/u, '');
    return (name.match(/[\p{L}\p{N}]/u) || ['客'])[0];
  }
  function adapt(tree, page) {
    for (const root of tree) walk(root, node => {
      if (node.tag === '#text') return;
      const attrs = node.attrs || {};
      if ('data-web-avatar-name' in attrs) {
        node.children = [text(initial(attrs['data-web-avatar-name']), node.key + '.web-initial')];
      }
      if (attrs['data-web-filter-value']) {
        const current = plain(node).trim();
        const compact = ['全部象限', '全部计划', '全部优先级', '金额：全部'].includes(current)
          ? '全部' : current.replace(/^金额[：:]\s*/, '');
        if (compact !== current) node.children = [text(compact, node.key + '.web-filter')];
      }
      if (attrs['data-web-review-stage']) {
        let stage, full;
        walk(node, child => {
          if (has(child, 'opportunity-stage')) stage = child;
          if (has(child, 'detail-opportunity-top')) {
            const identity = (child.children || []).find(c => c.tag === 'view');
            stage = (identity?.children || []).find(c => c.tag === 'label');
          }
          if (has(child, 'opportunity-meta') || has(child, 'detail-opportunity-meta')) {
            full = plain((child.children || []).find(c => c.tag === 'text'));
          }
        });
        // Retain the compact label if the detailed stage is absent or differs.
        if (stage && plain(stage).trim() && full?.startsWith(plain(stage).trim())) {
          stage.attrs = {...stage.attrs, hidden: true};
        }
      }
      if (page.route === 'pages/bi/index' && has(node, 'web-kpi-groups')) {
        for (const group of node.children || []) {
          const title = has(group, 'web-kpi-primary') ? '实绩与季度预测'
            : has(group, 'web-kpi-secondary') ? '商机结构' : null;
          if (!title) continue;
          group.children.unshift({tag: 'h2', key: group.key + '.review-title', attrs: {class: 'web-review-kpi-heading'}, events: {}, children: [text(title, group.key + '.review-title-text')]});
        }
      }
    });
    return quietCopy(tree, page);
  }
  global.SalesReviewPages = Object.freeze({adapt, initial});
})(window);
