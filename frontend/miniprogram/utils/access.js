// Capability checks mirror the server actor; this module never grants FDE a sales role.
const FDE_ROLES = ['fde', 'fde_lead'];
const FLAGS = {canReadCustomer:'customer.read',canCreateCustomer:'customer.create',canEditCustomer:'customer.edit',canClaimCustomer:'customer.claim',canEditOpportunity:'opportunity.edit',canManageFde:'fde.members.manage',canRecordVisit:'visit.create',canSupplementVisit:'visit.supplement',canCreateTask:'task.create',canRespondTask:'task.respond',canCoordinateTask:'task.coordinate',canResolveRisk:'risk.resolve',canDecideAdvice:'advice.decide',canManageActual:'actual.manage',canViewTeam:'team.view'};
const ROUTES = {'customer-create':'customer.create','customer-edit':'customer.edit','customer-claim':'customer.claim','opportunity-create':'opportunity.edit','visit-entry':'visit.create','visit-confirm':'visit.create','management-task-create':'task.create'};
function isFde(role) { return FDE_ROLES.includes(role); }
function can(session, key) {
  if (!session) return false;
  if (session.capabilities && typeof session.capabilities === 'object') return session.capabilities[key] === true;
  return false; // A role name is never a capability grant.
}
function flags(session) { const result={isFde:isFde(session && session.role),isFdeLead:!!session&&session.role==='fde_lead'}; Object.keys(FLAGS).forEach(k=>result[k]=can(session,FLAGS[k])); return result; }
function pageAllowed(session, route, options={}) {
  if(route==='fde-records')return isFde(session&&session.role)&&can(session,'opportunity.read');
  if(route==='management-task-create'&&options.adviceId&&!can(session,'advice.decide'))return false;
  if (route==='customer-assign-confirm') return can(session,'customer.create');
  if (route==='visit-confirm' && options.visitId) return can(session,'visit.supplement');
  return !ROUTES[route] || can(session,ROUTES[route]);
}
function identity(session) { return session ? [session.workspaceId,session.userId,session.role,session.permissionVersion || '',session.loginAt || ''].join(':') : ''; }
const ACTIONS={
 workbench:{createOpportunity:'opportunity.edit',editOpportunity:'opportunity.edit'},
 index:{startVisitRecording:'visit.create',submitVisitTranscript:'visit.create',redoVisitRecording:'visit.create',openCustomerClaim:'customer.claim',openCustomerCreate:'customer.create',confirmManagementCustomer:'customer.create',confirmVisit:'visit.create'},
 customers:{openCustomerClaim:'customer.claim',editCustomer:'customer.edit',createOpportunity:'opportunity.edit',editOpportunity:'opportunity.edit',createTask:'task.create',recordVisit:'visit.create'},
 'customer-detail':{supplementVisit:'visit.supplement',createOpportunity:'opportunity.edit',editOpportunity:'opportunity.edit',recordVisit:'visit.create',createTask:'task.create'},
 'customer-assets':{openForm:'actual.manage',submit:'actual.manage',submitConfirmed:'actual.manage',voidEntry:'actual.manage',saveFdeMembers:'fde.members.manage',recordFdeVisit:'visit.create'},
 'opportunity-create':{submit:'opportunity.edit'},'customer-create':{submit:'customer.create'},'customer-edit':{submit:'customer.edit'},
 'visit-entry':{toggleRecording:'visit.create',chooseMaterial:'visit.create',submitTranscript:'visit.create',toggleRecord:'visit.create',startRecord:'visit.create',chooseFile:'visit.create',uploadFile:'visit.create',retryImport:'visit.create',submit:'visit.create',generate:'visit.create'},
 'visit-confirm':{archive:'visit.create',submitArchive:'visit.create',saveSupplement:'visit.supplement',review:'visit.create'},
 'risk-detail':{resolve:'risk.resolve',submit:'risk.resolve',confirmResolve:'risk.resolve'},
 'management-task-create':{submit:'task.create',submitTask:'task.create',confirmCreate:'task.create'}
};
function protectActions(app,page,route){if(page._protectedActions)return;page._protectedActions=true;Object.entries(ACTIONS[route]||{}).forEach(([name,cap])=>{const original=page[name];if(typeof original!=='function')return;page[name]=function(...args){if(!app.can(cap)){wx.showToast({title:'当前身份未开放此项操作',icon:'none'});return;}return original.apply(this,args);};});}
module.exports={protectActions,FDE_ROLES,FLAGS,ROUTES,isFde,can,flags,pageAllowed,identity};
