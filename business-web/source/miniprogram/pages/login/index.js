const apiClient = require("../../utils/apiClient");
const rememberedLogin = require("../../utils/rememberedLogin");
Page({
  data: {
    heroTop: 0,
    account: "",
    password: "",
    newPassword: "",
    mustChangePassword: false,
    passwordVisible: false,
    rememberPassword: false,
    agreed: false,
    loading: false,
    accountFocused: false,
    accountSuggestions: [],
  },

  onLoad() {
    const saved = rememberedLogin.read(apiClient.getBaseUrl());
    if (saved) this.setData({...saved, rememberPassword:true, passwordVisible:false});
    if (typeof wx.getMenuButtonBoundingClientRect !== "function") return;
    const menu = wx.getMenuButtonBoundingClientRect();
    if (menu && menu.bottom > 0) this.setData({heroTop: menu.bottom + 26});
  },

  onShow() {
    const app = getApp();
    if (app.globalData.session && app.globalData.session.mustChangePassword) {
      this.setData({mustChangePassword:true,account:app.globalData.session.account});
      return;
    }
    if (this.data.mustChangePassword) {
      this.setData({mustChangePassword:false,password:"",newPassword:"",passwordVisible:false,loading:false});
    }
    if (app.globalData.session) {
      // 热重载时同步切换 Tab 偶发发生在页面栈尚未就绪之前，导致只剩空白的
      // 自定义导航页。下一帧再跳转，并在失败时清理失效会话让登录页可用。
      wx.nextTick(() => {
        wx.switchTab({
          url: "/pages/index/index",
          fail: () => {
            app.logout();
            this.setData({ loading: false });
          },
        });
      });
    }
  },

  inputAccount(e) {
    if (this.data.loading || this.data.mustChangePassword) return;
    const changed = rememberedLogin.normalizeAccount(e.detail.value) !== rememberedLogin.normalizeAccount(this.data.account);
    if (changed) {
      const saved = rememberedLogin.read(apiClient.getBaseUrl(),e.detail.value);
      this.setData({password:saved ? saved.password : "",newPassword:"",
        passwordVisible:false,rememberPassword:!!saved});
    }
    this.setData({ account: e.detail.value,
      accountSuggestions:rememberedLogin.suggest(apiClient.getBaseUrl(),e.detail.value) });
  },

  selectAccountSuggestion(e) {
    if (this.data.loading || this.data.mustChangePassword) return;
    const account = e.currentTarget.dataset.account;
    if (!this.data.accountSuggestions.includes(account)) return;
    this.inputAccount({detail:{value:account}});
    this.setData({accountSuggestions:[],accountFocused:false});
  },

  hideAccountSuggestions() { this.setData({accountSuggestions:[]}); },

  clearRememberedLogin(account=this.data.account, baseUrl=apiClient.getBaseUrl()) {
    try { rememberedLogin.clear(baseUrl,account); return true; }
    catch (_) {
      wx.showToast({title:"未能清除已保存的密码，请重试",icon:"none"});
      return false;
    }
  },

  toggleRememberPassword() {
    if (this.data.rememberPassword && !this.clearRememberedLogin()) return;
    this.setData({rememberPassword:!this.data.rememberPassword});
  },

  saveRememberedLogin(account, password, selected, baseUrl) {
    if (!selected || !this.data.rememberPassword || baseUrl !== apiClient.getBaseUrl()) return;
    try { rememberedLogin.save(baseUrl,account,password); }
    catch (_) {
      this.setData({rememberPassword:false});
      wx.showToast({title:"已登录，但未能记住密码",icon:"none"});
    }
  },

  onHide() { this.setData({passwordVisible:false,accountSuggestions:[]}); },
  onUnload() { this._loginAttempt = (this._loginAttempt || 0) + 1; },

  switchAccount() {
    // Invalidate page callbacks as well as the app/API session generation.
    this._loginAttempt = (this._loginAttempt || 0) + 1;
    getApp().logout();
    this.setData({account:"",password:"",newPassword:"",mustChangePassword:false,
      passwordVisible:false,rememberPassword:false,agreed:false,loading:false,accountFocused:true,accountSuggestions:[]});
  },

  blurAccount() { this.setData({accountFocused:false}); },

  inputPassword(e) {
    if (!this.data.loading) this.setData({ password: e.detail.value });
  },

  togglePassword() {
    this.setData({ passwordVisible: !this.data.passwordVisible });
  },

  toggleAgreement() {
    this.setData({ agreed: !this.data.agreed });
  },

  openPrivacy() {
    const unavailable = () => wx.showToast({title: "隐私指引暂不可用，请联系运营", icon: "none"});
    if (typeof wx.openPrivacyContract !== "function") return unavailable();
    wx.openPrivacyContract({fail: unavailable});
  },

  inputNewPassword(e) { if (!this.data.loading) this.setData({newPassword:e.detail.value}); },

  submitLogin() {
    if (this.data.loading) return;
    if (this.data.mustChangePassword) return this.submitPasswordChange();
    const account = this.data.account.trim();
    if (!account || !this.data.password) {
      wx.showToast({ title: "请输入账号和密码", icon: "none" });
      return;
    }
    const normalizedAccount = account.toUpperCase();
    const password = this.data.password;
    const remember = this.data.rememberPassword;
    const baseUrl = apiClient.getBaseUrl();
    if (!this.data.agreed) {
      wx.showToast({ title: "请先同意隐私与数据使用说明", icon: "none" });
      return;
    }
    this.setData({ loading: true, accountSuggestions:[] });
    const attempt = this._loginAttempt = (this._loginAttempt || 0) + 1;
    return getApp().loginWithApi(null, normalizedAccount, password).then((session) => {
      if (attempt !== this._loginAttempt) return;
      if (!session) throw new Error("登录账号无效");
      if (session.mustChangePassword) {
        this.setData({loading:false,mustChangePassword:true});
        return;
      }
      this.saveRememberedLogin(account,password,remember,baseUrl);
      this.setData({ loading: false, password:"",passwordVisible:false });
      wx.switchTab({ url: "/pages/index/index" });
    }).catch((error) => {
      if (attempt !== this._loginAttempt) return;
      if (error.statusCode === 401) this.clearRememberedLogin(account,baseUrl);
      this.setData({ loading: false });
      wx.showToast({ title: error.message || "登录失败，请重试", icon: "none" });
    });
  },
  submitPasswordChange() {
    if (this.data.loading) return;
    if (!this.data.password || !this.data.newPassword) {
      wx.showToast({title:"请输入当前密码和新密码",icon:"none"});return;
    }
    const account = this.data.account;
    const newPassword = this.data.newPassword;
    const remember = this.data.rememberPassword;
    const baseUrl = apiClient.getBaseUrl();
    this.setData({loading:true});
    const attempt = this._loginAttempt = (this._loginAttempt || 0) + 1;
    return apiClient.changePassword(this.data.password,newPassword).then(()=>{
      if (attempt !== this._loginAttempt) return;
      const app=getApp();
      app.globalData.session={...app.globalData.session,mustChangePassword:false};
      wx.setStorageSync("salesSession",app.globalData.session);
      this.saveRememberedLogin(account,newPassword,remember,baseUrl);
      this.setData({mustChangePassword:false,password:"",newPassword:"",passwordVisible:false});
      wx.switchTab({url:"/pages/index/index"});
    }).catch(error=>{
      if (attempt === this._loginAttempt) wx.showToast({title:error.message || "密码修改失败",icon:"none"});
    }).finally(()=>{
      if (attempt === this._loginAttempt) this.setData({loading:false});
    });
  },
});
