"""Execute the real worker's audio loop before any paid visual stage."""
import pytest
from test_audio_pause_pipeline import _loop, _run, QualityError


@pytest.mark.parametrize('language', ['en','tr'])
@pytest.mark.parametrize('passed', [True,False])
def test_every_fal_documentary_requires_actual_full_performance_review(language, passed):
    loop, context = _loop(repaired=False)
    context.update(short_form_prosody_required=False, duration_minutes=3,
        language=language, audio_generation_attempts=3)
    context['voice_result'].update(voice_model='fal-ai/elevenlabs/tts/turbo-v2.5',
        duration_after_fit=168, scene_durations=[5.6]*30)
    context['verify_audio_prosody'].side_effect=None
    context['verify_audio_prosody'].return_value={'available':True,'pass':passed,
        'reason':None if passed else 'mispronunciation'}
    if passed:
        _run(loop,context)
        assert context['audio_prosody_qc']['pass'] is True
    else:
        with pytest.raises(QualityError,match='mispronunciation'):_run(loop,context)
    context['verify_audio_prosody'].assert_called_once()
    assert context['verify_audio_prosody'].call_args.kwargs['audio_duration_seconds']==168
    context['_synthesize_voice_candidate'].assert_not_called()
    context['_repair_voice_internal_pauses'].assert_not_called()


def test_unknown_full_audio_listener_stops_before_any_replacement_voice():
    loop, context=_loop()
    context.update(short_form_prosody_required=False,duration_minutes=3)
    context['voice_result']['voice_model']='fal-ai/elevenlabs/tts/turbo-v2.5'
    context['verify_audio_prosody'].side_effect=RuntimeError('outcome unknown')
    with pytest.raises(RuntimeError,match='outcome unknown'):_run(loop,context)
    context['_synthesize_voice_candidate'].assert_not_called()
