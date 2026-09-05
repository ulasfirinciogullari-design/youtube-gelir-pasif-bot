"""A silence-only repair changes no speech, approval or paid-provider state."""

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_MISSING = object()
_NAMES = ('audio_pause_repair', 'audio_qc', 'voice')
_prior_modules = {name: sys.modules.get('app.services.' + name, _MISSING) for name in _NAMES}
_parent = sys.modules.get('app.services')
_prior_attributes = {name: getattr(_parent, name, _MISSING) for name in _NAMES}
for _name in _NAMES:
    sys.modules.pop('app.services.' + _name, None)
    if _parent is not None and hasattr(_parent, _name):
        delattr(_parent, _name)
try:
    from app.services import audio_pause_repair as repair
    _native_media_duration = repair._media_duration
finally:
    # Existing lightweight voice/STT tests bind private config stubs during
    # collection. Keep our real pure helpers without preloading their modules.
    _parent = sys.modules['app.services']
    for _name in _NAMES:
        if _prior_modules[_name] is _MISSING:
            sys.modules.pop('app.services.' + _name, None)
        else:
            sys.modules['app.services.' + _name] = _prior_modules[_name]
        if _prior_attributes[_name] is _MISSING:
            if hasattr(_parent, _name):
                delattr(_parent, _name)
        else:
            setattr(_parent, _name, _prior_attributes[_name])


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.mp3'
    path.write_bytes(b'ID3' + b'original' * 512)
    spoken = [
        'Bir barkod bunu değiştirdi.', 'Olay 26 Haziran tarihinde oldu.',
        'Kasada ürünün bilgisi okundu.', 'Bu işlem fiyatı gösterdi.',
        'Böylece alışveriş farklılaştı.', 'O tek tarama, küresel tedarik ağını değiştirdi.',
    ]
    raw_words = [
        ('Bir', .2, .5), ('barkod', .6, 1.1), ('bunu', 1.2, 1.5), ('değiştirdi', 1.6, 2.8),
        ('Olay', 3.4, 3.8), ('26', 5.05, 5.5), ('Haziran', 5.5, 6.2), ('tarihinde', 6.2, 6.7), ('oldu', 6.7, 7.2),
        ('Kasada', 11, 11.5), ('ürünün', 11.6, 12), ('bilgisi', 12.1, 12.6), ('okundu', 12.7, 14),
        ('Bu', 15.5, 15.8), ('işlem', 15.9, 16.4), ('fiyatı', 16.5, 17), ('gösterdi', 17.1, 18),
        ('Böylece', 20, 20.5), ('alışveriş', 20.6, 21.5), ('farklılaştı', 21.6, 23),
        ('O', 23.8, 24), ('tek', 24, 24.2), ('tarama', 24.2, 24.58),
        ('küresel', 26.08, 26.6), ('tedarik', 26.6, 27), ('ağını', 27.1, 27.5), ('değiştirdi', 27.6, 29.2),
    ]
    words = [{'text': text, 'start': start, 'end': end} for text, start, end in raw_words]
    voice = {
        'path': str(path), 'spoken_texts': spoken, 'voice_language_code': 'tr',
        'scene_durations': [3.214649, 7.402632, 4.670546, 4.246821, 4.003716, 5.981636],
        'duration_before_fit': 32.352, 'duration_after_fit': 29.52, 'tempo_rate': 1.096678,
        'removed_silence_seconds': 1.219,
        'audio_qc': {'pass': True}, 'audio_duration_qc': {'pass': True}, 'audio_prosody_qc': {'pass': False},
    }
    evidence = {'available': True, 'pass': True, 'provider': 'openai', 'audio_sha256': _sha(path),
                'word_timestamps': words, 'mismatch_details': {'timestamp_sequence_match': True}}
    review = {'available': True, 'pass': False, 'provider': 'gemini',
              'summary': 'Two long internal pauses disrupt otherwise intelligible narration.',
              'scores': dict(pronunciation=88, naturalness=52, pacing=50, sentence_flow=48, emphasis=60, roboticness=58),
              'issues': [
                  dict(code='unnatural_internal_pause', phrase='Olay 26 Haziran', start_seconds=3.4, end_seconds=6.2, detail='Long pause after Olay.'),
                  dict(code='unnatural_internal_pause', phrase='O tek tarama, küresel tedarik', start_seconds=23.8, end_seconds=27, detail='Long pause after tarama.'),
              ]}
    silences = [(3.849667, 4.922333), (24.57325, 25.937042)]
    durations = {str(path): 29.52}
    monkeypatch.setattr(repair, '_media_duration', lambda value: durations[str(value)])
    monkeypatch.setattr(repair, '_silences', Mock(return_value=silences))

    def cut(source, output, duration, cuts):
        assert source != path and source.read_bytes() == path.read_bytes()
        output.write_bytes(b'ID3' + b'cut' * 1024)
        durations[str(output)] = duration - sum(item['removed_seconds'] for item in cuts)

    def fit(voice, target):
        result = dict(voice)
        before = durations[voice['path']]
        desired = target - 1.25 if before < target - 1.30 else before
        rate = round(before / desired, 6)
        after = before / rate
        result.update(duration_after_fit=after, tempo_rate=voice['tempo_rate'] * rate,
                      scene_durations=[value * after / before for value in voice['scene_durations']])
        durations[voice['path']] = after
        Path(voice['path']).write_bytes(b'ID3' + b'fitted' * 1024)
        return result

    cutter = Mock(side_effect=cut)
    fitter = Mock(side_effect=fit)
    monkeypatch.setattr(repair, '_cut_audio', cutter)
    monkeypatch.setattr(repair, 'fit_existing_narration_candidate', fitter)
    return SimpleNamespace(path=path, voice=voice, evidence=evidence, review=review,
                           silences=silences, durations=durations, cutter=cutter, fitter=fitter)


def _run(c):
    return repair.repair_internal_pauses(c.voice, 30, transcript_evidence=c.evidence, prosody_review=c.review)


def test_observed_two_internal_pauses_repair_existing_voice_without_new_tts(candidate):
    c = candidate
    original = deepcopy((c.voice, c.evidence, c.review))
    before = _sha(c.path)
    result = _run(c)
    assert result is not None
    assert (c.voice, c.evidence, c.review) == original
    assert result['path'] == str(c.path)
    assert result['duration_before_fit'] == 32.352
    assert 28.7 <= result['duration_after_fit'] <= 29.75
    assert .94 <= result['tempo_rate'] <= 1.12
    assert sum(result['scene_durations']) == pytest.approx(result['duration_after_fit'])
    assert result['spoken_texts'] == c.voice['spoken_texts']
    assert not {'audio_qc', 'audio_duration_qc', 'audio_prosody_qc'} & result.keys()
    audit = result['internal_pause_repair']
    assert audit['source_sha256'] == before
    assert audit['output_sha256'] == _sha(c.path) != before
    assert audit['requires_full_qa'] is True
    assert [item['scene_index'] for item in audit['cuts']] == [1, 5]
    for cut, silence, gap in zip(audit['cuts'], c.silences, [(3.8, 5.05), (24.58, 26.08)]):
        assert cut['start_seconds'] >= silence[0] + .06
        assert cut['end_seconds'] <= silence[1] - .06
        assert cut['start_seconds'] >= gap[0] + .18
        assert cut['end_seconds'] <= gap[1] - .18
        assert gap[1] - gap[0] - cut['removed_seconds'] >= .36 - 1e-12
    assert result['removed_silence_seconds'] == pytest.approx(1.219 + audit['removed_silence_seconds'])
    c.cutter.assert_called_once()
    c.fitter.assert_called_once()
    fit_input = c.fitter.call_args.args[0]
    assert fit_input['tempo_rate'] == 1.096678
    assert fit_input['duration_before_fit'] == 32.352
    # Both original-timeline cuts are applied together, not shifted twice.
    assert c.cutter.call_args.args[3][1]['start_seconds'] > 24
    assert c.fitter.call_args.args[0]['scene_durations'][0] == pytest.approx(c.voice['scene_durations'][0])
    assert repair.repair_internal_pauses(result, 30, transcript_evidence=c.evidence, prosody_review=c.review) is None


@pytest.mark.parametrize('mutation', [
    lambda c: c.evidence.pop('audio_sha256'),
    lambda c: c.evidence.update(audio_sha256='0' * 64),
    lambda c: c.evidence.update(available=False),
    lambda c: c.evidence.update({'pass': False}),
    lambda c: c.evidence['mismatch_details'].update(timestamp_sequence_match=False),
    lambda c: c.evidence['word_timestamps'][5].update(start=3.7),
    lambda c: c.evidence['word_timestamps'][5].update(end=5.05),
    lambda c: c.evidence['word_timestamps'][5].update(start=float('nan')),
    lambda c: c.review.update(available=False),
    lambda c: c.review.update({'pass': True}),
    lambda c: c.review.update(issues=[]),
    lambda c: c.review.update(issues=c.review['issues'] * 2),
    lambda c: c.review['issues'].append(deepcopy(c.review['issues'][0])),
    lambda c: c.review['issues'][0].update(code='choppy_phrase_grouping'),
    lambda c: c.review['issues'][0].update(phrase='A phrase not in this narration'),
    lambda c: c.review['issues'][0].update(start_seconds=-1),
    lambda c: c.review['scores'].update(pronunciation=True),
    lambda c: c.voice.update(voice_language_code='en'),
    lambda c: c.voice.update(tempo_rate=.93),
    lambda c: c.voice.update(tempo_rate=1.13),
    lambda c: c.voice.update(tempo_rate=True),
    lambda c: c.voice.update(duration_after_fit=28),
    lambda c: c.voice.update(internal_pause_repair={}),
    lambda c: c.voice.update(scene_durations=[5] * 6),
    lambda c: c.voice['spoken_texts'].__setitem__(1, 'Different source words.'),
    lambda c: c.voice['scene_durations'].__setitem__(1, -1),
    lambda c: c.path.write_bytes(b'not mp3' * 512),
    lambda c: c.path.write_bytes(b'ID3small'),
])
def test_invalid_or_stale_evidence_never_changes_original(candidate, mutation):
    c = candidate
    mutation(c)
    before = c.path.read_bytes()
    assert _run(c) is None
    assert c.path.read_bytes() == before
    c.cutter.assert_not_called()


@pytest.mark.parametrize('spoken', ['Olay. 26 Haziran tarihinde oldu.', 'Olay! 26 Haziran tarihinde oldu.',
                                  'Olay? 26 Haziran tarihinde oldu.', 'Olay: 26 Haziran tarihinde oldu.'])
def test_sentence_boundaries_are_never_trimmed(candidate, spoken):
    candidate.voice['spoken_texts'][1] = spoken
    assert _run(candidate) is None
    candidate.cutter.assert_not_called()


def test_scene_boundaries_are_never_trimmed_even_if_gap_is_real(candidate):
    c = candidate
    c.voice['spoken_texts'][0] += ' Olay'
    c.voice['spoken_texts'][1] = '26 Haziran tarihinde oldu.'
    assert _run(c) is None
    c.cutter.assert_not_called()


@pytest.mark.parametrize('silences', [[], [(3.9, 4.5)], [(3.849667, 4.922333)], [(0, 3), (27, 29)],
                                     [(3.849667, 4.922333), (24.9, 25.4)]])
def test_every_issue_requires_its_own_long_waveform_and_word_gap(candidate, monkeypatch, silences):
    monkeypatch.setattr(repair, '_silences', lambda *_: silences)
    assert _run(candidate) is None
    candidate.cutter.assert_not_called()


def test_two_long_gaps_inside_one_issue_are_ambiguous(candidate, monkeypatch):
    c = candidate
    c.review['issues'] = [dict(c.review['issues'][0], phrase='Olay 26 Haziran tarihinde oldu', end_seconds=9.2)]
    c.evidence['word_timestamps'][7].update(start=7.4, end=8)
    c.evidence['word_timestamps'][8].update(start=8.1, end=9.2)
    monkeypatch.setattr(repair, '_silences', lambda *_: [c.silences[0], (6.25, 7.35)])
    assert _run(c) is None
    c.cutter.assert_not_called()


def test_cumulative_tempo_preflight_rejects_without_editing(candidate):
    candidate.voice['tempo_rate'] = .94
    assert _run(candidate) is None
    candidate.cutter.assert_not_called()


@pytest.mark.parametrize('gap,allowed', [(1.0, True), (1.6, False)])
def test_three_unique_cuts_share_one_three_second_total_budget(gap, allowed):
    texts = ['Bir', 'iki', 'üç', 'dört', 'beş', 'altı']
    words = []
    silences = []
    cursor = .2
    for index, text in enumerate(texts):
        words.append(dict(text=text, start=cursor, end=cursor + .2))
        cursor += .2
        if index % 2 == 0:
            silences.append((cursor, cursor + gap))
            cursor += gap
        else:
            cursor += .1
    duration = cursor + .4
    voice = {'spoken_texts': [' '.join(texts) + '.'], 'scene_durations': [duration]}
    review = {'issues': [{'start_seconds': words[i]['start'], 'end_seconds': words[i + 1]['end']}
                         for i in (0, 2, 4)]}
    if not allowed:
        with pytest.raises(ValueError, match='cut budget exceeded'):
            repair._planned_cuts(voice, duration, words, review, silences)
    else:
        cuts = repair._planned_cuts(voice, duration, words, review, silences)
        assert len(cuts) == 3
        assert sum(cut['removed_seconds'] for cut in cuts) <= 3


@pytest.mark.parametrize('duration,prior,allowed', [
    (25, 1.0, False), (27, 1.096678, False), (27.8, 1.096678, True),
    (27.8, .94, False), (29, 1.12, True), (31, 1.12, False),
])
def test_preflight_cannot_hide_thin_script_or_compound_outside_tempo_bounds(duration, prior, allowed):
    assert repair._fit_is_bounded(duration, 30, prior) is allowed


@pytest.mark.parametrize('point', ['detect', 'cut', 'cut_probe', 'fit', 'post_fit_duration', 'post_fit_tempo', 'post_fit_scene', 'post_fit_bytes'])
def test_failures_before_final_replace_preserve_original(candidate, monkeypatch, point):
    c = candidate
    before = c.path.read_bytes()
    if point == 'detect':
        monkeypatch.setattr(repair, '_silences', Mock(side_effect=RuntimeError('no diagnostic output')))
    elif point == 'cut':
        c.cutter.side_effect = RuntimeError('cut failed')
    elif point == 'cut_probe':
        original_cut = c.cutter.side_effect
        def bad_cut(*args):
            original_cut(*args)
            c.durations[str(args[1])] += .2
        c.cutter.side_effect = bad_cut
    elif point == 'fit':
        c.fitter.side_effect = RuntimeError('fit failed')
    else:
        original_fit = c.fitter.side_effect
        def bad_fit(*args):
            result = original_fit(*args)
            if point == 'post_fit_duration':
                c.durations[result['path']] = 25
                result['duration_after_fit'] = 25
            elif point == 'post_fit_tempo':
                result['tempo_rate'] = .9
            elif point == 'post_fit_scene':
                result['scene_durations'][0] = 0
            else:
                Path(result['path']).write_bytes(b'invalid')
            return result
        c.fitter.side_effect = bad_fit
    assert _run(c) is None
    assert c.path.read_bytes() == before
    assert not list(c.path.parent.glob('internal_pause_*'))


def test_concurrent_source_change_is_not_overwritten(candidate):
    c = candidate
    original_fit = c.fitter.side_effect
    def changed(*args):
        result = original_fit(*args)
        c.path.write_bytes(b'ID3' + b'concurrently changed' * 512)
        return result
    c.fitter.side_effect = changed
    assert _run(c) is None
    assert c.path.read_bytes().startswith(b'ID3concurrently changed')


@pytest.mark.parametrize('stderr', [
    'silence_end: 2', 'silence_start: 2', 'silence_start: 2\nsilence_start: 3',
    'silence_start: 2\nsilence_end: 1', 'silence_start: 2\nsilence_end: 31',
    'silence_start: 2\nsilence_end: 3\nsilence_start: 2.5\nsilence_end: 4',
])
def test_detector_rejects_partial_or_ambiguous_logs(monkeypatch, stderr):
    monkeypatch.setattr(repair.subprocess, 'run', Mock(return_value=SimpleNamespace(stderr=stderr)))
    with pytest.raises(ValueError):
        repair._silences(Path('local.mp3'), 30)


def test_detector_uses_checked_local_bounded_ffmpeg_only(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stderr='silence_start: 3.849667\nsilence_end: 4.922333'))
    monkeypatch.setattr(repair.subprocess, 'run', run)
    assert repair._silences(Path('local.mp3'), 30) == [(3.849667, 4.922333)]
    args, kwargs = run.call_args
    assert 'silencedetect=noise=-38dB:d=0.08' in args[0]
    assert kwargs['check'] is True and kwargs['timeout'] == 45
    assert '-protocol_whitelist' in args[0]


def test_cut_plan_uses_one_concat_and_original_source_times(monkeypatch):
    run = Mock()
    monkeypatch.setattr(repair.subprocess, 'run', run)
    repair._cut_audio(Path('source.mp3'), Path('candidate.mp3'), 29.52, [
        {'start_seconds': 4, 'end_seconds': 4.8}, {'start_seconds': 24.8, 'end_seconds': 25.7},
    ])
    command = run.call_args.args[0]
    filters = command[command.index('-filter_complex') + 1]
    assert 'atrim=start=4.800000:end=24.800000' in filters
    assert 'atrim=start=25.700000:end=29.520000' in filters
    assert 'concat=n=3:v=0:a=1' in filters
    assert 'atempo' not in filters


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Requires native FFmpeg')
def test_native_two_real_silences_are_cut_then_bounded_fit_runs(candidate, monkeypatch):
    c = candidate
    # Synthetic tone only: reproduce the two measured silent intervals locally.
    expression = 'if(between(t,3.849667,4.922333)+between(t,24.57325,25.937042),0,0.15*sin(2*PI*440*t))'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    f"aevalsrc='{expression}':s=48000:d=29.50", '-c:a', 'libmp3lame', '-b:a', '192k', str(c.path)],
                   capture_output=True, check=True, timeout=45)
    monkeypatch.undo()
    duration = _native_media_duration(c.path)
    c.voice['scene_durations'] = [value * duration / 29.52 for value in c.voice['scene_durations']]
    c.voice['duration_after_fit'] = duration
    c.evidence['audio_sha256'] = _sha(c.path)
    old_sha = _sha(c.path)
    result = _run(c)
    assert result is not None
    audit = result['internal_pause_repair']
    assert audit['source_sha256'] == old_sha and audit['output_sha256'] == _sha(c.path)
    assert 28.7 <= result['duration_after_fit'] <= 29.75
    assert .94 <= result['tempo_rate'] <= 1.12
    assert result['duration_before_fit'] == 32.352
    assert len(audit['cuts']) == 2
    assert all(end - start < .8 for start, end in repair._silences(c.path, result['duration_after_fit']))
    assert sum(result['scene_durations']) == pytest.approx(result['duration_after_fit'], abs=.08)
    assert not list(c.path.parent.glob('internal_pause_*'))
