import ast
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import content_plan_voice_resume as resume, voice_candidate_recovery as retained
from app.services import audio_checkpoint, commissioning_longform as longform, longform_voice_retry as legacy
from app.services import studio_state as jobs, voice
from test_voice_candidate_recovery import stored_candidate, SOURCE_ID, CHILD_ID


@pytest.mark.parametrize('changed_narration', [False, True])
def test_queued_long_retains_actual_candidate_bytes_and_forbids_editorial_rewrite(stored_candidate, monkeypatch, changed_narration):
    fixture = stored_candidate
    text = 'A printed banknote begins with artists carefully preparing detailed designs for engravers.'
    package = deepcopy(fixture.package)
    package['scenes'] = [{'narration': text, 'visual_queries': ['engraver drawing currency designs'],
                          'ai_prompt': None, 'pace': 'normal', 'transition': 'cut'} for _ in range(30)]
    saved_voice = {**fixture.voice, 'spoken_texts': [text]*30, 'scene_durations': [6.0]*30,
        'duration_before_fit': 180, 'duration_after_fit': 180, 'content_target_seconds': 180,
        'voice_language_code': 'en'}
    pointer = audio_checkpoint.persist_audio_candidate_checkpoint(SOURCE_ID, package, saved_voice)['audio_candidate_checkpoint']
    spec = {'topic': 'The journey of banknotes', 'duration_minutes': 3, 'language': 'en',
        'channel_id': 'owner-channel', 'mode': 'production', 'format': 'landscape',
        'content_plan_item_id': 'private-queued-item'}
    verify = Mock(); monkeypatch.setattr(resume, 'verify_child', verify)
    monkeypatch.setattr(jobs, 'get_job', lambda task: {'audio_candidate_checkpoint': pointer})
    update = Mock(); monkeypatch.setattr(jobs, 'update_job', update)
    monkeypatch.setattr(legacy.render, 'media_duration', lambda path: 180)
    tts = Mock(side_effect=AssertionError('Retained narration cannot be resynthesized'))
    monkeypatch.setattr(voice, 'synthesize_scene_sequence', tts)
    def review(value, topic, language, *, allow_revisions):
        assert allow_revisions is False and language == 'en' and len(value['scenes']) == 30
        assert value['narration_word_count'] == 360
        if changed_narration: value['scenes'][0]['narration'] = 'A different sentence.'
        return value
    critic = Mock(side_effect=review); monkeypatch.setattr(longform, 'review_story', critic)
    if changed_narration:
        with pytest.raises(retained.VoiceCandidateRecoveryError): resume.prepare_long(CHILD_ID, SOURCE_ID, spec, fixture.work)
        update.assert_not_called()
    else:
        result = resume.prepare_long(CHILD_ID, SOURCE_ID, spec, fixture.work)
        assert Path(result['voice_result']['path']).read_bytes() == fixture.audio
        assert result['voice_result']['scene_durations'] == [6.0]*30
        assert result['content_plan_voice_source'] == SOURCE_ID and result['preserve_audio_bytes'] is True
        assert update.call_args.kwargs['voice_candidate_reuse']['new_tts_requests'] == 0
    verify.assert_called_once_with(CHILD_ID, SOURCE_ID, spec)
    critic.assert_called_once(); tts.assert_not_called()


@pytest.mark.parametrize('cap,used,accepted', [(32,0,True), (32,1,False), (2,0,False)])
def test_actual_worker_gate_uses_private_queued_cap_and_never_the_legacy_cap(monkeypatch, cap, used, accepted):
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    pipeline = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'run_video_pipeline')
    gate = next(n for n in ast.walk(pipeline) if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp)
                and "saved_voice_retry.get('preserve_audio_bytes')" in ast.unparse(n.test))
    authorization = Mock(return_value=32); monkeypatch.setattr(resume, 'retained_long_cap', authorization)
    candidate = {'preserve_audio_bytes': True, 'content_plan_voice_source': SOURCE_ID}
    ledger = Mock(return_value={'cap': cap, 'used': used})
    namespace = {'saved_voice_retry': candidate, '_persisted_paid_create_budget': ledger,
        'task_id': CHILD_ID, 'total_paid_create_cap': None, 'runway_attempts': 5, 'FinalVisualQualityError': ValueError}
    code = compile(ast.Module(body=[gate], type_ignores=[]), str(path), 'exec')
    if accepted:
        exec(code, namespace)
        assert namespace['total_paid_create_cap'] == 32 and namespace['runway_attempts'] == 0
    else:
        with pytest.raises(ValueError, match='Queued long-form'): exec(code, namespace)
    authorization.assert_called_once_with(CHILD_ID, candidate); ledger.assert_called_once_with(CHILD_ID, 32)
