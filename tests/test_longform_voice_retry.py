import ast
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import longform_voice_retry as recovery


@pytest.fixture
def case(monkeypatch, tmp_path):
    source_id, child_id = 'source', 'child'
    spec = {'topic': 'Doların görünmeyen yolculuğu', 'duration_minutes': 3,
        'language': 'tr', 'channel_id': None, 'mode': 'production', 'format': 'landscape',
        'workflow': 'auto', 'publish_after_render': False, 'visual_mix': 'real_first',
        'content_style': 'documentary'}
    source = {'task_id': source_id, 'kind': 'render', 'state': 'FAILURE',
        'failure_stage': 'audio_qc', 'retry_child_task_id': child_id, 'retry_claimed': True,
        'spec': deepcopy(spec), 'audio_candidate_checkpoint': {'status': 'unapproved_candidate'}}
    child = {'task_id': child_id, 'kind': 'render', 'state': 'PROGRESS',
        'parent_id': source_id, 'spec': deepcopy(spec)}
    path = tmp_path / 'unchanged.mp3'
    raw = b'original-hash-bound-audio' * 100
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    texts = ['Kaynaklı bilgi.'] * 21
    package = {'scenes': [{'index': i, 'narration': text, 'ai_prompt': None,
        'visual_queries': ['authentic dollar banknote closeup']} for i, text in enumerate(texts)],
        'narration': ' '.join(texts), 'sources': [
            {'url': 'https://www.bep.gov/currency/faqs', 'evidence': 'The paper uses cotton and linen fibers.'},
            {'url': 'https://www.federalreserve.gov/paymentsystems/coin_about.htm',
             'evidence': 'Worn notes are inspected and removed from circulation.'}]}
    saved = {'path': str(path), 'spoken_texts': texts, 'scene_durations': [178.9058 / 21] * 21,
        'duration_before_fit': 178.9058, 'duration_after_fit': 178.9058, 'tempo_rate': 1.0}
    candidate = {'package': package, 'voice_result': saved, 'audio_sha256': digest}
    loader = Mock(return_value=candidate)
    critic = Mock(return_value={'accepted': True, 'model': 'test-model', 'reviewed_scene_count': 21})
    update = Mock()
    monkeypatch.setattr(recovery.studio_state, 'get_job', lambda key: source if key == source_id else child)
    monkeypatch.setattr(recovery.studio_state, 'update_job', update)
    monkeypatch.setattr(recovery.voice_candidate_recovery, 'load_voice_retry_candidate', loader)
    monkeypatch.setattr(recovery.voice, 'normalize_turkish_tts', lambda text, **kwargs: text)
    monkeypatch.setattr(recovery.gemini_critic, 'run_optional_gemini_critic', critic)
    monkeypatch.setattr(recovery.render, 'media_duration', lambda value: 178.92)
    return SimpleNamespace(**locals())


def test_immutable_three_minute_candidate_gets_real_readonly_critic_and_no_new_tts(case):
    c = case
    before = deepcopy((c.source, c.child, c.package, c.saved))
    result = recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    assert result['voice_result'] is c.saved and result['preserve_audio_bytes'] is True
    assert c.path.read_bytes() == c.raw
    assert (c.source, c.child, c.package, c.saved) == before
    c.loader.assert_called_once()
    c.critic.assert_called_once()
    assert c.critic.call_args.kwargs['enabled'] is True
    assert c.critic.call_args.kwargs['fresh_scheduled'] is False
    assert c.critic.call_args.kwargs['content_style'] == 'documentary'
    assert c.critic.call_args.args[0]['candidate_story_in_order'] == c.package['scenes']
    assert result['package']['scenes'] == c.package['scenes']
    assert result['package']['longform_story_qc']['requires_full_media_qa'] is True
    assert c.update.call_args.kwargs['voice_candidate_reuse']['new_tts_requests'] == 0
    assert recovery.preserved_voice(result) is c.saved


@pytest.mark.parametrize('key,value', [
    ('format', 'shorts'), ('duration_minutes', .5), ('duration_minutes', 4),
    ('duration_minutes', True), ('language', 'en'), ('publish_after_render', True),
    ('publish_after_render', None), ('production_scheduled', False), ('production_channel_id', ''),
    ('series_id', ''), ('repair_source_task_id', 'old'), ('channel_id', 'channel'),
    ('workflow', 'approved'), ('visual_mix', 'ai_first'), ('mode', 'preview'),
])
def test_new_recovery_cannot_change_scope_or_consume_any_series(case, key, value):
    c = case
    c.spec[key] = value
    with pytest.raises(recovery.LongformVoiceRetryError, match='no replacement voice'):
        recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    c.loader.assert_not_called()
    c.critic.assert_not_called()
    c.update.assert_not_called()


@pytest.mark.parametrize('key,value', [
    ('task_id', 'wrong'), ('state', 'SUCCESS'), ('kind', 'plan'),
    ('failure_stage', 'visual_qc'), ('retry_child_task_id', 'other'), ('retry_claimed', False),
    ('audio_candidate_checkpoint', None), ('result', {'status': 'ready'}),
    ('publication_hold', {'active': True}), ('generated_asset_candidates', [{}]),
    ('repair_checkpoint', {'saved': True}), ('audio_pause_repair', {'edited': True}),
    ('paid_create_slots_used', 0), ('paid_create_slots_used', None),
    ('preview_total_paid_create_cap', 6),
])
def test_unproven_claim_checkpoint_or_pre_media_boundary_fails_closed(case, key, value):
    c = case
    c.source[key] = value
    with pytest.raises(recovery.LongformVoiceRetryError):
        recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    c.loader.assert_not_called()
    c.critic.assert_not_called()


def test_source_child_and_runtime_must_remain_exactly_equal(case):
    c = case
    c.child['spec']['topic'] = 'Different topic'
    with pytest.raises(recovery.LongformVoiceRetryError):
        recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    c.loader.assert_not_called()


@pytest.mark.parametrize('failure', ['loader', 'spoken', 'third_ai', 'critic', 'missing_verdict',
                                     'false_verdict', 'wrong_scene_count', 'bad_sources'])
def test_failed_candidate_or_real_critic_never_falls_back_to_script_or_synthesis(case, failure):
    c = case
    if failure == 'loader': c.loader.side_effect = ValueError('private storage detail')
    elif failure == 'spoken': c.saved['spoken_texts'][0] = 'Changed'
    elif failure == 'third_ai':
        for scene in c.package['scenes'][:3]: scene['ai_prompt'] = 'Generated shot'
    elif failure == 'critic': c.critic.side_effect = RuntimeError('actual source rejection')
    elif failure == 'missing_verdict': c.critic.return_value = None
    elif failure == 'false_verdict': c.critic.return_value['accepted'] = False
    elif failure == 'wrong_scene_count': c.critic.return_value['reviewed_scene_count'] = 6
    elif failure == 'bad_sources': c.package['sources'] = []
    with pytest.raises(recovery.LongformVoiceRetryError, match='no replacement voice'):
        recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    assert not any('voice_candidate_reuse' in call.kwargs for call in c.update.call_args_list)
    assert c.update.call_args.kwargs['longform_voice_revalidation']['requires_full_qa'] is True


def test_rejected_critic_preserves_safe_existing_diagnostics_not_exception_text(case):
    c = case
    from app.services.planning_diagnostics import story_planning_error
    error = recovery.gemini_critic.GeminiCriticRejected('private detail must not be stored')
    error.planning_diagnostics = story_planning_error('Story rejected',
        scenes=c.package['scenes'], sources=c.package['sources'],
        review={'story': {'claims_supported_by_supplied_source_evidence': False}}).planning_diagnostics
    c.critic.side_effect = error
    with pytest.raises(recovery.LongformVoiceRetryError):
        recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    fields = c.update.call_args.kwargs
    assert fields['longform_voice_revalidation']['reason_code'] == 'independent_critic_rejected'
    assert 'prepaid_story_diagnostics' in fields
    assert 'private detail' not in str(fields)


@pytest.mark.parametrize('change', ['bytes', 'cue', 'speech', 'tempo', 'duration_measurement'])
def test_preserved_output_cannot_retime_trim_or_rebind_audio(case, monkeypatch, change):
    c = case
    result = recovery.prepare(c.child_id, c.source_id, c.spec, c.tmp_path)
    if change == 'bytes': c.path.write_bytes(b'new audio' * 300)
    elif change == 'cue': c.saved['scene_durations'][0] += .5
    elif change == 'speech': c.saved['spoken_texts'][0] += ' New speech'
    elif change == 'tempo': c.saved['tempo_rate'] = 1.02
    else: monkeypatch.setattr(recovery.render, 'media_duration', lambda value: 182)
    with pytest.raises(recovery.LongformVoiceRetryError, match='audio or cues changed'):
        recovery.preserved_voice(result)


def test_worker_keeps_existing_shorts_route_and_has_explicit_no_fit_long_branch():
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    prepare = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == '_prepare_saved_voice_retry')
    assert 'revalidate_immutable_short_story' in ast.unparse(prepare)
    assert "runtime_spec.get('duration_minutes') == 3" in ast.unparse(prepare)
    pipeline = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'run_video_pipeline')
    branch = next(n for n in ast.walk(pipeline) if isinstance(n, ast.If)
        and any(isinstance(call, ast.Name) and call.id == '_preserved_longform_voice_for_retry'
                for statement in n.body for call in ast.walk(statement))
        and isinstance(n.test, ast.Compare))
    assert "saved_voice_retry.get('preserve_audio_bytes') is True" in ast.unparse(branch.test)
    body = '\n'.join(ast.unparse(n) for n in branch.body)
    assert '_preserved_longform_voice_for_retry' in body
    assert 'fit' not in body and 'synthesize' not in body
    assert 'not saved_voice_retry' in ast.unparse(pipeline)


def test_future_measurement_failure_is_a_terminal_audio_error(monkeypatch):
    import sys
    module = SimpleNamespace(preserved_voice=Mock(side_effect=recovery.LongformVoiceRetryError()),
                            LongformVoiceRetryError=recovery.LongformVoiceRetryError)
    monkeypatch.setitem(sys.modules, 'app.services.longform_voice_retry', module)
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_preserved_longform_voice_for_retry')
    class TerminalAudioError(Exception): pass
    namespace = {'FinalAudioQualityError': TerminalAudioError}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    with pytest.raises(TerminalAudioError, match='no replacement voice'):
        namespace['_preserved_longform_voice_for_retry']({})


def test_verified_longform_uses_existing_durable_two_create_ledger_and_no_restart():
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    pipeline = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'run_video_pipeline')
    ledger = next(n for n in ast.walk(pipeline) if isinstance(n, ast.If)
        and isinstance(n.test, ast.BoolOp)
        and "saved_voice_retry.get('preserve_audio_bytes')" in ast.unparse(n.test))
    body = '\n'.join(ast.unparse(n) for n in ledger.body)
    assert '_persisted_paid_create_budget(task_id, 2)' in body
    assert 'total_paid_create_cap = 2' in body
    assert 'runway_attempts = 0' in body
    assert "get('used') != 0" in body
    text = ast.unparse(pipeline)
    assert 'allow_paid_terminal_resubmit=total_paid_create_cap is None' in text
    assert 'Long-form retained job stopped without automatic restart' in text


@pytest.mark.parametrize('candidate,budget,raises,expected_cap', [
    ({'preserve_audio_bytes': True}, {'cap': 2, 'used': 0}, False, 2),
    ({'preserve_audio_bytes': True}, {'cap': 2, 'used': 1}, True, None),
    ({'preserve_audio_bytes': True}, {'cap': 6, 'used': 0}, True, None),
    ({'preserve_audio_bytes': True}, {'cap': 2}, True, None),
    ({'preserve_audio_bytes': False}, {'cap': 2, 'used': 0}, False, None),
    (None, {'cap': 2, 'used': 0}, False, None),
])
def test_actual_retained_ledger_gate_does_not_touch_shorts_or_reuse_spent_budget(
        candidate, budget, raises, expected_cap):
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    pipeline = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'run_video_pipeline')
    ledger = next(n for n in ast.walk(pipeline) if isinstance(n, ast.If)
        and isinstance(n.test, ast.BoolOp)
        and "saved_voice_retry.get('preserve_audio_bytes')" in ast.unparse(n.test))
    persisted = Mock(return_value=budget)
    namespace = {'saved_voice_retry': candidate, '_persisted_paid_create_budget': persisted,
        'task_id': 'verified-child', 'total_paid_create_cap': None, 'runway_attempts': 5,
        'FinalVisualQualityError': ValueError}
    code = compile(ast.Module(body=[ledger], type_ignores=[]), str(path), 'exec')
    if raises:
        with pytest.raises(ValueError, match='fresh two-create'):
            exec(code, namespace)
    else:
        exec(code, namespace)
    assert namespace['total_paid_create_cap'] == expected_cap
    if candidate and candidate.get('preserve_audio_bytes') is True:
        persisted.assert_called_once_with('verified-child', 2)
    else:
        persisted.assert_not_called()
        assert namespace['runway_attempts'] == 5
