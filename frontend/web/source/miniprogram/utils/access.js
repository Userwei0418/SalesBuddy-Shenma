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
  index:['overview.read'], customers:['battle_map.read'], workbench:['opportunity.read','opportunity.shared_read'],
  opportunities:['opportunity.read','opportunity.shared_read'], bi:['dashboard.read','profile.fde_read'],
  profile:['profile.sales_read','profile.fde_read'], 'member-growth':['profile.sales_read','profile.fde_read'],
  'customer-detail':['customer.read'], 'customer-assets':['opportunity.read','actual.read'],
  'visit-detail':['visit.read','visit.claimed_customer_read'], tasks:['task.read'], 'task-detail':['task.read'],
  risks:['risk.read'], 'risk-detail':['risk.read'], 'report-detail':['agent.operating_report'],
  'fde-records':['profile.fde_activity'], 'demo-create':['demo_scene.create','demo_scene.update'],
};
function can(session, key) {
  if (!session) return false;
  if(Array.isArray(key))return key.some(code=>can(session,code));
  if(key==='opportunity.shared_read')return session.sessionKind!=='demo_readonly' && session.capabilities?.sales_shared_opportunities===true;
  if(key==='visit.claimed_customer_read')return session.sessionKind!=='demo_readonly' && session.capabilities?.customer_claim_visit_read===true;
  if(key==='visit.company_customer_follow_up')return session.sessionKind!=='demo_readonly' && session.capabilities?.company_customer_follow_up===true;
  if(key==='visit.company_opportunity_follow_up')return session.sessionKind!=='demo_readonly' && session.capabilities?.company_opportunity_follow_up===true;
  if(grants(session).some(g=>g.permission_code===key && g.effect==='deny'))return false;
  if(session.permissions && typeof session.permissions==='object') {
    if(key==='team.view')return ['dashboard.read','profile.sales_read','profile.fde_read','battle_map.read','task.read','opportunity.read','visit.read','risk.read']
      .some(code=>hasScope(session,code,['teams','workspace']));
    return (ALIASES[key] || [key]).some(code=>session.permissions[code]===true &&
      !grants(session).some(g=>g.permission_code===code && g.effect==='deny'));
  }
  const caps=session.capabilities || {};
  if(Object.prototype.hasOwnProperty.call(caps,key))return caps[key]===true;
  // Compatibility with the currently deployed server; never infer grants from role names.
  const old=Object.keys(ALIASES).find(alias=>ALIASES[alias].includes(key));
  return !!old && caps[old]===true;
}
function grants(session) { return Array.isArray(session && session.permissionGrants) ? session.permissionGrants : []; }
// These are server-resolved action grants. A role, directory row, or the overall
// data_scope must never widen a different action (especially writes/exports).
function scopeFor(session, key) {
  const enabled=can(session,key);
  const rows=enabled ? grants(session).filter(g=>g.permission_code===key && g.effect==='allow') : [];
  const order=['self','assigned','teams','workspace'];
  const scopes=order.filter(scope=>rows.some(g=>g.scope_code===scope));
  // Old capability-only sessions retain their existing team switch. No team IDs
  // or workspace grant is invented; the server still validates every request.
  const allowed=enabled && (!(session && session.permissions) || scopes.length>0);
  if(allowed && !(session && session.permissions) && can(session,'team.view')) scopes.push('teams');
  const scope=allowed ? scopes[scopes.length-1] || 'unknown' : 'none';
  const teamIds=[...new Set(rows.filter(g=>g.scope_code==='teams').flatMap(g=>Array.isArray(g.team_ids)?g.team_ids:[]))].sort();
  const label={none:'未授权',unknown:'已授权范围',self:'仅本人',assigned:'本人关联业务',teams:'已授权部门',workspace:'公司全部部门'}[scope];
  return {allowed,scope,scopes,teamIds,label};
}
function scopeLabel(session,key) {
  return session && session.permissions ? scopeFor(session,key).label : (session && session.scope) || '已授权范围';
}
function canObject(session,key,object,flag) {
  if(!object || !can(session,key))return false;
  if(object.action_permissions && Object.prototype.hasOwnProperty.call(object.action_permissions,key))return object.action_permissions[key]===true;
  if(flag && Object.prototype.hasOwnProperty.call(object,flag))return object[flag]===true;
  return !session.permissions; // Existing capability-only responses lack object flags.
}
function hasScope(session,key,scopes) {
  return scopeFor(session,key).scopes.some(scope=>scopes.includes(scope));
}
function canStartVisit(session) { return can(session,['visit.create','visit.company_opportunity_follow_up','visit.company_customer_follow_up']); }
function canVisitAction(session, permission, data = {}) {
  if (can(session,permission)) return true;
  if (!can(session,['visit.company_opportunity_follow_up','visit.company_customer_follow_up'])) return false;
  if (['visit.upload','visit.transcribe','visit.retry_import'].includes(permission)) return true;
  if (permission === 'visit.read') return Boolean(data.importId);
  const target=data.targetKind==='customer' && !data.opportunityId
    ? can(session,'visit.company_customer_follow_up') && Boolean(data.customerId)
    : can(session,'visit.company_opportunity_follow_up') && Boolean(data.opportunityId && data.opportunityId !== '__new__');
  return !data.editing && target && ['visit.create','visit.structure','visit.quality_review'].includes(permission);
}
function assignedVisitOnly(session) {
  if(!session || !session.permissions)return isFde(session && session.role);
  const scopes=scopeFor(session,'visit.create').scopes;
  return scopes.length===1 && scopes[0]==='assigned';
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
  result.canOpenCustomerCreate=pageAllowed(session,'customer-create');
  if(['index','visit-entry','visit-confirm'].includes(route))result.canRecordVisit=canStartVisit(session);
  result.canCompanyCustomerFollowUp=can(session,'visit.company_customer_follow_up');
  result.canCompanyOpportunityFollowUp=can(session,'visit.company_opportunity_follow_up');
  Object.entries({canCreateOpportunity:'opportunity.create',canUpdateOpportunity:'opportunity.update',
    canCreateActual:'actual.create',canVoidActual:'actual.void',canUploadVisit:'visit.upload',canTranscribe:'visit.transcribe',
    canRetryImport:'visit.retry_import',canReadVisit:'visit.read',canFirstVisit:'visit.first_visit',canManageAttendance:'visit.attendance_manage',
    canRequestAdvice:'advice.request',canReadAdvice:'advice.read',canViewRanking:'dashboard.ranking',
    canCreateDemo:'demo_scene.create',canEditDemo:'demo_scene.update',canDeleteDemo:'demo_scene.delete'}).forEach(([flag,key])=>result[flag]=can(session,key));
  // Import processing is part of recording, not permission to read saved visits.
  result.canReadVisitImport=result.canReadVisit;
  result.canReadVisitRecords=result.canReadVisit || can(session,'visit.claimed_customer_read');
  if(['visit-entry','visit-confirm'].includes(route) && (result.canCompanyOpportunityFollowUp || result.canCompanyCustomerFollowUp)) {
    result.canUploadVisit=true;result.canTranscribe=true;result.canRetryImport=true;result.canReadVisitImport=true;
  }
  const feature={customers:'battle_map.read',workbench:'opportunity.read',opportunities:'opportunity.read',bi:'dashboard.read',profile:'profile.sales_read',tasks:'task.read'}[route];
  if(feature)result.canViewTeam=canViewTeam(session,feature);
  result.canReadDemo=can(session,'demo_scene.read');
  result.canReadTargets=can(session,'target.read');
  result.canCustomerAdvice=can(session,'advice.request')&&can(session,'advice.customer');
  result.canOpportunityAdvice=can(session,'advice.request')&&can(session,'advice.opportunity');
  result.canReadCustomerAdvice=can(session,'advice.read')&&can(session,'advice.customer');
  result.canReadOpportunityAdvice=can(session,'advice.read')&&can(session,'advice.opportunity');
  result.canReadVisitAdvice=can(session,'advice.read')&&can(session,'advice.visit');
  result.requiresAssignedVisit=assignedVisitOnly(session);
  result.canSuggestVisitTasks=can(session,'advice.request') && (!session.permissions || can(session,'advice.visit'));
  return result;
}
function pageAllowed(session, route, options={}) {
  if(session && session.permissions && !can(session,'access.mini_program'))return false;
  if(route==='fde-records' && options.mode==='authorized')return can(session,['visit.read','visit.claimed_customer_read']);
  if(route==='customer-assets' && options.opportunity_id && can(session,'opportunity.shared_read'))return true;
  if(route==='customer-create')return can(session,['customer.create','customer.create_request']);
  if(route==='management-task-create'&&options.adviceId&&!can(session,'advice.decide'))return false;
  if(route==='customer-assign-confirm')return can(session,'customer.create');
  if(route==='visit-confirm'&&options.visitId)return can(session,'visit.supplement');
  if(['visit-entry','visit-confirm'].includes(route))return canStartVisit(session);
  if(route==='demo-create')return can(session,options.view==='1'?'demo_scene.read':options.demo_id?'demo_scene.update':'demo_scene.create');
  if(route==='opportunity-create')return can(session,(options.opportunityId||options.opportunity_id)?'opportunity.update':'opportunity.create');
  if(session && session.permissions && READ_ROUTES[route])return can(session,READ_ROUTES[route]);
  if(route==='fde-records')return isFde(session&&session.role)&&can(session,'opportunity.read');
  return !ROUTES[route] || can(session,ROUTES[route]);
}
function stableValue(value) {
  if(Array.isArray(value))return value.map(stableValue).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
  if(value && typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,stableValue(value[key])]));
  return value;
}
function authorizationIdentity(session) {
  if(!session)return '';
  return JSON.stringify(stableValue({permissions:session.permissions||null,grants:grants(session),
    capabilities:session.capabilities||null,clientExportPermissions:session.clientExportPermissions||{},teams:session.teamIds||[],version:session.permissionVersion||''}));
}
function identity(session) { return session ? [session.workspaceId,session.userId,session.role,session.loginAt || '',authorizationIdentity(session)].join(':') : ''; }
// A setData call may synchronously render in Web. On identity changes, clear
// visible read results in the very first patch, before flags or filters change.
function readStateReset(route) {
  switch(route) {
    case 'fde-records': return {context:null,records:[],recordsLoading:false,recordsError:'',recordsMore:false,recordsOffset:0,recordsTotal:0};
    case 'tasks': return {tasks:[],filteredTasks:[],totalCount:0,filteredTotal:0,pendingCount:0,completedCount:null,endedCount:null,stoppedCount:null,unknownOutcomeCount:null,rejectedCount:0,overdueCount:null,teamMemberCount:null,cancelOpen:false,cancelTaskId:'',hasMore:false};
    case 'risks': return {risks:[],filteredRisks:[],summary:null,total:0,openCount:null,resolvedCount:null,offset:0,hasMore:false,teamOptions:[],memberOptions:[],selectedTeamId:'',selectedMemberId:'',scopeMode:'all',memberPickerOpen:false};
    case 'opportunities': return {opportunities:[],filtered:[],opportunityGroups:[],total:0,totalAmount:'—',hasMore:false};
    case 'workbench': return {opportunityItems:[],filteredOpportunities:[],opportunityGroups:[],opportunityBoard:{},opportunityTotal:0,opportunityDataReady:false,opportunityHasMore:false};
    case 'customers': return {customers:[],plotCustomers:[],selectedCustomer:null,selectedBattleCustomer:null,plotCandidates:[],activeCustomerCount:0,assetSummary:null,recognizedText:'—',collectionText:'—',acvText:'—',scopeCustomerCount:'—',unknownAcvCount:0};
    case 'customer-detail': return {customer:null,detailPages:{},expandedVisitId:'',visitDetailState:{loading:false,error:'',ready:false}};
    case 'risk-detail': return {risk:null,resolutionNote:'',noteCount:0,decisionChoice:'',decisionNote:'',decisionError:'',refreshRequired:false};
    default: return {};
  }
}
// Resolve the actual operation, not the generic "retry / continue" button name.
// null is reserved for a local explanation that performs no network request.
function visitImportAction(data={}) {
  if(data.importId)return data.importStatus==='failed'
    ? {kind:'retry',permission:'visit.retry_import'} : {kind:'read',permission:'visit.read'};
  if(['legacy_unknown','conflict'].includes(data.uploadIntentState))return {kind:'local_reason',permission:null};
  return {kind:'upload',permission:'visit.upload'};
}
const ACTIONS={
 workbench:{createOpportunity:'opportunity.create',editOpportunity:'opportunity.update'},
 index:{startVisitRecording:['visit.create','visit.company_opportunity_follow_up','visit.company_customer_follow_up'],submitVisitTranscript:'visit.create',redoVisitRecording:'visit.create',openCustomerClaim:'customer.claim',openCustomerCreate:'customer.create',confirmManagementCustomer:'customer.create',confirmVisit:'visit.create'},
 customers:{openCustomerCreate:['customer.create','customer.create_request'],openCustomerClaim:'customer.claim',editCustomer:'customer.edit',createOpportunity:'opportunity.create',editOpportunity:'opportunity.update',createTask:'task.create',recordVisit:'visit.create'},
 'customer-claim':{openCustomerCreate:['customer.create','customer.create_request']},
 'customer-detail':{supplementVisit:'visit.supplement',createOpportunity:'opportunity.create',editOpportunity:'opportunity.update',recordVisit:'visit.create',createTask:'task.create'},
 'customer-assets':{saveFdeMembers:'fde.members.manage',recordFdeVisit:['visit.create','visit.company_opportunity_follow_up']},
 'opportunity-create':{submit:page=>(page.opportunityId||page.data.opportunityId||page.data.existing)?'opportunity.update':'opportunity.create'},'customer-create':{submitCustomer:page=>page.data?.requestMode?'customer.create_request':'customer.create'},'customer-assign-confirm':{confirmArchive:'customer.create'},'customer-edit':{submit:'customer.edit'},
 'visit-entry':{toggleRecording:'visit.transcribe',chooseMaterial:'visit.upload',submitTranscript:'visit.structure',toggleRecord:'visit.transcribe',startRecord:'visit.transcribe',chooseFile:'visit.upload',uploadFile:'visit.upload',retryImport:page=>visitImportAction(page.data).permission,submit:'visit.create',generate:'visit.create'},
 'visit-confirm':{archive:'visit.create',submitArchive:'visit.create',saveSupplement:'visit.supplement',review:'visit.quality_review'},
 'risk-detail':{resolve:'risk.resolve',submit:'risk.resolve',confirmResolve:'risk.resolve',confirmDecision:'risk.decide'},
 'management-task-create':{submit:'task.create',submitTask:'task.create',confirmCreate:'task.create'}
};
function protectActions(app,page,route){if(page._protectedActions)return;page._protectedActions=true;Object.entries(ACTIONS[route]||{}).forEach(([name,cap])=>{const original=page[name];if(typeof original!=='function')return;page[name]=function(...args){const key=typeof cap==='function'?cap(this):cap;if(key!==null && !(['visit-entry','visit-confirm'].includes(route) ? canVisitAction(app.globalData.session,key,this.data) : app.can(key))){wx.showToast({title:'当前身份未开放此项操作',icon:'none'});return;}return original.apply(this,args);};});}
module.exports={canStartVisit,canVisitAction,protectActions,visitImportAction,FDE_ROLES,FLAGS,ROUTES,isFde,can,canObject,flags,pageAllowed,identity,authorizationIdentity,scopeFor,scopeLabel,hasScope,assignedVisitOnly,fdeProjectView,canViewTeam,presentationOptions,readStateReset};
