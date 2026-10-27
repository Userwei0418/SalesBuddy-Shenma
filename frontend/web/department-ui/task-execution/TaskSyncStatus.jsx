import React from 'react';

export const taskSyncUnavailable = sync => ['offline', 'error', 'paused'].includes(sync?.status);
export default function TaskSyncStatus({sync, lastSyncedLabel}) {
  const labels = {current: '手动刷新', syncing: '正在刷新', offline: '当前离线', error: '刷新失败', paused: '手动刷新'};
  const time = sync?.lastSyncedAt && new Date(sync.lastSyncedAt);
  const last = time && Number.isFinite(time.getTime()) ? time.toLocaleTimeString('zh-CN', {hour12: false}) : lastSyncedLabel;
  return <p className={taskSyncUnavailable(sync) ? 'ds-td-error' : 'ds-muted'} role="status" aria-label="任务刷新状态">
    {labels[sync?.status] || '手动刷新'}{last ? ` · 最近刷新：${last}` : ''}
    {sync?.message && <span> · {sync.message}</span>}
  </p>;
}
