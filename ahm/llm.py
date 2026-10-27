"""Chat completions against Meta Model API (Muse Spark) or an OpenAI-compatible host serving Muse Spark Open.

Both speak the same request format, so the rest of the code only chooses a `Backend`:
  Backend.meta()      Muse Spark through Meta Model API (default)
  Backend.open()      Muse Spark Open on Baseten, or any self-hosted server (offline mode)
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import config


@dataclass
class Backend:
    name: str
    base_url: str
    model: str
    auth_header: str

    @classmethod
    def meta(cls, model: str | None = None) -> 'Backend':
        return cls('meta', config.META_BASE, model or config.SPARK_MODEL, f'Bearer {config.meta_api_key()}')

    @classmethod
    def open(cls, base_url: str | None = None, model: str | None = None) -> 'Backend':
        key = config.baseten_api_key()
        if not key and not base_url:
            raise RuntimeError('BASETEN_API_KEY is not set and no self-hosted base_url was given.')
        return cls('open', base_url or config.BASETEN_BASE, model or config.OPEN_MODEL,
                   f'Api-Key {key}' if key else '')


USAGE_LOG = Path(__file__).resolve().parent.parent / 'out' / 'usage.jsonl'
_lock = threading.Lock()


def _log_usage(backend: 'Backend', out: dict) -> None:
    """Append token counts per call, so spend can be estimated while the provider's usage page lags."""
    u = out.get('usage') or {}
    row = {'t': round(time.time()), 'backend': backend.name, 'model': out['model'],
           'in': u.get('prompt_tokens', 0), 'out': u.get('completion_tokens', 0), 's': out['seconds']}
    with _lock:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_LOG, 'a') as f:
            f.write(json.dumps(row) + '\n')


def _cache_get(key: str) -> dict | None:
    """Optional disk cache (set AHM_CACHE=path.sqlite) so rebuilding a recording does not pay twice."""
    path = os.environ.get('AHM_CACHE')
    if not path:
        return None
    with sqlite3.connect(path) as db:
        db.execute('create table if not exists c (k text primary key, v text)')
        row = db.execute('select v from c where k = ?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _cache_put(key: str, value: dict) -> None:
    path = os.environ.get('AHM_CACHE')
    if path:
        with sqlite3.connect(path) as db:
            db.execute('create table if not exists c (k text primary key, v text)')
            db.execute('insert or replace into c values (?, ?)', (key, json.dumps(value)))


def image_part(path: str) -> dict:
    mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
    data = base64.b64encode(open(path, 'rb').read()).decode()
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}}


def chat(messages: list[dict], *, backend: Backend | None = None, schema: dict | None = None,
         schema_name: str = 'result', max_tokens: int = 4000, reasoning_effort: str | None = 'low',
         temperature: float | None = None, retries: int = 2, timeout: float = 120) -> dict:
    """One completion. With `schema`, the reply is parsed JSON that matches it; otherwise {'text': ...}.

    Returns {'data' or 'text', 'usage', 'model', 'seconds'}.
    """
    backend = backend or Backend.meta()
    body: dict = {'model': backend.model, 'messages': messages, 'max_tokens': max_tokens}
    if reasoning_effort:
        body['reasoning_effort'] = reasoning_effort
    if temperature is not None:
        body['temperature'] = temperature
    if schema:
        body['response_format'] = {'type': 'json_schema',
                                   'json_schema': {'name': schema_name, 'strict': True, 'schema': schema}}
    key = hashlib.sha256(json.dumps([backend.base_url, body], sort_keys=True).encode()).hexdigest()
    hit = _cache_get(key)
    if hit:
        return hit
    headers = {'Content-Type': 'application/json'}
    if backend.auth_header:
        headers['Authorization'] = backend.auth_header
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = requests.post(f'{backend.base_url}/chat/completions', json=body, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f'{backend.name} HTTP {r.status_code}: {r.text[:300]}')
            d = r.json()
            content = d['choices'][0]['message'].get('content') or ''
            out = {'usage': d.get('usage'), 'model': d.get('model', backend.model), 'seconds': round(time.time() - t0, 2)}
            _log_usage(backend, out)
            if schema and not content.strip():
                # the reasoning used up the budget before the answer: try again with more room
                body['max_tokens'] = min(body['max_tokens'] * 2, 16000)
                raise json.JSONDecodeError('empty reply', '', 0)
            if schema:
                out['data'] = json.loads(content)
            else:
                out['text'] = content
            _cache_put(key, out)
            return out
        except (requests.RequestException, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f'{backend.name}: request failed: {last_err}')
