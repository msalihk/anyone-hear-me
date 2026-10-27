"""Replay the evaluation scenario through the live Desk, one report at a time, and store what happened.

The demo plays this file back: each step is a message, the agent's record of it, what the agent did
(rule match, same-building judgement, new task) and the task as it stood afterwards. Nothing in the
playback is scripted by hand; it is the agent's own output, recorded.

usage: python scripts/build_replay.py [--backend meta]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ahm.llm import Backend  # noqa: E402
from ahm.merge import Desk  # noqa: E402

KEEP = ('is_help_request', 'status', 'location', 'people_trapped', 'people_rescued', 'signs_of_life', 'needs',
        'urgency', 'urgency_reason', 'evidence', 'missing_info', 'summary_en', 'address_check')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='meta')
    a = ap.parse_args()
    be = Backend.meta() if a.backend == 'meta' else Backend.open()
    msgs = [json.loads(l) for l in (ROOT / 'data/eval/messages.jsonl').read_text().splitlines()]
    recs = {r['id']: r for r in map(json.loads, (ROOT / f'out/records-{a.backend}.jsonl').read_text().splitlines())}
    desk = Desk(be)
    steps = []
    for n, m in enumerate(msgs, 1):
        item = recs[m['id']]
        log = desk.add(item)
        steps.append({'id': m['id'], 'minute': m['minute'], 'channel': m['channel'], 'text': m['text'],
                      'record': {k: item['record'][k] for k in KEEP if k in item['record']},
                      'seconds': item['record'].get('_meta', {}).get('seconds'),
                      'agent': log, 'task': desk.task_for(m['id'])})
        if n % 20 == 0:
            print(f'{n}/{len(msgs)}: {len(desk.clusters)} tasks', flush=True)
    out = {'backend': a.backend, 'model': be.model, 'steps': steps, 'tasks': desk.tasks(),
           'decisions': desk.decisions}
    dest = ROOT / 'data' / 'demo'
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f'replay-{a.backend}.json').write_text(json.dumps(out, ensure_ascii=False))
    (ROOT / f'out/tasks-{a.backend}-live.json').write_text(json.dumps({'tasks': desk.tasks()}, ensure_ascii=False))
    print(f'{len(steps)} steps, {len(desk.clusters)} tasks -> data/demo/replay-{a.backend}.json')


if __name__ == '__main__':
    main()
