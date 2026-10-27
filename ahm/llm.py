"""Chat completions against Meta Model API (Muse Spark) or an OpenAI-compatible host serving Muse Spark Open.

Both speak the same request format, so the rest of the code only chooses a `Backend`:
  Backend.meta()      Muse Spark through Meta Model API (default)
  Backend.open()      Muse Spark Open on Baseten, or any self-hosted server (offline mode)
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from dataclasses import dataclass

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
            if schema:
                out['data'] = json.loads(content)
            else:
                out['text'] = content
            return out
        except (requests.RequestException, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f'{backend.name}: request failed: {last_err}')
