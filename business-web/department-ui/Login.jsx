import React, { useEffect, useRef } from 'react';
import { Button, Checkbox, Input } from 'antd';
import { SbField } from '@shandiant/ui-react';
import './login.css';

// 登录（原 pages/login/index）。壳层（shell.js）已经放了品牌、标题、错误条和连接状态，这里只剩表单。
// 壳层依赖的钩子保留：账号框在 .form-area .field-group:first-child 里，提交与同意勾选带 data-handler。
export function supportsLogin(page) {
  return Boolean(page && page.route === 'pages/login/index');
}

export default function Login({ data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const busy = Boolean(d.loading);
  const change = Boolean(d.mustChangePassword);
  const account = useRef(null);
  useEffect(() => { if (d.accountFocused) account.current?.focus(); }, [d.accountFocused]);
  const suggestions = !busy && !change ? d.accountSuggestions || [] : [];
  return <div className="ds-login" aria-label="登录工作空间">
    <div className="form-area">
      <div className="field-group">
        <SbField label={<span>账号 <small className="ds-muted">按账号自动识别身份</small></span>}>
          <Input ref={account} value={d.account || ''} disabled={change || busy} placeholder="请输入账号名或手机号" autoComplete="username" onChange={e => call('inputAccount', { detail: { value: e.target.value } })} onBlur={() => call('blurAccount')}
            suffix={(d.account || change) ? <Button type="link" size="small" tabIndex={-1} onClick={() => call('switchAccount')}>切换账号</Button> : <span />} />
          {suggestions.length > 0 && <div className="ds-login-suggest" role="listbox" aria-label="已记住的账号">
            <span className="ds-muted">已记住的账号</span>
            {suggestions.map(item => <button key={item} type="button" role="option" onMouseDown={e => e.preventDefault()} onClick={() => call('selectAccountSuggestion', { dataset: { account: item } })}><b>{item}</b><span>填入</span></button>)}
          </div>}
        </SbField>
      </div>
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
    <div className="ds-login-checks">
      <Checkbox checked={Boolean(d.rememberPassword)} disabled={busy} onChange={() => call('toggleRememberPassword')}>记住密码 <small className="ds-muted">仅在此设备保存</small></Checkbox>
      <div data-handler="toggleAgreement" className="ds-login-agree">
        <Checkbox checked={Boolean(d.agreed)} disabled={busy} onChange={() => call('toggleAgreement')}>我已阅读并同意</Checkbox>
        <button type="button" className="ds-login-link" onClick={() => call('openPrivacy')}>隐私与数据使用说明</button>
      </div>
    </div>
    <Button type="primary" block size="large" data-handler="submitLogin" loading={busy} disabled={busy} onClick={() => call('submitLogin')}>{busy ? '正在处理' : change ? '修改密码并进入' : '进入工作空间'}</Button>
    <p className="ds-muted ds-login-note">账号由运营开通；忘记密码请联系运营。</p>
  </div>;
}
