"""Provider wrappers preserve budget stops; real guards, fake Redis/transports."""
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

from app.services.production_spend import SpendBlocked
from app.services import production_spend_runtime as runtime
from test_production_spend_runtime import case
from test_visual_cross_provider_review import _namespace, _review, _run as run_visual
from test_visual_temporal_response_repair import _rows, _run as run_temporal


@pytest.mark.parametrize('provider', ['openai', 'gemini', 'elevenlabs'])
def test_audio_budget_refusal_does_not_try_another_provider(case, monkeypatch, tmp_path, provider):
    from app.services import audio_qc
    path = tmp_path / 'voice.wav'
    path.write_bytes(b'test audio; provider transport must never receive it')
    monkeypatch.setattr(audio_qc, 'settings', SimpleNamespace(
        openai_api_key='test-only' if provider == 'openai' else '',
        gemini_api_key='test-only' if provider != 'elevenlabs' else '',
        elevenlabs_api_key='test-only'))
    sender = Mock()
    monkeypatch.setattr(audio_qc.httpx, 'post', sender)
    uploaded = {'name': 'files/test-audio', 'uri': 'https://generativelanguage.googleapis.com/v1beta/files/test-audio',
                'mime_type': 'audio/wav'}
    upload = Mock(return_value=uploaded)
    cleanup = Mock()
    evidence = Mock()
    monkeypatch.setattr(audio_qc, '_upload_gemini_audio_file', upload)
    monkeypatch.setattr(audio_qc, '_delete_gemini_file', cleanup)
    next_gemini, next_elevenlabs = Mock(), Mock()
    if provider == 'openai':
        monkeypatch.setattr(audio_qc, '_verify_with_gemini', next_gemini)
    if provider != 'elevenlabs':
        monkeypatch.setattr(audio_qc, '_verify_with_elevenlabs', next_elevenlabs)
    before = case[1].snapshot()
    with pytest.raises(SpendBlocked, match='spend_request_not_priced'):
        audio_qc.verify_audio_narration(path, 'Bir örnek anlatım.', provider_evidence_sink=evidence)
    sender.assert_not_called()
    next_gemini.assert_not_called()
    next_elevenlabs.assert_not_called()
    evidence.assert_not_called()
    assert case[1].snapshot() == before
    if provider == 'gemini':
        upload.assert_called_once()
        cleanup.assert_called_once_with(uploaded['name'], 'test-only')
    else:
        upload.assert_not_called()
        cleanup.assert_not_called()


@pytest.mark.parametrize('route', ['omni', 'image'])
def test_scene_pre_dispatch_refusal_is_not_an_attempted_or_uncertain_create(case, monkeypatch, route):
    from app.services import runway
    monkeypatch.setattr(runway, 'settings', SimpleNamespace(
        gemini_api_key='test-only', runwayml_api_secret='test-only', fal_key=''))
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(runway.httpx, 'Client', Mock(return_value=client))
    runway_factory = Mock()
    monkeypatch.setattr(runway, 'RunwayML', runway_factory)
    monkeypatch.setattr(runway, '_runway_gen45_credits_known_insufficient', Mock(return_value=True))
    fal = Mock()
    monkeypatch.setattr(runway, 'generate_fal_video', fal)
    before = case[1].snapshot()
    with pytest.raises(SpendBlocked, match='spend_request_not_priced'):
        runway.generate_scene('A continuous shot.', duration=5 if route == 'omni' else 9,
            aspect_ratio='9:16', prefer_gemini_omni=route == 'omni',
            allow_image_motion=True, image_prompt='One bounded original image.')
    client.post.assert_not_called()
    client.get.assert_not_called()
    fal.assert_not_called()
    if route == 'omni':
        runway_factory.assert_not_called()
    else:
        runway_factory.return_value.text_to_video.create.assert_not_called()
    assert case[1].snapshot() == before


def test_anchor_review_stop_crosses_both_wrappers_and_cleans_candidates(case, monkeypatch, tmp_path):
    from app.services import runway
    source = tmp_path / 'source.mp4'
    original = b'\x00\x00\x00\x18ftyp' + b'0' * 2048
    source.write_bytes(original)
    output = tmp_path / 'anchor.jpg'
    monkeypatch.setattr(runway, 'settings', SimpleNamespace(gemini_api_key='test-only', gemini_model='test-model'))
    monkeypatch.setattr(runway, '_validated_gemini_omni_reference_image_file', Mock())
    def local_media(command, **kwargs):
        if command[0] == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout='{"format":{"duration":"5"}}')
        from pathlib import Path
        Path(command[-1]).write_bytes(b'test reference frame')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runway.subprocess, 'run', local_media)
    sender = Mock()
    def guarded_review(*args, **kwargs):
        return runtime.paid_post(sender,
            'https://generativelanguage.googleapis.com/v1beta/interactions', json={'model': 'unpriced'})
    review = Mock(side_effect=guarded_review)
    monkeypatch.setattr(runway, 'generate_gemini_multimodal_json', review)
    with pytest.raises(SpendBlocked, match='spend_request_not_priced'):
        runway.create_gemini_omni_continuity_reference(source, output)
    review.assert_called_once()
    sender.assert_not_called()
    assert source.read_bytes() == original
    assert not output.exists() and not list(tmp_path.glob('*.candidate-*.jpg'))
    assert case[1].snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('code', ['spend_day_limit', 'spend_request_not_priced', 'spend_request_already_reserved'])
@pytest.mark.parametrize('route', ['temporal', 'consistency'])
def test_visual_rereview_budget_stop_never_becomes_missing_or_rejected_review(monkeypatch, tmp_path, code, route):
    namespace = _namespace()
    error = SpendBlocked(code)
    if route == 'temporal':
        namespace['generate_gemini_multimodal_json'].side_effect = [
            {'reviews': _rows(namespace)}, error]
        run = lambda: run_temporal(namespace, tmp_path, provider='gemini')
    else:
        namespace['generate_gemini_multimodal_json'].return_value = {
            'reviews': [_review(namespace, candidate=1, score=68)]}
        monkeypatch.setattr(runtime, 'paid_response', Mock(side_effect=error))
        run = lambda: run_visual(namespace, tmp_path)
    with pytest.raises(SpendBlocked) as caught:
        run()
    assert caught.value is error
    assert namespace['generate_gemini_multimodal_json'].call_count == (2 if route == 'temporal' else 1)
    namespace['OpenAI'].return_value.responses.create.assert_not_called()
    if route == 'consistency':
        runtime.paid_response.assert_called_once()
