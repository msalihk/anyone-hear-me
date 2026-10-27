"""Coordinates for the demo places, from OpenStreetMap Nominatim (ODbL), fetched once and stored.

Follows the Nominatim usage policy: one request per second, an identifying User-Agent, results cached in
data/coords.json so the app never calls Nominatim at run time.

usage: python scripts/geocode.py [--redo]   (--redo retries entries that are missing or not the place itself)   (reads data/eval/incidents.json, adds districts and neighbourhoods)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'coords.json'
UA = 'AnyoneHearMe/0.1 (Meta hackathon demo; https://github.com/msalihk/anyone-hear-me)'


def lookup(q: str) -> dict | None:
    r = requests.get('https://nominatim.openstreetmap.org/search', timeout=30, headers={'User-Agent': UA},
                     params={'q': q, 'format': 'json', 'limit': 1, 'countrycodes': 'tr'})
    time.sleep(1.1)
    if r.status_code != 200 or not r.json():
        return None
    h = r.json()[0]
    return {'lat': float(h['lat']), 'lon': float(h['lon']), 'osm': h.get('display_name', '')[:120]}


def is_place(hit: dict, name: str) -> bool:
    first = hit['osm'].split(',')[0].strip()
    return first in (name, f'{name} Mahallesi', f'{name} Köyü')


def main() -> None:
    redo = '--redo' in sys.argv
    coords = json.loads(OUT.read_text()) if OUT.exists() else {'source': 'OpenStreetMap Nominatim, ODbL', 'places': {}}
    places = coords['places']
    incidents = json.loads((ROOT / 'data/eval/incidents.json').read_text())
    wanted = set()
    for i in incidents:
        wanted.add((i['province'], i['district'], None))
        wanted.add((i['province'], i['district'], i['neighbourhood']))
    extra = [('Kahramanmaraş', 'Dulkadiroğlu', 'Şeyh Adil'), ('Kahramanmaraş', 'Onikişubat', 'Hayrullah'),
             ('Hatay', 'Antakya', 'Haraparası'), ('Kahramanmaraş', 'Dulkadiroğlu', 'Şeyh Şamil')]
    wanted |= set(extra)
    for p, d, n in sorted(wanted, key=lambda x: (x[0], x[1], x[2] or '')):
        key = '|'.join(x for x in (p, d, n) if x)
        old = places.get(key)
        if old and not (redo and n and not is_place(old, n.removesuffix(' Köyü'))):
            continue
        if not n:
            hit = lookup(p if d == 'Merkez' else f'{d}, {p}')   # "Merkez" is the provincial capital
        else:
            base = n.removesuffix(' Köyü')
            hit = lookup(f'{base} Mahallesi, {d}, {p}') or lookup(f'{base}, {p}')
            # keep only hits that are the neighbourhood itself, not a street, school or shop named after it
            if hit and not is_place(hit, base):
                hit = None
        places[key] = hit
        print(key, '->', (hit['lat'], hit['lon']) if hit else None, flush=True)
    OUT.write_text(json.dumps(coords, ensure_ascii=False, indent=1))
    print(f'{sum(1 for v in places.values() if v)}/{len(places)} found -> {OUT}')


if __name__ == '__main__':
    sys.exit(main())
