"""Observed ASR currency spelling, not a tolerance for changed speech."""
from copy import deepcopy

import pytest

from app.services import audio_qc as qc


EXPECTED = (
    'Bir Amerikan senti neden kendi değerinden pahalıya mal oluyor? '
    'Üzerindeki bir sent, onu üretmenin maliyetini göstermiyor. '
    'Üretimin yanında yönetim ve dağıtım giderleri de hesaba giriyor. '
    'Darphanenin 7 Ocak 2026 güncellemeli açıklamasında maliyet 3, 69 sent. '
    'Dolaşım için üretim dursa da mevcut sentler hâlâ geçerli. '
    'Durdurulan, parayı kullanmak değil, değerinden pahalıya yenisini üretmek.'
)
# Exact text observed in the stored, hash-bound Whisper response for child
# 69ce7728-acce-4e5d-b30f-d5432cf7f3ac; original rejection is not overwritten.
HEARD = (
    'Bir Amerikan centi neden kendi değerinden pahalıya mal oluyor? '
    'Üzerindeki bir cent, onu üretmenin maliyetini göstermiyor. '
    'Üretimin yanında yönetim ve dağıtım giderleri de hesaba giriyor. '
    "Darphane'nin 7 Ocak 2026 güncellemeli açıklamasında maliyet 3,69 cent. "
    'Dolaşım için üretim dursa da mevcut centler hala geçerli. '
    'Durdurulan parayı kullanmak değil, değerinden pahalıya yenisini üretmek.'
)


def test_actual_rejected_child_transcript_now_matches_without_rewriting_evidence():
    result = qc.compare_transcript(EXPECTED, HEARD, provider='openai')
    assert result['pass'] is True and result['score'] == 100
    assert result['transcript'] == HEARD
    assert result['mismatch_details']['operations'] == []
    assert result['mismatch_details']['timestamp_sequence_match'] is None
    assert result['word_timestamps'] == []  # This fixture cannot grant timing or prosody.


@pytest.mark.parametrize('suffix', sorted(qc._TURKISH_CENT_SUFFIXES))
def test_closed_turkish_cases_and_plurals_keep_identical_suffixes(suffix):
    for context in ('Amerikan {} geçerli.', 'Dolaşımdaki {} geçerli.', 'İki {} kaldı.'):
        left, right = context.format('sent' + suffix), context.format('cent' + suffix)
        assert qc.compare_transcript(left, right)['pass'] is True
        assert qc.compare_transcript(right, left)['pass'] is True


@pytest.mark.parametrize('expected,heard', [
    ('Amerikan senti geçerli.', 'Amerikan centler geçerli.'),
    ('Amerikan sentin değeri.', 'Amerikan centi değeri.'),
    ('Dolaşımdaki sentlerden söz edildi.', 'Dolaşımdaki centleri söz edildi.'),
    ('İki senti aldı.', 'Üç centi aldı.'),
    ('Maliyet 3,69 sentti.', 'Maliyet 3,96 centti.'),
    ('Dolaşımdaki sentler geçerli.', 'Dolaşımdaki centler geçersiz.'),
    ('Dolaşımdaki sentler geçerli.', 'Dolaşımdaki centler geçerli değil.'),
    ('Bir Amerikan senti var.', 'Bir Amerikan dolar var.'),
])
def test_values_units_negation_and_grammatical_meaning_still_fail(expected, heard):
    assert qc.compare_transcript(expected, heard)['pass'] is False


@pytest.mark.parametrize('expected,heard', [
    ('senti', 'centi'), ('sentler', 'centler'), ('senten', 'centen'),
    ('Bir sentetik madde.', 'Bir centetik madde.'),
    ('Para sentilitre değildir.', 'Para centilitre değildir.'),
    ('Amerikan sentaur adı.', 'Amerikan centaur adı.'),
    ('Para bir bilgi. Sentler sözcüğü.', 'Para bir bilgi. Centler sözcüğü.'),
    ('Para bir bilgi; senti yazdı.', 'Para bir bilgi; centi yazdı.'),
    ('Para bir bilgi\nsenti yazdı.', 'Para bir bilgi\ncenti yazdı.'),
    ('Para a b c d e f g h senti.', 'Para a b c d e f g h centi.'),
    ('Para Kod_senti.', 'Para Kod_centi.'),
    ('Para senti2.', 'Para centi2.'),
])
def test_noncurrency_unknown_roots_or_distant_context_never_get_aliases(expected, heard):
    assert qc.compare_transcript(expected, heard)['pass'] is False


@pytest.mark.parametrize('language', ['en', 'de', 'es', 'ar'])
def test_inflection_aliases_are_turkish_only(language):
    assert qc.compare_transcript('Amerikan senti', 'Amerikan centi', comparison_language=language)['pass'] is False


def test_actual_timestamp_failures_are_not_repaired_by_spelling_equivalence():
    words = [{'word': 'Amerikan', 'start': 0, 'end': .2}, {'word': 'centi', 'start': .2, 'end': .2}]
    before = deepcopy(words)
    result = qc.compare_transcript('Amerikan senti', 'Amerikan centi', words=words, provider='openai')
    assert result['pass'] is True and words == before
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(result, 'OpenAI')
    result = qc.compare_transcript('Amerikan senti', 'Amerikan centi',
        words=[{'word': 'Amerikan', 'start': 0, 'end': .2}, {'word': 'sentleri', 'start': .2, 'end': .5}], provider='openai')
    assert result['pass'] is True
    assert result['mismatch_details']['timestamp_sequence_match'] is False


@pytest.mark.parametrize('context', ['kasada', 'kasalarda', 'kasaya', 'kasalara', 'kasadan', 'kasalardan'])
def test_currency_spelling_with_local_cash_register_cue(context):
    expected = f'Eldeki sentler {context} dönüyor.'
    heard = f'Eldeki centler {context} dönüyor.'
    words = [{'word': word, 'start': i * .4, 'end': (i + 1) * .4}
             for i, word in enumerate(heard.split())]
    before = deepcopy(words)
    result = qc.compare_transcript(expected, heard, words=words, provider='abacus_router')
    assert result['pass'] is True and result['score'] == 100
    assert result['transcript'] == heard and words == before
    qc._require_word_timing_evidence(result, 'Abacus router')


@pytest.mark.parametrize('expected,heard', [
    ('Sentler kasalarda dönüyor.', 'Centleri kasalarda dönüyor.'),
    ('İki sent kasada kaldı.', 'Üç cent kasada kaldı.'),
    ('Kasalarda üç sent var.', 'Kasalarda üç dolar var.'),
    ('Kasa kapandı. Sentler döndü.', 'Kasa kapandı. Centler döndü.'),
    ('Sentler kasabanın sözcüğü.', 'Centler kasabanın sözcüğü.'),
    ('Kasada sentetik bir madde.', 'Kasada centetik bir madde.'),
])
def test_register_context_never_changes_amount_suffix_or_word_root(expected, heard):
    assert qc.compare_transcript(expected, heard)['pass'] is False
