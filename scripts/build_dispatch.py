"""Add the agent's team suggestions to a recorded replay, at a few moments of the scenario.

At each moment the desk is rebuilt from the replay (the tasks as they stood then), the agent proposes
which free team goes where (ahm/dispatch.py), and the coordinator's approval is simulated: a suggested
team counts as busy until its task is cleared or, also simulated, until it reports back after SHIFT
minutes on site. The suggestions are stored as steps of kind "dispatch".

usage: python scripts/build_dispatch.py [--backend meta] [--at 60 150 240 330]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ahm.dispatch import plan  # noqa: E402
from ahm.llm import Backend  # noqa: E402

SHIFT = 180  # simulated: a team reports back and is free again after three hours on a building


def tasks_at(steps: list[dict], minute: int) -> list[dict]:
    """The tasks as they stood at `minute`, from the task snapshots in the replay."""
    tasks, owner = {}, {}
    for s in steps:
        if s['minute'] > minute or s.get('kind') == 'dispatch' or not s.get('task'):
            continue
        t = s['task']
        for mid in t['message_ids']:
            prev = owner.get(mid)
            if prev and prev != t['id']:
                tasks.pop(prev, None)  # merged into this task
            owner[mid] = t['id']
        tasks[t['id']] = t
    return list(tasks.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='meta')
    ap.add_argument('--at', nargs='+', type=int, default=[60, 150, 240, 330])
    a = ap.parse_args()
    path = ROOT / 'data/demo' / f'replay-{a.backend}.json'
    replay = json.loads(path.read_text())
    steps = [s for s in replay['steps'] if s.get('kind') != 'dispatch']
    teams = json.loads((ROOT / 'data/demo/teams.json').read_text())['teams']
    coords = {k: v for k, v in json.loads((ROOT / 'data/coords.json').read_text())['places'].items() if v}
    be = Backend.meta() if a.backend == 'meta' else Backend.open()
    busy: dict[str, str] = {}
    since: dict[str, int] = {}
    plans = []
    for n, minute in enumerate(a.at, 1):
        tasks = tasks_at(steps, minute)
        by_id = {t['id']: t for t in tasks}
        owner = {mid: t['id'] for t in tasks for mid in t['message_ids']}
        # a team is free again when its task is cleared; it follows its task when tasks merge
        for team, tid in list(busy.items()):
            if tid not in by_id:
                first = next((s['task']['message_ids'][0] for s in steps if s.get('task') and s['task']['id'] == tid), None)
                tid = owner.get(first)
                busy[team] = tid
            if not tid or by_id[tid]['status'] == 'resolved' or minute - since[team] >= SHIFT:
                busy.pop(team)
        p = plan(tasks, teams, dict(busy), coords, minute, be)
        for x in p['assignments']:
            busy[x['team']] = x['task']
            since[x['team']] = minute
        step = {'id': f'D{n}', 'kind': 'dispatch', 'minute': minute, 'channel': 'agent',
                'text': f'{len(p["assignments"])} team suggestions, {len(p["held_back"])} tasks held back',
                'record': {}, 'agent': [], 'task': None, 'plan': p, 'busy': dict(busy), 'simulated': True}
        plans.append(step)
        print(f'hour {minute / 60:.1f}: {[(x["team"], x["task"]) for x in p["assignments"]]}; held {[h["task"] for h in p["held_back"]]}', flush=True)
    merged = sorted(steps + plans, key=lambda s: (s['minute'], s.get('kind') == 'dispatch'))
    replay['steps'] = merged
    replay['teams'] = teams
    path.write_text(json.dumps(replay, ensure_ascii=False))
    print(f'{len(plans)} dispatch steps -> {path}')


if __name__ == '__main__':
    main()
