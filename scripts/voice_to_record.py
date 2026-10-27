"""Voice note -> transcript -> structured help request -> checked address.

usage: python scripts/voice_to_record.py NOTE.m4a [--province Kahramanmaraş] [--district Dulkadiroğlu] [--no-bias]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ahm.addresses import keywords_for, resolve  # noqa: E402
from ahm.asr import transcribe_file  # noqa: E402
from ahm.extract import extract  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('audio')
    ap.add_argument('--province', default='Kahramanmaraş', help='affected province: biases recognition to its place names')
    ap.add_argument('--district')
    ap.add_argument('--no-bias', action='store_true', help='transcribe without place-name keywords')
    a = ap.parse_args()

    kw = None if a.no_bias else keywords_for(a.province, a.district)
    t = transcribe_file(a.audio, keywords=kw)
    rec = extract(t.text, channel='voice note (transcribed)')
    rec['address_check'] = resolve(rec['location'])
    print(json.dumps({'transcript': t.text, 'record': rec}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
