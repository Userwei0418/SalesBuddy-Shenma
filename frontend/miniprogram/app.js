const apiClient = require("./utils/apiClient");
const access = require("./utils/access");
const companyEntry = require("./utils/companyEntry");

// MINI-ONLY-BEGIN
const restrictedSession = require("./utils/restrictedSession");
restrictedSession.installPageGuard();
// MINI-ONLY-END

App({
  globalData: {
    session: null,
    role: "sales",
    roles: {
      fde: {name:"FDE",scope:"本人协助项目"},
      fde_lead: {name:"FDE主管",scope:"FDE团队协助项目"},
      sales: { name: "一线销售", scope: "仅本人" },
      supervisor: { name: "销售主管", scope: "直属团队" },
      manager: { name: "销售总经理", scope: "全部团队" },
      operations: {name:"运营",scope:"已授权范围"},
      administrator: {name:"系统管理员",scope:"已授权范围"},
    },
  },

  onLaunch() {
    try { require("./utils/rememberedLogin").cleanup(); }
    catch (_) { wx.showToast({title:"旧凭据清理失败，请清除小程序本地数据",icon:"none"}); }
    // MINI-ONLY-BEGIN
    const restored = restrictedSession.restore();
    if (restored) {
      apiClient.saveAuth(null);wx.removeStorageSync('salesSession');
      this.globalData.session=restored;this.globalData.role=null;
      return;
    }
    // MINI-ONLY-END
    let savedSession = wx.getStorageSync("salesSession");
    if (savedSession && savedSession.apiBaseUrl && savedSession.apiBaseUrl !== apiClient.getBaseUrl()) {
      apiClient.saveAuth(null);
      wx.removeStorageSync("salesSession");
      savedSession = null;
    }
    if (savedSession && apiClient.isEnabled() && savedSession.authMethod !== "password") {
      wx.removeStorageSync("salesSession");
      savedSession = null;
    }
    if (savedSession && savedSession.remote && this.globalData.roles[savedSession.role]) {
      const roleInfo = this.globalData.roles[savedSession.role];
      this.globalData.session = {
        ...savedSession,
        roleName: savedSession.roleName || roleInfo.name,
        scope: savedSession.permissions ? '按功能授权' : savedSession.scope || roleInfo.scope,
      };
      this.globalData.role = savedSession.role;
      wx.setStorageSync("salesSession", this.globalData.session);
    } else if (savedSession) {
      wx.removeStorageSync("salesSession");
    }
  },

  onShow(options = {}) {
    // MINI-ONLY-BEGIN
    if (restrictedSession.active()) { restrictedSession.guard(options.path); return; }
    // MINI-ONLY-END
    const entry = companyEntry.parse(options);
    if (entry) {
      this._pendingCompanyEntry = entry;
      // Warm scans preserve the current session until the user confirms a switch.
      if (options.path && options.path !== 'pages/login/index') wx.reLaunch({url:'/pages/login/index'});
    }
    if(this.globalData.session) this.refreshCapabilities().catch(()=>undefined);
  },
  can(key) { return access.can(this.globalData.session,key); },
  guardPage(page,route,options) {
    // MINI-ONLY-BEGIN
    if(restrictedSession.active()){restrictedSession.go();return false;}
    // MINI-ONLY-END
    if(!this.globalData.session) return true;
    if(options) page._accessOptions=options;
    page._accessRoute=route;
    access.protectActions(this,page,route);
    if(!this.globalData.session.capabilities)page._awaitingInitialCapabilities=true;
    const allowed=access.pageAllowed(this.globalData.session,route,page._accessOptions);
    const identity=access.identity(this.globalData.session),reset=page._accessIdentity!==identity?access.readStateReset(route):{};
    page._accessIdentity=identity;
    page.setData({...reset,...access.flags(this.globalData.session,route),...(['visit-entry','visit-confirm'].includes(route)?{isFde:access.assignedVisitOnly(this.globalData.session)}:{}),accessBlocked:!allowed,accessMessage:allowed?'':'当前身份未开放此项操作。权限可能已调整，请返回重新查看。'});
    this.refreshCapabilities().catch(()=>undefined);
    return allowed;
  },
  async refreshCapabilities(force=false) {
    // MINI-ONLY-BEGIN
    if(restrictedSession.active()) return null;
    // MINI-ONLY-END
    if(!this.globalData.session) return null;
    if(this._capabilityFlight)return this._capabilityFlight;
    if(!force&&Date.now()-(this._capabilityCheckedAt||0)<10000)return this.globalData.session;
    const previous=this.globalData.session,context=access.identity(previous);
    const flight=apiClient.getCurrentActor().then(result=>{
      if(access.identity(this.globalData.session)!==context)return null;
      const actor=result.actor||result;
      if(actor.user_id!==previous.userId||(actor.workspace_id && actor.workspace_id!==previous.workspaceId)||!this.globalData.roles[actor.role])throw new Error('账号身份已变化，请重新登录');
      const next={...previous,...companyEntry.fromActor(actor),role:actor.role,roleName:actor.role_name||this.globalData.roles[actor.role].name,scope:actor.permissions?'按功能授权':actor.scope_name||this.globalData.roles[actor.role].scope,capabilities:actor.capabilities||{},permissions:actor.permissions||null,permissionGrants:actor.permission_grants||[],clientExportPermissions:actor.client_export_permissions||{},permissionVersion:actor.permission_version||'',teamIds:actor.team_ids||[],team:(actor.team_names||[])[0]||previous.team};
      const changed=access.identity(previous)!==access.identity(next);
      this.globalData.session=next;this.globalData.role=next.role;wx.setStorageSync('salesSession',next);this._capabilityCheckedAt=Date.now();
      const auth=apiClient.getAuth();if(auth)apiClient.updateActor(actor);
      if(!changed&&previous.roleName!==next.roleName&&typeof getCurrentPages==='function'){
        getCurrentPages().forEach(page=>{if(page.data&&Object.prototype.hasOwnProperty.call(page.data,'roleName'))page.setData({roleName:next.roleName});});
      }
      if(changed&&typeof getCurrentPages==='function'){
        const pages=getCurrentPages();pages.forEach(page=>{if(!['visit-entry','visit-confirm','customer-create','customer-edit','opportunity-create','management-task-create','demo-create'].includes(page._accessRoute))page.setData({customer:null,selectedCustomer:null,selectedBattleCustomer:null,opportunity:null,messages:[],visit:null,task:null,risk:null,customers:[],plotCustomers:[],items:[],...access.readStateReset(page._accessRoute)});if(page._accessRoute)this.guardPage(page,page._accessRoute);if(page._awaitingInitialCapabilities&&!page.data.accessBlocked){page._awaitingInitialCapabilities=false;if(page.onLoad)page.onLoad(page._accessOptions||{});}});
        const current=pages[pages.length-1];if(current&&current.onShow&&!current.data.accessBlocked){current.onShow();if(current.selectComponent){const component=current.selectComponent('#fdeContent');if(component&&component.load)component.load();}}
      }
      return next;
    }).catch(error=>{
      if(access.identity(this.globalData.session)===context&&[401,403].includes(error.statusCode)){this.logout();wx.reLaunch({url:'/pages/login/index'});}
      throw error;
    }).finally(()=>{if(this._capabilityFlight===flight)this._capabilityFlight=null;});
    this._capabilityFlight=flight;
    return flight;
  },
  // BACKEND-CONTRACT AUTH: /auth/session的actor决定真实role/workspace/user，不能信任界面选择。
  // 字段映射与生产认证缺口见docs/backend-handoff/登录看板与个人中心详解.md。
  loginWithApi(selectedRole, account, password, workspace, loginType='auto') {
    const baseUrl = apiClient.getBaseUrl();
    return apiClient.loginWithAccount(account, password, selectedRole, workspace, loginType).then((auth) => {
      // MINI-ONLY-BEGIN
      if(auth.session_kind === 'demo_readonly') {
        if(!apiClient.isCurrentRestrictedLogin(auth)) throw Object.assign(new Error('登录状态已变更，请重试'),{code:'SESSION_CHANGED'});
        if(workspace && companyEntry.normalizeCode(workspace)!==auth.company?.code) throw new Error('登录公司与入口不一致，请重新确认公司');
        const session=restrictedSession.accept(auth,baseUrl);
        this._capabilityFlight=null;this._capabilityCheckedAt=0;
        this.globalData.session=session;this.globalData.role=null;
        wx.removeStorageSync('salesSession');wx.removeStorageSync('salesRole');
        return session;
      }
      // MINI-ONLY-END
      const current = apiClient.getAuth();
      if (baseUrl !== apiClient.getBaseUrl() || !current || current.access_token !== auth.access_token) {
        throw Object.assign(new Error("登录状态已变更，请重试"),{code:"SESSION_CHANGED"});
      }
      // MINI-ONLY-BEGIN
      restrictedSession.clear();
      // MINI-ONLY-END
      const actor = auth.actor || {};
      if (workspace && actor.company_code && companyEntry.normalizeCode(actor.company_code) !== companyEntry.normalizeCode(workspace)) {
        apiClient.logout();
        throw new Error("登录公司与入口不一致，请重新确认公司");
      }
      const role = actor.role;
      const roleInfo = this.globalData.roles[role];
      if (!roleInfo) throw new Error("服务端返回了不支持的身份");
      if(actor.permissions && actor.permissions['access.mini_program']!==true){
        apiClient.logout();throw new Error("当前账号未获小程序使用授权，请联系管理员");
      }
      const session = {
        role,
        capabilities: actor.capabilities || null,
        permissions: actor.permissions || null,
        permissionGrants: actor.permission_grants || [],
        clientExportPermissions: actor.client_export_permissions || {},
        permissionVersion: actor.permission_version || "",
        roleName: actor.role_name || roleInfo.name,
        scope: actor.permissions ? '按功能授权' : actor.scope_name || roleInfo.scope,
        account: actor.account_code || account,
        userName: actor.display_name,
        team: (actor.team_names || [])[0] || (role === "manager" ? "全部团队" : ""),
        workspaceId: actor.workspace_id,
        ...companyEntry.fromActor(actor),
        apiBaseUrl: apiClient.getBaseUrl(),
        userId: actor.user_id,
        teamIds: actor.team_ids || [],
        remote: true,
        authMethod: auth.auth_method,
        mustChangePassword: auth.must_change_password === true,
        loginAt: Date.now(),
      };
      this.globalData.role = role;
      this.globalData.session = session;
      wx.setStorageSync("salesSession", session);
      return session;
    });
  },

  // BACKEND-CONTRACT AUTH: 先清本地会话，再后台注销token；远程失败不阻塞返回登录页。
  logout() {
    // MINI-ONLY-BEGIN
    if(restrictedSession.active()) restrictedSession.logout();
    // MINI-ONLY-END
    this._capabilityCheckedAt=0;
    this._capabilityFlight=null;
    this.globalData.role = "sales";
    this.globalData.session = null;
    wx.removeStorageSync("salesSession");
    wx.removeStorageSync("salesRole");
    apiClient.logout();
  },

  ensureLogin() {
    // MINI-ONLY-BEGIN
    if(restrictedSession.active()){restrictedSession.go();return false;}
    // MINI-ONLY-END
    if (this.globalData.session && !this.globalData.session.mustChangePassword) return true;
    wx.reLaunch({ url: "/pages/login/index" });
    return false;
  },
});
