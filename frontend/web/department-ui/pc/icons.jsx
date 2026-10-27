import React from 'react';

// 线性图标（24 栅格，1.6 描边），导航、顶栏与 PC 新页面共用；只用 currentColor，跟随文字颜色。
const PATHS = {
  workspace: '<rect x="3" y="4" width="18" height="14" rx="2.5"/><path d="M8 21h8M12 18v3"/>',
  switch: '<path d="M8 10l4-4 4 4M16 14l-4 4-4-4"/>',
  grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/>',
  customer: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0M16 4.5a3.5 3.5 0 010 7M18.5 14.5A6.5 6.5 0 0121.5 20"/>',
  opportunity: '<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7V5a2 2 0 012-2h3a2 2 0 012 2v2M3 12.5h18M10.5 12.5v2h3v-2"/>',
  visit: '<path d="M7 3.5h7l4.5 4.5v12.5H5.5V5A1.5 1.5 0 017 3.5z"/><path d="M14 3.5V8h4.5M8.5 12.5h7M8.5 16h4.5"/>',
  task: '<rect x="4.5" y="4" width="15" height="17" rx="2.5"/><path d="M9 3h6v3H9zM8.5 12.5l2.3 2.3 4.7-4.6"/>',
  report: '<path d="M4.5 20.5V5A1.5 1.5 0 016 3.5h12A1.5 1.5 0 0119.5 5v15.5l-3-2-3 2-3-2-3 2-3-2z"/><path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h4"/>',
  chart: '<path d="M4 3.5v17h16.5"/><path d="M8 16v-4.5M12.5 16V8M17 16v-6.5"/>',
  book: '<path d="M4 5.5A2 2 0 016 3.5h13v14H6a2 2 0 00-2 2z"/><path d="M4 19.5a2 2 0 002 2h13v-4M8.5 8h6.5M8.5 11.5h4.5"/>',
  add: '<path d="M12 5v14M5 12h14"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 014.8 1c0 1.7-2.4 2.1-2.4 3.6M12 17.3v.2"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 11-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  collapse: '<path d="M11 6l-6 6 6 6M19 6l-6 6 6 6"/>',
  expand: '<path d="M13 6l6 6-6 6M5 6l6 6-6 6"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21M8.5 21h7"/>',
  building: '<path d="M4 21V5.5A1.5 1.5 0 015.5 4h8A1.5 1.5 0 0115 5.5V21M15 9.5h3.5A1.5 1.5 0 0120 11v10M2.5 21h19M8 8h3M8 11.5h3M8 15h3"/>',
  send: '<path d="M4 12l16-7-6 16-2.5-6.5z"/><path d="M11.5 14.5L20 5"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M8 3v4M16 3v4M3.5 10h17"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0115 0"/>',
  team: '<circle cx="8.5" cy="8.5" r="3.2"/><circle cx="16.5" cy="9.5" r="2.6"/><path d="M2.8 20a5.8 5.8 0 0111.4 0M14.5 15.2a5 5 0 016.7 4.8"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0014.5 4h-9A1.5 1.5 0 004 5.5v9A1.5 1.5 0 005.5 16H8"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M4.5 19.5h15"/>',
  present: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v3.5M8 20.5h8M9.5 8l5 2-5 2z"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
  file: '<path d="M7 3.5h7l4.5 4.5v12.5H5.5V5A1.5 1.5 0 017 3.5z"/><path d="M14 3.5V8h4.5"/>',
  flag: '<path d="M5 21V4M5 4.5h11l-1.8 4 1.8 4H5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  alert: '<path d="M12 3.5l9 16H3z"/><path d="M12 10v4.5M12 17.2v.2"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
};

export function NavIcon({name, size}) {
  const path = PATHS[name] || PATHS.grid;
  return <span className="pc-icon" style={size ? {width: size, height: size} : undefined} aria-hidden="true"
    dangerouslySetInnerHTML={{__html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`}} />;
}
