const apiClient = require("./utils/apiClient");
const access = require("./utils/access");

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
    },
  },

  onLaunch() {
    let savedSession = wx.getStorageSync("salesSession");
    if (savedSession && apiClient.isEnabled() && savedSession.authMethod !== "password") {
      wx.removeStorageSync("salesSession");
      savedSession = null;
    }
    if (savedSession && savedSession.remote && this.globalData.roles[savedSession.role]) {
      const roleInfo = this.globalData.roles[savedSession.role];
      this.globalData.session = {
        ...savedSession,
        roleName: savedSession.roleName || roleInfo.name,
        scope: savedSession.scope || roleInfo.scope,
      };
      this.globalData.role = savedSession.role;
      wx.setStorageSync("salesSession", this.globalData.session);
    } else if (savedSession) {
      wx.removeStorageSync("salesSession");
    }
  },

  onShow() { if(this.globalData.session) this.refreshCapabilities().catch(()=>undefined); },
  can(key) { return access.can(this.globalData.session,key); },
  guardPage(page,route,options) {
    if(!this.globalData.session) return true;
    if(options) page._accessOptions=options;
    page._accessRoute=route;
    access.protectActions(this,page,route);
    if(!this.globalData.session.capabilities)page._awaitingInitialCapabilities=true;
    const allowed=access.pageAllowed(this.globalData.session,route,page._accessOptions);
    page.setData({...access.flags(this.globalData.session),accessBlocked:!allowed,accessMessage:allowed?'':'当前身份未开放此项操作。权限可能已调整，请返回重新查看。'});
    this.refreshCapabilities().catch(()=>undefined);
    return allowed;
  },
  async refreshCapabilities(force=false) {
    if(!this.globalData.session) return null;
    if(this._capabilityFlight)return this._capabilityFlight;
    if(!force&&Date.now()-(this._capabilityCheckedAt||0)<10000)return this.globalData.session;
    const previous=this.globalData.session,context=access.identity(previous);
    this._capabilityFlight=apiClient.getCurrentActor().then(result=>{
      if(access.identity(this.globalData.session)!==context)return null;
      const actor=result.actor||result;
      if(actor.user_id!==previous.userId||!this.globalData.roles[actor.role])throw new Error('账号身份已变化，请重新登录');
      const next={...previous,role:actor.role,roleName:actor.role_name||this.globalData.roles[actor.role].name,scope:actor.scope_name||this.globalData.roles[actor.role].scope,capabilities:actor.capabilities||{},permissionVersion:actor.permission_version||'',teamIds:actor.team_ids||[],team:(actor.team_names||[])[0]||previous.team};
      const changed=JSON.stringify(previous.capabilities)!==JSON.stringify(next.capabilities)||previous.permissionVersion!==next.permissionVersion||previous.role!==next.role;
      this.globalData.session=next;this.globalData.role=next.role;wx.setStorageSync('salesSession',next);this._capabilityCheckedAt=Date.now();
      const auth=apiClient.getAuth();if(auth)apiClient.updateActor(actor);
      if(changed&&typeof getCurrentPages==='function'){
        const pages=getCurrentPages();pages.forEach(page=>{if(access.isFde(next.role))page.setData({customer:null,selectedCustomer:null,selectedBattleCustomer:null,opportunity:null,messages:[],visit:null,task:null,risk:null,customers:[],plotCustomers:[],items:[]});if(page._accessRoute)this.guardPage(page,page._accessRoute);if(page._awaitingInitialCapabilities&&!page.data.accessBlocked){page._awaitingInitialCapabilities=false;if(page.onLoad)page.onLoad(page._accessOptions||{});}});
        const current=pages[pages.length-1];if(current&&current.onShow&&!current.data.accessBlocked){current.onShow();if(current.selectComponent){const component=current.selectComponent('#fdeContent');if(component&&component.load)component.load();}}
      }
      return next;
    }).catch(error=>{
      if(access.identity(this.globalData.session)===context&&[401,403].includes(error.statusCode)){this.logout();wx.reLaunch({url:'/pages/login/index'});}
      throw error;
    }).finally(()=>{this._capabilityFlight=null;});
    return this._capabilityFlight;
  },
  // BACKEND-CONTRACT AUTH: /auth/session的actor决定真实role/workspace/user，不能信任界面选择。
  // 字段映射与生产认证缺口见docs/backend-handoff/登录看板与个人中心详解.md。
  loginWithApi(selectedRole, account, password) {
    return apiClient.loginWithAccount(account, password, selectedRole).then((auth) => {
      const actor = auth.actor || {};
      const role = actor.role;
      const roleInfo = this.globalData.roles[role];
      if (!roleInfo) throw new Error("服务端返回了不支持的身份");
      const session = {
        role,
        capabilities: actor.capabilities || null,
        permissionVersion: actor.permission_version || "",
        roleName: actor.role_name || roleInfo.name,
        scope: actor.scope_name || roleInfo.scope,
        account: actor.account_code || account,
        userName: actor.display_name,
        team: (actor.team_names || [])[0] || (role === "manager" ? "全部团队" : ""),
        workspaceId: actor.workspace_id,
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
    this._capabilityCheckedAt=0;
    this.globalData.role = "sales";
    this.globalData.session = null;
    wx.removeStorageSync("salesSession");
    wx.removeStorageSync("salesRole");
    apiClient.logout();
  },

  ensureLogin() {
    if (this.globalData.session && !this.globalData.session.mustChangePassword) return true;
    wx.reLaunch({ url: "/pages/login/index" });
    return false;
  },
});
