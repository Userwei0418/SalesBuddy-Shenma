// Capability checks mirror the server actor; this module never grants FDE a sales role.
const FDE_ROLES = ['fde', 'fde_lead'];
const FLAGS = {canReadCustomer:'customer.read',canCreateCustomer:'customer.create',canEditCustomer:'customer.edit',canClaimCustomer:'customer.claim',canEditOpportunity:'opportunity.edit',canManageFde:'fde.members.manage',canRecordVisit:'visit.create',canSupplementVisit:'visit.supplement',canCreateTask:'task.create',canRespondTask:'task.respond',canCoordinateTask:'task.coordinate',canResolveRisk:'risk.resolve',canDecideAdvice:'advice.decide',canManageActual:'actual.manage',canViewTeam:'team.view'};
const ROUTES = {'customer-create':'customer.create','customer-edit':'customer.edit','customer-claim':'customer.claim','opportunity-create':'opportunity.edit','visit-entry':'visit.create','visit-confirm':'visit.create','management-task-create':'task.create'};
function isFde(role) { return FDE_ROLES.includes(role); }
const ALIASES = {
  'customer.edit':['customer.update'], 'opportunity.edit':['opportunity.create','opportunity.update'],
  'fde.members.manage':['opportunity.fde_members'], 'task.create':['task.create_daily','task.create_customer'],
  'task.respond':['task.accept','task.decline','task.complete','task.review'],
  'actual.manage':['actual.create','actual.void'], 'console.access':['access.console'],
};
const READ_ROUTES = {
  index:['overview.read'], customers:['battle_map.read'], workbench:['opportunity.read'],
  opportunities:['opportunity.read'], bi:['dashboard.read','profile.fde_read'],
  profile:['profile.sales_read','profile.fde_read'], 'member-growth':['profile.sales_read','profile.fde_read'],
  'customer-detail':['customer.read'], 'customer-assets':['opportunity.read','actual.read'],
  'visit-detail':['visit.read'], tasks:['task.read'], 'task-detail':['task.read'],
  risks:['risk.read'], 'risk-detail':['risk.read'], 'report-detail':['agent.operating_report'],
  'fde-records':['profile.fde_activity'], 'demo-create':['demo_scene.create','demo_scene.update'],
};
function can(session, key) {
  if (!session) return false;
  if(Array.isArray(key))return key.some(code=>can(session,code));
  if(session.permissions && typeof session.permissions==='object') {
    if(key==='team.view')return ['dashboard.read','profile.sales_read','profile.fde_read','battle_map.read','task.read']
      .some(code=>hasScope(session,code,['teams','workspace']));
    return (ALIASES[key] || [key]).some(code=>session.permissions[code]===true);
  }
  const caps=session.capabilities || {};
  if(Object.prototype.hasOwnProperty.call(caps,key))return caps[key]===true;
  // Compatibility with the currently deployed server; never infer grants from role names.
  const old=Object.keys(ALIASES).find(alias=>ALIASES[alias].includes(key));
  return !!old && caps[old]===true;
}
function hasScope(session,key,scopes) {
  if(!can(session,key))return false;
  return (session.permissionGrants || []).some(g=>g.permission_code===key && g.effect==='allow' && scopes.includes(g.scope_code));
}
function assignedVisitOnly(session) {
  if(!session || !session.permissions)return isFde(session && session.role);
  const grants=(session.permissionGrants || []).filter(g=>g.permission_code==='visit.create' && g.effect==='allow');
  return grants.length>0 && grants.every(g=>g.scope_code==='assigned');
}
function fdeProjectView(session) {
  return isFde(session && session.role) && !(session && session.permissions && can(session,['opportunity.create','opportunity.update']));
}
function canViewTeam(session, feature) {
  return session && session.permissions ? hasScope(session,feature,['teams','workspace']) : can(session,'team.view');
}
function presentationOptions(session, surface) {
  const definitions=surface==='bi'?[['sales','经营看板','dashboard.read'],['fde','FDE 看板','profile.fde_read']]:[['sales','销售经营','profile.sales_read'],['fde','FDE 协作','profile.fde_read']];
  return definitions.filter(([kind,,permission])=>session&&session.permissions?can(session,permission):kind===(isFde(session&&session.role)?'fde':'sales')).map(([value,label])=>({value,label}));
}
function flags(session, route) {
  const result={isFde:isFde(session && session.role),isFdeLead:!!session&&session.role==='fde_lead'};
  Object.keys(FLAGS).forEach(k=>result[k]=can(session,FLAGS[k]));
  Object.entries({canCreateOpportunity:'opportunity.create',canUpdateOpportunity:'opportunity.update',
    canCreateActual:'actual.create',canVoidActual:'actual.void',canUploadVisit:'visit.upload',canTranscribe:'visit.transcribe',
    canRetryImport:'visit.retry_import',canFirstVisit:'visit.first_visit',canManageAttendance:'visit.attendance_manage',
    canRequestAdvice:'advice.request',canReadAdvice:'advice.read',canViewRanking:'dashboard.ranking',
    canCreateDemo:'demo_scene.create',canEditDemo:'demo_scene.update',canDeleteDemo:'demo_scene.delete'}).forEach(([flag,key])=>result[flag]=can(session,key));
  const feature={customers:'battle_map.read',workbench:'opportunity.read',opportunities:'opportunity.read',bi:'dashboard.read',profile:'profile.sales_read',tasks:'task.read'}[route];
  if(feature)result.canViewTeam=canViewTeam(session,feature);
  result.canReadDemo=can(session,'demo_scene.read');
  result.canReadTargets=can(session,'target.read');
  result.canCustomerAdvice=can(session,'advice.request')&&can(session,'advice.customer');
  result.canOpportunityAdvice=can(session,'advice.request')&&can(session,'advice.opportunity');
  result.requiresAssignedVisit=assignedVisitOnly(session);
  result.canSuggestVisitTasks=can(session,'advice.request') && (!session.permissions || can(session,'advice.visit'));
  return result;
}
function pageAllowed(session, route, options={}) {
  if(session && session.permissions && !can(session,'access.mini_program'))return false;
  if(route==='management-task-create'&&options.adviceId&&!can(session,'advice.decide'))return false;
  if(route==='customer-assign-confirm')return can(session,'customer.create');
  if(route==='visit-confirm'&&options.visitId)return can(session,'visit.supplement');
  if(route==='demo-create')return can(session,options.view==='1'?'demo_scene.read':options.demo_id?'demo_scene.update':'demo_scene.create');
  if(route==='opportunity-create')return can(session,(options.opportunityId||options.opportunity_id)?'opportunity.update':'opportunity.create');
  if(session && session.permissions && READ_ROUTES[route])return can(session,READ_ROUTES[route]);
  if(route==='fde-records')return isFde(session&&session.role)&&can(session,'opportunity.read');
  return !ROUTES[route] || can(session,ROUTES[route]);
}
function identity(session) { return session ? [session.workspaceId,session.userId,session.role,session.permissionVersion || '',session.loginAt || ''].join(':') : ''; }
const ACTIONS={
 workbench:{createOpportunity:'opportunity.create',editOpportunity:'opportunity.update'},
 index:{startVisitRecording:'visit.create',submitVisitTranscript:'visit.create',redoVisitRecording:'visit.create',openCustomerClaim:'customer.claim',openCustomerCreate:'customer.create',confirmManagementCustomer:'customer.create',confirmVisit:'visit.create'},
 customers:{openCustomerClaim:'customer.claim',editCustomer:'customer.edit',createOpportunity:'opportunity.create',editOpportunity:'opportunity.update',createTask:'task.create',recordVisit:'visit.create'},
 'customer-detail':{supplementVisit:'visit.supplement',createOpportunity:'opportunity.create',editOpportunity:'opportunity.update',recordVisit:'visit.create',createTask:'task.create'},
 'customer-assets':{openForm:'actual.create',submit:'actual.create',submitConfirmed:'actual.create',voidEntry:'actual.void',saveFdeMembers:'fde.members.manage',recordFdeVisit:'visit.create'},
 'opportunity-create':{submit:page=>(page.opportunityId||page.data.opportunityId||page.data.existing)?'opportunity.update':'opportunity.create'},'customer-create':{submit:'customer.create'},'customer-edit':{submit:'customer.edit'},
 'visit-entry':{toggleRecording:'visit.transcribe',chooseMaterial:'visit.upload',submitTranscript:'visit.structure',toggleRecord:'visit.transcribe',startRecord:'visit.transcribe',chooseFile:'visit.upload',uploadFile:'visit.upload',retryImport:'visit.retry_import',submit:'visit.create',generate:'visit.create'},
 'visit-confirm':{archive:'visit.create',submitArchive:'visit.create',saveSupplement:'visit.supplement',review:'visit.quality_review'},
 'risk-detail':{resolve:'risk.resolve',submit:'risk.resolve',confirmResolve:'risk.resolve'},
 'management-task-create':{submit:'task.create',submitTask:'task.create',confirmCreate:'task.create'}
};
function protectActions(app,page,route){if(page._protectedActions)return;page._protectedActions=true;Object.entries(ACTIONS[route]||{}).forEach(([name,cap])=>{const original=page[name];if(typeof original!=='function')return;page[name]=function(...args){const key=typeof cap==='function'?cap(this):cap;if(!app.can(key)){wx.showToast({title:'当前身份未开放此项操作',icon:'none'});return;}return original.apply(this,args);};});}
module.exports={protectActions,FDE_ROLES,FLAGS,ROUTES,isFde,can,flags,pageAllowed,identity,hasScope,assignedVisitOnly,fdeProjectView,canViewTeam,presentationOptions};
