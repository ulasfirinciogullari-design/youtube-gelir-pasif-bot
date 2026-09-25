import pytest

from app.services.word_timed_narration import edit_plan
from app.services.voice import VoiceQualityError
from app.services.audio_qc import AudioQCError


def evidence(text):
    return {'text': text, 'language': 'Turkish', 'words': [
        {'word': word, 'start': index * .5, 'end': index * .5 + .4}
        for index, word in enumerate(text.split())]}


def test_actual_words_determine_scene_changes_and_whole_audio_is_retained():
    transcript = evidence('Bir fikir büyür. Sonra dünyaya yayılır.')
    plan = edit_plan(['Bir fikir büyür.', 'Sonra dünyaya yayılır.'], transcript, 4, language='tr')
    assert plan['scene_durations'] == pytest.approx([1.45, 2.55])
    assert plan['cuts'] == [] and plan['audio_edited'] is False
    assert sum(plan['scene_durations']) == 4


def test_spoken_number_and_asr_digits_keep_the_complete_word_timing():
    transcript = evidence('25 kişi geldi. Sonra 300 kişi ayrıldı.')
    plan = edit_plan(['Yirmi beş kişi geldi.', 'Sonra üç yüz kişi ayrıldı.'], transcript, 5, language='tr')
    assert plan['scene_durations'] == pytest.approx([1.45, 3.55])


def test_english_years_use_the_same_strict_comparison_as_final_audio_qa():
    transcript = evidence('In 1953 they opened. The shop grew.')
    plan = edit_plan(['In nineteen fifty-three they opened.', 'The shop grew.'], transcript, 5, language='en')
    assert plan['scene_durations'] == pytest.approx([1.95, 3.05])


@pytest.mark.parametrize('damage', ['missing_word', 'wrong_word', 'wrong_timestamps',
    'non_monotonic', 'overlap', 'beyond_media', 'nan'])
def test_bad_or_unbound_recognition_cannot_supply_scene_timing(damage):
    transcript = evidence('Bir fikir büyür. Sonra dünyaya yayılır.')
    if damage == 'missing_word': transcript['text'] = 'Bir büyür. Sonra dünyaya yayılır.'
    if damage == 'wrong_word': transcript['text'] = 'Bir fikir küçülür. Sonra dünyaya yayılır.'
    if damage == 'wrong_timestamps': transcript['words'][0]['word'] = 'Sahte'
    if damage == 'non_monotonic': transcript['words'][2]['start'] = 0
    if damage == 'overlap': transcript['words'][2]['end'] = 1.7
    if damage == 'beyond_media': transcript['words'][-1]['end'] = 9
    if damage == 'nan': transcript['words'][0]['start'] = float('nan')
    with pytest.raises((VoiceQualityError, AudioQCError)):
        edit_plan(['Bir fikir büyür.', 'Sonra dünyaya yayılır.'], transcript, 4, language='tr')


def test_cannot_cut_a_single_asr_word_between_two_scenes():
    transcript = {'text': 'İş büyür.', 'words': [{'word': 'İş büyür.', 'start': 0, 'end': 1}]}
    with pytest.raises(VoiceQualityError):
        edit_plan(['İş', 'büyür.'], transcript, 2, language='tr')
