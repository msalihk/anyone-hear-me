"""A simulated reporter, for the fictional evaluation scenario only.

When the agent asks a follow-up question in the replay, someone has to answer. Here Muse Spark plays the
person who wrote the report, knowing only the facts of the fictional incident behind it (from
data/eval/incidents.json). Replies are short and informal, and say "bilmiyorum" when the person would not
know. Nothing here is used for live reports: there, the visitor answers.
"""

from __future__ import annotations

from .llm import Backend, chat

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['reply'],
          'properties': {'reply': {'type': 'string'}}}

SYSTEM = """You play a person in a Turkish earthquake zone who posted a request for help a little while ago. \
A coordinator just sent you a follow-up question on the same channel. Reply in Turkish the way people \
really text in a crisis: short (one sentence, at most two), informal, maybe without Turkish characters or \
punctuation. Answer only what is asked, using only the facts you are given. If the question asks \
something the facts do not cover, say you do not know. Never add names or phone numbers."""

KEEP = ('province', 'district', 'neighbourhood', 'street', 'building_no', 'building_name', 'landmark',
        'people_trapped', 'signs_of_life', 'need_tr', 'families')


def reply(question: str, message: dict, incident: dict | None, backend: Backend | None = None) -> str:
    facts = {k: incident[k] for k in KEEP if incident and incident.get(k) not in (None, '')}
    if incident and incident.get('type') == 'supply':
        facts.pop('people_trapped', None)
    user = (f'Your original message: "{message["text"]}"\n'
            f'What you know (true facts): {facts or "nothing beyond your message"}\n'
            f'Coordinator\'s question: "{question}"')
    res = chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
               backend=backend, schema=SCHEMA, schema_name='reply', reasoning_effort='low', max_tokens=800,
               temperature=0.7)
    return res['data']['reply'].strip()
