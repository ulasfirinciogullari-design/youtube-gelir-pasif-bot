from copy import deepcopy
from pathlib import Path
import json

import pytest

from app.services import audio_qc


@pytest.mark.parametrize('spoken,written', [
    ('Yirminci yüzyıl başında başladı.', '20. yüzyıl başında başladı.'),
    ('Yirminci yüzyıl başında başladı.', "20'nci yüzyıl başında başladı."),
    ('On dokuzuncu yüzyılda başladı.', '19. yüzyılda başladı.'),
    ('Yirmi birinci yüzyılın başında başladı.', '21. yüzyılın başında başladı.'),
    ('İkinci paketi açtı.', "2'nci paketi açtı."),
    ('Otuz üçüncü sırada.', "33'üncü sırada."),
    ('Kırkıncı sırada.', "40'ıncı sırada."),
    ('Yüzüncü yıl.', "100'üncü yıl."),
])
def test_explicit_ordinal_spelling_preserves_spoken_meaning(spoken, written):
    result = audio_qc.compare_transcript(spoken, written, comparison_language='tr')
    assert result['pass'] is True and result['score'] == 100


@pytest.mark.parametrize('spoken,written', [
    ('Yirminci yüzyıl.', '19. yüzyıl.'),
    ('Yirminci yüzyıl.', '20 yüzyıl.'),
    ('Yirminci yüzyıl.', '20, yüzyıl.'),
    ('Yirminci yüzyıl.', '20.5 yüzyıl.'),
    ('Yirminci yüzyıl.', '-20. yüzyıl.'),
    ('Yirminci yüzyıl.', '%20. yüzyıl.'),
    ('Yirmi yıl.', "20'nci yıl."),
    ('Yirmi lira.', "20'nci lira."),
    ('Yirmi, birinci paket.', "21'inci paket."),
    ('İkinci paket.', "2'uncu paket."),
    ('İkinci paket açılmadı.', "2'nci paket açıldı."),
])
def test_changed_numbers_counts_grammar_and_words_stay_rejected(spoken, written):
    assert audio_qc.compare_transcript(spoken, written, comparison_language='tr')['pass'] is False


def test_real_two_provider_transcripts_keep_original_words_and_timestamps():
    fixture = json.loads((Path(__file__).parent/'fixtures/turkish_ordinal_audio_observations_20260922.json').read_text())
    before = deepcopy(fixture)
    outcomes = []
    for case in fixture['cases']:
        passed = 0
        for row in case['observations']:
            payload, provider = row['payload'], row['provider']
            result = audio_qc.compare_transcript(
                case['expected'], payload['text'], words=payload['words'],
                language_code=payload.get('language', payload.get('language_code')),
                language_probability=payload.get('language_probability'), provider=provider,
                comparison_language='tr')
            if case['source_task_id'].startswith('774a') and provider == 'openai':
                # These original Whisper word spans contradict their own text.
                # Keep them rejected; only the independent Scribe evidence may pass.
                with pytest.raises(audio_qc.AudioQCError, match='inconsistent word timestamps'):
                    audio_qc._require_word_timing_evidence(result, provider)
                continue
            result = audio_qc._require_word_timing_evidence(result, provider)
            if result['pass']: passed += 1
            if case['source_task_id'].startswith('75e7') and provider == 'openai':
                # The actual extra syllable still fails; it is not an ordinal.
                assert result['pass'] is False
            if provider == 'elevenlabs': assert result['pass'] is True
        assert passed >= 3
        outcomes.append(passed)
    assert fixture == before and len(outcomes) == 2
