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

export default function Login({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const busy = Boolean(d.loading);
  const blocked = Boolean(d.companySwitchRequired);
  const change = Boolean(d.mustChangePassword);
  const locked = busy || change || blocked;
  const phone = d.loginType === 'phone';
  const accountOnly = d.loginType === 'account';
  const companyError = d.companyState === 'error' ? d.companyError : '';
  // Only an explicit company link constrains Web login. Remembered metadata is
  // kept for account history, without presenting a company-code form.
  const companyVisible = Boolean(page?._entryExplicit || blocked || (change && d.companyName));
  const account = useRef(null);
  useEffect(() => { if (d.accountFocused) account.current?.focus(); }, [d.accountFocused]);
  const suggestions = !busy && !change ? d.accountSuggestions || [] : [];
  return <div className="ds-login" aria-label="登录工作空间">
    {companyVisible && <section className="ds-company-entry" aria-label="登录公司">
      <strong>{d.companyName || (d.companyState === 'loading' ? '正在确认公司…' : '公司专属入口')}</strong>
      {companyError && <Alert type="error" showIcon title={companyError} description="公司入口暂不可用，请重试或联系管理员核对链接。" />}
      {companyError && <Button disabled={busy || change} onClick={() => call('confirmCompany')}>重试公司验证</Button>}
      {!blocked && !change && <Button type="link" disabled={busy} onClick={() => call('useSimpleLogin')}>返回通用登录</Button>}
      {blocked && <Alert type="warning" showIcon title={`当前登录：${d.currentCompanyLabel}`} description={<Space wrap><Button disabled={d.companyState!=='ready'} onClick={()=>call('confirmCompanySwitch')}>切换公司并重新登录</Button><Button onClick={()=>call('continueCurrentCompany')}>继续当前公司</Button></Space>} />}
    </section>}
    <div className="form-area">
      <div className="field-group">
        <SbField label={<span>{phone ? '手机号' : accountOnly ? '账号' : '账号或手机号'} <small className="ds-muted">{companyVisible ? '使用该公司的账号' : '自动识别所属公司'}</small></span>}>
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
      <Checkbox checked={Boolean(d.rememberAccount)} disabled={busy} onChange={() => call('toggleRememberAccount')}>记住账号 <small className="ds-muted">不保存密码</small></Checkbox>
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
