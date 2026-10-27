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

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ahm.addresses import keywords_for, resolve
from ahm.answers import answer_item, interpret
from ahm.asr import to_pcm, transcribe_pcm
from ahm.extract import extract
from ahm.followup import draft, needs_followup
from ahm.llm import Backend
from ahm.merge import Desk

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / 'static'
REPLAY = json.loads((ROOT / 'data/demo/replay-meta.json').read_text())
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
_items = {s['id']: {'id': s['id'], 'minute': s['minute'], 'channel': s['channel'], 'text': s['text'],
                    'record': s['record'], **{k: s[k] for k in ANSWER_KEYS if k in s}} for s in REPLAY['steps']}
_not_requests = [s['id'] for s in REPLAY['steps'] if any(a['step'] == 'not_request' for a in s['agent'])]
SEED = Desk.from_tasks(_items, REPLAY['tasks'], REPLAY['decisions'], _not_requests)
LAST_MINUTE = max(s['minute'] for s in REPLAY['steps'])

# --- sessions and limits --------------------------------------------------------------------------------

_sessions: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()
_day = {'date': time.strftime('%Y-%m-%d'), 'count': 0}


def session(request: Request, response: Response) -> dict:
    sid = request.cookies.get('ahm')
    with _lock:
        if not sid or sid not in _sessions:
            sid = secrets.token_urlsafe(16)
            _sessions[sid] = {'desk': None, 'calls': [], 'n': 0}
            response.set_cookie('ahm', sid, httponly=True, samesite='lax', max_age=86400)
            while len(_sessions) > 300:
                _sessions.popitem(last=False)
        _sessions.move_to_end(sid)
        s = _sessions[sid]
        if s['desk'] is None:
            s['desk'] = copy.deepcopy(SEED)
            s['desk'].backend = Backend.meta()
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
def replay() -> JSONResponse:
    return JSONResponse({'model': REPLAY['model'], 'steps': REPLAY['steps'], 'coords': COORDS['places'],
                         'coords_source': COORDS['source']})


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
        return draft(task, desk.items)
    except Exception:  # noqa: BLE001 - drafts are a help, not a requirement
        return []


def _add(s: dict, text: str, channel: str, extra: dict | None = None) -> dict:
    desk: Desk = s['desk']
    s['n'] += 1
    mid = f'L{s["n"]:02d}'
    t0 = time.time()
    rec = extract(text, channel=channel)
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
    spend(s)
    t0 = time.time()
    s['n'] += 1
    rid = f'L{s["n"]:02d}'
    patch = interpret(body.question, asked, text)
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


@app.post('/api/reset')
def reset(request: Request, response: Response) -> dict:
    s = session(request, response)
    s['desk'] = None
    s['n'] = 0
    session(request, response)
    return {'ok': True}


@app.get('/api/health')
def health() -> dict:
    return {'ok': True, 'steps': len(REPLAY['steps']), 'tasks': len(REPLAY['tasks'])}
