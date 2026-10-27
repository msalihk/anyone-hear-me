"""Run every incoming message through extraction and the address check, with a cache on disk.

Records are keyed by message id and backend, so re-running the pipeline (or switching between Muse Spark
and Muse Spark Open) only calls the model for messages it has not seen.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .addresses import resolve
from .extract import extract
from .llm import Backend


def process_message(msg: dict, backend: Backend) -> dict:
    rec = extract(msg['text'], backend=backend, channel=msg.get('channel', 'message'),
                  image_paths=msg.get('images'))
    rec['address_check'] = resolve(rec['location'])
    return {'id': msg['id'], 'minute': msg.get('minute'), 'channel': msg.get('channel'), 'text': msg['text'],
            'record': rec}


def process_all(messages: list[dict], backend: Backend, cache_path: Path, workers: int = 4,
                log=print) -> list[dict]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r['id']] = r
    todo = [m for m in messages if m['id'] not in done]
    if todo:
        log(f'{backend.name}: {len(todo)} messages to process ({len(done)} cached)')
        with ThreadPoolExecutor(max_workers=workers) as pool, open(cache_path, 'a') as f:
            futs = {pool.submit(process_message, m, backend): m for m in todo}
            for n, fut in enumerate(as_completed(futs), 1):
                m = futs[fut]
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    log(f'  {m["id"]} failed: {e}')
                    continue
                done[r['id']] = r
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
                f.flush()
                if n % 20 == 0:
                    log(f'  {n}/{len(todo)}')
    return [done[m['id']] for m in messages if m['id'] in done]
