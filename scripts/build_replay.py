"""Replay the evaluation scenario through the live Desk, one report at a time, and store what happened.

With follow-ups (the default), when a task has something to settle the agent drafts questions to the
reporters; in the replay the coordinator's approval and the reporter's reply are simulated (ahm/simulate.py
answers from the fictional incident's facts), and the agent reads each reply, updates the task and checks
whether it is now the same building as another task.

The demo plays this file back: each step is a message, the agent's record of it, what the agent did
(rule match, same-building judgement, new task) and the task as it stood afterwards. Nothing in the
playback is scripted by hand; it is the agent's own output, recorded.

usage: python scripts/build_replay.py [--backend meta] [--no-followups]
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ahm.answers import answer_item, interpret  # noqa: E402
from ahm.followup import draft, needs_followup, open_issues  # noqa: E402
from ahm.llm import Backend  # noqa: E402
from ahm.merge import Desk  # noqa: E402
from ahm.simulate import reply  # noqa: E402

KEEP = ('is_help_request', 'status', 'location', 'people_trapped', 'people_rescued', 'signs_of_life', 'needs',
        'urgency', 'urgency_reason', 'evidence', 'missing_info', 'summary_en', 'address_check')


def delay(key: str) -> int:
    """Minutes until a reporter answers: 12 to 41, fixed per question so the replay is reproducible."""
    return 12 + int(hashlib.md5(key.encode()).hexdigest(), 16) % 30


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='meta')
    ap.add_argument('--no-followups', action='store_true', help='replay without follow-up questions')
    a = ap.parse_args()
    be = Backend.meta() if a.backend == 'meta' else Backend.open()
    msgs = [json.loads(l) for l in (ROOT / 'data/eval/messages.jsonl').read_text().splitlines()]
    by_msg = {m['id']: m for m in msgs}
    incidents = {i['id']: i for i in json.loads((ROOT / 'data/eval/incidents.json').read_text())}
    recs = {r['id']: r for r in map(json.loads, (ROOT / f'out/records-{a.backend}.jsonl').read_text().splitlines())}
    desk = Desk(be)
    steps: list[dict] = []
    replies: list[tuple[int, int, dict]] = []   # (minute, order, pending reply)
    counter = itertools.count()
    nreply = itertools.count(1)

    def maybe_ask(tid: str | None, minute: int, log: list[dict]) -> None:
        if a.no_followups or not tid or desk.waiting(tid) or desk.rounds(tid) >= 2:
            return
        task = desk.task_for(desk.cluster(tid).ids[0])
        if not needs_followup(task):
            return
        qs = draft(task, desk.items, be)
        if not qs:
            return
        desk.ask(tid, qs, minute)
        log.append({'step': 'ask', 'task': tid, 'issues': open_issues(task), 'questions': qs})
        for q in qs:
            heapq.heappush(replies, (minute + delay(q['to_message'] + q['text_tr']), next(counter), q))

    def answer(minute: int, q: dict) -> None:
        asked = desk.items[q['to_message']]
        inc = incidents.get(by_msg.get(q['to_message'], {}).get('truth', {}).get('incident') or '')
        text = reply(q['text_tr'], asked, inc, be)
        patch = interpret(q['text_tr'], asked, text, be)
        rid = f'R{next(nreply):02d}'
        item = answer_item(rid, minute, asked, q['text_tr'], text, patch)
        log = desk.answer(item)
        tid = desk.task_of(rid)
        maybe_ask(tid, minute, log)
        steps.append({'id': rid, 'minute': minute, 'channel': item['channel'], 'text': text,
                      'record': {k: item['record'][k] for k in KEEP if k in item['record']},
                      'answer': True, 'simulated': True, 'reply_to': q['to_message'], 'question': q['text_tr'],
                      'answered': item['answered'], 'settles': item['settles'],
                      'agent': log, 'task': desk.task_for(rid)})
        print(f'  {rid} -> {q["to_message"]} [{tid}] settles {item["settles"]}: {text[:70]}', flush=True)

    for n, m in enumerate(msgs, 1):
        while replies and replies[0][0] <= m['minute']:
            minute, _, q = heapq.heappop(replies)
            answer(minute, q)
        item = recs[m['id']]
        log = desk.add(item)
        tid = desk.task_of(m['id'])
        maybe_ask(tid, m['minute'], log)
        steps.append({'id': m['id'], 'minute': m['minute'], 'channel': m['channel'], 'text': m['text'],
                      'record': {k: item['record'][k] for k in KEEP if k in item['record']},
                      'seconds': item['record'].get('_meta', {}).get('seconds'),
                      'agent': log, 'task': desk.task_for(m['id'])})
        if n % 20 == 0:
            print(f'{n}/{len(msgs)}: {len(desk.clusters)} tasks', flush=True)
    while replies:
        minute, _, q = heapq.heappop(replies)
        answer(minute, q)
    out = {'backend': a.backend, 'model': be.model, 'followups': not a.no_followups, 'steps': steps,
           'tasks': desk.tasks(), 'decisions': desk.decisions}
    dest = ROOT / 'data' / 'demo'
    dest.mkdir(parents=True, exist_ok=True)
    suffix = '-nofollowup' if a.no_followups else ''
    (dest / f'replay-{a.backend}{suffix}.json').write_text(json.dumps(out, ensure_ascii=False))
    (ROOT / f'out/tasks-{a.backend}-live{suffix}.json').write_text(json.dumps({'tasks': desk.tasks()}, ensure_ascii=False))
    asks = sum(1 for s in steps for x in s['agent'] if x['step'] == 'ask')
    print(f'{len(steps)} steps ({asks} follow-up rounds), {len(desk.clusters)} tasks -> data/demo/replay-{a.backend}{suffix}.json')


if __name__ == '__main__':
    main()
