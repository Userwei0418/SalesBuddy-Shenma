from pathlib import Path
import argparse,json,subprocess
parser=argparse.ArgumentParser(description='Verify customer source and optional live repository isolation')
parser.add_argument('--verify-remote', action='store_true')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/'frontend/project.config.json').read_text())
assert cfg['appid']!='wx08463b79dbeb2ba7','Source AppID forbidden'
assert 'https://salesbuddy.shenzhoukuntai.com:28899/api/v1' in (root/'frontend/miniprogram/config.js').read_text()
assert 'www.ericepc.com' not in (root/'frontend/miniprogram/config.js').read_text()
if (root/'.git').exists():
 remotes=subprocess.check_output(['git','remote','-v'],cwd=root,text=True)
 assert 'Raccoon-SalesBuddy.git' not in remotes and 'gitlab.senseauto.com' not in remotes
 print('Repository remotes isolated')
if args.verify_remote:
 metadata=json.loads(subprocess.check_output(['gh','api','repos/Userwei0418/SalesBuddy-Shenma'],cwd=root,text=True))
 assert metadata.get('full_name')=='Userwei0418/SalesBuddy-Shenma' and metadata.get('private') is True, 'Customer GitHub repository must be PRIVATE before packaging'
 print('Live customer GitHub visibility verified: PRIVATE')
print('Frontend target isolated; AppID:', 'pending' if cfg['appid'].startswith('REPLACE_') else 'customer configured')
