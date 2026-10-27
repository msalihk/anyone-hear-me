"""Make the synthetic photos used in the scenario and check each one with SAM 3.1.

For each photo: Muse Image draws it from a prompt (no real place or person), SAM 3.1 finds collapsed
buildings, rubble and people, and the overlay plus the counts go to data/demo/media/. The photos are
attached to fictional messages in data/eval/messages.jsonl (field "images").

usage: python scripts/make_media.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ahm.images import generate  # noqa: E402
from ahm.vision import analyse  # noqa: E402

MEDIA = ROOT / 'data' / 'demo' / 'media'
STYLE = ('Documentary photograph taken on a phone, daylight, winter, after an earthquake in a Turkish city. '
         'No text, no signs, no logos, no recognisable faces.')
PHOTOS = {
    'P1': 'A five-storey concrete apartment block that has pancake-collapsed into a pile of slabs and rubble, '
          'twisted rebar, a narrow street in front, neighbouring buildings still standing.',
    'P2': 'A collapsed apartment building with rubble spilling into the street; four rescue workers in helmets '
          'and orange vests stand on the rubble, seen from a distance.',
    'P3': 'A six-storey residential building tilted sharply to one side, its ground floor crushed, '
          'debris and broken balconies on the pavement.',
}


def main() -> None:
    out = {}
    for pid, prompt in PHOTOS.items():
        src = MEDIA / f'{pid}.jpg'
        if not src.exists():
            generate(f'{prompt} {STYLE}', str(src))
            print('generated', src.name, flush=True)
        res = analyse(str(src), str(MEDIA / f'{pid}-sam.jpg'))
        out[pid] = {'image': f'{pid}.jpg', 'overlay': f'{pid}-sam.jpg', 'found': res['found'], 'synthetic': True,
                    'made_with': 'Muse Image 1.0', 'checked_with': 'SAM 3.1'}
        print(pid, res['found'], flush=True)
    (MEDIA / 'media.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
