"""Read a reporter's answer to a follow-up question and turn it into an update for the task.

A reply is short and only makes sense next to the question ("Onikişubat'taki", "9 kişi"), so the model reads
the original report, the question and the answer together and returns only what the answer states or
confirms. The update then becomes a report of its own on the task, marked as an answer: what it confirms
wins over earlier reports from other people, and the conflict it settles is shown as settled, with its
source.
"""

from __future__ import annotations

import copy

from .addresses import resolve
from .llm import Backend, chat

NULL_STR = {'type': ['string', 'null']}

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['answers_question', 'province', 'district', 'neighbourhood', 'street', 'building_no',
                 'building_name', 'people_trapped', 'people_rescued', 'status', 'signs_of_life', 'evidence',
                 'summary_en'],
    'properties': {
        'answers_question': {'type': 'boolean', 'description': 'false if the reply does not answer, or says it does not know'},
        'province': NULL_STR, 'district': NULL_STR, 'neighbourhood': NULL_STR,
        'street': NULL_STR, 'building_no': NULL_STR, 'building_name': NULL_STR,
        'people_trapped': {'type': ['integer', 'null'], 'description': 'people under the rubble, as the reply states or confirms'},
        'people_rescued': {'type': ['integer', 'null']},
        'status': {'type': 'string', 'enum': ['open', 'resolved', 'unclear']},
        'signs_of_life': {'type': 'string', 'enum': ['voices_heard', 'contact_by_phone', 'none_reported', 'unknown']},
        'evidence': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                'required': ['field', 'quote'],
                                                'properties': {'field': {'type': 'string'},
                                                               'quote': {'type': 'string', 'description': 'exact words from the reply'}}}},
        'summary_en': {'type': 'string', 'description': 'what the reply settles, one short English sentence'},
    },
}

SYSTEM = """A coordinator in a Turkish earthquake zone asked a reporter a follow-up question. Read the reporter's \
original message, the question and the reply, and return what the reply states or confirms.

Rules: fill a field only when the reply states it, or clearly confirms an option offered in the question \
("evet", "Onikişubat'taki" after "Onikişubat mı Göksun mu?"). Everything else is null. Do not copy details \
from the original message unless the reply confirms them. Numbers as digits. If the reply says it does not \
know, answers_question is false. status: resolved only if the reply says everyone is out; open if people \
are still inside; otherwise unclear. Quotes must be the reply's exact words."""

LOCATION = ('province', 'district', 'neighbourhood', 'street', 'building_no', 'building_name')


def interpret(question: str, asked: dict, reply: str, backend: Backend | None = None) -> dict:
    user = (f'Original message [{asked["id"]}]: "{asked["text"]}"\n'
            f'Question sent to this reporter: "{question}"\n'
            f'Reply: "{reply}"')
    res = chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
               backend=backend, schema=SCHEMA, schema_name='answer', reasoning_effort='low', max_tokens=1500)
    return res['data']


def settled_fields(patch: dict) -> list[str]:
    out = []
    if any(patch[k] for k in ('province', 'district', 'neighbourhood')):
        out.append('place')
    if patch['street'] or patch['building_no'] or patch['building_name']:
        out.append('building')
    if patch['people_trapped'] is not None:
        out.append('people_trapped')
    if patch['people_rescued'] is not None or patch['status'] == 'resolved':
        out.append('status')
    return out


def answer_item(rid: str, minute: int, asked: dict, question: str, reply: str, patch: dict,
                channel: str | None = None) -> dict:
    """The reply as an item for the desk: the asked report's record, updated with what the reply confirms."""
    rec = copy.deepcopy(asked['record'])
    loc = rec['location']
    for k in LOCATION:
        if patch[k]:
            loc[k] = patch[k]
    loc['raw'] = f'{reply} | {loc.get("raw") or ""}'.strip(' |')
    rec['address_check'] = resolve(loc)
    rec['people_trapped'] = patch['people_trapped']
    rec['people_rescued'] = patch['people_rescued']
    if patch['status'] != 'unclear':
        rec['status'] = patch['status']
    if patch['signs_of_life'] != 'unknown':
        rec['signs_of_life'] = patch['signs_of_life']
    rec['is_help_request'] = True
    rec['needs'] = []
    rec['missing_info'] = []
    rec['evidence'] = patch['evidence']
    rec['summary_en'] = patch['summary_en']
    return {'id': rid, 'minute': minute, 'channel': channel or asked.get('channel'), 'text': reply,
            'record': rec, 'answer': True, 'reply_to': asked['id'], 'question': question,
            'answered': patch['answers_question'], 'settles': settled_fields(patch) if patch['answers_question'] else []}
