import React from 'react';
import {SbProvider} from '@shandiant/ui-react';
import {PC_THEME} from './theme.js';

// 所有 React 根节点（外框与页面）统一从这里取主题：部门桥接主题 + PC 视觉 2.0 覆盖。
export function PcProvider({children}) {
  return <SbProvider theme={PC_THEME}>{children}</SbProvider>;
}
