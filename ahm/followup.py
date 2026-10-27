"""Draft the questions a coordinator would send back to the reporters of a task.

Only for tasks with something to settle: an address that could be several places, people counts that
disagree, or a building rescuers cannot find (no number and no name). Drafts are short, plain Turkish,
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


def open_issues(task: dict) -> list[str]:
    """What still needs a reporter's answer: unsettled conflicts, an unclear place, an unnamed building."""
    if task['status'] == 'resolved':
        return []
    out = [f"conflict:{c['field']}" for c in task['conflicts'] if 'settled' not in c]
    if task['place_status'] in ('ambiguous', 'unknown') and 'conflict:place' not in out:
        out.append('place')
    b = task['building']
    if not b.get('building_no') and not b.get('building_name'):
        out.append('building')
    return out


def needs_followup(task: dict) -> bool:
    return bool(open_issues(task))


def draft(task: dict, items_by_id: dict, backend: Backend | None = None) -> list[dict]:
    msgs = '\n'.join(f'- [{m}] "{items_by_id[m]["text"]}"' for m in task['message_ids']
                     if not items_by_id[m].get('answer'))
    facts = {k: task[k] for k in ('place', 'place_status', 'building', 'people_reported', 'open_questions')}
    facts['unsettled'] = open_issues(task)
    facts['conflicts'] = [c for c in task['conflicts'] if 'settled' not in c]
    asked = [f'{q["to"]}: {q["question"]} -> {"answered" if q["answer"] else "no answer yet"}'
             for q in task.get('followups') or []]
    user = f'Reports:\n{msgs}\n\nWhat the task knows: {facts}'
    if asked:
        user += '\n\nAlready asked (do not repeat; ask someone else or something else):\n' + '\n'.join(asked)
    res = chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
               backend=backend, schema=SCHEMA, schema_name='followups', reasoning_effort='low', max_tokens=1500)
    valid = set(task['message_ids'])
    return [q for q in res['data']['questions'] if q['to_message'] in valid][:2]
