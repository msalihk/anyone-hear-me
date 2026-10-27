"""Draft the questions a coordinator would send back to the reporters of a task.

Only for tasks with something to settle: an address that could be several places, people counts that
disagree, or details rescuers need (street, number, building name). Drafts are short, plain Turkish,
one per reporter channel, and are never sent without a coordinator approving them.
"""

from __future__ import annotations

from .llm import Backend, chat

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['questions'],
    'properties': {'questions': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['to_message', 'text_tr', 'why_en'],
        'properties': {'to_message': {'type': 'string', 'description': 'id of the message being answered'},
                       'text_tr': {'type': 'string', 'description': 'the reply in Turkish, 1-2 short sentences'},
                       'why_en': {'type': 'string', 'description': 'what it settles, one short English line'}}}}},
}

SYSTEM = """You help a disaster-response coordinator in Türkiye. For one rescue task built from several \
reports, write the follow-up questions to send back to reporters, to settle what is uncertain.

Rules: at most 2 questions in total; ask the reporter who can answer (the one whose message is unclear or \
who gave a different count); plain, calm, very short Turkish; ask one thing at a time; offer the options \
when the address could be several places; never promise that help is on the way; no medical advice; never ask for phone numbers, names or other personal details (the reply channel already reaches them). When one report says everyone was rescued and others still ask for help, ask to confirm the rescue."""


def needs_followup(task: dict) -> bool:
    return task['status'] != 'resolved' and (bool(task['conflicts']) or task['place_status'] in ('ambiguous', 'unknown')
                                             or not task['building'].get('building_no'))


def draft(task: dict, items_by_id: dict, backend: Backend | None = None) -> list[dict]:
    msgs = '\n'.join(f'- [{m}] "{items_by_id[m]["text"]}"' for m in task['message_ids'])
    facts = {k: task[k] for k in ('place', 'place_status', 'building', 'people_reported', 'conflicts', 'open_questions')}
    res = chat([{'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': f'Reports:\n{msgs}\n\nWhat the task knows: {facts}'}],
               backend=backend, schema=SCHEMA, schema_name='followups', reasoning_effort='low', max_tokens=1500)
    return res['data']['questions']
