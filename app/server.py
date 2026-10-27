"""Web app: replay the recorded scenario and let visitors add their own report, voice note or photo.

Every visitor gets a private copy of the coordinator desk, seeded with the recorded scenario. Live calls
go to Meta Model API with the server's key, so they are limited per visitor and per day.

run: uvicorn app.server:app --host 0.0.0.0 --port 7860
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

import asyncio

import websockets
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ahm.addresses import keywords_for, resolve
from ahm.answers import answer_item, interpret
from ahm.asr import ASR_URL, handshake, to_pcm, transcribe_pcm
from ahm.config import meta_api_key
from ahm.extract import extract
from ahm.followup import draft, needs_followup
from ahm.llm import Backend
from ahm.merge import Desk

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / 'static'
REPLAYS = {b: json.loads((ROOT / f'data/demo/replay-{b}.json').read_text())
           for b in ('meta', 'open') if (ROOT / f'data/demo/replay-{b}.json').exists()}
REPLAY = REPLAYS['meta']
COORDS = json.loads((ROOT / 'data/coords.json').read_text())
MEDIA = ROOT / 'data' / 'demo' / 'media'

PER_SESSION = int(os.environ.get('AHM_LIVE_PER_SESSION', '12'))   # live requests per visitor per hour
PER_DAY = int(os.environ.get('AHM_LIVE_PER_DAY', '400'))          # all visitors, per day
MAX_AUDIO_SECONDS = 45

app = FastAPI(title='Anyone Hear Me')
app.mount('/static', StaticFiles(directory=STATIC), name='static')
if MEDIA.exists():
    app.mount('/media', StaticFiles(directory=MEDIA), name='media')

# --- seed desk ------------------------------------------------------------------------------------------

ANSWER_KEYS = ('answer', 'reply_to', 'question', 'answered', 'settles')


def _seed(replay: dict) -> Desk:
    items = {s['id']: {'id': s['id'], 'minute': s['minute'], 'channel': s['channel'], 'text': s['text'],
                       'record': s['record'], **{k: s[k] for k in ANSWER_KEYS if k in s}} for s in replay['steps']}
    not_requests = [s['id'] for s in replay['steps'] if any(a['step'] == 'not_request' for a in s['agent'])]
    return Desk.from_tasks(items, replay['tasks'], replay['decisions'], not_requests)


SEEDS = {b: _seed(r) for b, r in REPLAYS.items()}
LAST_MINUTE = max(s['minute'] for r in REPLAYS.values() for s in r['steps'])


def backends() -> list[str]:
    """Models a visitor can run live: Muse Spark always, Muse Spark Open when its key is set."""
    out = ['meta']
    if 'open' in REPLAYS and os.environ.get('BASETEN_API_KEY'):
        out.append('open')
    return out


def make_backend(name: str) -> Backend:
    return Backend.open() if name == 'open' else Backend.meta()

# --- sessions and limits --------------------------------------------------------------------------------

_sessions: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()
_day = {'date': time.strftime('%Y-%m-%d'), 'count': 0}


def session(request: Request, response: Response) -> dict:
    sid = request.cookies.get('ahm')
    with _lock:
        if not sid or sid not in _sessions:
            sid = secrets.token_urlsafe(16)
            _sessions[sid] = {'desk': None, 'calls': [], 'n': 0, 'mode': 'meta'}
            response.set_cookie('ahm', sid, httponly=True, samesite='lax', max_age=86400)
            while len(_sessions) > 300:
                _sessions.popitem(last=False)
        _sessions.move_to_end(sid)
        s = _sessions[sid]
        if s['desk'] is None:
            s['desk'] = copy.deepcopy(SEEDS[s['mode']])
            try:
                s['desk'].backend = make_backend(s['mode'])
            except RuntimeError:  # no key for this model here: the replay still works, live calls are refused
                s['desk'].backend = None
        return s


def spend(s: dict) -> None:
    now = time.time()
    with _lock:
        today = time.strftime('%Y-%m-%d')
        if _day['date'] != today:
            _day.update(date=today, count=0)
        s['calls'] = [t for t in s['calls'] if now - t < 3600]
        if len(s['calls']) >= PER_SESSION:
            raise HTTPException(429, f'This demo allows {PER_SESSION} live reports per hour per visitor. '
                                     'The recorded scenario keeps working.')
        if _day['count'] >= PER_DAY:
            raise HTTPException(429, 'The live demo reached its daily limit. The recorded scenario keeps working.')
        s['calls'].append(now)
        _day['count'] += 1


# --- pages and recorded data ----------------------------------------------------------------------------

@app.get('/')
def index() -> FileResponse:
    return FileResponse(STATIC / 'index.html')


@app.get('/api/replay')
def replay(request: Request, response: Response, backend: str = 'meta') -> dict:
    session(request, response)  # sets the visitor's cookie early, so a live call can find its desk
    r = REPLAYS.get(backend, REPLAY)
    return {'backend': r['backend'], 'model': r['model'], 'steps': r['steps'],
            'coords': COORDS['places'], 'coords_source': COORDS['source'], 'backends': backends(),
            'recorded': sorted(REPLAYS)}


class ModeIn(BaseModel):
    backend: str


@app.post('/api/mode')
def mode(body: ModeIn, request: Request, response: Response) -> dict:
    """Switch this visitor's desk to another model; the desk starts again from that model's recording."""
    if body.backend not in REPLAYS:
        raise HTTPException(400, 'That model has no recorded scenario.')
    s = session(request, response)
    s.update(mode=body.backend, desk=None, n=0)
    session(request, response)
    return {'ok': True, 'backend': body.backend, 'live': body.backend in backends()}


# --- live -------------------------------------------------------------------------------------------------

class TextIn(BaseModel):
    text: str
    channel: str = 'form'


def _questions(desk: Desk, tid: str | None) -> list[dict]:
    """Draft follow-up questions when the task has something to settle and nobody is being asked yet."""
    if not tid or desk.waiting(tid) or desk.rounds(tid) >= 2:
        return []
    task = desk.task_for(desk.cluster(tid).ids[0])
    if not needs_followup(task):
        return []
    try:
        return draft(task, desk.items, desk.backend)
    except Exception:  # noqa: BLE001 - drafts are a help, not a requirement
        return []


def _add(s: dict, text: str, channel: str, extra: dict | None = None) -> dict:
    desk: Desk = s['desk']
    s['n'] += 1
    mid = f'L{s["n"]:02d}'
    t0 = time.time()
    if s['mode'] not in backends():
        raise HTTPException(503, 'Live reports on this model are not available right now. The recorded scenario keeps working.')
    rec = extract(text, channel=channel, backend=desk.backend)
    rec['address_check'] = resolve(rec['location'])
    item = {'id': mid, 'minute': LAST_MINUTE + s['n'], 'channel': channel, 'text': text, 'record': rec,
            **(extra or {})}
    log = desk.add(item)
    tid = desk.task_of(mid)
    questions = _questions(desk, tid)
    return {'id': mid, 'minute': item['minute'], 'channel': channel, 'text': text, 'record': rec,
            'seconds': round(time.time() - t0, 1), 'agent': log, 'task': desk.task_for(mid),
            'questions': questions, **(extra or {})}


class Approve(BaseModel):
    task: str
    questions: list[dict]


@app.post('/api/live/approve')
def live_approve(body: Approve, request: Request, response: Response) -> dict:
    """The coordinator approved the drafted questions: from now on the task waits for the answers."""
    s = session(request, response)
    desk: Desk = s['desk']
    c = desk.cluster(body.task)
    if not c:
        raise HTTPException(404, 'This task is no longer on the desk. Reload the page to start again.')
    qs = [q for q in body.questions[:2] if q.get('to_message') in c.ids and q.get('text_tr')]
    if not qs:
        raise HTTPException(400, 'There are no questions to approve for this task.')
    desk.ask(body.task, [{'to_message': q['to_message'], 'text_tr': str(q['text_tr'])[:400],
                          'why_en': str(q.get('why_en', ''))[:300]} for q in qs], LAST_MINUTE + s['n'])
    return {'ok': True, 'task': desk.task_for(c.ids[0])}


class ReplyIn(BaseModel):
    to_message: str
    question: str
    text: str


@app.post('/api/live/reply')
def live_reply(body: ReplyIn, request: Request, response: Response) -> dict:
    """The visitor answers a follow-up question as the reporter; the agent reads it and updates the task."""
    text = body.text.strip()
    if not 1 <= len(text) <= 600:
        raise HTTPException(400, 'Write the answer in 1 to 600 characters.')
    s = session(request, response)
    desk: Desk = s['desk']
    asked = desk.items.get(body.to_message)
    tid = desk.task_of(body.to_message)
    c = desk.cluster(tid) if tid else None
    if not asked or not c or not any(q['to'] == body.to_message and q['question'] == body.question
                                     and q['answer'] is None for q in c.asked):
        raise HTTPException(400, 'This question was not sent, or it was already answered.')
    if s['mode'] not in backends():
        raise HTTPException(503, 'Live answers on this model are not available right now.')
    spend(s)
    t0 = time.time()
    s['n'] += 1
    rid = f'L{s["n"]:02d}'
    patch = interpret(body.question, asked, text, desk.backend)
    item = answer_item(rid, LAST_MINUTE + s['n'], asked, body.question, text, patch)
    log = desk.answer(item)
    new_tid = desk.task_of(rid)
    return {'id': rid, 'minute': item['minute'], 'channel': item['channel'], 'text': text,
            'record': item['record'], 'answer': True, 'reply_to': body.to_message, 'question': body.question,
            'answered': item['answered'], 'settles': item['settles'], 'seconds': round(time.time() - t0, 1),
            'agent': log, 'task': desk.task_for(rid), 'questions': _questions(desk, new_tid)}


@app.post('/api/live/message')
def live_message(body: TextIn, request: Request, response: Response) -> dict:
    text = body.text.strip()
    if not 3 <= len(text) <= 1200:
        raise HTTPException(400, 'Write the help request in 3 to 1,200 characters.')
    s = session(request, response)
    spend(s)
    return _add(s, text, body.channel[:20])


@app.post('/api/live/voice')
async def live_voice(request: Request, response: Response, audio: UploadFile = File(...),
                     province: str = 'Kahramanmaraş') -> dict:
    s = session(request, response)
    data = await audio.read()
    if len(data) > 8_000_000:
        raise HTTPException(400, 'The voice note is too large; keep it under 45 seconds.')
    with tempfile.NamedTemporaryFile(suffix=Path(audio.filename or 'note.webm').suffix) as f:
        f.write(data)
        f.flush()
        try:
            pcm = to_pcm(f.name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, 'Could not read this audio file. Try m4a, mp3, ogg, wav or webm.') from e
    if len(pcm) / 48000 > MAX_AUDIO_SECONDS:
        raise HTTPException(400, f'Keep voice notes under {MAX_AUDIO_SECONDS} seconds.')
    spend(s)
    kw = keywords_for(province, limit=60) if province else None
    t = await transcribe_pcm(pcm, keywords=kw)
    if not t.text:
        raise HTTPException(422, 'No speech was recognised in this voice note.')
    out = _add(s, t.text, 'voice note')
    out['transcript'] = t.text
    return out


# --- live call: speech streams in, the agent fills the record and says what to ask next ------------------

MAX_CALL_SECONDS = 90


def next_questions(rec: dict) -> list[str]:
    """What the call-taker should ask next, from what the record is still missing."""
    ac, loc, out = rec['address_check'], rec['location'], []
    n = ac['neighbourhood']
    if n['status'] == 'ambiguous':
        out.append('Which ' + n['given'] + '? ' + ', '.join(a.split(' (')[1].rstrip(')') for a in n['alternatives'][:3]))
    elif not n['value']:
        out.append('Which neighbourhood and district?')
    if not loc.get('building_no') and not loc.get('building_name'):
        out.append('Building name, or street and number?')
    if rec['people_trapped'] is None:
        out.append('How many people are inside?')
    if rec['signs_of_life'] == 'unknown':
        out.append('Can they hear voices or reach anyone by phone?')
    return out


def _call_fields(text: str, desk: Desk) -> dict:
    rec = extract(text, channel='phone call', backend=desk.backend, effort='minimal')
    rec['address_check'] = resolve(rec['location'])
    return {'type': 'fields', 'record': rec, 'ask': next_questions(rec) if rec['is_help_request'] else []}


@app.websocket('/ws/call')
async def call(ws: WebSocket, province: str = 'Kahramanmaraş') -> None:
    """Browser microphone (16-bit PCM, 24 kHz) -> Muse Voice Transcribe realtime -> transcript and fields back.

    Messages to the browser: {type: partial|fields|done|error, ...}. The browser sends audio as binary
    frames and {"type": "end"} to hang up.
    """
    await ws.accept()
    s = _sessions.get(ws.cookies.get('ahm') or '')
    if not s:
        await ws.send_json({'type': 'error', 'detail': 'Reload the page and try the call again.'})
        return await ws.close()
    if s['mode'] not in backends():
        await ws.send_json({'type': 'error', 'detail': 'Live calls on this model are not available right now.'})
        return await ws.close()
    try:
        spend(s)
    except HTTPException as e:
        await ws.send_json({'type': 'error', 'detail': e.detail})
        return await ws.close()
    desk: Desk = s['desk']
    segments: list[str] = []
    partial = ''
    analysing: asyncio.Task | None = None
    analysed = ''

    async def analyse() -> None:
        nonlocal analysed
        while True:
            text = ' '.join(segments + [partial]).strip()
            if not text or text == analysed:
                return
            analysed = text
            try:
                await ws.send_json(await asyncio.to_thread(_call_fields, text, desk))
            except Exception:  # noqa: BLE001 - the transcript keeps flowing even if one reading fails
                return

    try:
        kw = keywords_for(province, limit=60) if province else None
        async with websockets.connect(ASR_URL, max_size=None, open_timeout=15) as up:
            await up.send(json.dumps(handshake(meta_api_key(), keywords=kw)))
            ack = json.loads(await asyncio.wait_for(up.recv(), 10))
            if 'sessionId' not in ack:
                raise RuntimeError('The speech service did not accept the call.')
            await ws.send_json({'type': 'ready'})

            async def from_browser() -> None:
                t0, sent = time.time(), 0
                try:
                    while True:
                        msg = await ws.receive()
                        if msg.get('bytes'):
                            sent += len(msg['bytes'])
                            if sent / 48000 > MAX_CALL_SECONDS or time.time() - t0 > MAX_CALL_SECONDS + 15:
                                break
                            await up.send(msg['bytes'])
                        elif msg.get('text') and 'end' in msg['text']:
                            break
                        elif msg.get('type') == 'websocket.disconnect':
                            break
                finally:
                    await up.send(json.dumps({'type': 'endStream'}))

            sender = asyncio.create_task(from_browser())
            async for raw in up:
                if isinstance(raw, bytes):
                    continue
                m = json.loads(raw)
                if m.get('type') == 'transcript':
                    partial = m.get('transcript') or partial
                    now = ' '.join(segments + [partial]).strip()
                    await ws.send_json({'type': 'partial', 'text': now})
                    # read it again every few words, without waiting for the caller to pause
                    if len(now) - len(analysed) >= 30 and (not analysing or analysing.done()):
                        analysing = asyncio.create_task(analyse())
                elif m.get('type') == 'speechComplete':
                    t = (m.get('transcript') or partial).strip()
                    partial = ''
                    if t:
                        segments.append(t)
                        await ws.send_json({'type': 'partial', 'text': ' '.join(segments).strip()})
                        if not analysing or analysing.done():
                            analysing = asyncio.create_task(analyse())
                elif m.get('type') == 'error':
                    raise RuntimeError('The speech service stopped the call.')
            await sender
        if partial.strip():
            segments.append(partial.strip())
        text = ' '.join(segments).strip()
        if analysing:
            await analysing
        if not text:
            await ws.send_json({'type': 'error', 'detail': 'No speech was recognised in this call.'})
            return await ws.close()
        step = await asyncio.to_thread(_add, s, text, 'phone call', {'transcript': text})
        await ws.send_json({'type': 'done', 'step': step})
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        try:
            await ws.send_json({'type': 'error', 'detail': str(e) if isinstance(e, RuntimeError) else 'The call failed. Try again.'})
        except Exception:  # noqa: BLE001
            pass
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


@app.post('/api/reset')
def reset(request: Request, response: Response) -> dict:
    s = session(request, response)
    s['desk'] = None
    s['n'] = 0
    session(request, response)
    return {'ok': True}


@app.get('/api/health')
def health() -> dict:
    return {'ok': True, 'steps': len(REPLAY['steps']), 'tasks': len(REPLAY['tasks']), 'recorded': sorted(REPLAYS),
            'live': backends()}
