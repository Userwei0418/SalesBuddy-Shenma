from pathlib import Path
import json,subprocess
root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/'frontend/project.config.json').read_text())
assert cfg['appid']=='wx2824bdeb58528fd8','Customer AppID must match the confirmed project'
assert cfg.get('setting', {}).get('urlCheck') is True, 'Domain validation must remain enabled'
assert 'https://salesbuddy.shenzhoukuntai.com:28899/api/v1' in (root/'frontend/miniprogram/config.js').read_text()
assert 'www.ericepc.com' not in (root/'frontend/miniprogram/config.js').read_text()
if (root/'.git').exists():
 remotes=subprocess.check_output(['git','remote','-v'],cwd=root,text=True)
 assert 'Raccoon-SalesBuddy.git' not in remotes and 'gitlab.senseauto.com' not in remotes
 print('Repository remotes isolated')
print('Frontend target isolated; AppID:', 'pending' if cfg['appid'].startswith('REPLACE_') else 'customer configured')

web=root/'business-web'
if web.exists():
 connection=json.loads((web/'connection.config.json').read_text())
 assert connection['apiTarget']=='https://salesbuddy.shenzhoukuntai.com:28899/api/v1'
 assert connection['adminUrl']=='/admin'
 for name in ['index.html','department-ui/Frame.jsx']:
  assert 'www.ericepc.com' not in (web/name).read_text(),name
 print('Business Web customer endpoint isolated; shared login retained')
