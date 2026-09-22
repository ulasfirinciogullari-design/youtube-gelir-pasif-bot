from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('written,number', [
    ('binbeşyüz dolar', '1500 dolar'), ('bin beşyüz dolar', '1500 dolar'),
    ('iki binbeşyüz dolar', '2500 dolar'), ('ikibinbeşyüz dolar', '2500 dolar'),
    ('yirmibeş lira', '25 lira'), ('yüzoniki adet', '112 adet'),
    ('bir milyon ikiyüzbin dolar', '1200000 dolar'),
    ('BİNBEŞYÜZ DOLAR', '1500 DOLAR'),
])
def test_exact_compact_cardinal_grammar_is_symmetric(written, number):
    for expected, heard in ((written, number), (number, written)):
        result = qc.compare_transcript(expected, heard)
        assert result['pass'] is True and result['score'] == 100
        assert result['transcript'] == heard


@pytest.mark.parametrize('written,number', [
    ('binbeşyüz dolar', '150 dolar'), ('binbeşyüz dolar', '1500 lira'),
    ('binbeşyüz dolar', '1500 dolar değil'), ('üçikibin dolar', '5000 dolar'),
    ('binyüzbin dolar', '101000 dolar'), ('binvirgülbeş dolar', '1000,5 dolar'),
    ('biriki dolar', '3 dolar'), ('biriki dolar', '12 dolar'),
    ('binbeşyüzdolar', '1500 dolar'), ('kod_binbeşyüz', 'kod_1500'),
    ('-binbeşyüz dolar', '1500 dolar'), ('+binbeşyüz dolar', '1500 dolar'),
    ('bin, beşyüz dolar', '1500 dolar'), ('bin / beşyüz dolar', '1500 dolar'),
    ('altın yüz dolar', '600 dolar'), ('binbirçeşit ürün', '1001 ürün'),
])
def test_values_grammar_negation_names_and_punctuation_are_not_forgiven(written, number):
    assert qc.compare_transcript(written, number)['pass'] is False


def test_original_word_spans_and_provider_times_survive_numeric_equivalence():
    words = [{'word': '1500', 'start': 0., 'end': .7}, {'word': 'dolar', 'start': .8, 'end': 1.3}]
    before = deepcopy(words)
    result = qc.compare_transcript('binbeşyüz dolar', '1500 dolar', words=words, provider='openai')
    assert result['pass'] is True and words == before
    assert qc._comparison_units('binbeşyüz dolar')[0] == (qc._numeric_key('1500'), ('binbeşyüz',))
    qc._require_word_timing_evidence(result, 'OpenAI')
    words[0]['end'] = 0.
    result = qc.compare_transcript('binbeşyüz dolar', '1500 dolar', words=words, provider='openai')
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(result, 'OpenAI')


@pytest.mark.parametrize('language', ['en', 'de', 'es'])
def test_compact_turkish_never_rewrites_other_languages(language):
    assert qc.compare_transcript('binbeşyüz dolar', '1500 dolar', comparison_language=language)['pass'] is False


def test_actual_six_observed_transcripts_keep_their_exact_words_and_timing():
    fixture = json.loads((Path(__file__).parent/'fixtures/compact_turkish_audio_observations_20260922.json').read_text())
    assert len(fixture['observations']) == 6
    for row in fixture['observations']:
        payload = row['payload']
        before = deepcopy(payload)
        result = qc.compare_transcript(fixture['expected'], payload['text'],
            words=payload['words'], provider=row['provider'], comparison_language='tr')
        assert result['pass'] is True and result['score'] == 100
        qc._require_word_timing_evidence(result, row['provider'])
        assert payload == before and result['transcript'] == payload['text']
