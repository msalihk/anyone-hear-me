"""Suggest which rescue team goes where, with a reason a coordinator can check.

Two parts, each doing what it is good at:
  1. The agent reads the open tasks (place and how sure it is, signs of life, people, hours since the first
     report, what is still unsettled) and ranks the ones that need a team now, with the reason; tasks whose
     place is not settled are held back with what must be confirmed first (a team sent to the wrong
     "Çağlayan" loses hours).
  2. Code picks which free team goes to which of the top tasks so that total travel is lowest.
Nothing is dispatched without a coordinator.

Distances are straight-line kilometres times 1.3, a rough stand-in for roads; the demo has no routing.
"""

from __future__ import annotations

import math

from .llm import Backend, chat

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['priorities', 'held_back'],
    'properties': {
        'priorities': {'type': 'array', 'description': 'tasks that need a rescue team now, most urgent first', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['task', 'reason'],
            'properties': {'task': {'type': 'string'},
                           'reason': {'type': 'string', 'description': 'why it is this urgent: one short English sentence a coordinator can check'}}}},
        'held_back': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['task', 'reason'],
            'properties': {'task': {'type': 'string'}, 'reason': {'type': 'string'}}}},
    },
}

SYSTEM = """You support a search-and-rescue coordinator after an earthquake in Türkiye. Rank the open tasks \
that need a rescue team now, most urgent first. Team choice and travel are worked out separately; a \
coordinator approves every assignment.

Urgency, in order: people trapped with signs of life (voices, phone contact); then people trapped with no \
signs reported; the longer since the first report, the more urgent; more people still trapped, more \
urgent. A task whose place is not settled (ambiguous, unknown or conflicting) goes in held_back with what \
must be confirmed first, never in priorities. Supply-only tasks (water, shelter) do not need a rescue \
team: leave them out. Every reason names the facts it rests on (signs of life, people, hours). Use only \
the ids given."""


def km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a['lat'], a['lon'], b['lat'], b['lon']))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def where(task: dict, coords: dict) -> dict | None:
    p = task.get('place') or {}
    for key in ('|'.join(x for x in (p.get('province'), p.get('district'), p.get('neighbourhood')) if x),
                '|'.join(x for x in (p.get('province'), p.get('district')) if x)):
        if key and coords.get(key):
            return coords[key]
    return None


def plan(tasks: list[dict], teams: list[dict], busy: dict[str, str], coords: dict, now: int,
         backend: Backend | None = None) -> dict:
    """tasks: desk tasks; busy: team -> task it is on. Returns {'assignments', 'held_back'} (validated)."""
    covered = set(busy.values())
    open_tasks = [t for t in tasks if t['status'] != 'resolved' and t['id'] not in covered
                  and ('search_and_rescue' in t['needs'] or t['people_reported'] or t['signs_of_life'] in ('voices_heard', 'contact_by_phone'))]
    free = [tm for tm in teams if tm['id'] not in busy]
    if not open_tasks or not free:
        return {'assignments': [], 'held_back': [], 'free': [tm['id'] for tm in free], 'open': [t['id'] for t in open_tasks]}
    lines = []
    for t in open_tasks:
        w = where(t, coords)
        settled = t['place_status'] in ('confirmed', 'confirmed_by_reporter', 'settled_by_cross_reports')
        dists = ', '.join(f'{tm["id"]} {round(km(w, coords[tm["base"]]) * 1.3)} km' for tm in free) if w else 'unknown'
        b = t['building']
        lines.append(f'- {t["id"]}: place {" / ".join((t.get("place") or {}).values()) or "?"} '
                     f'(place {"settled" if settled else t["place_status"]}); building {b.get("building_name") or ""} '
                     f'{b.get("street") or ""} {b.get("building_no") or ""}; people trapped {t["people_reported"] or "?"}; '
                     f'rescued {t["people_rescued"] or 0}; signs of life {t["signs_of_life"]}; needs {t["needs"]}; '
                     f'first report {round((now - (t["first_minute"] or now)) / 60, 1)} h ago; '
                     f'unsettled: {[c["field"] for c in t["conflicts"] if "settled" not in c] or "none"}; road distance: {dists}')
    team_lines = [f'- {tm["id"]}: base {tm["base"].replace("|", " / ")}, {tm["people"]} people, {", ".join(tm["equipment"])}'
                  for tm in free]
    user = f'Hour {now // 60}:{now % 60:02d} after the earthquake.\n\nOpen tasks:\n' + '\n'.join(lines) + \
           '\n\nFree teams:\n' + '\n'.join(team_lines)
    res = chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}], backend=backend,
               schema=SCHEMA, schema_name='dispatch', reasoning_effort='low', max_tokens=6000)
    d = res['data']
    by_id = {t['id']: t for t in open_tasks}
    ranked, seen = [], set()
    for p in d['priorities']:
        if p['task'] in by_id and p['task'] not in seen and where(by_id[p['task']], coords):
            seen.add(p['task'])
            ranked.append(p)
    dist = {(tm['id'], p['task']): km(coords[tm['base']], where(by_id[p['task']], coords)) * 1.3
            for tm in free for p in ranked}
    pairs = assign([tm['id'] for tm in free], [p['task'] for p in ranked], dist)
    assignments = []
    for rank, p in enumerate(ranked, 1):
        team = pairs.get(p['task'])
        if not team:
            continue
        d_km = round(dist[(team, p['task'])])
        nearest = min(free, key=lambda tm: dist[(tm['id'], p['task'])])['id']
        if nearest == team:
            why = f'{team} is the nearest free team, about {d_km} km by road'
        else:
            other = next(t for t, m in pairs.items() if m == nearest)
            why = f'{team} is about {d_km} km by road; {nearest} is nearer but is going to {other}'
        assignments.append({'team': team, 'task': p['task'], 'km': d_km, 'rank': rank, 'reason': p['reason'],
                            'team_reason': why})
    held = [h for h in d['held_back'] if h['task'] in by_id and h['task'] not in seen]
    return {'assignments': assignments, 'held_back': held, 'free': [tm['id'] for tm in free],
            'open': sorted(by_id), 'waiting': [p['task'] for p in ranked if p['task'] not in pairs]}


RANK_KM = 20      # one place lower in the agent's ranking is worth 20 km less travel
FAR_KM = 150      # beyond this, a team is only sent when nothing nearer needs it


def assign(teams: list[str], tasks: list[str], dist: dict) -> dict[str, str]:
    """Task -> team: every free team gets one of the ranked tasks, trading urgency against travel.

    Cost of sending a team = road km + RANK_KM per place down the ranking (+1000 beyond FAR_KM), solved
    exactly with the Hungarian method.
    """
    if not teams or not tasks:
        return {}
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    cost = np.array([[dist[(tm, t)] + RANK_KM * r + (1000 if dist[(tm, t)] > FAR_KM else 0)
                      for r, t in enumerate(tasks)] for tm in teams])
    rows, cols = linear_sum_assignment(cost)
    return {tasks[c]: teams[r] for r, c in zip(rows, cols)}
