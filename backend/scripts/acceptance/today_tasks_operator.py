"""Install only non-secret today-task rollout and approved source scope.

Called under the deploy lock after publication/live authorization. Never reads
an EnvironmentFile. Other capability configurations must remain byte-equivalent
as objects. release removes only the test narrowing, keeping production enabled.
"""

import argparse
import grp
import json
import os
from pathlib import Path

from today_tasks_business_live import ACTOR, SCENARIOS, WORKSPACE, select_scope

PRIOR = ('battle_map_review', 'opportunity_draft', 'personal_risks', 'visit_entry')


def configuration(plan, mode, prior):
    scope = select_scope(plan, 'production' if mode == 'release' else mode, live=True)
    config = json.loads(json.dumps(prior))
    caps = config[WORKSPACE]['capabilities']
    assert set(caps) in (set(PRIOR), set(PRIOR) | {'today_tasks'})
    assert all(caps[name] == {'enabled': True, 'rollout': 'production'} for name in PRIOR)
    scope_value = {'approval_status': 'approved', 'expires_at': scope.expires_at.isoformat(),
                   'sources': dict(scope.sources)}
    if mode == 'release':
        assert caps['today_tasks'] == {'enabled': True, 'rollout': 'production'}
        scope_value['enabled'] = False
    else:
        caps['today_tasks'] = {'enabled': mode != 'off', 'rollout': 'production' if mode == 'production' else 'pilot'}
        if mode not in ('off', 'production'):
            caps['today_tasks'].update(user_ids=[ACTOR], expires_at=scope.expires_at.isoformat(),
                                       block_platform_requests=mode == 'blocked')
    return config, {WORKSPACE: {ACTOR: scope_value}}


def atomic_json(path, value):
    temporary = Path(str(path) + '.today-next')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 0, grp.getgrnam('sales-backend').gr_gid)
    os.replace(temporary, path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--mode', choices=(*SCENARIOS, 'release'), required=True)
    parser.add_argument('--rollout', default='/etc/sales-fde-pilot.json')
    parser.add_argument('--scope', default='/etc/sales-today-tasks-acceptance.json')
    args = parser.parse_args()
    config, scope = configuration(json.loads(Path(args.plan).read_text()), args.mode,
                                  json.loads(Path(args.rollout).read_text()))
    # Narrowing is installed before the route. An in-flight old scope fails its
    # transactional recheck; missing/partial scope never broadens the sources.
    atomic_json(args.scope, scope)
    atomic_json(args.rollout, config)
    print('TODAY_TASKS_MODE=' + args.mode, flush=True)
