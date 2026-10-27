import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ahm.addresses import fold, keywords_for, resolve, words_to_number  # noqa: E402


def test_fold_drops_suffix_and_spaces():
    assert fold('Şeyh Adil Mahallesi') == fold('ŞEYH ADİL mah.') == 'şeyhadil'


def test_exact_and_spacing_variants():
    r = resolve({'province': 'Kahramanmaraş', 'district': 'Dulkadiroğlu', 'neighbourhood': 'Şeyhadil'})
    assert r['ok'] and r['neighbourhood']['value'] == 'Şeyh Adil' and r['neighbourhood']['status'] == 'exact'


def test_misheard_district_is_corrected():
    r = resolve({'province': 'Kahramanmaraş', 'district': 'Durkadiroğlu', 'neighbourhood': 'Şeyh Adil'})
    assert r['district']['value'] == 'Dulkadiroğlu' and r['district']['status'] == 'corrected'
    assert 'Durkadiroğlu → Dulkadiroğlu' in r['note']


def test_short_province_name():
    r = resolve({'province': 'Maraş', 'district': 'Onikişubat', 'neighbourhood': 'Hayrullah'})
    assert r['province']['value'] == 'Kahramanmaraş' and r['ok']


def test_close_names_are_flagged_not_guessed():
    r = resolve({'province': 'Kahramanmaraş', 'district': 'Dulkadiroğlu', 'neighbourhood': 'Şeyh Halil'})
    assert r['neighbourhood']['status'] == 'ambiguous' and not r['ok']
    assert any(a.startswith('Şeyh Adil') for a in r['neighbourhood']['alternatives'])


def test_missing_district_found_from_unique_neighbourhood_or_flagged():
    r = resolve({'province': 'Hatay', 'district': None, 'neighbourhood': 'Haraparası'})
    assert r['district']['value'] == 'Antakya' and r['district']['status'] == 'inferred'
    r2 = resolve({'province': 'Hatay', 'district': None, 'neighbourhood': 'Cumhuriyet'})
    assert r2['neighbourhood']['status'] == 'ambiguous' and len(r2['neighbourhood']['alternatives']) > 1


def test_words_to_number():
    assert words_to_number('on iki') == 12
    assert words_to_number('üçüncü') == 3
    assert words_to_number('yüz yirmi üç') == 123
    assert words_to_number('sokak') is None


def test_keywords_start_with_places():
    kw = keywords_for('Kahramanmaraş', 'Dulkadiroğlu')
    assert kw[0] == 'Kahramanmaraş' and 'Dulkadiroğlu' in kw and 'Şeyh Adil' in kw
