import {Input} from './ImeInput.jsx';
import React, { useEffect, useRef } from 'react';
import {Alert, Button, Checkbox, Space} from 'antd';
import {SbField} from '@shandiant/ui-react';
import './login.css';

// 登录（原 pages/login/index）。壳层（shell.js）已经放了品牌、标题、错误条和连接状态，这里只剩表单。
// 壳层依赖的钩子保留：账号框在 .form-area .field-group:first-child 里，提交与同意勾选带 data-handler。
export function supportsLogin(page) {
  return Boolean(page && page.route === 'pages/login/index');
}

export default function Login({ data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const busy = Boolean(d.loading);
  const blocked = Boolean(d.companySwitchRequired);
  const change = Boolean(d.mustChangePassword);
  const locked = busy || change || blocked;
  const phone = d.loginType === 'phone';
  const accountOnly = d.loginType === 'account';
  const companyError = d.companyState === 'error' ? d.companyError : '';
  // Remembered accounts can carry a company without an explicit company URL.
  // A failed lookup must expose that context and the original recovery actions.
  const companyVisible = Boolean(d.workspace || d.showCompany || d.editingCompany || d.companyState === 'ready' || companyError || blocked);
  const account = useRef(null);
  useEffect(() => { if (d.accountFocused) account.current?.focus(); }, [d.accountFocused]);
  const suggestions = !busy && !change ? d.accountSuggestions || [] : [];
  return <div className="ds-login" aria-label="登录工作空间">
    {companyVisible && <section className="ds-company-entry" aria-label="登录公司">
      {d.companyState === 'ready' && !d.editingCompany ? <><strong>{d.companyName}</strong><p className="ds-muted">公司码：{d.workspace}</p><Button type="link" disabled={busy || change} onClick={()=>call('editCompany')}>更换公司</Button></> : <>
        <label htmlFor="login-company">{d.workspace && d.companyState === 'legacy' && !d.editingCompany ? '已记住的公司码' : '公司码'}</label><Space.Compact><Input id="login-company" aria-label="公司码" value={d.workspace || ''} placeholder="填写管理员提供的公司码" disabled={busy || change} onChange={e=>call('inputWorkspace',{detail:{value:e.target.value}})} onPressEnter={()=>call('confirmCompany')} /><Button loading={d.companyState==='loading'} disabled={busy || change} onClick={()=>call('confirmCompany')}>确认公司</Button></Space.Compact>
        <p className="ds-muted">公司码不是公司名称；使用管理员提供的公司入口时填写，通常可直接用账号或已绑定手机号登录。</p>
      </>}
      {companyError && <Alert type="error" showIcon title={companyError} description="公司验证未通过，登录尚未继续。请核对公司码与当前工作区，或改用账号自动识别。" />}
      {!blocked && !change && <Button type="link" disabled={busy} onClick={() => call('useSimpleLogin')}>返回通用登录</Button>}
      {blocked && <Alert type="warning" showIcon title={`当前登录：${d.currentCompanyLabel}`} description={<Space wrap><Button disabled={d.companyState!=='ready'} onClick={()=>call('confirmCompanySwitch')}>切换公司并重新登录</Button><Button onClick={()=>call('continueCurrentCompany')}>继续当前公司</Button></Space>} />}
    </section>}
    <div className="form-area">
      <div className="field-group">
        <SbField label={<span>{phone ? '手机号' : accountOnly ? '账号' : '账号或手机号'} <small className="ds-muted">{phone ? '使用管理员绑定的手机号' : '按登录信息识别身份'}</small></span>}>
          <Input ref={account} value={d.account || ''} disabled={locked} placeholder={phone ? '请输入已绑定的手机号' : accountOnly ? '请输入账号，支持数字账号' : '请输入账号或已绑定的手机号'} autoComplete="username" onChange={e => call('inputAccount', { detail: { value: e.target.value } })} onBlur={() => call('blurAccount')}
            suffix={(d.account || change) ? <Button type="link" size="small" tabIndex={-1} disabled={busy || blocked} onClick={() => call('switchAccount')}>切换账号</Button> : <span />} />
          {suggestions.length > 0 && <div className="ds-login-suggest" role="listbox" aria-label="已记住的账号">
            <span className="ds-muted">已记住的账号</span>
            {suggestions.map(item => <button key={item} type="button" role="option" onMouseDown={e => e.preventDefault()} onClick={() => call('selectAccountSuggestion', { dataset: { account: item } })}><b>{item}</b><span>填入</span></button>)}
          </div>}
        </SbField>
      </div>
      {d.numericAccount && !accountOnly && !change && !blocked && <div className="ds-login-mode-hint"><span>这是原有数字账号？</span><Button type="link" size="small" disabled={busy} onClick={() => call('useAccountLogin')}>按账号登录</Button></div>}
      <div className="field-group">
        <SbField label="密码">
          <Input.Password value={d.password || ''} maxLength={128} disabled={busy} placeholder="请输入密码" autoComplete="current-password" visibilityToggle={{ visible: Boolean(d.passwordVisible), onVisibleChange: () => call('togglePassword') }} onChange={e => call('inputPassword', { detail: { value: e.target.value } })} onFocus={() => call('hideAccountSuggestions')} onPressEnter={() => call('submitLogin')} />
        </SbField>
      </div>
      {change && <div className="field-group">
        <SbField label="设置新密码" help="首次登录请先修改初始密码。">
          <Input.Password value={d.newPassword || ''} maxLength={128} disabled={busy} placeholder="请输入 12–128 位新密码" autoComplete="new-password" onChange={e => call('inputNewPassword', { detail: { value: e.target.value } })} onPressEnter={() => call('submitLogin')} />
        </SbField>
      </div>}
    </div>
    {!change && !blocked && <section className="ds-login-alternatives" aria-label="其他登录方式">
      <div className="ds-login-help-actions">
        <Button type="link" disabled={busy} aria-expanded={Boolean(d.loginHelp)} aria-controls="login-alternatives" onClick={() => call('toggleLoginHelp')}>{d.loginHelp ? '收起登录方式' : '更多登录方式'}</Button>
        {!companyVisible && <Button type="link" disabled={busy} onClick={() => call('editCompany')}>使用公司入口</Button>}
      </div>
      {d.loginHelp && <div id="login-alternatives" className="ds-login-help">
        <p className="ds-muted">演示账号用账号和密码登录；企业成员可用已绑定的手机号和密码登录。</p>
        <div className="ds-login-help-actions">
          <Button disabled={busy} aria-pressed={phone} onClick={() => call('usePhoneLogin')}>手机号登录</Button>
          <Button disabled={busy} aria-pressed={accountOnly} onClick={() => call('useAccountLogin')}>账号登录</Button>
        </div>
      </div>}
    </section>}
    <div className="ds-login-checks">
      <Checkbox checked={Boolean(d.rememberAccount)} disabled={busy} onChange={() => call('toggleRememberAccount')}>记住公司与账号 <small className="ds-muted">不保存密码</small></Checkbox>
      {!change && !blocked && d.rememberAccount && <Button className="ds-login-forget" type="link" disabled={busy} onClick={() => call('forgetRememberedPassword')}>忘记此账号的本机记忆</Button>}
      <div data-handler="toggleAgreement" className="ds-login-agree">
        <Checkbox checked={Boolean(d.agreed)} disabled={busy} onChange={() => call('toggleAgreement')}>我已阅读并同意</Checkbox>
        <button type="button" className="ds-login-link" onClick={() => call('openPrivacy')}>隐私与数据使用说明</button>
      </div>
    </div>
    <Button type="primary" block size="large" data-handler="submitLogin" loading={busy} disabled={busy || blocked} onClick={() => call('submitLogin')}>{busy ? '正在处理' : change ? '修改密码并进入' : '进入工作空间'}</Button>
    <p className="ds-muted ds-login-note">账号由运营开通；忘记密码请联系运营。</p>
  </div>;
}
