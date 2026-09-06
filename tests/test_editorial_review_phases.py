"""Fixed diagnostic phase only; all review, byte and authorization gates stay shut."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import editorial_routes as routes
from app.services import external_editorial_review as review
from test_external_editorial_review import case, approve, raw, saved, REAL_STORAGE_PROOF, CHANNEL
from test_editorial_routes import case as route_case, post


def fail(*_a, **_k):
    raise RuntimeError('secret credential /signed-object-url raw provider message')


@pytest.mark.parametrize('phase', ['source_snapshot', 'evidence', 'source_contract', 'source_readiness',
    'storage_proof', 'stored_provenance', 'current_state', 'reservation_binding', 'authorization',
    'projection', 'commit', 'existing_receipt'])
def test_creation_reports_only_exact_phase_without_changing_failed_job(case, monkeypatch, phase):
    c = case
    if phase == 'source_snapshot': monkeypatch.setattr(review.ingest, '_redis', fail)
    elif phase == 'evidence': c.pack['version'] = 2
    elif phase == 'source_contract':
        c.source['spec']['workflow'] = 'not-external'
        c.client.set(review.JOB_PREFIX + c.task, raw(c.source))
    elif phase == 'source_readiness':
        c.source['spec']['publish_after_render'] = True
        c.client.set(review.JOB_PREFIX + c.task, raw(c.source))
    elif phase == 'storage_proof': monkeypatch.setattr(review, '_storage_proof', fail)
    elif phase == 'stored_provenance': c.proof['video_sha256'] = '0' * 64
    elif phase == 'current_state':
        def changed(*_a):
            c.client.set(review.UPLOAD_PREFIX + c.task, '{}')
            return deepcopy(c.proof)
        monkeypatch.setattr(review, '_storage_proof', changed)
    elif phase == 'reservation_binding':
        c.client.set(review.ingest.RESERVATION_PREFIX + c.task, raw({'status': 'invalid'}))
    elif phase == 'authorization':
        monkeypatch.setattr(review, '_context', fail)
    elif phase == 'projection': monkeypatch.setattr(review, '_projection', fail)
    elif phase == 'commit':
        original = c.client.pipeline
        def pipeline(*args, **kwargs):
            pipe = original(*args, **kwargs)
            pipe.execute = fail
            return pipe
        monkeypatch.setattr(c.client, 'pipeline', pipeline)
    elif phase == 'existing_receipt':
        approve(c)
        c.pack['frames'][0]['observation'] += ' Different.'
    before = c.client.get(review.JOB_PREFIX + c.task)
    with pytest.raises(review.EditorialReviewError) as caught:
        approve(c)
    assert caught.value.phase == 'editorial_review_' + phase
    assert str(caught.value) == 'external_editorial_review_unavailable_or_invalid'
    assert c.client.get(review.JOB_PREFIX + c.task) == before
    if phase != 'existing_receipt':
        assert not c.client.exists(review.EDITORIAL_RECEIPT_PREFIX + c.task)


@pytest.mark.parametrize('phase', ['stored_download', 'metadata_validation', 'media_structure', 'pcm_decode', 'pcm_binding'])
def test_storage_subphase_is_precise_without_decoder_output(case, monkeypatch, phase):
    c = case
    source, manifest = deepcopy(c.source), c.pack['manifest']
    metadata = {'manifest': manifest, 'descriptor_id': source['result']['external_descriptor_id']}
    blob = review.ingest._json(metadata).encode('utf-8')
    source['result']['metadata_key'] = 'external-masters/v1/' + review._sha(blob) + '/metadata.json'
    def download(key, target):
        if phase == 'stored_download': fail()
        if str(target).endswith('metadata.json'):
            target.write_bytes(b'corrupt' if phase == 'metadata_validation' else blob)
        else:
            target.write_bytes(b'mocked actual download')
    monkeypatch.setattr(review.ingest.storage, 'download_file', download)
    descriptor = {**c.proof, 'descriptor_id': metadata['descriptor_id'], 'media_structure': {
        'frame_count': 900, 'frame_rate': '30/1', 'sample_rate': 48000, 'audio_channels': 2}}
    def validate(*_a):
        if phase == 'media_structure':
            raise review.artifact.ExternalArtifactValidationError('private decoder detail')
        return descriptor
    monkeypatch.setattr(review.artifact, 'validate_staged_external_artifact', validate)
    def decode(command, **kwargs):
        Path(command[-1]).write_bytes(b'wrong full PCM')
        return SimpleNamespace(returncode=1 if phase == 'pcm_decode' else 0, stderr=b'private codec output')
    monkeypatch.setattr(review.subprocess, 'run', decode)
    binding = {'sample_rate': 48000, 'channels': 2, 'sample_frames': 1440000, 'pcm_sha256': '2' * 64}
    with pytest.raises(Exception) as caught:
        REAL_STORAGE_PROOF(source, manifest, binding)
    assert caught.value.editorial_phase == 'editorial_review_' + phase
    # The helper keeps its exception type; only the owner-facing outer call
    # converts it to a redacted EditorialReviewError and allowlisted phase.
    if phase == 'media_structure':
        assert isinstance(caught.value, review.artifact.ExternalArtifactValidationError)
    def storage(*_a):
        raise caught.value
    monkeypatch.setattr(review, '_storage_proof', storage)
    with pytest.raises(review.EditorialReviewError) as outer:
        approve(c)
    assert outer.value.phase == 'editorial_review_' + phase
    assert 'private' not in str(outer.value) and 'PCM' not in str(outer.value)


@pytest.mark.parametrize('phase', sorted(review.EDITORIAL_REVIEW_FAILURE_CODES))
def test_owner_route_exposes_only_fixed_phase_not_raw_exception(route_case, monkeypatch, phase):
    def unavailable(*_a):
        raise routes.EditorialReviewError('credential=private /signed-url provider-output', phase=phase)
    monkeypatch.setattr(routes, '_review_and_queue', unavailable)
    response = post(route_case, json={'evidence_pack': {}})
    assert response.status_code == 409 and response.json() == {'detail': phase}


@pytest.mark.parametrize('phase', [None, 'raw-private-value', {}, ['editorial_review_pcm_binding']])
def test_unknown_or_malformed_phase_remains_generic(route_case, monkeypatch, phase):
    error = routes.EditorialReviewError('secret')
    error.phase = phase
    def unavailable(*_a):
        raise error
    monkeypatch.setattr(routes, '_review_and_queue', unavailable)
    response = post(route_case, json={'evidence_pack': {}})
    assert response.status_code == 409 and response.json() == {'detail': 'editorial_review_not_eligible'}
