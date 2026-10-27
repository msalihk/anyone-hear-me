"""Merge reports about the same building into one rescue task, and show what does not add up.

Two passes:
  1. Rules: reports at the same resolved neighbourhood with the same building name, or the same street and
     building number, are the same place.
  2. Agent: for groups that could still be the same building (compatible places, compatible building
     details), Muse Spark reads both groups' messages and answers same / different / unsure with a reason.
     "unsure" is never merged; the coordinator sees it as a possible duplicate.

Then each group becomes a task: best address (an ambiguous address can be settled by another report of the
same building), reported people counts with their sources (conflicts are listed, not averaged), status
from the latest update, strongest sign of life, union of needs, and the questions still open.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .addresses import fold
from .llm import Backend, chat

SIGN_RANK = {'voices_heard': 3, 'contact_by_phone': 2, 'unknown': 1, 'none_reported': 0}
URGENCY_RANK = {'critical': 3, 'high': 2, 'medium': 1, 'low': 0}


# --- places --------------------------------------------------------------------------------------------

def places_of(rec: dict) -> set[tuple[str, str, str]] | None:
    """Possible (province, district, neighbourhood) of one report, folded. None = no place information."""
    ac = rec['address_check']
    p, d, n = (ac[k]['value'] for k in ('province', 'district', 'neighbourhood'))
    if p and d and n:
        return {(fold(p), fold(d), fold(n))}
    alts = ac['neighbourhood'].get('alternatives') or []
    opts = set()
    for a in alts:
        m = re.match(r'^(.*) \((.*), (.*)\)$', a)
        if m:
            opts.add((fold(m.group(3)), fold(m.group(2)), fold(m.group(1))))
    if opts:
        return opts
    if n:  # neighbourhood known, district/province not: match on the name only
        return {('*', '*', fold(n))}
    return None


def compatible(a: set | None, b: set | None) -> bool:
    if a is None or b is None:
        return True
    for x in a:
        for y in b:
            if x[2] == y[2] and all(u == v or '*' in (u, v) for u, v in zip(x[:2], y[:2])):
                return True
    return False


def building_key(loc: dict) -> str | None:
    name = loc.get('building_name')
    if not name:
        return None
    return fold(re.sub(r'\b(apartmanı|apartman|apt|sitesi|site|konutları|konut|blok|blokları)\b\.?', ' ',
                       name.replace('I', 'ı').replace('İ', 'i').lower()))


# --- clusters ------------------------------------------------------------------------------------------

@dataclass
class Cluster:
    ids: list[str]
    places: set | None
    buildings: set[str] = field(default_factory=set)
    street_nos: set[tuple[str, str]] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)
    tid: str | None = None  # stable task id once shown to a coordinator


def _street_no(loc: dict) -> tuple[str, str] | None:
    s, n = loc.get('street'), loc.get('building_no')
    if s and n:
        return (re.sub(r'\D', '', s) or fold(s), re.sub(r'\s', '', n).lower())
    return None


def _intersect(a, b):
    if a is None:
        return b
    if b is None:
        return a
    out = set()
    for x in a:
        for y in b:
            if x[2] == y[2] and all(u == v or '*' in (u, v) for u, v in zip(x[:2], y[:2])):
                out.add(tuple(u if u != '*' else v for u, v in zip(x, y)))
    return out or a


def rule_clusters(items: list[dict]) -> list[Cluster]:
    clusters: list[Cluster] = []
    for it in items:
        rec = it['record']
        c = Cluster([it['id']], places_of(rec))
        bk = building_key(rec['location'])
        if bk:
            c.buildings.add(bk)
        sn = _street_no(rec['location'])
        if sn:
            c.street_nos.add(sn)
        clusters.append(c)
    merged = True
    while merged:  # union while two clusters share a resolved place and a building identity
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                a, b = clusters[i], clusters[j]
                exact_place = a.places is not None and b.places is not None and len(a.places) == 1 \
                    and a.places == b.places and '*' not in next(iter(a.places))
                same_building = bool(same_name(a.buildings, b.buildings)) or bool(a.street_nos & b.street_nos)
                if exact_place and same_building:
                    a.ids += b.ids
                    a.buildings |= b.buildings
                    a.street_nos |= b.street_nos
                    a.reasons.append('same neighbourhood and building')
                    clusters.pop(j)
                    merged = True
                    break
            if merged:
                break
    return clusters


# --- agent judgement -----------------------------------------------------------------------------------

JUDGE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'reason'],
    'properties': {'verdict': {'type': 'string', 'enum': ['same', 'different', 'unsure']},
                   'reason': {'type': 'string', 'description': 'one short English sentence'}},
}

JUDGE_SYSTEM = """Two groups of help requests from an earthquake zone may describe the same collapsed \
building. Decide whether they do.

same: the neighbourhood (or district) is the same or compatible AND the building matches (same building \
name, or same street and number, or same name and landmark), and no detail contradicts. Missing details \
are not contradictions: a follow-up that names the building and neighbourhood but not the street is the \
same building. Spelling differences ("Orhanli"/"Orhanlı", "Marti"/"Martı") are the same name.
different: the places disagree, or the building name, street or number clearly differ (neighbouring \
buildings on one street are different buildings).
unsure: one group gives neither a building name nor a street and number, so it cannot be tied to a building.

People counts often differ between reporters; that is a conflict to report, not a reason to answer \
"different". Updates ("they were rescued") belong to the building they name."""


def _describe(c: Cluster, by_id: dict) -> str:
    lines = []
    for mid in c.ids:
        it = by_id[mid]
        loc = it['record']['location']
        ac = it['record']['address_check']
        place = ' / '.join(str(ac[k]['value'] or (ac[k]['alternatives'][:3] if ac[k].get('alternatives') else '?'))
                           for k in ('province', 'district', 'neighbourhood'))
        lines.append(f'- [{mid}, minute {it.get("minute")}] "{it["text"]}"\n  place: {place}; street: {loc.get("street")};'
                     f' no: {loc.get("building_no")}; building: {loc.get("building_name")}; landmark: {loc.get("landmark")}')
    return '\n'.join(lines)


def judge(a: Cluster, b: Cluster, by_id: dict, backend: Backend | None = None) -> dict:
    user = f'Group A:\n{_describe(a, by_id)}\n\nGroup B:\n{_describe(b, by_id)}'
    res = chat([{'role': 'system', 'content': JUDGE_SYSTEM}, {'role': 'user', 'content': user}],
               backend=backend, schema=JUDGE_SCHEMA, schema_name='same_building', reasoning_effort='low',
               max_tokens=1500)
    return res['data']


def same_name(a: set[str], b: set[str]) -> set[str]:
    """Building keys that match, also when one contains the other ("sümerlale" / "lale")."""
    out = set()
    for x in a:
        for y in b:
            if x == y or (min(len(x), len(y)) >= 4 and (x.endswith(y) or y.endswith(x))):
                out.add(min(x, y, key=len))
    return out


def name_places(clusters: list[Cluster]) -> dict[str, list[set]]:
    """building key -> the neighbourhood-name sets of located groups using it."""
    out: dict[str, list[set]] = defaultdict(list)
    for c in clusters:
        if c.places is not None:
            for bk in c.buildings:
                out[bk].append({p[2] for p in c.places})
    return out


def unique_place(bk: str, places_by_name: dict) -> bool:
    """True when every located group with this building name could be the same neighbourhood."""
    sets = [s for k, v in places_by_name.items() if same_name({k}, {bk}) for s in v]
    return bool(sets) and bool(set.intersection(*sets))


def is_candidate(a: Cluster, b: Cluster, places_by_name: dict) -> bool:
    """Could these two groups be the same building, so that the agent should be asked?"""
    if not compatible(a.places, b.places):
        return False
    # building details must not contradict: different names or different street+number -> no
    if a.buildings and b.buildings and not same_name(a.buildings, b.buildings):
        return False
    if a.street_nos and b.street_nos and not (a.street_nos & b.street_nos):
        return False
    # and something must tie them: a shared building identity, or a shared named neighbourhood
    shared_building = bool(same_name(a.buildings, b.buildings)) or bool(a.street_nos & b.street_nos)
    shared_place = a.places is not None and b.places is not None and \
        {p[2] for p in a.places} & {p[2] for p in b.places}
    if not (shared_building or shared_place):
        return False
    # a report with no place at all is only tied by street and number; a name alone
    # ("Güneş Apartmanı") exists in too many towns, so it becomes a possible match instead
    if (a.places is None or b.places is None) and not (a.street_nos & b.street_nos):
        shared = same_name(a.buildings, b.buildings)
        if not shared or not all(unique_place(bk, places_by_name) for bk in shared):
            return False
    return True


def candidate_pairs(clusters: list[Cluster]) -> list[tuple[int, int]]:
    places_by_name = name_places(clusters)
    return [(i, j) for i in range(len(clusters)) for j in range(i + 1, len(clusters))
            if is_candidate(clusters[i], clusters[j], places_by_name)]


def name_only_matches(clusters: list[Cluster]) -> list[dict]:
    """Groups with no place whose building name matches another group: shown, never merged."""
    out = []
    places_by_name = name_places(clusters)
    for i, a in enumerate(clusters):
        if a.places is not None or not a.buildings:
            continue
        for j, b in enumerate(clusters):
            shared = same_name(a.buildings, b.buildings)
            if i != j and shared and not all(unique_place(bk, places_by_name) for bk in shared):
                out.append({'a': a.ids, 'b': b.ids, 'verdict': 'unsure',
                            'reason': 'same building name, but this report gives no place'})
    return out


def agent_merge(clusters: list[Cluster], by_id: dict, backend: Backend | None = None, log=print,
                workers: int = 6) -> tuple[list[Cluster], list[dict]]:
    """Ask the model about every candidate pair; merge 'same' (transitively). Returns clusters and decisions."""
    pairs = candidate_pairs(clusters)
    log(f'agent: {len(pairs)} candidate pairs among {len(clusters)} groups')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        verdicts = list(pool.map(lambda p: judge(clusters[p[0]], clusters[p[1]], by_id, backend), pairs))
    parent = list(range(len(clusters)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    decisions = []
    for (i, j), v in zip(pairs, verdicts):
        decisions.append({'a': clusters[i].ids, 'b': clusters[j].ids, **v})
        if v['verdict'] == 'same':
            parent[find(i)] = find(j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(clusters)):
        groups[find(i)].append(i)
    out = []
    for members in groups.values():
        c = clusters[members[0]]
        for k in members[1:]:
            o = clusters[k]
            c = Cluster(c.ids + o.ids, _intersect(c.places, o.places), c.buildings | o.buildings,
                        c.street_nos | o.street_nos, c.reasons + o.reasons + ['agent: same building'])
        out.append(c)
    return out, decisions


# --- tasks ---------------------------------------------------------------------------------------------

def build_task(tid: str, c: Cluster, by_id: dict, decisions: list[dict]) -> dict:
    items = sorted((by_id[m] for m in c.ids), key=lambda it: it.get('minute') or 0)
    recs = [it['record'] for it in items]
    # place: a resolved address wins; otherwise the places every report allows
    resolved = Counter((r['address_check']['province']['value'], r['address_check']['district']['value'],
                        r['address_check']['neighbourhood']['value']) for r in recs if r['address_check']['ok'])
    place, place_status, options = None, 'unknown', []
    if resolved:
        place, _ = resolved.most_common(1)[0]
        place_status = 'confirmed' if len(resolved) == 1 else 'conflicting'
        options = [' / '.join(p) for p in resolved]
    elif c.places:
        full = [p for p in c.places if '*' not in p]
        if len(full) == 1:
            place, place_status = full[0], 'settled_by_cross_reports'
        else:
            place_status, options = 'ambiguous', [' / '.join(p) for p in sorted(c.places)][:8]
    loc_votes = defaultdict(Counter)
    for r in recs:
        for k in ('street', 'building_no', 'building_name', 'landmark'):
            if r['location'].get(k):
                loc_votes[k][r['location'][k]] += 1
    building = {k: v.most_common(1)[0][0] for k, v in loc_votes.items()}

    counts = [(r['people_trapped'], it['id']) for r, it in zip(recs, items) if r['people_trapped']]
    distinct = sorted({n for n, _ in counts})
    rescued = max([r.get('people_rescued') or 0 for r in recs] + [0])
    latest = recs[-1]
    if latest['status'] == 'resolved':
        status = 'resolved'
    elif rescued:
        status = 'partly_rescued'
    else:
        status = 'open'
    signs = max((r['signs_of_life'] for r in recs), key=lambda s: SIGN_RANK.get(s, 0))
    open_recs = [r for r in recs if r['status'] != 'resolved'] or recs
    urgency = max((r['urgency'] for r in open_recs), key=lambda u: URGENCY_RANK[u])
    needs = sorted({n for r in recs for n in r['needs']})
    conflicts = []
    if len(distinct) > 1:
        conflicts.append({'field': 'people_trapped', 'values': {str(n): [m for k, m in counts if k == n] for n in distinct}})
    if place_status in ('conflicting', 'ambiguous'):
        conflicts.append({'field': 'place', 'values': options})
    missing = sorted({q for r in recs for q in r.get('missing_info', [])})[:6]
    maybe = [d for d in decisions if d['verdict'] == 'unsure' and (set(d['a']) & set(c.ids) or set(d['b']) & set(c.ids))]
    return {
        'id': tid, 'message_ids': [it['id'] for it in items], 'reports': len(items),
        'channels': sorted({it.get('channel') or '?' for it in items}),
        'first_minute': items[0].get('minute'), 'last_minute': items[-1].get('minute'),
        'place': dict(zip(('province', 'district', 'neighbourhood'), place)) if place else None,
        'place_status': place_status, 'building': building,
        'people_reported': distinct, 'people_rescued': rescued or None, 'signs_of_life': signs,
        'status': status, 'urgency': 'closed' if status == 'resolved' else urgency, 'needs': needs,
        'conflicts': conflicts, 'open_questions': missing,
        'possible_duplicates': [sorted(set(d['a'] + d['b']) - set(c.ids)) for d in maybe],
        'summary': latest['summary_en'], 'merge_reasons': sorted(set(c.reasons)),
        'evidence': [{'message': it['id'], 'quote': e['quote'], 'field': e['field']}
                     for it in items for e in it['record'].get('evidence', [])
                     if e['field'] in ('people_trapped', 'signs_of_life', 'status', 'location.building_name')][:12],
    }


def merge(items: list[dict], backend: Backend | None = None, log=print) -> dict:
    """Processed messages -> {'tasks': [...], 'not_requests': [...], 'decisions': [...]}."""
    requests_ = [it for it in items if it['record']['is_help_request'] or it['record']['status'] == 'resolved']
    others = [it['id'] for it in items if it not in requests_]
    by_id = {it['id']: it for it in items}
    clusters = rule_clusters(requests_)
    log(f'rules: {len(requests_)} reports -> {len(clusters)} groups')
    clusters, decisions = agent_merge(clusters, by_id, backend, log=log)
    decisions += name_only_matches(clusters)
    log(f'agent: -> {len(clusters)} groups')
    tasks = [build_task(f'T{i + 1:02d}', c, by_id, decisions)
             for i, c in enumerate(sorted(clusters, key=lambda c: min(by_id[m].get('minute') or 0 for m in c.ids)))]
    order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'closed': 4}
    tasks.sort(key=lambda t: (order[t['urgency']], -(t['reports']), t['first_minute'] or 0))
    return {'tasks': tasks, 'not_requests': others, 'decisions': decisions}


# --- incremental desk (live use) -----------------------------------------------------------------------

def single(item: dict) -> Cluster:
    rec = item['record']
    c = Cluster([item['id']], places_of(rec))
    bk = building_key(rec['location'])
    if bk:
        c.buildings.add(bk)
    sn = _street_no(rec['location'])
    if sn:
        c.street_nos.add(sn)
    return c


def _join(a: Cluster, b: Cluster, reason: str) -> Cluster:
    tids = sorted(t for t in (a.tid, b.tid) if t)
    return Cluster(a.ids + b.ids, _intersect(a.places, b.places), a.buildings | b.buildings,
                   a.street_nos | b.street_nos, a.reasons + b.reasons + [reason], tids[0] if tids else None)


class Desk:
    """Holds the tasks of one session and takes reports one at a time, as they arrive.

    add() returns what the agent did with the report, in order, for the activity log:
      {'step': 'not_request' | 'rule' | 'agent' | 'new' | 'possible', ...}
    """

    def __init__(self, backend: Backend | None = None):
        self.backend = backend
        self.items: dict[str, dict] = {}
        self.clusters: list[Cluster] = []
        self.decisions: list[dict] = []
        self.not_requests: list[str] = []
        self._next = 1

    def add(self, item: dict) -> list[dict]:
        self.items[item['id']] = item
        rec = item['record']
        if not rec['is_help_request'] and rec['status'] != 'resolved':
            self.not_requests.append(item['id'])
            return [{'step': 'not_request', 'reason': rec.get('summary_en', '')}]
        new = single(item)
        log: list[dict] = []
        # rules: same resolved neighbourhood and same building identity
        for i, c in enumerate(self.clusters):
            exact = new.places is not None and c.places is not None and len(new.places) == 1 \
                and new.places == c.places and '*' not in next(iter(new.places))
            if exact and (same_name(new.buildings, c.buildings) or new.street_nos & c.street_nos):
                self.clusters[i] = _join(c, new, 'same neighbourhood and building')
                return [{'step': 'rule', 'task': c.tid, 'reason': 'same neighbourhood and building'}]
        # agent: ask about every compatible group
        pbn = name_places(self.clusters + [new])
        cands = [i for i, c in enumerate(self.clusters) if is_candidate(new, c, pbn)]
        same = []
        for i in cands:
            v = judge(new, self.clusters[i], self.items, self.backend)
            self.decisions.append({'a': new.ids, 'b': list(self.clusters[i].ids), **v})
            log.append({'step': 'agent', 'task': self.clusters[i].tid, **v})
            if v['verdict'] == 'same':
                same.append(i)
        if same:
            joined_tids = [self.clusters[k].tid for k in same]
            target = same[0]
            merged = _join(self.clusters[target], new, 'agent: same building')
            for k in same[1:]:  # the new report links two groups: they are one building
                merged = _join(merged, self.clusters[k], 'agent: linked through a new report')
            self.clusters = [c for k, c in enumerate(self.clusters) if k not in same] + [merged]
            if len(same) > 1:
                log.append({'step': 'linked', 'task': merged.tid, 'tasks': joined_tids})
            return log
        new.tid = f'T{self._next:02d}'
        self._next += 1
        self.clusters.append(new)
        log.append({'step': 'new', 'task': new.tid})
        for m in name_only_matches(self.clusters):
            if m['a'] == new.ids or m['b'] == new.ids:
                self.decisions.append(m)
                log.append({'step': 'possible', 'reason': m['reason']})
                break
        return log

    @classmethod
    def from_tasks(cls, items: dict[str, dict], tasks: list[dict], decisions: list[dict],
                   not_requests: list[str], backend: Backend | None = None) -> 'Desk':
        """Rebuild a desk from recorded tasks without calling the model (seed for live sessions)."""
        d = cls(backend)
        d.items = dict(items)
        d.decisions = list(decisions)
        d.not_requests = list(not_requests)
        for t in tasks:
            members = [single(items[m]) for m in t['message_ids']]
            c = members[0]
            for o in members[1:]:
                c = _join(c, o, 'recorded')
            c.reasons = t.get('merge_reasons', [])
            c.tid = t['id']
            d.clusters.append(c)
        d._next = 1 + max((int(t['id'][1:]) for t in tasks), default=0)
        return d

    def task_for(self, mid: str) -> dict | None:
        for c in self.clusters:
            if mid in c.ids:
                return build_task(c.tid, c, self.items, self.decisions)
        return None

    def tasks(self) -> list[dict]:
        tasks = [build_task(c.tid, c, self.items, self.decisions) for c in self.clusters]
        order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'closed': 4}
        tasks.sort(key=lambda t: (order[t['urgency']], -(t['reports']), t['first_minute'] or 0))
        return tasks

    def task_of(self, mid: str) -> str | None:
        return next((c.tid for c in self.clusters if mid in c.ids), None)
