"""Check that every evidence quote in a recording appears word for word in its message.

A model can paraphrase; an evidence quote must not. Matching ignores case and runs of whitespace.

usage: python scripts/check_quotes.py [data/demo/replay-meta.json]
"""

import json
import sys
import unicodedata
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'data/demo/replay-meta.json')
norm = lambda s: ' '.join(unicodedata.normalize('NFC', s or '').split()).lower()  # noqa: E731
total = found = 0
missing = []
for s in json.loads(path.read_text())['steps']:
    if s.get('kind') == 'dispatch':
        continue
    text = norm(s['text'])
    for e in (s.get('record') or {}).get('evidence', []):
        q = norm(e.get('quote'))
        if not q:
            continue
        total += 1
        if q in text:
            found += 1
        else:
            missing.append((s['id'], e.get('field'), e.get('quote')))
print(f'{found} of {total} evidence quotes appear word for word in their message')
for m in missing:
    print('  not found:', *m)
