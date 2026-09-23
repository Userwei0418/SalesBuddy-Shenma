"""Exercise account lock rendering and the actual confirmation form workflow."""

import json

import pytest

from tests.test_admin_role_labels import run_web_script

ACCOUNT_FIXTURE = r"""
    const organization={
      roles:['fde','sales','operations','administrator'].map(code=>({code,name:code})),
      departments:[{id:'team',name:'业务部门',code:'TEAM',status:'active'}],
      accounts:[['member','fde',true],['sales','sales',false],
        ['operator','operations',true],['admin','administrator',true]].map(
        ([id,role,locked])=>({id,account_code:'TEST_'+id,display_name:id,
          roles:[role],team_id:'team',team_name:'业务部门',status:'active',
          has_password:true,version_no:7,login_locked:locked,
          login_retry_at:locked?'2026-09-13T05:15:00Z':null,
          login_attempts:locked?6:0}))};
    core.state.actor={workspace_id:'test-workspace',user_id:'test-admin',role:'administrator'};
    core.state.filters={};core.state.offset=0;
    const requests=[];
    globalThis.fetch=async(url,options)=>{
      requests.push({url,options});
      return {ok:true,status:200,json:async()=>organization};
    };
    const accountRow=(html,id)=>html.split('<tr>').find(
      row=>row.includes('<small>TEST_'+id+'</small>'));
"""

FORM_FIXTURE = r"""
    let reason='已核实为本人调试输入错误';
    let closeCount=0,refreshCount=0;
    const errorBox={innerHTML:''},submitButton={disabled:false};
    const form={querySelector:selector=>selector==='button[type=submit]'
      ?submitButton:selector==='.form-error'?errorBox:null};
    const modal={innerHTML:'',querySelectorAll:()=>[],showModal(){},
      close(){closeCount++;}};
    const toast={textContent:'',style:{}};
    globalThis.document={querySelector:selector=>selector==='#dialog'?modal:
      selector==='#dialog-form'?form:selector==='#toast'?toast:null};
    globalThis.FormData=class {get(name){return name==='reason'?reason:null;}};
    core.state.refresh=async()=>{refreshCount++;};
    const page=await pages.accounts();
    const root={querySelector:()=>null,querySelectorAll:()=>[]};page.bind(root);
    const button={dataset:{action:'unlock-login',id:'member'},disabled:false};
    await root.onclick({target:{closest:()=>button}});
    const submit=()=>form.onsubmit({preventDefault(){},currentTarget:form});
"""


@pytest.mark.parametrize("actor_role", ["operations", "administrator"])
def test_account_lock_status_and_unlock_action_respect_account_management(actor_role):
    run_web_script(ACCOUNT_FIXTURE + f"core.state.actor.role={json.dumps(actor_role)};" + r"""
        const page=await pages.accounts();
        assert.match(page.html, /<th>登录限制<\/th>/);
        const locked=accountRow(page.html,'member');
        assert.match(locked,/登录受限/);
        assert.match(locked,/13:15:00/);
        assert.match(locked,/北京时间/);
        assert.match(locked,/data-action="unlock-login"/);
        const unlocked=accountRow(page.html,'sales');
        assert.match(unlocked,/未受限/);
        assert.doesNotMatch(unlocked,/data-action="unlock-login"/);
        for(const id of ['operator','admin']){
          const row=accountRow(page.html,id);
          assert.match(row,/登录受限/);
          if(core.state.actor.role==='administrator')
            assert.match(row,/data-action="unlock-login"/);
          else {
            assert.doesNotMatch(row,/data-action="unlock-login"/);
            assert.match(row,/由系统管理员维护/);
          }
        }
        assert.ok(requests.every(request=>request.options.method==='GET'));
    """)


@pytest.mark.parametrize(
    ("reason", "message"), [("   ", "请填写解除原因"), ("字" * 501, "500 字以内")]
)
def test_unlock_requires_a_nonempty_bounded_reason_before_sending(reason, message):
    run_web_script(ACCOUNT_FIXTURE + FORM_FIXTURE + f"reason={json.dumps(reason)};" + r"""
        assert.match(modal.innerHTML,/仅解除此账号的登录限制/);
        assert.match(modal.innerHTML,/当前网络的登录保护保持不变/);
        assert.match(modal.innerHTML,/<textarea name="reason" required/);
        await submit();
        assert.equal(requests.filter(request=>request.options.method==='POST').length,0);
        assert.equal(closeCount,0);assert.equal(refreshCount,0);
        assert.equal(submitButton.disabled,false);
    """ + f"assert.ok(errorBox.innerHTML.includes({json.dumps(message)}));")


def test_unlock_sends_version_reason_and_idempotency_key_then_refreshes_actual_table():
    run_web_script(ACCOUNT_FIXTURE + FORM_FIXTURE + r"""
        let refreshedPage;
        globalThis.fetch=async(url,options)=>{
          requests.push({url,options});
          if(options.method==='POST'){
            const member=organization.accounts[0];
            member.login_locked=false;member.login_retry_at=null;
            member.login_attempts=0;member.version_no=8;
            return {ok:true,status:200,json:async()=>member};
          }
          return {ok:true,status:200,json:async()=>organization};
        };
        core.state.refresh=async()=>{refreshCount++;refreshedPage=await pages.accounts();};
        reason='  已核实为本人调试输入错误  ';
        await submit();
        const writes=requests.filter(request=>request.options.method==='POST');
        assert.equal(writes.length,1);
        assert.equal(writes[0].url,'/api/v1/console/accounts/member/unlock-login');
        assert.deepEqual(JSON.parse(writes[0].options.body),{
          version_no:7,reason:'已核实为本人调试输入错误'});
        assert.match(writes[0].options.headers['Idempotency-Key'],/^[0-9a-f-]{36}$/);
        assert.equal(closeCount,1);assert.equal(refreshCount,1);
        assert.equal(errorBox.innerHTML,'');
        assert.equal(toast.textContent,'账号登录限制已解除');
        assert.match(accountRow(refreshedPage.html,'member'),/未受限/);
        assert.doesNotMatch(accountRow(refreshedPage.html,'member'),/data-action="unlock-login"/);
        assert.equal(submitButton.disabled,false);
        clearTimeout(core.toast.timer);
    """)


def test_unlock_network_retry_keeps_dialog_and_original_submission_key():
    run_web_script(ACCOUNT_FIXTURE + FORM_FIXTURE + r"""
        let writes=0;
        globalThis.fetch=async(url,options)=>{
          requests.push({url,options});
          if(options.method==='POST' && ++writes===1) throw Error('offline');
          return {ok:true,status:200,json:async()=>organization};
        };
        await submit();
        assert.match(errorBox.innerHTML,/连接中断/);
        assert.equal(closeCount,0);assert.equal(refreshCount,0);
        assert.equal(submitButton.disabled,false);
        assert.equal(toast.textContent,'');
        await submit();
        const posts=requests.filter(request=>request.options.method==='POST');
        assert.equal(posts.length,2);
        assert.equal(posts[0].options.headers['Idempotency-Key'],
          posts[1].options.headers['Idempotency-Key']);
        assert.equal(closeCount,1);assert.equal(refreshCount,1);
        clearTimeout(core.toast.timer);
    """)


@pytest.mark.parametrize("status", [403, 409])
def test_unlock_server_rejection_stays_in_form_without_success_or_refresh(status):
    run_web_script(ACCOUNT_FIXTURE + FORM_FIXTURE + f"const status={status};" + r"""
        const message=status===409?'账号已被修改，请刷新后重试':'无权维护此账号';
        globalThis.fetch=async(url,options)=>{
          requests.push({url,options});
          return {ok:false,status,json:async()=>({detail:message})};
        };
        await submit();
        assert.ok(errorBox.innerHTML.includes(message));
        assert.equal(closeCount,0);assert.equal(refreshCount,0);
        assert.equal(submitButton.disabled,false);
        assert.equal(toast.textContent,'');
        assert.equal(organization.accounts[0].login_locked,true);
        assert.equal(requests.filter(request=>request.options.method==='POST').length,1);
    """)
