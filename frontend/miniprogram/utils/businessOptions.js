// Runtime contract, populated exclusively by the authenticated metadata endpoint.
// Arrays keep their identity so consumers never retain a previous account's copy.
const customer = {industry:[],customer_type:[],level_code:[],source:[],contact_role:[]};
const stages = [], grades = [], mapAmountRanges = [];
let ready = false;
function clear() {ready=false;Object.values(customer).forEach(a=>a.splice(0));stages.splice(0);grades.splice(0);mapAmountRanges.splice(0);}
function install(value) {
  if (!value || value.contract_version!==1 || !value.customer || !value.opportunity ||
      Object.keys(customer).some(k=>!Array.isArray(value.customer[k]) || !value.customer[k].length || value.customer[k].some(v=>typeof v!=='string')) ||
      !Array.isArray(value.opportunity.stages) || !value.opportunity.stages.length ||
      !Array.isArray(value.opportunity.grades) || !value.opportunity.grades.length) throw Error('业务选项目录不可用，请重试');
  if(!Array.isArray(value.map_amount_ranges)||value.map_amount_ranges.some(r=>!r.value||!r.label||!Number.isFinite(r.min)||!(r.max===null||Number.isFinite(r.max))))throw Error('金额筛选目录不可用，请重试');
  const ss=value.opportunity.stages,gg=value.opportunity.grades;
  if(ss.some(s=>!s.code||!s.label||!s.text||!['open','won','lost'].includes(s.status)||!(s.probability===null||Number.isFinite(s.probability))) ||
     gg.some(g=>!g.code||!g.label||!Number.isFinite(g.min)||!(g.max===null||Number.isFinite(g.max))) ||
     new Set(ss.map(s=>s.code)).size!==ss.length||new Set(gg.map(g=>g.code)).size!==gg.length) throw Error('业务选项目录格式错误，请重试');
  Object.keys(customer).forEach(k=>customer[k].splice(0,customer[k].length,...value.customer[k]));
  stages.splice(0,stages.length,...ss.map(s=>({...s})));
  grades.splice(0,grades.length,...gg.map(g=>({...g,max:g.max===null?Infinity:g.max})));
  mapAmountRanges.splice(0,mapAmountRanges.length,...value.map_amount_ranges.map(r=>({...r,max:r.max===null?Infinity:r.max})));
  ready=true;return value;
}
module.exports={customer,stages,grades,mapAmountRanges,install,clear,isReady:()=>ready};
