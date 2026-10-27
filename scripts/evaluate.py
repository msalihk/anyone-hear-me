"""Score the pipeline on the fictional evaluation set (data/eval) for one or more backends.

Message level: help request vs noise, address (correct / correctly flagged / wrong), people count, status.
Task level (when out/tasks-<backend>.json exists): pairwise merge precision and recall, incident status.

usage: python scripts/evaluate.py [--backends meta open] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
ADDRESS_STYLES = {'full', 'no_district', 'misspell', 'words', 'name_landmark', 'wrong_count'}


def load(backend: str):
    incidents = {i['id']: i for i in json.loads((ROOT / 'data/eval/incidents.json').read_text())}
    msgs = {m['id']: m for m in map(json.loads, (ROOT / 'data/eval/messages.jsonl').read_text().splitlines())}
    recs = {r['id']: r for r in map(json.loads, (ROOT / f'out/records-{backend}.jsonl').read_text().splitlines())}
    return incidents, msgs, recs


def states_number(text: str, n: int) -> bool:
    from ahm.addresses import words_to_number
    t = text.replace('I', 'ı').replace('İ', 'i').lower()
    if re.search(rf'(?<![\d/]){n}(?![\d/])', t):
        return True
    toks = re.findall(r'[a-zçğıöşü]+', t)
    for i in range(len(toks)):
        for j in (1, 2):
            if words_to_number(' '.join(toks[i:i + j])) == n:
                return True
    return False


def message_scores(incidents, msgs, recs) -> dict:
    s = {k: 0 for k in ['n', 'noise_ok', 'noise_n', 'req_ok', 'req_n', 'addr_n', 'addr_correct', 'addr_flagged',
                        'addr_wrong', 'addr_missing', 'people_n', 'people_ok', 'people_null', 'resolved_n',
                        'resolved_ok', 'partial_n', 'partial_ok', 'urgency_n', 'urgency_ok']}
    errors = []
    for mid, m in msgs.items():
        r = recs.get(mid)
        if not r:
            continue
        rec, t = r['record'], m['truth']
        s['n'] += 1
        if t['style'] == 'noise':
            s['noise_n'] += 1
            s['noise_ok'] += not rec['is_help_request']
            continue
        inc = incidents[t['incident']]
        if t['style'] != 'resolved':  # "everyone was rescued" is an update, not a request
            s['req_n'] += 1
            s['req_ok'] += bool(rec['is_help_request'])
        if t['style'] in ADDRESS_STYLES:
            s['addr_n'] += 1
            ac = rec['address_check']
            nb, d = ac['neighbourhood'], ac['district']
            if nb['value'] == inc['neighbourhood'] and d['value'] == inc['district'] and ac['ok']:
                s['addr_correct'] += 1
            elif (not ac['ok']) and (inc['neighbourhood'] in ' '.join(nb.get('alternatives') or [])
                                     or nb['value'] == inc['neighbourhood']):
                s['addr_flagged'] += 1  # not resolved, but the right place is among the options shown
            elif nb['value'] is None and nb['status'] in ('missing', 'not_found'):
                s['addr_missing'] += 1
                errors.append((mid, 'address missing', m['text'][:90], rec['location']))
            else:
                s['addr_wrong'] += 1
                errors.append((mid, f'address wrong: {d["value"]}/{nb["value"]}', m['text'][:90]))
        if inc['type'] == 'rescue' and t['style'] not in ('update_partial', 'resolved', 'caps'):
            want = t.get('stated_people', inc['people_trapped'])
            if not states_number(m['text'], want):
                continue  # the message does not say how many; nothing to check
            s['people_n'] += 1
            if rec['people_trapped'] is None:
                s['people_null'] += 1
            elif rec['people_trapped'] == want:
                s['people_ok'] += 1
            else:
                errors.append((mid, f'people {rec["people_trapped"]} != {want}', m['text'][:90]))
        if inc['type'] == 'rescue' and t['style'] in ('full', 'words', 'no_district') and inc['signs_of_life'] == 'voices_heard':
            s['urgency_n'] += 1
            s['urgency_ok'] += rec['urgency'] == 'critical'
        if t['style'] == 'resolved':
            s['resolved_n'] += 1
            s['resolved_ok'] += rec['status'] == 'resolved'
        if t['style'] == 'update_partial':
            s['partial_n'] += 1
            ok = rec['status'] == 'open' and rec.get('people_rescued') == t.get('rescued')
            s['partial_ok'] += ok
            if not ok:
                errors.append((mid, f'partial: status={rec["status"]} rescued={rec.get("people_rescued")} want {t.get("rescued")}', m['text'][:90]))
    return {'scores': s, 'errors': errors}


def task_scores(incidents, msgs, backend) -> dict | None:
    path = ROOT / f'out/tasks-{backend}.json'
    if not path.exists():
        return None
    tasks = json.loads(path.read_text())['tasks']
    pred = {}
    for t in tasks:
        for mid in t['message_ids']:
            pred[mid] = t['id']
    truth = {mid: m['truth']['incident'] for mid, m in msgs.items() if m['truth']['incident']}
    ids = sorted(truth)
    tp = fp = fn = 0
    for a, b in combinations(ids, 2):
        same_t = truth[a] == truth[b]
        same_p = a in pred and b in pred and pred[a] == pred[b]
        tp += same_t and same_p
        fp += same_p and not same_t
        fn += same_t and not same_p
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    # incident status: majority task of each incident
    status_ok = status_n = 0
    for iid, inc in incidents.items():
        if inc['type'] != 'rescue':
            continue
        mids = [m for m in ids if truth[m] == iid and m in pred]
        if not mids:
            continue
        tid = max(set(pred[m] for m in mids), key=lambda x: sum(pred[m] == x for m in mids))
        task = next(t for t in tasks if t['id'] == tid)
        want = {'resolved': 'resolved', 'partial': 'partly_rescued', 'open': 'open'}[inc['outcome']]
        status_n += 1
        status_ok += task['status'] == want
    return {'tasks': len(tasks), 'incidents': len(incidents), 'pair_precision': round(precision, 3),
            'pair_recall': round(recall, 3), 'status_ok': status_ok, 'status_n': status_n}


def pct(a, b):
    return f'{a}/{b} ({100 * a / b:.0f}%)' if b else '-'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backends', nargs='+', default=['meta', 'open'])
    ap.add_argument('--errors', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    report = {}
    for b in a.backends:
        incidents, msgs, recs = load(b)
        ms = message_scores(incidents, msgs, recs)
        report[b] = {'messages': ms['scores'], 'tasks': task_scores(incidents, msgs, b)}
        if a.errors:
            print(f'--- {b} errors')
            for e in ms['errors']:
                print(' ', e)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    rows = [
        ('Noise recognised as not a request', lambda s: pct(s['noise_ok'], s['noise_n'])),
        ('Requests recognised', lambda s: pct(s['req_ok'], s['req_n'])),
        ('Address resolved correctly', lambda s: pct(s['addr_correct'], s['addr_n'])),
        ('Address flagged with right option', lambda s: pct(s['addr_flagged'], s['addr_n'])),
        ('Address wrong', lambda s: pct(s['addr_wrong'], s['addr_n'])),
        ('People count as stated', lambda s: pct(s['people_ok'], s['people_n'])),
        ('Critical when voices heard', lambda s: pct(s['urgency_ok'], s['urgency_n'])),
        ('"Rescued" updates marked resolved', lambda s: pct(s['resolved_ok'], s['resolved_n'])),
        ('Partial updates (open + count)', lambda s: pct(s['partial_ok'], s['partial_n'])),
    ]
    print(f'{"":38}' + ''.join(f'{b:>18}' for b in a.backends))
    for name, f in rows:
        print(f'{name:38}' + ''.join(f'{f(report[b]["messages"]):>18}' for b in a.backends))
    for b in a.backends:
        if report[b]['tasks']:
            t = report[b]['tasks']
            print(f'{b}: {t["tasks"]} tasks for {t["incidents"]} incidents; merge precision {t["pair_precision"]}, '
                  f'recall {t["pair_recall"]}; incident status {t["status_ok"]}/{t["status_n"]}')


if __name__ == '__main__':
    main()
