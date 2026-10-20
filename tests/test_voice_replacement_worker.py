"""Exercise real worker/voice functions in isolation; never import app.tasks."""
import ast
import base64
import binascii
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]


class QualityError(RuntimeError):
    pass


class ScriptError(QualityError):
    pass


def _function(file, name, namespace):
    path = ROOT / file
    node = next(item for item in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


@pytest.fixture
def synthesis(monkeypatch):
    events = []
    reservation = Mock(side_effect=lambda *_a, **_k: events.append('reserved') or dict(model_id='eleven_multilingual_v2', max_attempts=1))
    monkeypatch.setitem(sys.modules, 'app.services.voice_replacement', SimpleNamespace(acquire_voice_replacement_attempt=reservation))
    archive = Mock(return_value={'status': 'unapproved_raw_voice', 'reusable': False})
    monkeypatch.setitem(sys.modules, 'app.services.voice_replacement_diagnostic', SimpleNamespace(persist_raw_voice_replacement=archive))
    def synth(*_args, **kwargs):
        callback = kwargs.get('before_paid_request')
        if callback:
            callback('selected-voice')
        events.append('provider')
        if kwargs.get('raw_audio_sink'):
            kwargs['raw_audio_sink'](b'paid raw bytes')
        return {'path': '/tmp/new.mp3', 'spoken_texts': ['Frozen sentence.']}
    provider = Mock(side_effect=synth)
    namespace = dict(MAX_AUDIO_GENERATION_ATTEMPTS=3, FinalAudioQualityError=QualityError,
                     VoiceScriptFitError=ScriptError, VoiceQualityError=QualityError,
                     synthesize_scene_sequence=provider, httpx=httpx, json=json,
                     is_transient_voice_http_error=lambda _error: True,
                     voice_http_retry_delay_seconds=lambda *_: 0, time=SimpleNamespace(sleep=Mock()), update_job=Mock())
    fn = _function('app/tasks.py', '_synthesize_voice_candidate', namespace)
    request = dict(source_task_id='source', audio_sha256='a' * 64, spec={'language': 'tr'})
    return SimpleNamespace(**locals())


def test_reserved_replacement_uses_exactly_one_explicit_continuous_profile(synthesis):
    s = synthesis
    result = s.fn([{'narration': 'Frozen sentence.'}], 'child', 30, language='tr', voice_replacement_request=s.request)
    assert s.events == ['reserved', 'provider']
    assert s.provider.call_count == 1
    assert s.provider.call_args.kwargs['profile_override'] == 'turkish_multilingual_v2'
    assert s.provider.call_args.kwargs['generation_attempt'] == 0
    assert result['_generation_attempts_used'] == 1
    s.reservation.assert_called_once_with('child', 'source', 'a' * 64, {'language': 'tr'}, voice_id='selected-voice')
    s.archive.assert_called_once_with('child', b'paid raw bytes')
    s.namespace['update_job'].assert_called_once_with('child', voice_replacement_raw_audio=s.archive.return_value)


@pytest.mark.parametrize('failure', ['timeout', 'quality', 'fit', 'processing', 'reservation', 'bad_reservation', 'archive'])
def test_ambiguous_or_failed_replacement_never_spends_a_second_take(synthesis, failure):
    s = synthesis
    original = s.provider.side_effect
    if failure == 'reservation':
        s.reservation.side_effect = RuntimeError('Lost reservation reply')
    elif failure == 'bad_reservation':
        s.reservation.side_effect = lambda *_a, **_k: {'model_id': 'another', 'max_attempts': True}
    elif failure == 'archive':
        s.archive.side_effect = RuntimeError('Private storage unavailable')
    else:
        def failed(*args, **kwargs):
            original(*args, **kwargs)
            if failure == 'timeout':
                raise httpx.ReadTimeout('Possibly accepted')
            if failure == 'quality':
                raise QualityError('Rejected delivery')
            if failure == 'fit':
                raise ScriptError('Unsafe tempo')
            raise RuntimeError('Local encoder failed after paid response')
        s.provider.side_effect = failed
    with pytest.raises(QualityError):
        s.fn([{'narration': 'Frozen sentence.'}], 'child', 30, language='tr', voice_replacement_request=s.request)
    assert s.provider.call_count == 1
    assert s.events.count('provider') <= 1
    s.namespace['time'].sleep.assert_not_called()


def test_ordinary_fresh_voice_keeps_its_existing_three_take_budget(synthesis):
    s = synthesis
    s.provider.side_effect = QualityError('Rejected take')
    with pytest.raises(QualityError):
        s.fn([{'narration': 'Ordinary sentence.'}], 'ordinary', 30, language='tr')
    assert s.provider.call_count == 3
    assert all('profile_override' not in call.kwargs for call in s.provider.call_args_list)
    s.reservation.assert_not_called()


@pytest.mark.parametrize('code', ['spend_day_limit', 'native_credit_previous_outcome_unknown'])
def test_voice_does_not_hide_or_retry_a_credit_refusal_as_bad_content(synthesis, code):
    from app.services.production_spend import SpendBlocked

    s = synthesis
    refusal = SpendBlocked(code)
    s.provider.side_effect = refusal
    with pytest.raises(SpendBlocked) as caught:
        s.fn([{'narration': 'A bounded sentence.'}], 'ordinary', 30, language='en')
    assert caught.value is refusal and s.provider.call_count == 1
    s.namespace['time'].sleep.assert_not_called()


@pytest.mark.parametrize('transport,expected', [(False, 'audio_rejected'), (True, 'review_unverified')])
def test_exhausted_voice_takes_keep_quality_and_transport_outcomes_distinct(synthesis, transport, expected):
    from app.services.production_failures import classify_failure, classified_hold_reason

    s = synthesis
    s.provider.side_effect = httpx.ReadTimeout('Temporary provider failure') if transport else QualityError('Bad take')
    with pytest.raises(QualityError) as caught:
        s.fn([{'narration': 'A bounded sentence.'}], 'ordinary', 30, language='en')
    job = {'failure_stage': 'voice_and_visuals', 'error': str(caught.value),
           'failure_classification': classify_failure(caught.value, 'voice_and_visuals')}
    assert classified_hold_reason(job) == expected and s.provider.call_count == 3


@pytest.mark.parametrize('kwargs', [{'language': 'en'}, {'target_seconds': 60}, {'start_attempt': 1}])
def test_replacement_outside_explicit_short_turkish_scope_cannot_start(synthesis, kwargs):
    args = dict(target_seconds=30, language='tr', voice_replacement_request=synthesis.request)
    args.update(kwargs)
    with pytest.raises(QualityError):
        synthesis.fn([], 'child', **args)
    synthesis.provider.assert_not_called()


@pytest.fixture
def voice_sequence(tmp_path):
    events = []
    def mapped_path(value):
        return tmp_path if value == '/tmp' else Path(value)
    def run(command, **_kwargs):
        Path(command[-1]).write_bytes(b'normalized')
    provider = Mock(side_effect=lambda *_a, **_k: events.append('provider') or (b'audio', {}))
    namespace = dict(Path=mapped_path, VoiceScriptFitError=ScriptError,
        _selected_voice_or_raise=Mock(return_value={'voice_id': 'selected', 'name': 'Existing selected voice'}),
        _use_turkish_short_preview_profile=lambda language, seconds: language == 'tr' and seconds <= 40,
        normalize_turkish_tts=lambda text, **_kwargs: text, _voice_speed=lambda _, **kw: 1.0,
        _join_scene_narration=lambda spoken: (' '.join(spoken), [(0, 7)]),
        _deterministic_scene_seed=lambda *_args: 123,
        synthesize_voice_with_timestamps=provider, _media_duration=lambda _: 29.5,
        _short_preview_audio_edit_plan=lambda *_args: dict(removed_silence_seconds=0, interior_pause_count=0, tail_trimmed=False),
        _apply_short_preview_audio_edit_plan=lambda *_args: ([29.5], 29.5),
        subprocess=SimpleNamespace(run=run, DEVNULL=-3), _fit_duration=lambda *_args: ([29.5], 29.5, 29.5, 1),
        ELEVENLABS_TURKISH_SHORT_MODEL_ID='eleven_flash_v2_5', ELEVENLABS_MULTILINGUAL_V2_MODEL_ID='eleven_multilingual_v2')
    fn = _function('app/services/voice.py', 'synthesize_scene_sequence', namespace)
    callback = Mock(side_effect=lambda voice_id: events.append('reserved:' + voice_id))
    sink = Mock()
    return SimpleNamespace(**locals())


def test_override_keeps_one_continuous_request_and_correct_multilingual_metadata(voice_sequence):
    v = voice_sequence
    result = v.fn([{'narration': 'Sabit cümle.'}], 'child', 30, language='tr',
                  profile_override='turkish_multilingual_v2', before_paid_request=v.callback, raw_audio_sink=v.sink)
    assert v.events == ['reserved:selected', 'provider']
    v.provider.assert_called_once_with('Sabit cümle.', 'selected', speed=1.0, seed=123, raw_audio_sink=v.sink)
    assert result['voice_model'] == 'eleven_multilingual_v2'
    assert result['voice_language_code'] is None  # This model has no language_code API parameter.
    assert result['spoken_texts'] == ['Sabit cümle.']


def test_default_turkish_profile_remains_flash_without_new_reservation(voice_sequence):
    v = voice_sequence
    result = v.fn([{'narration': 'Sabit cümle.'}], 'child', 30, language='tr')
    v.provider.assert_called_once_with('Sabit cümle.', 'selected', speed=1.0, seed=123, turkish_short_preview=True)
    assert result['voice_model'] == 'eleven_flash_v2_5'
    v.callback.assert_not_called()


@pytest.mark.parametrize('change', [{'profile_override': 'anything'}, {'language': 'en'}, {'target_seconds': 31},
                                   {'generation_attempt': 1}, {'generation_attempt': False}, {'before_paid_request': None}, {'raw_audio_sink': None}])
def test_profile_override_requires_reserved_exact_scope_before_even_selecting_voice(voice_sequence, change):
    v = voice_sequence
    kwargs = dict(target_seconds=30, language='tr', profile_override='turkish_multilingual_v2', before_paid_request=v.callback, raw_audio_sink=v.sink)
    kwargs.update(change)
    with pytest.raises(ScriptError):
        v.fn([{'narration': 'Sabit cümle.'}], 'child', **kwargs)
    v.namespace['_selected_voice_or_raise'].assert_not_called()
    v.provider.assert_not_called()


def test_failed_durable_callback_prevents_actual_synthesis(voice_sequence):
    v = voice_sequence
    v.callback.side_effect = RuntimeError('Cannot reserve')
    with pytest.raises(RuntimeError):
        v.fn([{'narration': 'Sabit cümle.'}], 'child', 30, language='tr',
             profile_override='turkish_multilingual_v2', before_paid_request=v.callback, raw_audio_sink=v.sink)
    v.provider.assert_not_called()


def test_english_short_failed_naturalness_cannot_reach_media_or_new_tts():
    tree = ast.parse((ROOT / 'app/tasks.py').read_text(encoding='utf-8'))
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    gate = next(node for node in ast.walk(pipeline) if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'short_form_prosody_required' for t in node.targets))
    loop = next(node for node in ast.walk(pipeline) if isinstance(node, ast.While)
                and '_verify_audio_narration_with_retry' in ast.unparse(node))
    passing = {'available': True, 'pass': True, 'score': 100}
    prosody = Mock(return_value={'available': True, 'pass': False, 'reason': 'choppy_phrase_grouping', 'issues': []})
    blocked = Mock(side_effect=AssertionError('No edit or new TTS for saved English audio'))
    n = dict(language='en', duration_minutes=.5, recovered_voice=False, saved_voice_retry=True,
             selected_recovery=None,
             options={'mode': 'preview', 'format': 'shorts'},
             _effective_short_edit_target=_function('app/tasks.py', '_effective_short_edit_target', {}),
             voice_result={'path': '/tmp/existing.mp3', 'duration_after_fit': 29.5}, voice_path='/tmp/existing.mp3',
             expected_spoken_narration='An existing English script.', task_id='child', audio_generation_attempts=1,
             audio_pause_repair_attempted=False, audio_qc_retry_history=[], audio_synthesis_quality_errors=[],
             MAX_AUDIO_GENERATION_ATTEMPTS=3, FinalAudioQualityError=QualityError, json=json,
             _audio_qa_fingerprint=lambda _: 'a' * 64,
             _verify_audio_narration_with_retry=Mock(return_value=passing),
             _short_preview_voice_duration_qc=Mock(return_value=passing), verify_audio_prosody=prosody,
             _repair_voice_internal_pauses=blocked, _synthesize_voice_candidate=blocked)
    exec(compile(ast.Module(body=[gate], type_ignores=[]), '<gate>', 'exec'), n)
    assert n['short_form_prosody_required'] is True
    with pytest.raises(QualityError, match='Audio narration QA rejected'):
        exec(compile(ast.Module(body=[loop], type_ignores=[]), '<loop>', 'exec'), n)
    assert prosody.call_args.kwargs['language'] == 'en'
    blocked.assert_not_called()


def test_replacement_still_uses_saved_story_and_disables_seed_regeneration():
    text = (ROOT / 'app/tasks.py').read_text(encoding='utf-8')
    tree = ast.parse(text)
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    assert pipeline.args.args[-2].arg == 'voice_replacement_source_id'
    assert pipeline.args.args[-1].arg == 'full_rebuild_source_id'
    assert pipeline.args.defaults[-1].value is None
    source = ast.unparse(pipeline)
    assert source.index('_prepare_saved_voice_retry(') < source.index('_prepare_voice_replacement_request(')
    assert source.index('_require_voice_replacement_checkpoint(') < source.index('while True:')
    regeneration = next(node for node in ast.walk(pipeline) if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'can_regenerate' for t in node.targets))
    assert 'not saved_voice_retry' in ast.unparse(regeneration.value)


@pytest.mark.parametrize('alignment', [{}, None, 'invalid'])
def test_raw_paid_take_is_archived_before_alignment_rejection(alignment):
    events = []
    raw = b'ID3' + b'raw paid data' * 100
    response = SimpleNamespace(raise_for_status=lambda: None,
        json=lambda: {'audio_base64': base64.b64encode(raw).decode(), 'alignment': alignment})
    post = Mock(side_effect=lambda *_a, **_k: events.append('http') or response)
    n = dict(httpx=SimpleNamespace(post=post), ELEVENLABS_BASE='https://provider.invalid',
             _headers=lambda: {}, _voice_request_body=lambda *_a, **_k: {},
             VoiceQualityError=QualityError, base64=base64, binascii=binascii)
    fn = _function('app/services/voice.py', 'synthesize_voice_with_timestamps', n)
    sink = Mock(side_effect=lambda _: events.append('archive'))
    if isinstance(alignment, dict):
        assert fn('Frozen script', 'existing-id', raw_audio_sink=sink) == (raw, alignment)
    else:
        with pytest.raises(QualityError, match='missing source alignment'):
            fn('Frozen script', 'existing-id', raw_audio_sink=sink)
    assert events == ['http', 'archive']
    sink.assert_called_once_with(raw)
    assert post.call_count == 1


def test_raw_diagnostic_failure_is_terminal_before_fit_or_a_second_request():
    raw = b'ID3' + b'a' * 2048
    post = Mock(return_value=SimpleNamespace(raise_for_status=lambda: None,
        json=lambda: {'audio_base64': base64.b64encode(raw).decode(), 'alignment': {}}))
    n = dict(httpx=SimpleNamespace(post=post), ELEVENLABS_BASE='https://provider.invalid',
             _headers=lambda: {}, _voice_request_body=lambda *_a, **_k: {},
             VoiceQualityError=QualityError, base64=base64, binascii=binascii)
    fn = _function('app/services/voice.py', 'synthesize_voice_with_timestamps', n)
    with pytest.raises(RuntimeError, match='archive failed'):
        fn('Frozen', 'existing-id', raw_audio_sink=Mock(side_effect=RuntimeError('archive failed')))
    assert post.call_count == 1


def test_replacement_policy_uses_verified_saved_audio_hash_and_does_not_mutate_spec(monkeypatch):
    spec = {'language': 'tr', 'topic': 'Frozen story'}
    before = deepcopy(spec)
    policy = {'model_id': 'eleven_multilingual_v2', 'max_attempts': 1, 'requires_full_qa': True}
    getter = Mock(return_value=policy)
    monkeypatch.setitem(sys.modules, 'app.services.voice_replacement', SimpleNamespace(get_voice_replacement_policy=getter))
    n = dict(FinalAudioQualityError=QualityError, update_job=Mock())
    fn = _function('app/tasks.py', '_prepare_voice_replacement_request', n)
    request = fn('child', 'parent', spec, {'source_audio_sha256': 'b' * 64})
    assert spec == before and request['spec'] == spec and request['spec'] is not spec
    getter.assert_called_once_with('child', 'parent', 'b' * 64, spec)
    n['update_job'].assert_called_once_with('child', voice_candidate_reuse=None, voice_replacement=policy)


@pytest.mark.parametrize('policy', [None, {}, {'model_id': 'other', 'max_attempts': 1},
                                   {'model_id': 'eleven_multilingual_v2', 'max_attempts': True},
                                   {'model_id': 'eleven_multilingual_v2', 'max_attempts': 2}])
def test_invalid_policy_never_becomes_a_voice_request(monkeypatch, policy):
    monkeypatch.setitem(sys.modules, 'app.services.voice_replacement', SimpleNamespace(get_voice_replacement_policy=lambda *_: policy))
    n = dict(FinalAudioQualityError=QualityError, update_job=Mock())
    fn = _function('app/tasks.py', '_prepare_voice_replacement_request', n)
    with pytest.raises(QualityError, match='authorization could not be verified'):
        fn('child', 'parent', {}, {'source_audio_sha256': 'a' * 64})
    n['update_job'].assert_not_called()


@pytest.mark.parametrize('mutation', [{}, {'audio_sha256': 'b' * 64}, {'qa_approved': True},
                                     {'requires_full_qa': False}, {'status': 'approved'}])
def test_paid_candidate_requires_new_durable_unapproved_exact_audio_checkpoint(monkeypatch, mutation):
    checkpoint = {'status': 'unapproved_candidate', 'qa_approved': False, 'requires_full_qa': True,
                  'audio_sha256': 'a' * 64}
    checkpoint.update(mutation)
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(get_job=lambda _: {'audio_candidate_checkpoint': checkpoint}))
    n = dict(FinalAudioQualityError=QualityError, _audio_qa_fingerprint=lambda _: 'a' * 64)
    fn = _function('app/tasks.py', '_require_voice_replacement_checkpoint', n)
    if mutation:
        with pytest.raises(QualityError, match='checkpoint could not be verified'):
            fn('child', {'path': '/tmp/child.mp3'})
    else:
        fn('child', {'path': '/tmp/child.mp3'})


@pytest.mark.parametrize('job', [None, {}, {'audio_candidate_checkpoint_error': 'storage unavailable'}])
def test_missing_checkpoint_cannot_proceed_to_paid_media(monkeypatch, job):
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(get_job=lambda _: job))
    fn = _function('app/tasks.py', '_require_voice_replacement_checkpoint',
                   dict(FinalAudioQualityError=QualityError, _audio_qa_fingerprint=lambda _: 'a' * 64))
    with pytest.raises(QualityError):
        fn('child', {'path': '/tmp/child.mp3'})
