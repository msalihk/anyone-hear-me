"""Turkish speech to text with Muse Voice Transcribe (Meta Model API, realtime WebSocket).

The API only offers a streaming endpoint, so a recorded voice note is streamed as if it were live:
16-bit mono PCM at 24 kHz (the engine's native rate), in chunks of at most four seconds.

Protocol (as used by Pipecat's Meta STT service):
  1. open wss://api.meta.ai/v1/asr/realtime
  2. send a JSON handshake with the key, audio encoding, model, mode and language bias
  3. read the acknowledgement (it carries a sessionId, or an error message)
  4. send raw PCM bytes, then {"type": "endStream"}
  5. read events: transcript (cumulative partials), speechStart / speechEnd, speechComplete (final text)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field

import websockets

from .config import meta_api_key

ASR_URL = 'wss://api.meta.ai/v1/asr/realtime'
ASR_MODEL = 'muse-voice-transcribe-1.0'
SAMPLE_RATE = 24000
CHUNK_SECONDS = 0.5  # small chunks keep the stream close to real time; the API allows up to 4 s


@dataclass
class Transcript:
    text: str
    segments: list[str] = field(default_factory=list)  # one entry per speechComplete
    session_id: str | None = None


def to_pcm(path: str, rate: int = SAMPLE_RATE) -> bytes:
    """Any audio file (m4a, ogg, mp3, wav) -> raw 16-bit mono PCM at `rate`, via ffmpeg."""
    out = subprocess.run(
        ['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(rate), '-f', 's16le', '-'],
        check=True, capture_output=True)
    return out.stdout


def handshake(key: str, *, keywords: list[str] | None = None, mode: str = 'ENDPOINTING') -> dict:
    h = {
        'authorization': {'accessToken': f'Bearer {key}'},
        'audioEncoding': f'PCM_{SAMPLE_RATE // 1000}KHZ',
        'model': ASR_MODEL,
        'mode': mode,
        'partialMode': 'CUMULATIVE',
        'emitAudioProgress': False,
        'languageBias': ['Turkish'],
    }
    if keywords:
        h['keywords'] = keywords
    return h


async def transcribe_pcm(pcm: bytes, *, keywords: list[str] | None = None, realtime: bool = False,
                         timeout: float = 60.0) -> Transcript:
    """Stream PCM to the API and return the final text.

    keywords: terms to bias recognition toward, e.g. the neighbourhood names of the affected district.
    realtime: pace the upload like a live call (slower, closer to how a phone would stream).
    """
    key = meta_api_key()
    segments: list[str] = []
    last_partial = ''
    async with websockets.connect(ASR_URL, max_size=None, open_timeout=15) as ws:
        await ws.send(json.dumps(handshake(key, keywords=keywords)))
        ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if 'sessionId' not in ack:
            raise RuntimeError(f'Muse Voice Transcribe rejected the session: {ack.get("message", ack)}')
        session_id = ack['sessionId']

        async def sender():
            step = int(SAMPLE_RATE * 2 * CHUNK_SECONDS)  # 2 bytes per sample
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i:i + step])
                if realtime:
                    await asyncio.sleep(CHUNK_SECONDS)
            await ws.send(json.dumps({'type': 'endStream'}))

        send_task = asyncio.create_task(sender())
        try:
            async with asyncio.timeout(timeout):
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    msg = json.loads(raw)
                    kind = msg.get('type')
                    if kind == 'transcript':
                        last_partial = msg.get('transcript') or last_partial
                    elif kind == 'speechComplete':
                        text = (msg.get('transcript') or last_partial).strip()
                        if text:
                            segments.append(text)
                        last_partial = ''
                    elif kind == 'error':
                        raise RuntimeError(f'Muse Voice Transcribe error: {msg.get("message", msg)}')
        except websockets.ConnectionClosedOK:
            pass
        finally:
            await send_task
    if last_partial.strip():  # stream ended mid-turn: keep the last hypothesis
        segments.append(last_partial.strip())
    return Transcript(text=' '.join(segments), segments=segments, session_id=session_id)


def transcribe_file(path: str, **kw) -> Transcript:
    return asyncio.run(transcribe_pcm(to_pcm(path), **kw))
