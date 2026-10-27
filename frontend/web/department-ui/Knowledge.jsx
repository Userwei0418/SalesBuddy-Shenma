import React from 'react';
import KnowledgeUnavailable from './knowledge/KnowledgeUnavailable.jsx';

// Do not mount knowledge services, legacy tools, or distribution drawers.
export default function Knowledge({page}) {
  const title = {dashboard:'知识看板',materials:'资料库',assistant:'知识助手',works:'生成材料',catalog:'资料库',playbook:'知识助手',proposal:'生成材料'}[page?.options?.tab] || '知识库';
  return <KnowledgeUnavailable title={title} />;
}
