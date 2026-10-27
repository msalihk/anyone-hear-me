"""Build the fictional evaluation set: incidents (ground truth) and the messages people would post about them.

Places are real neighbourhoods from data/gazetteer.json; buildings, people and events are invented.
Each incident gets 2-7 messages written by Muse Spark from a precise spec (what the sender knows, which
mistakes to make), so every message keeps its ground truth: which incident it is about, what it states.

usage: python scripts/make_dataset.py [--incidents 30] [--seed 7] [--out data/eval]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ahm.addresses import gazetteer  # noqa: E402
from ahm.llm import chat  # noqa: E402

AREAS = [('Kahramanmaraş', 'Dulkadiroğlu'), ('Kahramanmaraş', 'Onikişubat'), ('Hatay', 'Antakya'),
         ('Hatay', 'Defne'), ('Hatay', 'İskenderun'), ('Adıyaman', 'Merkez'), ('Malatya', 'Battalgazi'),
         ('Malatya', 'Yeşilyurt')]
BUILDINGS = ['Yıldız', 'Gül', 'Karaca', 'Ekinci', 'Doğan', 'Bahar', 'Lale', 'Umut', 'Çınar', 'Akasya', 'Barış',
             'Huzur', 'Şafak', 'Papatya', 'Ayyıldız', 'Kardelen', 'Menekşe', 'Gökkuşağı', 'Defne', 'Zeytin',
             'Nar', 'Sedir', 'Mavi', 'Güneş', 'Serçe', 'Martı', 'Erguvan', 'Ilgın', 'Pınar', 'Toprak']
KINDS = ['Apartmanı', 'Apartmanı', 'Sitesi', 'Konutları']
LANDMARKS = ['eczanenin karşısı', 'caminin arkası', 'ilkokulun yanı', 'fırının üstü', 'parkın karşısı',
             'sağlık ocağının yanı', 'market sokağı', 'dolmuş durağının önü']
NEEDS_SUPPLY = [('shelter', 'çadır'), ('heating', 'battaniye ve soba'), ('water', 'içme suyu'),
                ('baby_supplies', 'bebek maması ve bezi'), ('food', 'sıcak yemek')]

# message styles: what the sender knows and how they write it
STYLES = {
    'full': 'Gives province, district, neighbourhood, street, building number and name.',
    'no_district': 'Gives the neighbourhood and building but NOT the district or province.',
    'misspell': 'Misspells the neighbourhood name exactly as given in "spelled_as".',
    'words': 'Writes every number as Turkish words ("on iki", "üçüncü sokak").',
    'name_landmark': 'Gives only the building name, the neighbourhood and the landmark; no street or number.',
    'wrong_count': 'States the people count given in "stated_people" (it is wrong; the sender is unsure).',
    'relative_abroad': 'Written in English by a relative abroad, with the Turkish place names.',
    'caps': 'Very short, urgent, partly in capital letters, few details.',
    'update_partial': 'An update: says how many were rescued ("rescued") and that others are still inside.',
    'resolved': 'An update: everyone was rescued or the teams arrived; no more help needed here.',
}


def misspell(name: str, rng: random.Random) -> str:
    swaps = [('ş', 's'), ('ı', 'i'), ('ğ', 'g'), ('ü', 'u'), ('ö', 'o'), ('ç', 'c')]
    out = name
    for a, b in rng.sample(swaps, len(swaps)):
        if a in out:
            out = out.replace(a, b, 1)
            break
    if out == name and len(name) > 4:  # drop a letter
        i = rng.randrange(1, len(name) - 1)
        out = name[:i] + name[i + 1:]
    if ' ' in out and rng.random() < 0.5:
        out = out.replace(' ', '')
    return out


def make_incidents(n: int, rng: random.Random) -> list[dict]:
    gz = gazetteer()
    incidents, used = [], set()
    while len(incidents) < n:
        prov, dist = rng.choice(AREAS)
        nb = rng.choice(gz[prov][dist])
        twin = len(incidents) % 6 == 5 and incidents  # every 6th: a second building in the same neighbourhood
        if twin:
            prev = incidents[-1]
            prov, dist, nb = prev['province'], prev['district'], prev['neighbourhood']
        name = f'{rng.choice(BUILDINGS)} {rng.choice(KINDS)}'
        key = (prov, dist, nb, name)
        if key in used:
            continue
        used.add(key)
        street = rng.randint(1, 40)
        no = rng.randint(1, 120)
        if twin:
            street = incidents[-1]['street_no']  # same street, different building: must not be merged
        supply = rng.random() < 0.2
        inc = {
            'id': f'I{len(incidents) + 1:02d}', 'type': 'supply' if supply else 'rescue',
            'province': prov, 'district': dist, 'neighbourhood': nb, 'street_no': street,
            'street': f'{street}. Sokak', 'building_no': str(no), 'building_name': name,
            'landmark': rng.choice(LANDMARKS),
        }
        if supply:
            need, tr = rng.choice(NEEDS_SUPPLY)
            inc.update(need=need, need_tr=tr, families=rng.randint(4, 30), people_trapped=0,
                       signs_of_life='none_reported')
        else:
            inc.update(people_trapped=rng.randint(1, 7),
                       signs_of_life=rng.choice(['voices_heard', 'voices_heard', 'contact_by_phone', 'unknown']))
            r = rng.random()
            inc['outcome'] = 'resolved' if r < 0.25 else 'partial' if r < 0.5 else 'open'
            if inc['outcome'] == 'partial':
                inc['rescued'] = rng.randint(1, max(1, inc['people_trapped'] - 1))
        incidents.append(inc)
    return incidents


def plan_messages(inc: dict, rng: random.Random) -> list[dict]:
    if inc['type'] == 'supply':
        k = rng.randint(1, 3)
        return [{'style': rng.choice(['full', 'no_district', 'words']), 'about': 'supply'} for _ in range(k)]
    k = rng.randint(2, 6)
    pool = ['full', 'no_district', 'misspell', 'words', 'name_landmark', 'wrong_count', 'relative_abroad', 'caps']
    styles = ['full'] + rng.sample(pool[1:], k - 1)
    msgs = []
    for s in styles:
        m = {'style': s, 'about': 'rescue'}
        if s == 'misspell':
            m['spelled_as'] = misspell(inc['neighbourhood'], rng)
        if s == 'wrong_count':
            m['stated_people'] = inc['people_trapped'] + rng.choice([-1, 1, 2]) or 1
        msgs.append(m)
    if inc['outcome'] == 'partial':
        msgs.append({'style': 'update_partial', 'about': 'rescue', 'rescued': inc['rescued']})
    if inc['outcome'] == 'resolved':
        msgs.append({'style': 'resolved', 'about': 'rescue'})
    return msgs


WRITER = """You write FICTIONAL test messages for a disaster-response tool. They imitate what people posted \
on social media and messaging apps after an earthquake in Türkiye. Never use real people's names, phone \
numbers or accounts; refer to people as "annem", "kardeşim", "komşularımız" and so on.

For each spec, write ONE message in informal Turkish (unless the style says English): 1-3 sentences, \
natural, sometimes without punctuation. Include exactly the facts the spec allows and nothing else. \
If the style says to misspell or to use a wrong count, do it exactly as given."""

OUT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['messages'],
              'properties': {'messages': {'type': 'array', 'items': {'type': 'string'}}}}


def write_messages(inc: dict, plans: list[dict]) -> list[str]:
    facts = {k: inc[k] for k in ('province', 'district', 'neighbourhood', 'street', 'building_no',
                                 'building_name', 'landmark') if k in inc}
    if inc['type'] == 'supply':
        facts.update(need=inc['need_tr'], families=inc['families'])
    else:
        facts.update(people_trapped=inc['people_trapped'], signs_of_life=inc['signs_of_life'])
    specs = [{**p, 'style_rule': STYLES[p['style']]} for p in plans]
    user = (f'Incident facts (true): {json.dumps(facts, ensure_ascii=False)}\n'
            f'Kind: {"supply request (no one trapped)" if inc["type"] == "supply" else "people under rubble"}\n'
            f'Write {len(specs)} messages, one per spec, in order:\n{json.dumps(specs, ensure_ascii=False, indent=1)}')
    res = chat([{'role': 'system', 'content': WRITER}, {'role': 'user', 'content': user}],
               schema=OUT_SCHEMA, schema_name='messages', reasoning_effort='low', max_tokens=3000)
    texts = res['data']['messages']
    if len(texts) != len(specs):
        raise RuntimeError(f'{inc["id"]}: asked for {len(specs)} messages, got {len(texts)}')
    return texts


NOISE = [
    'Herkese geçmiş olsun, Allah yardımcımız olsun 🙏',
    'Deprem bölgesine yardım göndermek isteyenler resmi kurumların hesaplarını kullansın, dolandırıcılara dikkat.',
    'Kahramanmaraş merkezli 7.7 büyüklüğünde deprem, artçılar devam ediyor.',
    'Lütfen bu paylaşımı yaymayın, ekipler bölgeye ulaştı diye yazmışlar, teyit edilmemiş bilgi.',
    'Gönüllü olarak gitmek isteyenler için koordinasyon grubu kuruldu mu bilen var mı',
    'Ankara’dan iki tır yardım malzemesi yola çıktı.',
    'Bütün ekiplere kolay gelsin, dualarımız sizinle.',
    'Hatay için kan bağışı çağrısı yapılıyor, en yakın kan merkezine gidin.',
    'Antakya’da elektrikler kesik, telefonlar şarj olmuyor diye haber geldi.',
    'Yardım çağrılarını paylaşırken adresi tam yazın arkadaşlar, mahalle sokak numara.',
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--incidents', type=int, default=30)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--out', default='data/eval')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    incidents = make_incidents(a.incidents, rng)
    messages = []
    for inc in incidents:
        plans = plan_messages(inc, rng)
        texts = write_messages(inc, plans)
        for p, t in zip(plans, texts):
            messages.append({'text': t, 'truth': {'incident': inc['id'], **p}})
        print(inc['id'], inc['type'], inc['neighbourhood'], len(plans), 'messages', flush=True)
    for t in NOISE:
        messages.append({'text': t, 'truth': {'incident': None, 'style': 'noise', 'about': 'noise'}})

    rng.shuffle(messages)
    t0 = 0
    for i, m in enumerate(messages, 1):  # arrival order over ~6 hours, updates after their incident's first report
        t0 += rng.randint(30, 240)
        m['id'] = f'M{i:03d}'
        m['minute'] = t0 // 60
        m['channel'] = rng.choice(['x', 'whatsapp', 'sms', 'instagram', 'form'])
    seen: dict[str, int] = {}
    for m in messages:  # move updates after at least one report of the same incident
        inc = m['truth']['incident']
        if m['truth']['style'] in ('update_partial', 'resolved') and inc not in seen:
            m['minute'] += 360
        seen.setdefault(inc, m['minute'])
    messages.sort(key=lambda m: m['minute'])

    (out / 'incidents.json').write_text(json.dumps(incidents, ensure_ascii=False, indent=1))
    with open(out / 'messages.jsonl', 'w') as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + '\n')
    print(f'{len(incidents)} incidents, {len(messages)} messages -> {out}')


if __name__ == '__main__':
    main()
