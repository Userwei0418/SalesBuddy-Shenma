"""Emit the isolated customer weekly.v2 Agent spec; contains no credentials."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'backend/src/sales_backend/weekly_contract'


def build():
    return {
        'name': '神码-销售周报-weekly.v2',
        'description': '神码独立周报；weekly.v2；只读后端本人事实，Web 草稿，无业务写入工具。',
        'role': '销售周报',
        'agent_soul': {
            'schema_version': 1,
            'prompt': {'system_prompt': (CONTRACT/'system-prompt.txt').read_text() +
                                       (CONTRACT/'runtime-guard.txt').read_text()},
            'model': {'plugin_id': 'langgenius/openai_api_compatible',
                      'model_provider': 'langgenius/openai_api_compatible/openai_api_compatible',
                      'model': 'senseaudio-s2', 'model_settings': {'temperature': 0, 'max_tokens': 4096}},
            'tools': {'dify_tools': [], 'cli_tools': []}, 'knowledge': {'sets': []},
        },
    }


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    with args.output.open('x') as out:
        json.dump(build(), out, ensure_ascii=False, indent=2)
