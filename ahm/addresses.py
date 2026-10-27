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
    s = re.sub(r'\b(mahallesi|mahalle|mah|mh|ilçesi|ilçe|merkez)\b\.?', ' ', s)
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


def _best(given: str | None, choices: list[str]) -> Match:
    if not given:
        return Match(given, None, 'missing')
    folded = {fold(c): c for c in choices}
    g = fold(given)
    if g in folded:
        return Match(given, folded[g], 'exact', 100.0)
    hits = process.extract(g, list(folded), scorer=fuzz.WRatio, limit=3)
    if not hits:
        return Match(given, None, 'not_found')
    (k1, s1, _), rest = hits[0], hits[1:]
    s2 = rest[0][1] if rest else 0
    alts = [folded[k] for k, _, _ in rest]
    if s1 >= ACCEPT and s1 - s2 >= 2:
        return Match(given, folded[k1], 'corrected' if s1 < 100 else 'exact', s1, alts)
    if s1 >= CORRECT and s1 - s2 >= MARGIN:
        return Match(given, folded[k1], 'corrected', s1, alts)
    if s1 >= CORRECT:
        return Match(given, None, 'ambiguous', s1, [folded[k1]] + alts)
    return Match(given, None, 'not_found', s1, [folded[k1]] + alts)


def resolve(loc: dict) -> dict:
    """Extracted location -> {province, district, neighbourhood: Match..., ok, note}.

    When the district is missing, the neighbourhood is searched across the whole province; a unique hit
    also fills the district.
    """
    gz = gazetteer()
    prov = _best(loc.get('province'), list(gz))
    out = {'province': prov}
    if not prov.value:
        out.update(district=Match(loc.get('district'), None, 'missing'),
                   neighbourhood=Match(loc.get('neighbourhood'), None, 'missing'))
        return _summary(out)
    districts = gz[prov.value]
    dist = _best(loc.get('district'), list(districts))
    out['district'] = dist
    if dist.value:
        out['neighbourhood'] = _best(loc.get('neighbourhood'), districts[dist.value])
    else:
        everywhere = {f'{m}|{d}': m for d, ms in districts.items() for m in ms}
        nb = _best(loc.get('neighbourhood'), list({m for m in everywhere.values()}))
        if nb.value:
            homes = [k.split('|')[1] for k, m in everywhere.items() if m == nb.value]
            if len(homes) == 1:
                out['district'] = Match(loc.get('district'), homes[0], 'inferred', nb.score)
            else:
                nb = Match(nb.given, nb.value, 'ambiguous', nb.score, [f'{nb.value} ({h})' for h in homes])
        out['neighbourhood'] = nb
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
