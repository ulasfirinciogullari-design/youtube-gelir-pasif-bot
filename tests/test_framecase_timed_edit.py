from copy import deepcopy
import json
import math
from pathlib import Path
from unittest.mock import Mock

import fakeredis
import pytest

from app.services import framecase_timed_edit as edit, framecase_pipeline as pipeline
from app.services.production_spend import SpendBlocked


@pytest.fixture
def accepted():
    evidence = json.loads((Path(__file__).parent/'fixtures/framecase-accepted-narration.json').read_text())
    voice = {key: evidence[key] for key in ('spoken_texts', 'scene_durations', 'duration_after_fit')}
    voice['asset'] = {'sha256': 'a'*64}
    package = {'narration': ' '.join(voice['spoken_texts']),
               'scenes': [{'narration': text} for text in voice['spoken_texts']],
               'fiction_review': {**{key: True for key in pipeline.CHECKS}, 'findings': []}}
    return package, voice, evidence['transcript'], pipeline.short_edit_target(voice, package)


def test_actual_long_final_scene_rebalances_in_real_word_gaps_without_changing_speech(accepted):
    package, voice, transcript, effective = accepted
    original = deepcopy(accepted)
    assert not edit.fits(voice['scene_durations'], effective)
    result = edit.balanced_cuts(package, voice, transcript, effective)
    assert accepted == original
    assert result['scene_narrations'][:2] == voice['spoken_texts'][:2]
    assert result['scene_narrations'][2].endswith("That wasn't proof.")
    assert result['scene_narrations'][3].startswith('Inside the tower,')
    assert ' '.join(result['scene_narrations']) == package['narration']
    assert len(result['scene_durations']) == 4
    assert abs(sum(result['scene_durations'])-voice['duration_after_fit']) < 1e-8
    assert 554 <= result['cut_frames'][2] <= 564
    assert edit.fits(result['scene_durations'], effective)
    assert result['audio_edited'] is False
    assert all(8 <= len(pipeline._words(text)) <= 23 for text in result['scene_narrations'])
    # The cut is after proof's real end and before Inside's real start.
    assert 17.88 <= result['cut_frames'][2]/30 <= 18.82


@pytest.mark.parametrize('damage', ['unmatched_words', 'missing_timing', 'false_pass', 'nonfinite', 'too_long'])
def test_unverified_or_infeasible_timing_never_invents_cuts(accepted, damage):
    package, voice, transcript, effective = accepted
    if damage == 'unmatched_words': transcript['word_timestamps'][0]['text'] = 'Unspoken'
    elif damage == 'missing_timing': transcript['word_timestamps'] = []
    elif damage == 'false_pass': transcript['pass'] = False
    elif damage == 'nonfinite': transcript['word_timestamps'][0]['end'] = math.nan
    else:
        voice['duration_after_fit'] = 38
        effective = 38.6
    with pytest.raises((SpendBlocked, ValueError)):
        edit.balanced_cuts(package, voice, transcript, effective)


def test_reviewed_edit_is_cached_while_original_package_voice_and_allowance_stay_unchanged(accepted):
    package, voice, transcript, effective = accepted
    checkpoint = {'package': deepcopy(package), 'voice': deepcopy(voice)}
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set('original_native_receipt', 'paid-once')
    def draft(segments):
        return {**deepcopy(package), 'scenes': [{'narration': text} for text in segments]}
    reviewer = Mock(side_effect=draft)
    kwargs = dict(longform=False, checkpoint=checkpoint, task='same-original-root', client=client, draft=reviewer)
    first = edit.prepare(package, voice, {'transcript': transcript}, effective, **kwargs)
    assert reviewer.call_count == 1 and checkpoint['package'] == package and checkpoint['voice'] == voice
    before = deepcopy(checkpoint)
    kwargs['draft'] = Mock(side_effect=AssertionError('must reuse the reviewed edit'))
    assert edit.prepare(package, voice, {'transcript': transcript}, effective, **kwargs) == first
    assert checkpoint == before and client.get('original_native_receipt') == 'paid-once'
    assert list(client.scan_iter()) == ['original_native_receipt']
    checkpoint['timed_edit']['cut_frames'][0] += 1
    with pytest.raises(SpendBlocked, match='framecase_timed_edit_changed'):
        edit.prepare(package, voice, {'transcript': transcript}, effective, **kwargs)


@pytest.mark.parametrize('prior', ['image', 'video', 'clip', 'cast'])
def test_old_media_or_unknown_request_cannot_be_rebudgeted_around_new_cuts(accepted, prior):
    package, voice, transcript, effective = accepted
    client = fakeredis.FakeRedis(decode_responses=True); checkpoint = {}
    if prior in {'image', 'video'}:
        prefix = 'framecase_image' if prior == 'image' else 'video'
        client.set('youtube_studio:commissioning:v1:'+prefix+':original', 'unknown-or-paid')
    elif prior == 'clip': checkpoint['clips'] = {'3': {'asset': 'already-created'}}
    else: checkpoint['cast_reference'] = {'asset': 'already-bound'}
    draft = Mock()
    with pytest.raises(SpendBlocked, match='framecase_timed_edit_media_already_bound'):
        edit.prepare(package, voice, {'transcript': transcript}, effective, longform=False,
                     checkpoint=checkpoint, task='original', client=client, draft=draft)
    draft.assert_not_called()


def test_fitting_scene_plan_does_not_trigger_editorial_generation(accepted):
    package, voice, transcript, effective = accepted
    voice['scene_durations'] = [voice['duration_after_fit']/4]*4
    original = deepcopy((package, voice)); draft = Mock()
    result = edit.prepare(package, voice, {'transcript': transcript}, effective, longform=False,
        checkpoint={}, task='original', client=fakeredis.FakeRedis(), draft=draft)
    assert result == (package, voice['scene_durations']) and (package, voice) == original
    draft.assert_not_called()


def test_changed_narration_or_rejected_editorial_review_never_passes(accepted):
    package, voice, transcript, effective = accepted
    checkpoint = {}
    def reject(segments):
        candidate = {**deepcopy(package), 'scenes': [{'narration': text} for text in segments]}
        candidate['fiction_review'][pipeline.CHECKS[0]] = False
        return candidate
    with pytest.raises(SpendBlocked):
        edit.prepare(package, voice, {'transcript': transcript}, effective, longform=False,
            checkpoint=checkpoint, task='original', client=fakeredis.FakeRedis(), draft=reject)
    assert checkpoint == {}


def test_long_performance_keeps_thirty_shots_and_all_words():
    sentence = 'Mira followed the narrow corridor and found another clue beside the locked door.'
    segments = [sentence]*30
    durations = [9., *([6.]*29)]
    voice = {'spoken_texts': segments, 'scene_durations': durations, 'duration_after_fit': sum(durations)}
    narration = ' '.join(segments)
    package = {'narration': narration, 'scenes': [{'narration': text} for text in segments]}
    words = []; cursor = 0.
    for segment, seconds in zip(segments, durations):
        tokens = segment.split(); step = seconds/len(tokens)
        for index, token in enumerate(tokens):
            words.append({'text': token, 'start': cursor+index*step,
                          'end': cursor+(index+1)*step-.12})
        cursor += seconds
    transcript = {'available': True, 'pass': True, 'transcript': narration,
                  'provider': 'elevenlabs', 'word_timestamps': words}
    effective = pipeline.long_edit_target(voice, package)
    result = edit.balanced_cuts(package, voice, transcript, effective, longform=True)
    assert ' '.join(result['scene_narrations']) == narration
    assert len(result['scene_durations']) == 30
    assert edit.fits(result['scene_durations'], effective)
    assert all(10 <= len(text.split()) <= 20 for text in result['scene_narrations'])
