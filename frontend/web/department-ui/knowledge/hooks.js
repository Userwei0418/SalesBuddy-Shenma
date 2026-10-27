import {useAsync} from '../pc/ui.jsx';
export function useKnowledgeRead(read, deps, enabled = true) {
  return useAsync(signal => enabled ? read(signal) : null, [enabled, ...deps]);
}
