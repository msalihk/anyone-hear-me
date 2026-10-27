"""Check an extracted address against the official neighbourhood list and correct what was misheard.

Voice notes and hurried messages get place names wrong ("Durkadiroğlu", "Şeyh Halil"). The gazetteer
(11 provinces of the 6 Feb 2023 earthquakes, data/gazetteer.json) is the ground truth: a name is accepted
when it matches, corrected when one candidate is clearly closest, and flagged when it is ambiguous.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

DATA = Path(__file__).resolve().parent.parent / 'data' / 'gazetteer.json'

ACCEPT = 92   # >= : same name (spelling noise)
CORRECT = 80  # >= and clearly ahead of the runner-up: corrected
MARGIN = 6    # points the best candidate must lead the second by to be a correction


def fold(s: str) -> str:
    """Turkish-aware lowercase, drop suffix words and spaces: 'Şeyh Adil Mahallesi' -> 'şeyhadil'."""
    s = s.replace('I', 'ı').replace('İ', 'i').lower()
    s = re.sub(r'\b(mahallesi|mahalle|mah|mh|ilçesi)\b\.?', ' ', s)
    s = re.sub(r'(\S)\s+köyü?\s*$', r'\1', s.strip())  # "Uzunköy Köyü" -> "Uzunköy"
    return re.sub(r'[^\wçğıöşü]', '', s)


@lru_cache(maxsize=1)
def gazetteer() -> dict[str, dict[str, list[str]]]:
    return json.loads(DATA.read_text(encoding='utf-8'))['provinces']


@dataclass
class Match:
    given: str | None
    value: str | None
    status: str          # exact | corrected | ambiguous | not_found | missing
    score: float = 0.0
    alternatives: list[str] = field(default_factory=list)


def _best(given: str | None, choices: list[str], strict: bool = False) -> Match:
    """strict: the search space is the whole region (no province or district to anchor it), where a
    near-miss is as likely to be another town's neighbourhood as a typo, so fuzzy corrections need a
    much higher score and a clear lead; otherwise the options are shown."""
    if not given:
        return Match(given, None, 'missing')
    folded = {fold(c): c for c in choices}
    g = fold(given)
    if g in folded:
        return Match(given, folded[g], 'exact', 100.0)
    hits = process.extract(g, list(folded), scorer=fuzz.ratio, limit=3)
    if not hits:
        return Match(given, None, 'not_found')
    (k1, s1, _), rest = hits[0], hits[1:]
    s2 = rest[0][1] if rest else 0
    alts = [folded[k] for k, _, _ in rest]
    accept, correct, margin = (96, 92, 10) if strict else (ACCEPT, CORRECT, MARGIN)
    if s1 >= accept and s1 - s2 >= 2:
        return Match(given, folded[k1], 'corrected' if s1 < 100 else 'exact', s1, alts)
    if s1 >= correct and s1 - s2 >= margin:
        return Match(given, folded[k1], 'corrected', s1, alts)
    if strict and s1 >= CORRECT:
        return Match(given, None, 'ambiguous', s1, [folded[k1]] + alts)
    if s1 >= CORRECT:
        return Match(given, None, 'ambiguous', s1, [folded[k1]] + alts)
    return Match(given, None, 'not_found', s1, [folded[k1]] + alts)


def resolve(loc: dict) -> dict:
    """Extracted location -> {province, district, neighbourhood: Match..., ok, note}.

    Messages rarely name the province, and often not the district. The search narrows as far as the
    message allows and widens where it is silent:
      province given  -> its districts; else every district in the region
      district found  -> its neighbourhoods; else every neighbourhood in the provinces still possible
    A neighbourhood that exists in exactly one place fills in the district and province ("inferred").
    A name that exists in several places is flagged with every option, never guessed.
    A district-shaped word that is really a neighbourhood ("Karşıyaka'da") is tried as one.
    """
    gz = gazetteer()
    given_p, given_d, given_n = loc.get('province'), loc.get('district'), loc.get('neighbourhood')
    prov = _best(given_p, list(gz))
    provs = [prov.value] if prov.value else list(gz)

    dist_choices = {d for p in provs for d in gz[p]}
    dist = _best(given_d, sorted(dist_choices), strict=not prov.value)
    if dist.value:
        homes = [p for p in provs if dist.value in gz[p]]
        if not prov.value and len(homes) == 1:
            prov = Match(given_p, homes[0], 'inferred', dist.score)
            provs = homes
        elif len(homes) > 1:
            provs = homes  # e.g. "Merkez": still open, the neighbourhood decides
    query = given_n or (given_d if not dist.value else None)

    cands = [(p, d, m) for p in provs for d, ms in gz[p].items()
             if not dist.value or d == dist.value for m in ms]
    anchored = bool(prov.value and prov.status != 'inferred') or (dist.value is not None and len(provs) == 1)
    nb = _best(query, sorted({m for _, _, m in cands}), strict=not anchored)
    if nb.status != 'exact' and given_d and given_n:
        # "Merkez Beydağı" split into district "Merkez" + neighbourhood "Beydağı": try them as one name
        joined = f'{given_d} {given_n}'
        cands2 = [(p, d, m) for p in (provs if prov.value else list(gz)) for d, ms in gz[p].items() for m in ms]
        nb2 = _best(joined, sorted({m for _, _, m in cands2}), strict=not prov.value)
        if nb2.status == 'exact' or (not nb.value and nb.status != 'ambiguous' and (nb2.value or nb2.status == 'ambiguous')):
            nb, query, cands = nb2, joined, cands2
            dist = Match(given_d, None, 'not_found', dist.score)
    raw = fold(loc.get('raw') or '')
    if raw and (nb.value or nb.status == 'ambiguous'):
        # the message may hold a longer official name than the model put in the field
        # ("Merkez Beydağı" vs "Beydağı"): prefer the longest official name written in the raw text
        longer = sorted({m for _, _, m in cands if len(fold(m)) > len(fold(nb.value or '')) >= 3
                         and fold(m) in raw and fold(nb.value or query or '') in fold(m)}, key=len)
        if longer:
            nb = Match(query, longer[-1], 'exact', 100.0)
    if nb.value or nb.status == 'ambiguous':
        names = {fold(nb.value)} if nb.value else {fold(a) for a in nb.alternatives}
        places = [(p, d, m) for p, d, m in cands if fold(m) in names]
        if nb.value and len({(p, d) for p, d, _ in places}) == 1:
            p, d, _ = places[0]
            if not prov.value:
                prov = Match(given_p, p, 'inferred', nb.score)
            if not dist.value or dist.value != d:
                dist = Match(given_d, d, 'inferred', nb.score)
        elif places:
            nb = Match(query, None, 'ambiguous', nb.score,
                       [f'{m} ({d}, {p})' for p, d, m in places][:8])
    if given_n is None and query and nb.value:
        nb.given = None  # the neighbourhood came from the district slot
    out = {'province': prov, 'district': dist, 'neighbourhood': nb}
    if not dist.value and dist.status != 'missing' and nb.value:
        out['district'] = Match(given_d, out['district'].value, 'not_found', dist.score)
    return _summary(out)


def _summary(out: dict) -> dict:
    parts = [out[k] for k in ('province', 'district', 'neighbourhood')]
    out['ok'] = all(p.value for p in parts) and not any(p.status == 'ambiguous' for p in parts)
    fixes = [f'{p.given} → {p.value}' for p in parts if p.status == 'corrected']
    out['note'] = '; '.join(fixes)
    return {k: (asdict(v) if isinstance(v, Match) else v) for k, v in out.items()}


def keywords_for(province: str, district: str | None = None, limit: int = 400) -> list[str]:
    """Place names to bias speech recognition toward (Muse Voice Transcribe `keywords`)."""
    gz = gazetteer()[province]
    names = [province] + list(gz)
    pool = gz[district] if district else [m for ms in gz.values() for m in ms]
    return (names + sorted(set(pool)))[:limit]


# --- numbers written as words (voice notes) --------------------------------------------------------

ONES = {'bir': 1, 'iki': 2, 'üç': 3, 'dört': 4, 'beş': 5, 'altı': 6, 'yedi': 7, 'sekiz': 8, 'dokuz': 9}
TENS = {'on': 10, 'yirmi': 20, 'otuz': 30, 'kırk': 40, 'elli': 50, 'altmış': 60, 'yetmiş': 70,
        'seksen': 80, 'doksan': 90}
ORD = {'birinci': 1, 'ikinci': 2, 'üçüncü': 3, 'dördüncü': 4, 'beşinci': 5, 'altıncı': 6, 'yedinci': 7,
       'sekizinci': 8, 'dokuzuncu': 9, 'onuncu': 10}


def words_to_number(s: str) -> int | None:
    """'on iki' -> 12, 'yüz yirmi üç' -> 123, 'üçüncü' -> 3. None when it is not a number."""
    toks = s.replace('I', 'ı').replace('İ', 'i').lower().split()
    if len(toks) == 1 and toks[0] in ORD:
        return ORD[toks[0]]
    total, seen = 0, False
    for t in toks:
        if t == 'yüz':
            total = (total or 1) * 100
        elif t in TENS:
            total += TENS[t]
        elif t in ONES:
            total += ONES[t]
        else:
            return None
        seen = True
    return total if seen else None
