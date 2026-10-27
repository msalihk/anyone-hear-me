"""What the follow-up loop changes: score the replay with and without follow-up questions.

Both replays run the same recorded reports through the same desk; the difference is whether the agent asks
reporters to settle what does not add up (answers come from the simulated reporter, ahm/simulate.py).
Scores per incident of the fictional set (data/eval), using the task that holds most of its reports:

  located      the task's place is the incident's true neighbourhood, district and province
  count right  the task shows one people count, and it is the true one (rescue incidents)
  open issues  tasks with a conflict nobody settled, or no place at all
  merge        pairwise precision and recall over the original reports (answers are not counted)

usage: python scripts/eval_followups.py [--backend meta] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ahm.addresses import fold  # noqa: E402


def score(replay: dict, incidents: dict, msgs: dict) -> dict:
    tasks = replay['tasks']
    pred = {mid: t['id'] for t in tasks for mid in t['message_ids'] if not mid.startswith('R')}
    by_id = {t['id']: t for t in tasks}
    truth = {mid: m['truth']['incident'] for mid, m in msgs.items() if m['truth']['incident']}
    ids = sorted(truth)
    tp = fp = fn = 0
    for a, b in combinations(ids, 2):
        same_t, same_p = truth[a] == truth[b], a in pred and b in pred and pred[a] == pred[b]
        tp += same_t and same_p
        fp += same_p and not same_t
        fn += same_t and not same_p
    located = count_ok = count_n = status_ok = status_n = 0
    for iid, inc in incidents.items():
        mids = [m for m in ids if truth[m] == iid and m in pred]
        if not mids:
            continue
        # the task holding most of the incident's reports; ties go to the earliest task, so runs agree
        tid = max(sorted({pred[m] for m in mids}), key=lambda x: sum(pred[m] == x for m in mids))
        t = by_id[tid]
        p = t.get('place') or {}
        if p and all(fold(p.get(k) or '') == fold(inc[k]) for k in ('province', 'district', 'neighbourhood')):
            located += 1
        if inc['type'] == 'rescue':
            count_n += 1
            count_ok += t['people_reported'] == [inc['people_trapped']]
            want = {'resolved': 'resolved', 'partial': 'partly_rescued', 'open': 'open'}[inc['outcome']]
            status_n += 1
            status_ok += t['status'] == want
    open_issues = sum(1 for t in tasks if t['status'] != 'resolved' and (
        any('settled' not in c for c in t['conflicts']) or not t.get('place')))
    steps = replay['steps']
    asked = sum(len(a['questions']) for s in steps for a in s['agent'] if a['step'] == 'ask')
    answers = [s for s in steps if s.get('answer')]
    return {'tasks': len(tasks), 'incidents': len(incidents),
            'merge_precision': round(tp / (tp + fp), 3) if tp + fp else 1.0,
            'merge_recall': round(tp / (tp + fn), 3) if tp + fn else 1.0,
            'located': located, 'count_right': count_ok, 'count_n': count_n,
            'status_right': status_ok, 'status_n': status_n, 'open_issues': open_issues,
            'questions': asked, 'answers': len(answers),
            'answers_settling': sum(1 for s in answers if s.get('settles'))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='meta')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    incidents = {i['id']: i for i in json.loads((ROOT / 'data/eval/incidents.json').read_text())}
    msgs = {m['id']: m for m in map(json.loads, (ROOT / 'data/eval/messages.jsonl').read_text().splitlines())}
    out = {}
    for name, f in (('without', f'replay-{a.backend}-nofollowup.json'), ('with', f'replay-{a.backend}.json')):
        out[name] = score(json.loads((ROOT / 'data/demo' / f).read_text()), incidents, msgs)
    if a.json:
        print(json.dumps(out, indent=1))
        return
    w, f = out['without'], out['with']
    n = w['incidents']
    rows = [(f'Tasks ({n} incidents)', w['tasks'], f['tasks']),
            ('Incidents located correctly', f'{w["located"]}/{n}', f'{f["located"]}/{n}'),
            ('Rescue incidents with the right count', f'{w["count_right"]}/{w["count_n"]}', f'{f["count_right"]}/{f["count_n"]}'),
            ('Rescue incidents with the right status', f'{w["status_right"]}/{w["status_n"]}', f'{f["status_right"]}/{f["status_n"]}'),
            ('Open tasks with an unsettled issue', w['open_issues'], f['open_issues']),
            ('Merge precision', w['merge_precision'], f['merge_precision']),
            ('Merge recall', w['merge_recall'], f['merge_recall']),
            ('Questions asked / answers that settled something', '-', f'{f["questions"]} / {f["answers_settling"]}')]
    print(f'{"":48}{"without":>12}{"with follow-ups":>18}')
    for r in rows:
        print(f'{r[0]:48}{str(r[1]):>12}{str(r[2]):>18}')


if __name__ == '__main__':
    main()
