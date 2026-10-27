import echartsTheme from '@shandiant/tokens/bridge-echarts';

// PC 视觉 2.0 的 Ant Design 主题：在部门桥接主题（@shandiant/tokens bridge-antd）之上覆盖。
// 神码主色（飞书蓝 #2863CD 一档），控件与字号收紧到 PC 办公软件的密度；颜色与 pc-theme.css 的 --ui-* 一一对应。
export const PC_COLORS = Object.freeze({
  primary: '#2863CD', primaryHover: '#3F77DA', primaryActive: '#1F4FA7', primaryBg: '#EDF3FC', primaryBgStrong: '#DFEAFE',
  ink: '#1F2329', secondary: '#646A73', muted: '#8F959E', line: '#E3E5E8', lineStrong: '#D0D3D6',
  background: '#F5F6F8', surface: '#FFFFFF', subtle: '#F7F8FA',
  success: '#1F8A4C', warning: '#B25F00', danger: '#D83931',
});

export const PC_THEME = Object.freeze({
  token: {
    colorPrimary: PC_COLORS.primary, colorInfo: PC_COLORS.primary, colorLink: PC_COLORS.primary,
    colorLinkHover: PC_COLORS.primaryHover, colorLinkActive: PC_COLORS.primaryActive,
    colorSuccess: PC_COLORS.success, colorWarning: PC_COLORS.warning, colorError: PC_COLORS.danger,
    colorText: PC_COLORS.ink, colorTextSecondary: PC_COLORS.secondary, colorTextTertiary: PC_COLORS.muted, colorTextQuaternary: '#BBBFC4',
    colorBgLayout: PC_COLORS.background, colorBgContainer: PC_COLORS.surface, colorBgElevated: PC_COLORS.surface,
    colorBorder: PC_COLORS.lineStrong, colorBorderSecondary: PC_COLORS.line, colorSplit: PC_COLORS.line,
    colorPrimaryBg: PC_COLORS.primaryBg, colorPrimaryBgHover: PC_COLORS.primaryBgStrong, colorFillTertiary: '#F2F3F5', colorFillQuaternary: PC_COLORS.subtle,
    borderRadius: 6, borderRadiusLG: 8, borderRadiusSM: 4,
    controlHeight: 32, controlHeightLG: 36, controlHeightSM: 24,
    fontSize: 14, fontSizeSM: 12, fontSizeLG: 15, fontSizeXL: 18,
    fontSizeHeading1: 24, fontSizeHeading2: 20, fontSizeHeading3: 18, fontSizeHeading4: 16, fontSizeHeading5: 14,
    lineHeight: 1.5715,
    boxShadow: '0 4px 16px rgba(31, 35, 41, 0.08)',
    boxShadowSecondary: '0 6px 20px rgba(31, 35, 41, 0.10), 0 1px 4px rgba(31, 35, 41, 0.04)',
  },
  components: {
    Button: {fontWeight: 400, primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none', paddingInline: 14},
    Table: {headerBg: PC_COLORS.subtle, headerColor: PC_COLORS.secondary, headerSplitColor: 'transparent', cellFontSize: 13,
      cellPaddingBlock: 10, cellPaddingInline: 12, cellPaddingBlockSM: 7, cellPaddingInlineSM: 10, rowHoverBg: '#F5F8FF', borderColor: PC_COLORS.line},
    Menu: {itemHeight: 36, itemBorderRadius: 6, itemMarginInline: 8, itemMarginBlock: 2, itemBg: 'transparent', subMenuItemBg: 'transparent',
      itemColor: PC_COLORS.ink, itemHoverColor: PC_COLORS.ink, itemHoverBg: 'rgba(31, 35, 41, 0.06)', itemSelectedBg: PC_COLORS.primaryBgStrong,
      itemSelectedColor: PC_COLORS.primary, itemActiveBg: PC_COLORS.primaryBgStrong, groupTitleColor: PC_COLORS.muted, groupTitleFontSize: 12,
      iconSize: 16, collapsedIconSize: 18, iconMarginInlineEnd: 10, subMenuItemBorderRadius: 6, fontSize: 14},
    Tabs: {titleFontSize: 14, horizontalItemGutter: 24, horizontalItemPadding: '10px 0', cardPadding: '6px 14px'},
    Segmented: {itemSelectedColor: PC_COLORS.primary, trackBg: '#EFF0F2', itemColor: PC_COLORS.secondary, itemHoverColor: PC_COLORS.ink},
    Card: {headerFontSize: 15, headerHeight: 48, paddingLG: 16, headerPadding: 16},
    Modal: {titleFontSize: 16},
    Drawer: {padding: 20},
    Tag: {defaultBg: '#F2F3F5', defaultColor: PC_COLORS.secondary},
    Statistic: {contentFontSize: 24, titleFontSize: 13},
    Breadcrumb: {fontSize: 14, lastItemColor: PC_COLORS.ink, linkColor: PC_COLORS.secondary, separatorColor: PC_COLORS.muted},
    Select: {optionSelectedBg: PC_COLORS.primaryBg},
    Badge: {textFontSizeSM: 11},
    Tooltip: {fontSize: 12},
  },
});

// 图表配色跟着主色变浅：部门组件库在第一次画图时才注册 ECharts 主题，这里在启动时把同一份主题对象改为 PC 配色。
const CHART_COLORS = ['#2863CD', '#14C0B4', '#7B61FF', '#FF9A2E', '#8F959E'];
if (Array.isArray(echartsTheme?.color)) echartsTheme.color.splice(0, echartsTheme.color.length, ...CHART_COLORS);
if (echartsTheme?.textStyle) echartsTheme.textStyle.color = PC_COLORS.secondary;
for (const axis of ['categoryAxis', 'valueAxis']) {
  const value = echartsTheme?.[axis];
  if (value?.axisLine?.lineStyle) value.axisLine.lineStyle.color = PC_COLORS.line;
  if (value?.axisTick?.lineStyle) value.axisTick.lineStyle.color = PC_COLORS.line;
  if (value?.axisLabel) value.axisLabel.color = PC_COLORS.secondary;
  if (value?.splitLine?.lineStyle) value.splitLine.lineStyle.color = '#EEF0F2';
}
