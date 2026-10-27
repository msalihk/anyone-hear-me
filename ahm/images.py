"""Synthetic photos for the fictional scenario, made with Muse Image (Meta Model API).

Every image made here is marked as AI-generated wherever it is shown. Nothing real is depicted.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import requests
from PIL import Image

from .config import META_BASE, meta_api_key


def generate(prompt: str, out_path: str, size: str = '1024x1024', timeout: float = 180) -> str:
    """Generate one image and save it as JPEG (SAM 3.1 and browsers take it directly)."""
    r = requests.post(f'{META_BASE}/images/generations', timeout=timeout,
                      headers={'Authorization': f'Bearer {meta_api_key()}', 'Content-Type': 'application/json'},
                      json={'model': 'muse-image-1.0', 'prompt': prompt, 'n': 1, 'size': size})
    if r.status_code != 200:
        raise RuntimeError(f'Muse Image HTTP {r.status_code}: {r.text[:300]}')
    raw = base64.b64decode(r.json()['data'][0]['b64_json'])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(raw)).convert('RGB').save(out_path, quality=88)
    return out_path
