"""Process the evaluation messages and merge them into tasks for one backend, then score.

usage: python scripts/run_eval.py [--backend meta|open]
writes out/records-<backend>.jsonl and out/tasks-<backend>.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ahm.llm import Backend  # noqa: E402
from ahm.merge import merge  # noqa: E402
from ahm.pipeline import process_all  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', choices=['meta', 'open'], default='meta')
    a = ap.parse_args()
    be = Backend.meta() if a.backend == 'meta' else Backend.open()
    msgs = [json.loads(line) for line in (ROOT / 'data/eval/messages.jsonl').read_text().splitlines()]
    items = process_all(msgs, be, ROOT / f'out/records-{a.backend}.jsonl', workers=6)
    result = merge(items, backend=be)
    (ROOT / f'out/tasks-{a.backend}.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
    subprocess.run([sys.executable, str(ROOT / 'scripts/evaluate.py'), '--backends', a.backend], check=False)


if __name__ == '__main__':
    main()
