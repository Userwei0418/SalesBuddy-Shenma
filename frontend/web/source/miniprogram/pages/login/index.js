const apiClient = require("../../utils/apiClient");
const rememberedLogin = require("../../utils/rememberedLogin");
const companyEntry = require("../../utils/companyEntry");
const encryptedCredentials = require("../../utils/encryptedCredentials");
Page({
  data: {
    heroTop: 0,
    account: "",
    workspace: "",
    companyName: "",
    companyState: "legacy",
    companyError: "",
    editingCompany: false,
    companySwitchRequired: false,
    currentCompanyLabel: "",
    password: "",
    newPassword: "",
    mustChangePassword: false,
    passwordVisible: false,
    rememberAccount: false,
    rememberPassword: false,
    secureStorageAvailable: false,
    loginType: 'auto',
    showCompany: false,
    loginHelp: false,
    numericAccount: false,
    agreed: false,
    loading: false,
    accountFocused: false,
    accountSuggestions: [],
  },

  onLoad(options = {}) {
    this._closed = false;
    this._visible = true;
    try { rememberedLogin.cleanup(); } catch (_) { wx.showToast({title:"无法清理旧凭据，请清除小程序本地数据后重新登录",icon:"none"}); }
    this.setData({secureStorageAvailable:encryptedCredentials.available()});
    const saved = rememberedLogin.read(apiClient.getBaseUrl());
    if (saved) {
      this._rememberedRecord=saved;this._scopeAccount=rememberedLogin.normalizeAccount(saved.account);
      this.setData({...saved,loginType:saved.loginType || 'auto',rememberAccount:true,passwordVisible:false,numericAccount:/^\d{11}$/.test(saved.account)});
    }
    const app = getApp();
    const entry = app._pendingCompanyEntry || companyEntry.parse(options);
    app._pendingCompanyEntry = null;
    if (entry) this.applyCompanyEntry(entry);
    else {
      const session = app.globalData.session;
      if (session && companyEntry.validCode(session.companyCode)) this.adoptSessionCompany(session);
      else if(saved)this.loadRememberedPassword(saved);
    }
    if (typeof wx.getMenuButtonBoundingClientRect === "function") {
      const menu = wx.getMenuButtonBoundingClientRect();
      if (menu && menu.bottom > 0) this.setData({heroTop: menu.bottom + 26});
    }
  },

  onShow(options = {}) {
    this._visible = true;
    const app = getApp();
    const entry = app._pendingCompanyEntry || companyEntry.parse(options);
    app._pendingCompanyEntry = null;
    if (entry) { this.applyCompanyEntry(entry); return; }
    this.resumeSession();
  },

  adoptSessionCompany(session) {
    if (!companyEntry.validCode(session.companyCode)) return;
    this.setData({workspace:session.companyCode,companyName:session.companyName || "",
      companyState:"ready",companyError:"",editingCompany:false});
  },

  applyCompanyEntry(entry) {
    if (this.data.loading && !getApp().globalData.session) apiClient.cancelPendingLogin();
    this._entryExplicit = true;
    this.cancelCredentialRead();
    this._rememberedRecord=null;this._scopeAccount='';
    this._loginAttempt = (this._loginAttempt || 0) + 1;
    this._companySerial = (this._companySerial || 0) + 1;
    this.setData({workspace:entry.code || "",companyName:"",companyState:entry.error ? "error" : "loading",
      companyError:entry.error || "",password:"",newPassword:"",mustChangePassword:false,
      loading:false,rememberAccount:false,rememberPassword:false,loginType:'auto',showCompany:true,loginHelp:false,numericAccount:false,account:"",accountSuggestions:[],editingCompany:false});
    this.syncCompanySwitch();
    if (!entry.error) return this.loadCompany();
  },

  syncCompanySwitch() {
    const session = getApp().globalData.session;
    const required = !!session && !!this._entryExplicit &&
      (this.data.companyState !== "ready" || session.companyCode !== this.data.workspace);
    this.setData({companySwitchRequired:required,
      currentCompanyLabel:session ? session.companyName || session.companyCode || "当前已登录公司" : ""});
    return required;
  },

  loadCompany() {
    const code = this.data.workspace;
    if (!code) {
      this.setData({companyName:"",companyState:"legacy",companyError:""});
      this.syncCompanySwitch();
      return Promise.resolve(true);
    }
    if (!companyEntry.validCode(code)) {
      this.setData({companyName:"",companyState:"error",companyError:"公司码格式不正确，请核对管理员提供的入口"});
      return Promise.resolve(false);
    }
    const serial = this._companySerial = (this._companySerial || 0) + 1;
    const baseUrl = apiClient.getBaseUrl();
    const current = () => !this._closed && serial === this._companySerial && code === this.data.workspace && baseUrl === apiClient.getBaseUrl();
    this.setData({companyName:"",companyState:"loading",companyError:""});
    return apiClient.getCompanyEntry(code).then(result => {
      if (!current()) return false;
      if (!result || companyEntry.normalizeCode(result.company_code) !== companyEntry.normalizeCode(code) || typeof result.company_name !== "string" || !result.company_name.trim()) {
        throw new Error("公司信息未能确认，请重试");
      }
      this.setData({companyName:result.company_name.trim(),companyState:"ready",companyError:"",editingCompany:false});
      this.syncCompanySwitch();
      if (this._visible) this.resumeSession();
      return true;
    }).catch(error => {
      if (!current()) return false;
      this.setData({companyState:"error",companyError:error.statusCode === 404 ? "未找到可用公司，请核对公司码" : error.statusCode === 429 ? "公司查询过于频繁，请稍后重试" : "公司信息暂时无法确认，请重试"});
      this.syncCompanySwitch();
      return false;
    });
  },

  resumeSession() {
    if (this._closed || this._visible === false) return;
    const session = getApp().globalData.session;
    if (this.syncCompanySwitch()) return;
    if (session && session.mustChangePassword) {
      this.adoptSessionCompany(session);
      this.setData({mustChangePassword:true,account:session.account || this.data.account});
      return;
    }
    if (this.data.mustChangePassword) this.setData({mustChangePassword:false,password:"",newPassword:"",passwordVisible:false,loading:false});
    if (session) this.enterWorkspace(session);
  },

  enterWorkspace(session) {
    const attempt = this._loginAttempt;
    wx.nextTick(() => {
      if (this._closed || this._visible === false || attempt !== this._loginAttempt || companyEntry.sessionKey(getApp().globalData.session) !== companyEntry.sessionKey(session) || this.data.companySwitchRequired) return;
      wx.switchTab({url:"/pages/index/index",fail:() => {
        // Navigation failure does not invalidate a valid server session.
        if (!this._closed && companyEntry.sessionKey(getApp().globalData.session) === companyEntry.sessionKey(session)) {
          this.setData({loading:false});
          wx.showToast({title:"页面暂未打开，请重试进入",icon:"none"});
        }
      }});
    });
  },

  editCompany() {
    if (this.data.loading || this.data.mustChangePassword) return;
    this.setData({editingCompany:true,showCompany:true});
  },

  inputWorkspace(e) {
    if (this.data.loading || this.data.mustChangePassword) return;
    this._entryExplicit = true;
    this.cancelCredentialRead();this._rememberedRecord=null;
    this._companySerial = (this._companySerial || 0) + 1;
    this.setData({workspace:e.detail.value.trim().toLowerCase(),companyName:"",companyState:"unconfirmed",companyError:"",
      password:"",newPassword:"",rememberAccount:false,rememberPassword:false,showCompany:true,accountSuggestions:[]});
    this.syncCompanySwitch();
  },

  confirmCompany() {
    if (this.data.loading) return;
    if (!this.data.workspace) this._entryExplicit = false;
    return this.loadCompany().then(ok => { if (ok && !this._closed) this.setData({editingCompany:false}); });
  },

  confirmCompanySwitch() {
    if (!this.data.companySwitchRequired || this.data.companyState !== "ready") return;
    const session = getApp().globalData.session, code = this.data.workspace, serial = this._companySerial;
    wx.showModal({title:"切换公司并重新登录",content:`当前登录：${this.data.currentCompanyLabel}。将退出当前账号，进入 ${this.data.companyName}（${code}）登录。`,
      confirmText:"确认切换",success:result => {
        if (!result.confirm || this._closed || this._visible === false || serial !== this._companySerial || code !== this.data.workspace || companyEntry.sessionKey(getApp().globalData.session) !== companyEntry.sessionKey(session)) return;
        this.switchAccount();
        // Clear native cached business pages along with the API/session generation.
        wx.reLaunch({url:"/pages/login/index?company="+encodeURIComponent(code)});
      }});
  },

  continueCurrentCompany() {
    const session = getApp().globalData.session;
    if (!session) return;
    this._entryExplicit = false;
    this._companySerial = (this._companySerial || 0) + 1;
    this.setData({companySwitchRequired:false,companyError:"",companyState:"legacy",workspace:"",companyName:""});
    this.adoptSessionCompany(session);
    this.resumeSession();
  },

  inputAccount(e) {
    if (this.data.loading || this.data.mustChangePassword) return;
    const changed = rememberedLogin.normalizeAccount(e.detail.value) !== rememberedLogin.normalizeAccount(this.data.account);
    if (changed) {
      this.cancelCredentialRead();
      const saved = this.compatibleMemory(rememberedLogin.read(apiClient.getBaseUrl(),e.detail.value,this._entryExplicit?this.data.workspace:undefined));
      this._rememberedRecord=saved;this._scopeAccount=saved?rememberedLogin.normalizeAccount(saved.account):'';
      this.setData({password:"",newPassword:"",passwordVisible:false,rememberAccount:!!saved,rememberPassword:false,
        ...(!this._entryExplicit?{workspace:saved?.workspace || '',companyName:'',companyState:'legacy',companyError:''}:{})});
    }
    this.setData({account:e.detail.value,numericAccount:/^\d{11}$/.test(e.detail.value.trim()),
      accountSuggestions:rememberedLogin.suggest(apiClient.getBaseUrl(),e.detail.value,this._entryExplicit?this.data.workspace:undefined)});
    if(changed && this._rememberedRecord)this.loadRememberedPassword(this._rememberedRecord);
  },

  cancelCredentialRead() { this._credentialRead=(this._credentialRead || 0)+1; },

  compatibleMemory(saved) {
    if(!saved || this.data.loginType==='auto')return saved;
    return (saved.loginType || 'account')===this.data.loginType?saved:null;
  },

  credentialIdentity(saved=this._rememberedRecord,baseUrl=apiClient.getBaseUrl()) {
    return saved?{baseUrl,workspace:saved.workspace,account:saved.resolvedAccount || saved.account}:null;
  },

  async loadRememberedPassword(saved) {
    const serial=this._credentialRead=(this._credentialRead || 0)+1,baseUrl=apiClient.getBaseUrl();
    const account=rememberedLogin.normalizeAccount(this.data.account),workspace=this.data.workspace,loginType=this.data.loginType;
    const current=()=>!this._closed && this._visible!==false && serial===this._credentialRead && !this.data.loading && !this.data.mustChangePassword && account===rememberedLogin.normalizeAccount(this.data.account) && loginType===this.data.loginType && workspace===this.data.workspace && baseUrl===apiClient.getBaseUrl();
    const password=await encryptedCredentials.read(this.credentialIdentity(saved,baseUrl),current);
    if(password && current())this.setData({password,rememberAccount:true,rememberPassword:true,passwordVisible:false});
  },

  toggleLoginHelp() { if(!this.data.loading && !this.data.mustChangePassword)this.setData({loginHelp:!this.data.loginHelp}); },

  usePhoneLogin() {
    if(this.data.loading || this.data.mustChangePassword)return;
    this.cancelCredentialRead();this._rememberedRecord=null;this._scopeAccount='';
    this.setData({loginType:'phone',account:'',password:'',newPassword:'',rememberAccount:false,rememberPassword:false,accountFocused:true,accountSuggestions:[],numericAccount:false,
      ...(!this._entryExplicit?{workspace:'',companyName:'',companyState:'legacy',companyError:'',showCompany:false}:{})});
  },

  useAccountLogin() {
    if(this.data.loading || this.data.mustChangePassword)return;
    this.cancelCredentialRead();
    this.setData({loginType:'account',password:'',rememberPassword:false,accountFocused:true});
    const saved=this.compatibleMemory(rememberedLogin.read(apiClient.getBaseUrl(),this.data.account,this._entryExplicit?this.data.workspace:undefined));
    this._rememberedRecord=saved;this._scopeAccount=saved?rememberedLogin.normalizeAccount(saved.account):'';
    if(!this._entryExplicit)this.setData({workspace:saved?.workspace || '',companyName:'',companyState:'legacy'});
    if(saved)this.loadRememberedPassword(saved);
  },

  useSimpleLogin() {
    if(this.data.loading || this.data.mustChangePassword || this.data.companySwitchRequired)return;
    this.cancelCredentialRead();this._entryExplicit=false;this._scopeAccount='';this._rememberedRecord=null;
    this._companySerial=(this._companySerial || 0)+1;
    this.setData({showCompany:false,editingCompany:false,workspace:'',companyName:'',companyState:'legacy',companyError:'',password:'',rememberPassword:false,rememberAccount:false});
  },

  async toggleRememberPassword(e) {
    if(this.data.loading)return;
    if(!this.data.secureStorageAvailable){this.toggleRememberAccount(e);return;}
    const selected=e && e.detail && typeof e.detail.checked === "boolean" ? e.detail.checked : !this.data.rememberPassword;
    this.cancelCredentialRead();
    const serial=this._credentialRead,account=this.data.account;
    this.setData({rememberPassword:selected,rememberAccount:selected});
    if(!selected){
      try{await encryptedCredentials.clear(this.credentialIdentity());}catch(error){if(!this._closed && serial===this._credentialRead && account===this.data.account)wx.showToast({title:error.message,icon:'none'});}
    }
  },

  async forgetRememberedPassword() {
    if(this.data.loading)return;
    const saved=this._rememberedRecord,account=this.data.account,baseUrl=apiClient.getBaseUrl();
    this.cancelCredentialRead();
    this.setData({password:'',rememberPassword:false,rememberAccount:false});
    try{
      await encryptedCredentials.clear(this.credentialIdentity(saved,baseUrl));
      if(saved)rememberedLogin.clear(baseUrl,saved.account,saved.workspace);
      if(this._closed || account!==this.data.account || baseUrl!==apiClient.getBaseUrl())return;
      this._rememberedRecord=null;this._scopeAccount='';
      this.setData({password:'',rememberPassword:false,rememberAccount:false,...(!this._entryExplicit?{workspace:'',companyName:'',companyState:'legacy'}:{})});
      wx.showToast({title:'已忘记此账号的本机记忆',icon:'none'});
    }catch(error){if(!this._closed)wx.showToast({title:error.message || '未能清除，请重试',icon:'none'});}
  },

  selectAccountSuggestion(e) {
    if (this.data.loading || this.data.mustChangePassword) return;
    const account = e.currentTarget.dataset.account;
    if (!this.data.accountSuggestions.includes(account)) return;
    const saved=rememberedLogin.read(apiClient.getBaseUrl(),account,this._entryExplicit?this.data.workspace:undefined);
    this.setData({loginType:saved?.loginType || 'account'});
    this.inputAccount({detail:{value:account}});
    this.setData({accountSuggestions:[],accountFocused:false});
  },

  hideAccountSuggestions() { this.setData({accountSuggestions:[]}); },

  clearRememberedLogin(account=this.data.account, baseUrl=apiClient.getBaseUrl(), workspace=this.data.workspace) {
    try { rememberedLogin.clear(baseUrl,account,workspace); return true; }
    catch (_) {
      wx.showToast({title:"未能清除已保存的账号，请重试",icon:"none"});
      return false;
    }
  },

  toggleRememberAccount(e) {
    const selected = e && e.detail && typeof e.detail.checked === "boolean" ? e.detail.checked : !this.data.rememberAccount;
    if (!selected && this.data.rememberAccount && !this.clearRememberedLogin()) return;
    this.setData({rememberAccount:selected});
  },

  async saveRememberedLogin(account, selected, baseUrl, session, password, savePassword=false, current=()=>!this._closed, loginType='auto') {
    if (!current() || !selected || !this.data.rememberAccount || baseUrl !== apiClient.getBaseUrl()) return;
    const workspace = companyEntry.validCode(session.companyCode) ? session.companyCode : "";
    const resolvedAccount=session.account || account;
    const resolvedType=loginType==='auto'?(rememberedLogin.normalizeAccount(resolvedAccount)===rememberedLogin.normalizeAccount(account)?'account':'phone'):loginType;
    const metadata={resolvedAccount,loginType:resolvedType};
    try { rememberedLogin.save(baseUrl,account,workspace,metadata);this._rememberedRecord={account,workspace,...metadata};this._scopeAccount=rememberedLogin.normalizeAccount(account); }
    catch (_) {
      this.setData({rememberAccount:false,rememberPassword:false});
      wx.showToast({title:"已登录，但未能记住账号",icon:"none"});
      return;
    }
    if(savePassword && this.data.rememberPassword && current()){
      try{
        const saved=await encryptedCredentials.save({baseUrl,workspace,account:session.account || account},password,()=>current() && this.data.rememberPassword);
        if(!saved && current() && this.data.rememberPassword){this.setData({rememberPassword:false});wx.showToast({title:'已记住账号，本次未保存密码',icon:'none'});}
      }
      catch(error){if(current()){this.setData({rememberPassword:false});wx.showToast({title:error.message || '已登录，密码未保存',icon:'none'});}}
    }
  },

  onHide() { this._visible = false;this.cancelCredentialRead(); this.setData({passwordVisible:false,accountSuggestions:[]}); },
  onUnload() {
    if (this.data.loading && !getApp().globalData.session) apiClient.cancelPendingLogin();
    this._closed = true;
    this._companySerial = (this._companySerial || 0) + 1;
    this._loginAttempt = (this._loginAttempt || 0) + 1;
    this.cancelCredentialRead();encryptedCredentials.invalidate();this._rememberedRecord=null;this._scopeAccount='';
  },

  switchAccount() {
    // Invalidate page callbacks as well as the app/API session generation.
    this._loginAttempt = (this._loginAttempt || 0) + 1;
    this.cancelCredentialRead();encryptedCredentials.invalidate();this._rememberedRecord=null;this._scopeAccount='';this._passwordChangeLogin=null;
    getApp().logout();
    this.setData({companySwitchRequired:false,account:"",password:"",newPassword:"",mustChangePassword:false,
      passwordVisible:false,rememberAccount:false,rememberPassword:false,loginType:'auto',numericAccount:false,agreed:false,loading:false,accountFocused:true,accountSuggestions:[],
      ...(!this._entryExplicit?{workspace:'',companyName:'',companyState:'legacy',showCompany:false}:{})});
  },

  blurAccount() { this.setData({accountFocused:false}); },

  inputPassword(e) {
    if (!this.data.loading) {this.cancelCredentialRead();this.setData({ password: e.detail.value });}
  },

  togglePassword() {
    this.setData({ passwordVisible: !this.data.passwordVisible });
  },

  // Preserve the A package sb-input visibility event contract.
  onPasswordVisibility(e) {
    if (!!(e && e.detail && e.detail.visible) !== this.data.passwordVisible) this.togglePassword();
  },

  toggleAgreement(e) {
    const agreed = e && e.detail && typeof e.detail.checked === "boolean" ? e.detail.checked : !this.data.agreed;
    this.setData({ agreed });
  },

  openPrivacy() {
    const unavailable = () => wx.showToast({title: "隐私指引暂不可用，请联系运营", icon: "none"});
    if (typeof wx.openPrivacyContract !== "function") return unavailable();
    wx.openPrivacyContract({fail: unavailable});
  },

  inputNewPassword(e) { if (!this.data.loading) this.setData({newPassword:e.detail.value}); },

  async submitLogin() {
    if (this.data.loading) return;
    if (this.data.companySwitchRequired) return;
    if (this.data.mustChangePassword) return this.submitPasswordChange();
    const account = this.data.account.trim();
    if (!account || !this.data.password) {
      wx.showToast({ title: "请输入账号或手机号及密码", icon: "none" });
      return;
    }
    const normalizedAccount = account.toUpperCase();
    const password = this.data.password;
    const remember = this.data.rememberAccount;
    const rememberPassword=this.data.rememberPassword;
    const baseUrl = apiClient.getBaseUrl();
    const matched=this._scopeAccount===rememberedLogin.normalizeAccount(account);
    const remembered=this._rememberedRecord;
    const loginType=this.data.loginType==='auto' && matched && remembered ? remembered.loginType==='phone'?'phone':'account' : this.data.loginType;
    // General login resolves the company on the server. Keep remembered company
    // metadata only for encrypted credential lookup/cleanup, never as login scope.
    const workspace=this._entryExplicit?this.data.workspace:'';
    if (!this.data.agreed) {
      wx.showToast({ title: "请先同意隐私与数据使用说明", icon: "none" });
      return;
    }
    this.setData({ loading: true, accountSuggestions:[] });
    this.cancelCredentialRead();
    const attempt = this._loginAttempt = (this._loginAttempt || 0) + 1;
    const current=()=>attempt===this._loginAttempt && !this._closed && baseUrl===apiClient.getBaseUrl();
    if (this._entryExplicit && this.data.companyState === "error" && !workspace) { this.setData({loading:false}); return; }
    if (workspace && this.data.companyState !== "ready") {
      const confirmed = await this.loadCompany();
      if (attempt !== this._loginAttempt || this._closed) return;
      if (!confirmed || this._visible === false) { this.setData({loading:false}); return; }
    }
    return getApp().loginWithApi(null, normalizedAccount, password, workspace || undefined,loginType).then(async(session) => {
      if (!current()) return;
      if (!session) throw new Error("登录账号无效");
      this.adoptSessionCompany(session);
      if (session.mustChangePassword) {
        try{await encryptedCredentials.clear({baseUrl,workspace:session.companyCode,account:session.account || account});}
        catch(error){if(current())wx.showToast({title:error.message,icon:'none'});}
        if(!current())return;
        this._passwordChangeLogin={account,loginType};
        this.setData({loading:false,mustChangePassword:true});
        return;
      }
      await this.saveRememberedLogin(account,remember,baseUrl,session,password,rememberPassword,current,loginType);
      if(!current())return;
      this.setData({ loading: false, password:"",passwordVisible:false });
      this.enterWorkspace(session);
    }).catch(async(error) => {
      if (!current()) return;
      if (error.statusCode === 401) {
        const target=remembered?this.credentialIdentity(remembered,baseUrl):{baseUrl,workspace,account};
        try{await encryptedCredentials.clear(target);}catch(_){if(current())wx.showToast({title:'保存的密码未能清除，请点击忘记后重试',icon:'none'});}
        if(!current())return;
        this.setData({rememberPassword:false,loginHelp:true});
      }
      this.setData({ loading: false });
      // Final password-login presentation must not ask an already signed-out
      // user to log in again. Explicit account, permission and service errors stay intact.
      const loginErrorCode = error.serverCode || error.code || '';
      const invalidCredentials = error.statusCode === 401 &&
        ['', 'HTTP_401', 'AUTH_REQUIRED', 'INVALID_CREDENTIALS'].includes(loginErrorCode);
      if (this._visible !== false) wx.showToast({ title: invalidCredentials ? '账号或密码错误' : error.message || "登录失败，请重试", icon: "none" });
    });
  },
  submitPasswordChange() {
    if (this.data.loading) return;
    if (!this.data.password || !this.data.newPassword) {
      wx.showToast({title:"请输入当前密码和新密码",icon:"none"});return;
    }
    const account = this.data.account;
    const newPassword = this.data.newPassword;
    const remember = this.data.rememberAccount;
    const rememberPassword=this.data.rememberPassword;
    const baseUrl = apiClient.getBaseUrl();
    const session = getApp().globalData.session;
    this.setData({loading:true});
    const attempt = this._loginAttempt = (this._loginAttempt || 0) + 1;
    return apiClient.changePassword(this.data.password,newPassword).then(async()=>{
      if (attempt !== this._loginAttempt || companyEntry.sessionKey(getApp().globalData.session) !== companyEntry.sessionKey(session)) return;
      const app=getApp();
      app.globalData.session={...app.globalData.session,mustChangePassword:false};
      wx.setStorageSync("salesSession",app.globalData.session);
      const changedSession=app.globalData.session;
      const current=()=>!this._closed && attempt===this._loginAttempt && baseUrl===apiClient.getBaseUrl() && companyEntry.sessionKey(getApp().globalData.session)===companyEntry.sessionKey(changedSession);
      await this.saveRememberedLogin(this._passwordChangeLogin?.account || account,remember,baseUrl,changedSession,newPassword,rememberPassword,current,this._passwordChangeLogin?.loginType || this.data.loginType);
      if(!current())return;
      this.setData({mustChangePassword:false,password:"",newPassword:"",passwordVisible:false});
      this.enterWorkspace(app.globalData.session);
    }).catch(error=>{
      if (attempt === this._loginAttempt) wx.showToast({title:error.message || "密码修改失败",icon:"none"});
    }).finally(()=>{
      if (attempt === this._loginAttempt) this.setData({loading:false});
    });
  },
});
