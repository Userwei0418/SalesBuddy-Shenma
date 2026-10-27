const periods=require('../../utils/opportunityPeriodFilters');
Component({
 options:{styleIsolation:'apply-shared'},
 properties:{value:{type:Object,value:periods.initial()}},
 data:{expanded:false,quarters:['Q1','Q2','Q3','Q4']},
 methods:{
  toggle(){this.setData({expanded:!this.data.expanded});},
  update(patch){const value={...periods.initial(),...this.properties.value,...patch};periods.params(value);this.triggerEvent('change',{value});},
  toggleCreated(e){this.update({createdEnabled:e.detail.value===true});},
  toggleActive(e){this.update({activeQuarter:e.detail.value===true});},
  changeYear(e){const key=e.currentTarget.dataset.key,year=Number(String(e.detail.value).slice(0,4));if(!['createdYear','financialYear'].includes(key)||!Number.isInteger(year)||year<2000||year>2100)return;this.update({[key]:year});},
  changeQuarter(e){const key=e.currentTarget.dataset.key,quarter=Number(e.detail.value)+1;if(!['createdQuarter','financialQuarter'].includes(key)||!Number.isInteger(quarter)||quarter<1||quarter>4)return;this.update({[key]:quarter});}
 }
});
