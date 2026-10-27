"""Help request text (transcribed voice note, message, post) -> one structured record.

The model only reads what the sender said. It does not judge medical conditions or give advice; it
records what was reported, quotes the words each field came from, and says when something is unknown.
"""

from __future__ import annotations

from .llm import Backend, chat, image_part

NEEDS = ['search_and_rescue', 'heavy_machinery', 'medical_team', 'water', 'food', 'shelter', 'heating',
         'baby_supplies', 'transport', 'other']

SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['is_help_request', 'status', 'location', 'people_trapped', 'signs_of_life', 'needs',
                 'urgency', 'urgency_reason', 'evidence', 'missing_info', 'summary_en'],
    'properties': {
        'is_help_request': {'type': 'boolean', 'description': 'false for news, prayers, rumours or unrelated text'},
        'status': {'type': 'string', 'enum': ['open', 'resolved', 'unclear'],
                   'description': 'resolved when the sender says the people were rescued or no longer need help'},
        'location': {
            'type': 'object', 'additionalProperties': False,
            'required': ['province', 'district', 'neighbourhood', 'street', 'building_no', 'building_name',
                         'landmark', 'raw'],
            'properties': {
                'province': {'type': ['string', 'null']},
                'district': {'type': ['string', 'null']},
                'neighbourhood': {'type': ['string', 'null'], 'description': 'without the word Mahallesi'},
                'street': {'type': ['string', 'null'], 'description': 'digits for numbered streets, e.g. "3. Sokak"'},
                'building_no': {'type': ['string', 'null'], 'description': 'digits, e.g. "12" or "12/A"'},
                'building_name': {'type': ['string', 'null'], 'description': 'apartment or site name'},
                'landmark': {'type': ['string', 'null'], 'description': 'nearby school, mosque, pharmacy...'},
                'raw': {'type': 'string', 'description': 'the location words exactly as written'},
            }},
        'people_trapped': {'type': ['integer', 'null'], 'description': 'number of people reported under rubble'},
        'signs_of_life': {'type': 'string', 'enum': ['voices_heard', 'contact_by_phone', 'none_reported', 'unknown']},
        'needs': {'type': 'array', 'items': {'type': 'string', 'enum': NEEDS}},
        'urgency': {'type': 'string', 'enum': ['critical', 'high', 'medium', 'low']},
        'urgency_reason': {'type': 'string', 'description': 'one short English sentence'},
        'evidence': {
            'type': 'array',
            'description': 'for each filled field, the exact words of the message it came from',
            'items': {'type': 'object', 'additionalProperties': False, 'required': ['field', 'quote'],
                      'properties': {'field': {'type': 'string'}, 'quote': {'type': 'string'}}}},
        'missing_info': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'what a coordinator would need to ask (English)'},
        'summary_en': {'type': 'string', 'description': 'one-line English summary for the coordinator'},
    },
}

SYSTEM = """You read help requests sent after an earthquake in Türkiye. Most are Turkish; some are \
transcribed voice notes, so spelling and punctuation can be wrong and numbers may be written as words.

Extract only what the sender reported. Do not guess an address that was not given; use null.
Write numbers as digits ("on iki" -> "12", "üçüncü sokak" -> "3. Sokak").
Urgency: critical = people trapped with signs of life; high = people trapped, no signs reported, or \
injured people waiting; medium = urgent supplies (water, shelter, heating) for a group; low = general needs.
You do not give medical advice and you do not assess injuries beyond what the sender said.
Every filled field needs an evidence quote copied from the message."""


def extract(text: str, *, image_paths: list[str] | None = None, backend: Backend | None = None,
            channel: str = 'message') -> dict:
    content: list[dict] | str = f'Channel: {channel}\n\nMessage:\n{text}'
    if image_paths:
        content = [{'type': 'text', 'text': content}] + [image_part(p) for p in image_paths]
    res = chat([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': content}],
               backend=backend, schema=SCHEMA, schema_name='help_request')
    rec = res['data']
    rec['_meta'] = {'model': res['model'], 'seconds': res['seconds'], 'usage': res['usage']}
    return rec
