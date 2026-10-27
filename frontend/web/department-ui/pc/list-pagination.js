import {useCallback, useState} from 'react';
import {session} from './data.js';

export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
export function normalizePageSize(value, max = 100) {
  const options = PAGE_SIZE_OPTIONS.filter(size => size <= max);
  const size = Number(value);
  return options.includes(size) ? size : options.includes(20) ? 20 : options[0] || 10;
}
export function listPageSizeKey(name, account = session()) {
  return account?.workspaceId && account?.userId
    ? `sales-web:list-size:${account.workspaceId}:${account.userId}:${name}` : null;
}
export function readListPageSize(key, max = 100, storage) {
  try { return normalizePageSize(key ? (storage ?? globalThis.localStorage)?.getItem(key) : null, max); }
  catch { return normalizePageSize(null, max); }
}

// A UI preference only: no business rows, scopes or authentication values are stored.
export function useListPageSize(name, {max = 100} = {}) {
  const key = listPageSizeKey(name);
  const [saved, setSaved] = useState(() => ({key, value: readListPageSize(key, max)}));
  const value = saved.key === key ? normalizePageSize(saved.value, max) : readListPageSize(key, max);
  const update = useCallback(next => {
    const size = normalizePageSize(next, max);
    try { if (key) globalThis.localStorage?.setItem(key, String(size)); } catch {}
    setSaved({key, value: size});
  }, [key, max]);
  return [value, update];
}

// For tables whose authorized result is already fully loaded by their data layer.
export function useLocalTablePagination(name, resetKey = '') {
  const [pageSize, setPageSize] = useListPageSize(name);
  const key = `${listPageSizeKey(name)}:${resetKey}`;
  const [position, setPosition] = useState({key, page: 1});
  return {
    current: position.key === key ? position.page : 1,
    pageSize, showSizeChanger: true, pageSizeOptions: PAGE_SIZE_OPTIONS,
    showTotal: total => `共 ${total} 条`,
    onChange: (page, size) => {
      setPageSize(size);
      setPosition({key, page: size === pageSize ? page : 1});
    },
  };
}
