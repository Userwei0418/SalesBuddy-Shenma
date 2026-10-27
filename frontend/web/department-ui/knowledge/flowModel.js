// Business context is user input, never retrieved evidence or an implicit CRM read.
export function composeKnowledgeQuestion(question, context = '', purpose = '') {
  return [context.trim() && `用户提供的业务背景（不是资料证据）：${context.trim()}`, purpose.trim() && `本次用途：${purpose.trim()}`, question.trim()].filter(Boolean).join('\n\n');
}
