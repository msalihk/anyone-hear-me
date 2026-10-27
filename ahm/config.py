"""Settings from the environment. Keys are never stored in the repository.

META_API_KEY        Meta Model API key (https://dev.meta.ai)
BASETEN_API_KEY     optional: Muse Spark Open on Baseten (offline-mode stand-in)
AHM_ENV_FILE        optional: a dotenv file to read first (default: ~/.config/anyone-hear-me/.env)
"""

from __future__ import annotations

import os
from pathlib import Path

META_BASE = os.environ.get('META_API_BASE', 'https://api.meta.ai/v1')
BASETEN_BASE = os.environ.get('BASETEN_API_BASE', 'https://inference.baseten.co/v1')
SPARK_MODEL = os.environ.get('AHM_SPARK_MODEL', 'muse-spark-1.3')
OPEN_MODEL = os.environ.get('AHM_OPEN_MODEL', 'meta-models/muse-spark-open-1026')


def _load_env_file() -> None:
    path = Path(os.environ.get('AHM_ENV_FILE', Path.home() / '.config' / 'anyone-hear-me' / '.env'))
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()


def meta_api_key() -> str:
    key = os.environ.get('META_API_KEY')
    if not key:
        raise RuntimeError('META_API_KEY is not set (see README: Setup).')
    return key


def baseten_api_key() -> str | None:
    return os.environ.get('BASETEN_API_KEY')
