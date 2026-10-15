import {Input} from '../ImeInput.jsx';
import React, {useState} from 'react';
import {Alert, App, Avatar, Button, Drawer, Form, Tooltip} from 'antd';
import {NavIcon} from './icons.jsx';
import {api, session} from './data.js';
import {useAsync} from './ui.jsx';

// 个人中心 · 账号设置（需求清单第 24 条）。
// 已有接口能做的直接做：读取 /auth/me 的个人信息、经 /auth/password 修改密码。
// 手机号、邮箱、头像的修改需要后端提供个人资料接口，当前如实提示由管理员在运营管理后台维护，不在浏览器里假保存。
const pick = (actor, keys) => keys.map(key => actor?.[key]).find(value => typeof value === 'string' && value.trim()) || '';

export function AccountSettingsButton() {
  const [open, setOpen] = useState(false);
  return <>
    <Button icon={<NavIcon name="user" />} onClick={() => setOpen(true)}>账号设置</Button>
    <AccountSettings open={open} onClose={() => setOpen(false)} />
  </>;
}

function AccountSettings({open, onClose}) {
  const {message} = App.useApp();
  const s = session();
  const me = useAsync(() => open ? api().getCurrentActor() : null, [open]);
  const actor = me.value?.actor || null;
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const phone = pick(actor, ['phone', 'mobile', 'phone_number', 'mobile_phone']);
  const email = pick(actor, ['email', 'mail']);
  const submit = async values => {
    setError('');
    if (values.next !== values.confirm) { setError('两次输入的新密码不一致'); return; }
    if (values.next === values.current) { setError('新密码不能与原密码相同'); return; }
    setSaving(true);
    try { await api().changePassword(values.current, values.next); form.resetFields(); message.success('密码已修改，下次登录请使用新密码'); }
    catch (err) { setError(err?.message || '修改失败，请稍后重试'); }
    finally { setSaving(false); }
  };
  const rows = [
    ['姓名', actor?.display_name || s?.userName], ['账号', actor?.account_code || s?.account], ['角色', actor?.role_name || s?.roleName],
    ['团队', (actor?.team_names || []).join('、') || s?.team], ['公司', actor?.company_name || s?.companyName], ['数据范围', actor?.scope_name || s?.scope],
  ].filter(([, value]) => value);
  return <Drawer open={open} onClose={onClose} size={520} title="账号设置" rootClassName="department-ui pc-account">
    <section className="pc-account-head">
      <Tooltip title="上传照片需要后端提供头像上传接口，开放后可在此更换"><span className="pc-account-avatar"><Avatar size={56}>{String(s?.userName || '我').slice(0, 1)}</Avatar><Button size="small" disabled>更换照片</Button></span></Tooltip>
      <div><b>{s?.userName}</b><span className="pc-muted">{[s?.roleName, s?.team].filter(Boolean).join(' · ')}</span></div>
    </section>
    <section className="pc-account-block">
      <h3>基本信息</h3>
      <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </section>
    <section className="pc-account-block">
      <h3>联系方式</h3>
      <dl>
        <div><dt>手机号</dt><dd>{phone || <span className="pc-muted">未登记</span>}</dd></div>
        <div><dt>邮箱</dt><dd>{email || <span className="pc-muted">未登记</span>}</dd></div>
      </dl>
      <p className="pc-muted">手机号、邮箱由企业管理员在运营管理后台维护；后端开放个人资料接口后，可在这里直接修改。</p>
    </section>
    <section className="pc-account-block">
      <h3>修改密码</h3>
      {globalThis.SALES_MODE === 'preview' && <Alert type="info" showIcon title="演示工作区不支持修改密码" description="企业工作区登录后可在这里修改。" />}
      <Form form={form} layout="vertical" onFinish={submit} requiredMark={false} disabled={saving || globalThis.SALES_MODE === 'preview'}>
        <Form.Item name="current" label="原密码" rules={[{required: true, message: '请输入原密码'}]}><Input.Password autoComplete="current-password" /></Form.Item>
        <Form.Item name="next" label="新密码" rules={[{required: true, message: '请输入新密码'}, {min: 8, message: '至少 8 位，具体规则以企业密码策略为准'}]}><Input.Password autoComplete="new-password" /></Form.Item>
        <Form.Item name="confirm" label="确认新密码" rules={[{required: true, message: '请再次输入新密码'}]}><Input.Password autoComplete="new-password" /></Form.Item>
        {error && <Alert type="error" showIcon title={error} className="pc-account-error" />}
        <Button type="primary" htmlType="submit" loading={saving}>保存新密码</Button>
      </Form>
    </section>
  </Drawer>;
}
