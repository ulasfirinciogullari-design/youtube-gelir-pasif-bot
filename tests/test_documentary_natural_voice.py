"""Actual full synthesis orchestration, with one offline provider response."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import commissioning_longform as longform, narrator_rotation, voice
from test_commissioning_longform import long_case, setup, commissioned, client
from test_production_cash_disabled import dump


def test_natural_timing_requires_real_current_owner_long_authority(long_case, monkeypatch):
    ledger, _, sender, context, entry, previous, root = long_case
    before = dump(ledger.client)
    assert longform.active() is True
    # This fixture uses a synthetic channel; authorization alone cannot opt
    # another channel into the owner's documentary timing profile.
    assert longform.natural_documentary_timing() is False
    assert dump(ledger.client) == before
    sender.assert_not_called()
    from app.services import channel_cadence
    with monkeypatch.context() as other_channel:
        other_channel.setattr(channel_cadence, 'CHANNELS', {context['channel_id']})
        assert longform.natural_documentary_timing() is True
        assert dump(ledger.client) == before
    from app.services import content_plan as plan
    ledger.client.delete(plan.DISPATCH_PREFIX + entry['id'])
    with pytest.raises(plan.ContentPlanError): longform.natural_documentary_timing()


@pytest.mark.parametrize('seconds,words,accepted', [(152.37, 300, True), (180, 360, True),
    (218.8, 420, True), (125.9, 300, False), (219.1, 420, False),
    (152.37, 100, False), (152.37, 600, False)])
def test_complete_continuous_take_keeps_natural_timing_and_still_rejects_thin_dense_or_extreme_voice(
        tmp_path, monkeypatch, seconds, words, accepted):
    real_path = Path
    monkeypatch.setattr(voice, 'Path', lambda p: tmp_path if str(p) == '/tmp' else real_path(p))
    monkeypatch.setattr(longform, 'active', lambda: True)
    monkeypatch.setattr(longform, 'natural_documentary_timing', lambda: True)
    monkeypatch.setattr(narrator_rotation, 'assigned', lambda _: {'voice_id': 'unchanged', 'name': 'Narrator'})
    provider = Mock(return_value=(b'original-paid-audio', {'original_alignment': True}))
    monkeypatch.setattr(voice, 'synthesize_voice_with_timestamps', provider)
    monkeypatch.setattr(voice, '_media_duration', lambda _: seconds)
    monkeypatch.setattr(voice, '_short_preview_audio_edit_plan', lambda *_: {
        'removed_silence_seconds': 0., 'interior_pause_count': 0, 'tail_trimmed': False})
    durations = [seconds / 30] * 30
    monkeypatch.setattr(voice, '_apply_short_preview_audio_edit_plan', lambda *_: (durations, None))
    commands = []
    def normalize(command, **_):
        commands.append(command)
        assert not any('atempo=' in str(value) for value in command)
        real_path(command[-1]).write_bytes(b'normalized-at-original-speed')
    monkeypatch.setattr(voice.subprocess, 'run', normalize)
    texts = ['word ' * (words // 30 + (i < words % 30)) for i in range(30)]
    scenes = [{'narration': text.strip()} for text in texts]
    if not accepted:
        with pytest.raises(voice.VoiceScriptFitError, match='duration or speech-density'):
            voice.synthesize_scene_sequence(scenes, 'natural-documentary', 180, language='tr')
    else:
        result = voice.synthesize_scene_sequence(scenes, 'natural-documentary', 180, language='tr')
        assert result['scene_durations'] == durations
        assert result['tempo_rate'] == 1.0
        assert result['duration_before_fit'] == result['duration_after_fit'] == result['content_target_seconds'] == seconds
        assert Path(result['path']).read_bytes() == b'normalized-at-original-speed'
        assert not result.get('audio_qc') and not result.get('publish_eligible')
    provider.assert_called_once()
    assert len(commands) == 1
