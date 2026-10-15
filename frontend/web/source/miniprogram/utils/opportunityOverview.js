const {beijingDateParts}=require('./opportunityQuarter');

function overviewRequest(selection) {
  const allYears=selection.isAllYears || selection.year==='all';
  return {includeOverview:true,overviewYear:allYears?beijingDateParts(new Date()).year:Number(selection.year),
    overviewQuarters:allYears?[]:[...new Set(selection.quarters.map(Number))].sort()};
}

function validateFilteredOverview(response, selection) {
  const overview=response&&response.overview, metrics=overview&&overview.metrics;
  const expected=overviewRequest(selection);
  if(!overview || overview.definition_version!=='opportunity_filtered_overview_v1') {
    throw Error('商机筛选统计暂不可用，请稍后重试');
  }
  const keys=['total','won','active','newCount','missingCloseDates','missingWonDates','missingCreatedDates','demo_scene_count'];
  if(!metrics || !keys.every(key=>Number.isInteger(metrics[key])&&metrics[key]>=0)
      || !response.summary || metrics.total!==response.summary.total
      || ['won','active','newCount','missingCloseDates','missingWonDates','missingCreatedDates'].some(key=>metrics[key]>metrics.total)
      || overview.year!==expected.overviewYear || !Array.isArray(overview.quarters)
      || JSON.stringify(overview.quarters)!==JSON.stringify(expected.overviewQuarters)) {
    throw Error('商机统计与当前筛选不一致，请重试');
  }
  return metrics;
}

module.exports={overviewRequest,validateFilteredOverview};
