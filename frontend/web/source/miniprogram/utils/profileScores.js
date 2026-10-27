const { scoreLight } = require('./statusLight');
function numeric(value) {
  if ((typeof value !== 'number' && typeof value !== 'string') || String(value).trim() === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
// Business scoring and rule versions belong to the backend. A missing result stays missing.
function displayScore(score) {
  const value = numeric(score && score.value);
  if (value === null || value < 0 || value > 100) {
    return {signal:scoreLight(null),value:null,text:'--',count:0,total:0,explanation:'暂无有效评分数据'};
  }
  return {...score,value,text:score.text || String(Math.round(value)),signal:scoreLight(value)};
}
module.exports={numeric,displayScore};
