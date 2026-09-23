"""Exercise the actual web role formatter and account/edit renderers with Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).parents[1] / "src/sales_backend/web/assets"


def run_web_script(body: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for actual web renderer checks")
    imports = (
        'import assert from "node:assert/strict";\n'
        f"const core = await import({json.dumps((ASSETS / 'core.js').as_uri())});\n"
        f"const pages = await import({json.dumps((ASSETS / 'pages.js').as_uri())});\n"
    )
    result = subprocess.run(
        [node, "--input-type=module"],
        input=imports + body,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_role_labels_use_directory_with_fde_titles_and_nonempty_fallback():
    run_web_script(r"""
        core.state.org={roles:[{code:'fde',name:'旧的成员称呼'},
          {code:'fde_lead',name:'旧的主管称呼'},
          {code:'sales',name:'区域销售'},
          {code:'specialist',name:'产品支持<script>'},
          {code:'blank',name:'  '}]};
        assert.equal(core.roleLabel('fde'),'FDE');
        assert.equal(core.roleLabel('fde_lead'),'FDE主管');
        assert.equal(core.roleLabel('sales'),'区域销售');
        assert.equal(core.roleLabel('specialist'),'产品支持<script>');
        assert.equal(core.roleLabel('blank'),'blank');
        assert.equal(core.roleLabel('unrecognized'),'unrecognized');
        assert.equal(core.roleLabel(''),'未分配角色');
        const options=core.roleOptions('fde');
        assert.match(options, /value="fde" selected>FDE<\/option>/);
        assert.match(options, /value="fde_lead" >FDE主管<\/option>/);
        assert.match(options, /产品支持&lt;script&gt;/);
        assert.doesNotMatch(options, /<script>/);
    """)


@pytest.mark.parametrize("edited_role", ["fde", "fde_lead"])
def test_account_table_and_edit_choices_show_the_same_fde_titles(edited_role):
    run_web_script(r"""
        const organization={roles:[{code:'fde',name:'',assignable:true},
          {code:'fde_lead',name:'部门老大',assignable:true}],
          departments:[{id:'all',name:'全部团队',code:'ALL',status:'active'},
            {id:'team',name:'FDE部门',code:'FDE',status:'active',parent_team_id:'all'}],
          accounts:[{id:'lead',account_code:'FDEL001',display_name:'李鹏程',team_id:'team',team_name:'FDE部门',roles:['fde_lead'],status:'active',has_password:true},
            ...['周玮','张家涛','叶源'].map((name,i)=>({id:'member'+i,account_code:'FDE00'+(i+1),display_name:name,team_id:'team',team_name:'FDE部门',roles:['fde'],status:'active',has_password:true}))]};
        const requests=[];
        globalThis.fetch=async (url,options)=>{requests.push({url,options});return {ok:true,status:200,json:async()=>organization};};
        core.state.actor={workspace_id:'test-workspace',user_id:'test-admin',role:'administrator'};core.state.token='unit-test-only';
        const page=await pages.accounts();
        assert.equal((page.html.match(/class="pill">FDE<\/span>/g)||[]).length,3);
        assert.equal((page.html.match(/class="pill">FDE主管<\/span>/g)||[]).length,1);
        assert.doesNotMatch(page.html, /class="pill"><\/span>/);
        assert.match(page.html, /FDE部门<\/strong>/);
        const dialog={innerHTML:'',querySelectorAll:()=>[],querySelector:()=>({addEventListener(){}}),showModal(){}};
        const form={};
        globalThis.document={querySelector:selector=>selector==='#dialog'?dialog:selector==='#dialog-form'?form:null};
        const root={querySelector:()=>null,querySelectorAll:()=>[]};page.bind(root);
    """ + f"const role={json.dumps(edited_role)};\n" + r"""
        const row=organization.accounts.find(account=>account.roles.includes(role));
        const button={dataset:{action:'edit',id:row.id},disabled:false};
        await root.onclick({target:{closest:()=>button}});
        assert.match(dialog.innerHTML, /data-role-label="fde">FDE<\/span>/);
        assert.match(dialog.innerHTML, /data-role-label="fde_lead">FDE主管<\/span>/);
        assert.match(dialog.innerHTML, new RegExp('value="'+role+'" checked'));
        assert.doesNotMatch(dialog.innerHTML, /部门老大/);
        assert.deepEqual(row.roles,[role]);
        assert.ok(requests.every(request=>request.options.method==='GET'));
    """)
