"""Exact same-runtime reference equality, not a tolerant old-PCM comparison."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services import external_editorial_review as review
from test_external_editorial_review import case, raw, saved, REAL_STORAGE_PROOF
from test_external_artifact_import import _manifest, _captions


@pytest.fixture
def pair(monkeypatch):
    reference = b'old provider-bound reference' * 80
    current = b'current illustrative master' * 80
    captions = b'exact captions'
    sha = review._sha
    manifest = {'files': {'video': {'sha256': sha(current), 'size': len(current)},
                          'captions': {'sha256': sha(captions), 'size': len(captions)}}}
    descriptor = {'descriptor_id': 'a' * 64, 'video_sha256': sha(current), 'captions_sha256': sha(captions),
                  'manifest_sha256': review._digest(manifest), 'media_structure': {
                      'frame_count': 900, 'frame_rate': '30/1', 'sample_rate': 48000, 'audio_channels': 2,
                      'audio_duration_seconds': 30.0, 'video_duration_seconds': 30.0}}
    metadata = review.ingest._json({'manifest': manifest, 'descriptor_id': descriptor['descriptor_id']}).encode()
    result = {'external_descriptor_id': descriptor['descriptor_id'],
              'video_key': 'external-masters/v1/' + sha(current) + '/master.mp4',
              'caption_key': 'external-masters/v1/' + sha(captions) + '/captions.srt',
              'metadata_key': 'external-masters/v1/' + sha(metadata) + '/metadata.json'}
    objects = {result['video_key']: current, result['caption_key']: captions, result['metadata_key']: metadata}
    binding = {'video_sha256': sha(reference), 'video_size': len(reference), 'sample_rate': 48000,
               'channels': 2, 'duration_seconds': 30.0, 'sample_frames': 1440000, 'pcm_sha256': 'f' * 64}
    p = SimpleNamespace(reference=reference, source={'result': result}, manifest=manifest,
                        descriptor=descriptor, binding=binding, pcm=b'\x01\x00\x02\x00' * 1440000,
                        reference_pcm=None, calls=[], changed=None)
    monkeypatch.setattr(review.ingest.storage, 'download_file', lambda key, path: path.write_bytes(objects[key]))
    monkeypatch.setattr(review.artifact, 'validate_staged_external_artifact', lambda *_a: deepcopy(descriptor))
    monkeypatch.setattr(review.artifact, '_probe_mp4', lambda *_a: deepcopy(descriptor['media_structure']))
    def decode(command, **kwargs):
        p.calls.append((list(command), dict(kwargs)))
        video, target = Path(command[command.index('-i') + 1]), Path(command[-1])
        data = p.reference_pcm if video.name == 'reference.mp4' and p.reference_pcm is not None else p.pcm
        target.write_bytes(data)
        if p.changed:
            p.changed(video)
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(review.subprocess, 'run', decode)
    p.run = lambda **kwargs: REAL_STORAGE_PROOF(p.source, p.manifest, p.binding, **kwargs)
    return p


def test_exact_pair_has_distinct_truthful_server_and_retained_hashes(pair):
    p = pair
    old = deepcopy(p.binding)
    proof = p.run(reference_video=p.reference)
    assert proof['pcm_verification_mode'] == 'same_runtime_reference_equivalence'
    assert proof['pcm_sha256'] == proof['reference_pcm_sha256'] == review._sha(p.pcm)
    assert proof['retained_pcm_sha256'] == 'f' * 64 and proof['retained_pcm_exact_match'] is False
    assert proof['reference_and_current_server_pcm_exact_match'] is True
    assert proof['reference_video_sha256'] == p.binding['video_sha256']
    assert proof['server_sample_frames'] == proof['retained_sample_frames'] == 1440000
    assert p.binding == old and 'qa_approved' not in proof and 'word_timing_verified' not in proof
    assert len(p.calls) == 2
    first, second = [call[0] for call in p.calls]
    def shape(command):
        command = list(command)
        command[command.index('-i') + 1] = '<master>'
        command[-1] = '<pcm>'
        return command
    assert shape(first) == shape(second)
    assert '-af' not in first and '-t' not in first and '-ss' not in first


def test_server_padding_frame_count_is_reported_not_rewritten_into_old_binding(pair):
    p = pair
    p.pcm += b'\x00\x00\x00\x00' * 768
    proof = p.run(reference_video=p.reference)
    assert proof['server_sample_frames'] == 1440768
    assert proof['retained_sample_frames'] == p.binding['sample_frames'] == 1440000


def test_default_without_reference_still_requires_original_exact_pcm(pair):
    p = pair
    with pytest.raises(review.EditorialReviewError): p.run()
    assert len(p.calls) == 1
    p.binding['pcm_sha256'] = review._sha(p.pcm)
    proof = p.run()
    assert proof['pcm_sha256'] == p.binding['pcm_sha256']
    assert 'pcm_verification_mode' not in proof  # Existing default proof schema.
    p.binding['sample_frames'] -= 1
    with pytest.raises(review.EditorialReviewError): p.run()


@pytest.mark.parametrize('damage', ['different_payload', 'different_size', 'wrong_type', 'oversize'])
def test_reference_must_be_exact_provider_bound_bytes_before_decode(pair, damage):
    p = pair
    value = p.reference
    if damage == 'different_payload': value = b'x' * len(value)
    elif damage == 'different_size': value += b'x'
    elif damage == 'wrong_type': value = bytearray(value)
    else: value = b'x' * (review.MAX_REFERENCE_VIDEO_BYTES + 1)
    with pytest.raises(review.EditorialReviewError) as caught: p.run(reference_video=value)
    assert caught.value.editorial_phase == 'editorial_review_reference_binding'
    assert not p.calls


@pytest.mark.parametrize('damage', ['one_sample', 'size', 'channels', 'sample_rate', 'duration',
                                   'reference_changed', 'source_changed'])
def test_no_tolerance_or_reference_swap_can_authorize_pair(pair, damage):
    p = pair
    if damage == 'one_sample': p.reference_pcm = b'\x00' + p.pcm[1:]
    elif damage == 'size': p.reference_pcm = p.pcm[:-4]
    elif damage == 'channels': p.descriptor['media_structure']['audio_channels'] = 1
    elif damage == 'sample_rate': p.descriptor['media_structure']['sample_rate'] = 44100
    elif damage == 'duration': p.descriptor['media_structure']['audio_duration_seconds'] = 29.9
    elif damage == 'reference_changed':
        p.changed = lambda video: video.write_bytes(b'changed') if video.name == 'reference.mp4' else None
    else:
        p.changed = lambda video: video.write_bytes(b'changed') if video.name == 'master.mp4' else None
    with pytest.raises(Exception): p.run(reference_video=p.reference)


def _bind_case_reference(c, value):
    update = {'video_sha256': review._sha(value), 'video_size': len(value)}
    asr = json.loads(c.pack['asr_provider_evidence_json'])
    asr['binding'].update(update)
    c.pack['asr_provider_evidence_json'] = raw(asr)
    for key in ('asr_attempt_json', 'prosody_attempt_json'):
        item = json.loads(c.pack[key]); item.update(update); c.pack[key] = raw(item)
    prosody = json.loads(c.pack['prosody_result_json'])
    prosody['binding'].update(update)
    prosody['result']['preflight']['provider_evidence_sha256'] = review._sha(c.pack['asr_provider_evidence_json'].encode())
    c.pack['prosody_result_json'] = raw(prosody)


def test_reference_is_validated_before_idempotent_receipt_return(case, monkeypatch):
    c = case
    reference = b'provider-bound-reference' * 100
    _bind_case_reference(c, reference)
    original = deepcopy(c.pack)
    calls = []
    def storage(source, manifest, binding, **kwargs):
        calls.append(kwargs)
        return {**c.proof, 'pcm_verification_mode': 'same_runtime_reference_equivalence'}
    monkeypatch.setattr(review, '_storage_proof', storage)
    receipt = review.create_editorial_review(c.task, c.pack, reference_video=reference)
    assert len(calls) == 1 and calls[0] == {'reference_video': reference}
    assert c.pack == original and receipt['evidence'] == original
    assert saved(c)['result']['word_timing_verified'] is False
    assert receipt['automated_qa_approved'] is False
    assert review.create_editorial_review(c.task, c.pack) == receipt  # Stored immutable proof is still checked.
    assert review.create_editorial_review(c.task, c.pack, reference_video=reference) == receipt
    before = c.client.get(review.JOB_PREFIX + c.task)
    with pytest.raises(review.EditorialReviewError) as caught:
        review.create_editorial_review(c.task, c.pack, reference_video=b'x' * len(reference))
    assert caught.value.phase == 'editorial_review_reference_binding'
    assert len(calls) == 1 and c.client.get(review.JOB_PREFIX + c.task) == before


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg unavailable')
def test_real_complete_decodes_of_bound_reference_and_remux_are_byte_identical(tmp_path, monkeypatch):
    reference, current, captions = tmp_path / 'reference.mp4', tmp_path / 'current.mp4', tmp_path / 'captions.srt'
    subprocess.run(['ffmpeg', '-n', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=navy:s=720x1280:r=30:d=30', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=30',
        '-ac', '2', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', str(reference)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    subprocess.run(['ffmpeg', '-n', '-nostdin', '-v', 'error', '-i', str(reference), '-map', '0', '-c', 'copy',
        '-metadata', 'comment=distinct-container-same-narration', str(current)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    assert review._sha(reference.read_bytes()) != review._sha(current.read_bytes())
    captions.write_bytes(b'placeholder')
    manifest = _manifest(current, captions)
    captions.write_bytes(_captions(manifest['scenes']))
    manifest['files']['captions'].update(size=captions.stat().st_size, sha256=review._sha(captions.read_bytes()))
    descriptor = review.artifact.validate_staged_external_artifact(tmp_path, current, captions, manifest)
    metadata = review.ingest._json({key: value for key, value in descriptor.items() if key != 'files'}).encode()
    result = {'external_descriptor_id': descriptor['descriptor_id'],
        'video_key': 'external-masters/v1/' + descriptor['video_sha256'] + '/master.mp4',
        'caption_key': 'external-masters/v1/' + descriptor['captions_sha256'] + '/captions.srt',
        'metadata_key': 'external-masters/v1/' + review._sha(metadata) + '/metadata.json'}
    objects = {result['video_key']: current.read_bytes(), result['caption_key']: captions.read_bytes(),
               result['metadata_key']: metadata}
    monkeypatch.setattr(review.ingest.storage, 'download_file', lambda key, target: target.write_bytes(objects[key]))
    # Simulate a different retained decoder's PCM hash, while cryptographically
    # binding the actual original container; no retained record is modified.
    binding = {'video_sha256': review._sha(reference.read_bytes()), 'video_size': reference.stat().st_size,
               'sample_rate': 48000, 'channels': 2, 'sample_frames': 1440000, 'duration_seconds': 30.0,
               'pcm_sha256': 'f' * 64}
    proof = REAL_STORAGE_PROOF({'result': result}, manifest, binding, reference_video=reference.read_bytes())
    assert proof['reference_pcm_sha256'] == proof['pcm_sha256'] and proof['retained_pcm_exact_match'] is False
    assert proof['server_sample_frames'] >= 1440000
    with pytest.raises(review.EditorialReviewError): REAL_STORAGE_PROOF({'result': result}, manifest, binding)
