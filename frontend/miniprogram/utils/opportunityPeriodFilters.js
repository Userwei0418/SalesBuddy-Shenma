const {beijingDateParts}=require('./opportunityQuarter');
function initial(){const {year,quarter}=beijingDateParts(new Date());return {createdEnabled:false,createdYear:year,createdQuarter:quarter,activeQuarter:false,financialYear:year,financialQuarter:quarter};}
function active(value={}){return value.createdEnabled===true||value.activeQuarter===true;}
function params(value={}){
 const result={};
 const period=(year,quarter)=>{if(!Number.isInteger(year)||year<2000||year>2100||!Number.isInteger(quarter)||quarter<1||quarter>4)throw Error('请选择有效年份和季度');};
 if(value.createdEnabled===true){period(value.createdYear,value.createdQuarter);result.createdYear=value.createdYear;result.createdQuarter=value.createdQuarter;}
 if(value.activeQuarter===true){period(value.financialYear,value.financialQuarter);result.activeQuarter=true;result.financialYear=value.financialYear;result.financialQuarter=value.financialQuarter;}
 return result;
}
module.exports={initial,active,params};
