"""Owner-review evidence and immutable publication checks; no live service calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import fakeredis
import pytest

from app.services import external_editorial_review as review
from test_external_artifact_import import _manifest, _captions

REAL_STORAGE_PROOF = review._storage_proof


NARRATION = [
    'Why pay Costco before anything lands in your shopping cart?',
    'Your membership card buys access, not groceries or guaranteed shopping savings.',
    'Inside, limited selection and pallet displays help keep operating costs down.',
    'That efficiency helps Costco operate profitably while keeping merchandise margins low.',
    'Costco earns merchandise gross margin too, not just membership fee revenue.',
    'Annual access pays off only when savings outweigh your membership fee.',
]
CHANNEL, CONNECTION, REVISION = 'UCeditorial12345678', 'connection-12345678', 'revision-12345678'


def raw(value):
    return review.ingest._json(value) + '\n'


def replace_json(pack, key, update):
    value = json.loads(pack[key]); update(value); pack[key] = raw(value)


@pytest.fixture
def case(tmp_path, monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(review.ingest, '_redis', lambda: client)
    video, captions = tmp_path / 'video.mp4', tmp_path / 'captions.srt'
    video.write_bytes(b'fixture-video' * 1000); captions.write_bytes(b'fixture-captions')
    manifest = _manifest(video, captions)
    for row, text in zip(manifest['scenes'], NARRATION):
        row['narration'] = text
    task = str(uuid4())
    provenance = {k: v for k, v in (
        ('video_sha256', manifest['files']['video']['sha256']),
        ('captions_sha256', manifest['files']['captions']['sha256']),
        ('manifest_sha256', review._digest(manifest)))}
    descriptor_id = review._digest(provenance)
    result = {'video_key': 'external-masters/v1/' + provenance['video_sha256'] + '/master.mp4',
        'caption_key': 'external-masters/v1/' + provenance['captions_sha256'] + '/captions.srt',
        'metadata_key': 'external-masters/v1/' + 'a' * 64 + '/metadata.json',
        'external_descriptor_id': descriptor_id, 'external_provenance': provenance,
        'quality_disposition': 'manual_qa_preview', 'manual_qa_required': True, 'qa_approved': False,
        'audio_transcription_verified': False, 'source_evidence_verified': False, 'publish_eligible': False,
        'contains_synthetic_media': True, 'new_media_generated': False, 'media_generation_authorized': False}
    source = {'task_id': task, 'kind': 'render', 'state': 'SUCCESS', 'parent_id': None, 'error': None,
        'spec': {'workflow': 'external_import', 'mode': 'production', 'format': 'shorts', 'language': 'en',
                 'duration_minutes': .5, 'production_scheduled': False, 'publish_after_render': False,
                 'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
                 'production_profile_revision': REVISION}, 'result': result}
    profile = {'channel_id': CHANNEL, 'profile_revision': REVISION, 'languages': ['en'],
               'auto_publish': True, 'release_mode': 'public', 'series_id': '', 'series_total': 0}
    client.set(review.JOB_PREFIX + task, raw(source))
    client.set(review.ingest.RESERVATION_PREFIX + task, raw({'status': 'complete', 'task_id': task,
        'descriptor_id': descriptor_id, 'job_sha256': review._digest(source)}))
    client.set(review.ingest.PROFILE_PREFIX + CHANNEL, raw(profile))
    client.set(review.ingest.CHANNEL_PREFIX + CHANNEL, raw({'id': CHANNEL, 'connection_id': CONNECTION,
                                                          'requires_reconnect': False, 'title': 'Business'}))
    client.set(review.ingest.CREDENTIAL_PREFIX + CHANNEL, 'test-encrypted-credential')
    client.set(review.ingest.AUTH_EPOCH_KEY, '15')
    client.sadd(review.ingest.CHANNEL_INDEX_KEY, CHANNEL)
    client.set('old-failed-job', 'unchanged'); client.set('old-series-cursor', '2')
    binding = {'version': 1, **provenance, 'video_sha256': '1' * 64, 'video_size': 2000000,
        'pcm_sha256': '2' * 64, 'wav_sha256': '3' * 64, 'sample_rate': 48000, 'channels': 2,
        'sample_width_bytes': 2, 'sample_frames': 1440000, 'duration_seconds': 30,
        'conversion': 'decode_complete_master_audio_to_pcm_s16le_no_other_filters', 'review_code_sha256': {'runner': '4' * 64}}
    words = [{'word': word, 'start': i * .4, 'end': i * .4 + .3}
             for i, word in enumerate(review._words(' '.join(NARRATION)))]
    for index in (43, 51):
        words[index]['end'] = words[index]['start']
    asr = {'binding': binding, 'unapproved_provider_evidence': {'language': 'en', 'model': 'whisper-1',
        'provider': 'openai', 'payload': {'language': 'english', 'text': ' '.join(NARRATION), 'words': words}}}
    asr_raw = raw(asr)
    attempt = {'state': 'reserved_before_request', 'maximum_paid_requests': 1,
               'started_at': '2026-09-06T14:00:00+00:00', **binding}
    prosody = {'binding': binding, 'component': 'prosody', 'started_at': attempt['started_at'],
        'completed_at': '2026-09-06T14:01:00+00:00', 'qa_approved': False, 'publish_eligible': False,
        'media_generation_authorized': False, 'result': {'available': True, 'pass': True, 'provider': 'gemini',
            'diagnostic_only': True, 'review_attempts': 1, 'audio_sha256': binding['wav_sha256'], 'issues': [],
            'diagnostic_status': 'audible_prosody_component_pass', 'asr_approved': False,
            'combined_audio_approved': False, 'word_timing_verified': False, 'qa_approved': False,
            'publish_eligible': False, 'scores': {'pronunciation': 95, 'naturalness': 92, 'pacing': 94,
                'sentence_flow': 93, 'emphasis': 92, 'roboticness': 10},
            'preflight': {'provider_evidence_sha256': review._sha(asr_raw.encode()),
                'text_exact': True, 'text_comparison_score': 100, 'word_timing_verified': False, 'asr_approved': False}}}
    urls = [s['url'] for s in manifest['sources']]
    pack = {'version': 1, 'manifest': manifest, 'asr_provider_evidence_json': asr_raw,
        'asr_attempt_json': raw({**attempt, 'component': 'asr', 'provider': 'openai', 'model': 'whisper-1'}),
        'prosody_attempt_json': raw({**attempt, 'component': 'prosody', 'provider': 'gemini', 'model': 'gemini-test'}),
        'prosody_result_json': raw(prosody), 'frames': [{'frame_index': i * 150 + 60, 'sha256': str(i) * 64,
            'observation': 'Actual encoded frame shows clear unclipped explanatory diagram.'} for i in range(6)],
        'scenes': [{'index': i, 'frame_indices': [i * 150 + 60], 'visual_observation': 'Diagram clearly illustrates the claim.',
            'alignment_observation': 'Scene-level captions and narration agree; word timing is not certified.',
            'source_urls': urls, 'claim_note': 'Read primary source supports the stated business explanation.'} for i in range(6)],
        'sources': [{'url': url, 'content_sha256': 'a' * 64, 'evidence': 'Primary source evidence retained.',
                     'observation': 'Editor opened and read the cited primary page.'} for url in urls],
        'rights_basis': [{'asset_type': kind, 'basis': 'owner_authorized_export', 'evidence_ref': 'b' * 64,
                         'observation': 'Owner authorized use of exported material; original vendor remains unknown.'}
                        for kind in ('graphics', 'fonts', 'voice')],
        'limitations': {**review._LIMITATIONS, 'notes': ['Two word timestamps are unavailable.']}, 'blocking_issues': [],
        'publish_metadata': {'title': 'How membership works', 'description': 'Illustrative business model explanation.',
            'tags': ['Membership', 'Business'], 'hashtags': ['Business', 'Shorts'], 'sources': urls,
            'contains_synthetic_media': True}}
    proof = {**provenance, 'pcm_sha256': binding['pcm_sha256'], 'server_verified_stored_bytes_and_pcm': True,
             'media_structure': {'frame_count': 900, 'frame_rate': '30/1'}}
    calls = []
    def storage_proof(*args):
        calls.append(args)
        return deepcopy(proof)
    monkeypatch.setattr(review, '_storage_proof', storage_proof)
    return SimpleNamespace(client=client, task=task, source=source, pack=pack, proof=proof, calls=calls)


def saved(c):
    return json.loads(c.client.get(review.JOB_PREFIX + c.task))


def approve(c):
    return review.create_editorial_review(c.task, c.pack)


def change(client, key, update):
    value = json.loads(client.get(key)); update(value); client.set(key, raw(value))


def plan(c):
    source = saved(c)
    value = {'source_task_id': c.task, 'target_channel_id': CHANNEL, 'profile_revision': REVISION,
             'contains_synthetic_media': True, 'series': None, 'title': 'Server-authored title',
             'quality_snapshot': {k: source['result'][k] for k in
                 ('quality_disposition', 'manual_qa_required', 'editorial_review_id', 'editorial_review_sha256')}}
    c.client.set(review.UPLOAD_PREFIX + c.task, raw({'publish_plan': value}))
    return value


def test_real_editorial_receipt_does_not_claim_generated_qa_or_repair_series(case):
    c = case; original = deepcopy(c.source); receipt = approve(c); updated = saved(c)
    assert receipt['reviewer_type'] == 'delegated_editorial_agent' and receipt['unverified_word_indices'] == [43, 51]
    assert receipt['evidence']['asr_provider_evidence_json'] == c.pack['asr_provider_evidence_json']
    assert receipt['automated_qa_approved'] is receipt['word_timing_verified'] is False
    assert updated['result']['quality_disposition'] == 'editorial_review_pass'
    assert updated['result']['qa_approved'] is updated['result']['audio_transcription_verified'] is False
    assert updated['spec']['production_scheduled'] is False and updated['spec']['publish_after_render'] is True
    assert c.client.get('old-failed-job') == 'unchanged' and c.client.get('old-series-cursor') == '2'
    assert c.source == original and not review.automated_quality_approved(updated)
    assert review.publication_quality_approved(updated) and review.validate_editorial_publication(updated) == receipt
    assert receipt['publish_metadata_sha256'] == review._digest(updated['result']['publish_metadata'])


def test_same_review_is_idempotent_without_new_storage_reads(case):
    first = approve(case)
    assert approve(case) == first and len(case.calls) == 1
    case.pack['frames'][0]['observation'] += ' Changed.'
    with pytest.raises(review.EditorialReviewError): approve(case)


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(qa_approved=True), lambda p: p.update(blocking_issues=['clipped label']),
    lambda p: p['limitations'].update(word_timing='verified'),
    lambda p: p['limitations'].update(human_listened=True), lambda p: p['frames'].clear(),
    lambda p: p['frames'][0].update(sha256='bad'), lambda p: p['frames'][1].update(frame_index=p['frames'][0]['frame_index']),
    lambda p: p['scenes'][0].update(frame_indices=[850]), lambda p: p['scenes'][0].update(claim_note=''),
    lambda p: p['scenes'][0].update(index=True), lambda p: p['sources'].pop(),
    lambda p: p['sources'][0].update(url='https://unreviewed.example/'),
    lambda p: p['sources'][0].update(content_sha256='unknown'), lambda p: p['rights_basis'].pop(),
    lambda p: p['rights_basis'][0].update(basis='unknown'),
    lambda p: p['rights_basis'][0].update(observation='api_key=sk-should-never-be-output'),
    lambda p: p['manifest']['scenes'][0].update(narration='The groceries are free.'),
    lambda p: p['publish_metadata'].update(contains_synthetic_media=False),
    lambda p: p['publish_metadata'].update(series_number=2),
    lambda p: p['publish_metadata'].update(title='Episode 2/4'),
    lambda p: p['publish_metadata'].update(title='Поддельное название'),
    lambda p: p['publish_metadata'].update(sources=['https://unreviewed.example/']),
])
def test_missing_unknown_forged_or_unresolved_evidence_fails_closed_without_writes(case, mutate):
    before = case.client.get(review.JOB_PREFIX + case.task); mutate(case.pack)
    with pytest.raises(review.EditorialReviewError, match='external_editorial_review_unavailable_or_invalid') as failure:
        approve(case)
    assert 'sk-' not in str(failure.value) and not case.calls
    assert case.client.get(review.JOB_PREFIX + case.task) == before
    assert not case.client.exists(review.EDITORIAL_RECEIPT_PREFIX + case.task)


@pytest.mark.parametrize('key,update', [
    ('asr_provider_evidence_json', lambda x: x['unapproved_provider_evidence']['payload']['words'].pop()),
    ('asr_provider_evidence_json', lambda x: x['unapproved_provider_evidence']['payload']['words'][2].update(word='Tesco')),
    ('asr_provider_evidence_json', lambda x: x['unapproved_provider_evidence']['payload']['words'][0].update(start=-1)),
    ('asr_provider_evidence_json', lambda x: x['unapproved_provider_evidence']['payload'].update(text='False narration')),
    ('asr_attempt_json', lambda x: x.update(pcm_sha256='9' * 64)),
    ('prosody_attempt_json', lambda x: x.update(state='not_run')),
    ('prosody_result_json', lambda x: x['binding'].update(pcm_sha256='9' * 64)),
    ('prosody_result_json', lambda x: x['result'].update(pass_=False, **{'pass': False})),
    ('prosody_result_json', lambda x: x['result'].update(issues=['Bad pronunciation'])),
    ('prosody_result_json', lambda x: x['result'].update(word_timing_verified=True)),
    ('prosody_result_json', lambda x: x['result']['scores'].update(pronunciation=60)),
    ('prosody_result_json', lambda x: x['result']['scores'].update(roboticness=95)),
    ('prosody_result_json', lambda x: x['result']['preflight'].update(provider_evidence_sha256='9' * 64)),
])
def test_retained_provider_records_must_actually_agree(case, key, update):
    replace_json(case.pack, key, update)
    with pytest.raises(review.EditorialReviewError): approve(case)
    assert not case.calls


@pytest.mark.parametrize('target,update', [
    ('source', lambda x: x.update(parent_id=str(uuid4()))),
    ('source', lambda x: x['spec'].update(production_scheduled=True)),
    ('source', lambda x: x['spec'].update(production_topic_index=2)),
    ('source', lambda x: x['result'].update(qa_approved=True)),
    ('reservation', lambda x: x.update(status='uncertain')),
    ('reservation', lambda x: x.update(job_sha256='9' * 64)),
    ('profile', lambda x: x.update(profile_revision='changed-revision')),
    ('profile', lambda x: x.update(auto_publish=False)),
    ('channel', lambda x: x.update(connection_id='changed-connection')),
    ('channel', lambda x: x.update(requires_reconnect=True)),
])
def test_unrelated_failed_or_stale_import_cannot_become_editorial_approved(case, target, update):
    keys = {'source': review.JOB_PREFIX + case.task, 'reservation': review.ingest.RESERVATION_PREFIX + case.task,
            'profile': review.ingest.PROFILE_PREFIX + CHANNEL, 'channel': review.ingest.CHANNEL_PREFIX + CHANNEL}
    change(case.client, keys[target], update)
    with pytest.raises(review.EditorialReviewError): approve(case)
    assert not case.client.exists(review.EDITORIAL_RECEIPT_PREFIX + case.task)


@pytest.mark.parametrize('target,update', [
    ('job', lambda x: x['result'].update(video_key='different/master.mp4')),
    ('job', lambda x: x['result'].update(expected_video_sha256='9' * 64)),
    ('job', lambda x: x['result']['publish_metadata'].update(title='Unreviewed title')),
    ('job', lambda x: x['result'].update(editorial_review_sha256='9' * 64)),
    ('profile', lambda x: x.update(profile_revision='changed-revision')),
    ('profile', lambda x: x.update(auto_publish=False)),
    ('channel', lambda x: x.update(connection_id='changed-connection')),
    ('channel', lambda x: x.update(title='Changed identity')),
    ('receipt', lambda x: x.update(automated_qa_approved=True)),
])
def test_publication_rechecks_current_private_receipt_and_all_bindings(case, target, update):
    approve(case); old = saved(case)
    keys = {'job': review.JOB_PREFIX + case.task, 'receipt': review.EDITORIAL_RECEIPT_PREFIX + case.task,
            'profile': review.ingest.PROFILE_PREFIX + CHANNEL, 'channel': review.ingest.CHANNEL_PREFIX + CHANNEL}
    change(case.client, keys[target], update)
    assert not review.publication_quality_approved(old)
    with pytest.raises(review.EditorialReviewError): review.validate_editorial_publication(saved(case))


@pytest.mark.parametrize('kind', ['epoch', 'credential', 'membership', 'receipt_missing'])
def test_revoked_or_missing_authority_blocks_even_unchanged_artifact(case, kind):
    approve(case); source = saved(case)
    if kind == 'epoch': case.client.set(review.ingest.AUTH_EPOCH_KEY, '16')
    elif kind == 'credential': case.client.set(review.ingest.CREDENTIAL_PREFIX + CHANNEL, 'rotated-cipher')
    elif kind == 'membership': case.client.srem(review.ingest.CHANNEL_INDEX_KEY, CHANNEL)
    else: case.client.delete(review.EDITORIAL_RECEIPT_PREFIX + case.task)
    assert not review.publication_quality_approved(source)


def test_publisher_progress_and_analytics_refresh_do_not_invalidate_review(case):
    approve(case)
    change(case.client, review.JOB_PREFIX + case.task, lambda x: x['result'].update(youtube={'video_id': 'actual-public-id'}))
    change(case.client, review.ingest.CHANNEL_PREFIX + CHANNEL, lambda x: x.update(statistics={'views': 1500}))
    assert review.publication_quality_approved(saved(case))


def test_frozen_plan_must_be_exact_server_reserved_plan(case):
    approve(case); frozen = plan(case)
    assert review.validate_editorial_publication(saved(case), frozen)['receipt_id'] == case.task
    frozen['title'] = 'Caller changed metadata'
    with pytest.raises(review.EditorialReviewError): review.validate_editorial_publication(saved(case), frozen)


def test_real_current_series_assignment_is_read_only_and_not_retry_lineage(case):
    change(case.client, review.ingest.PROFILE_PREFIX + CHANNEL,
           lambda x: x.update(series_id='business-v1', series_name='Business', series_total=4))
    approve(case); frozen = plan(case)
    frozen['series'] = {'id': 'business-v1', 'name': 'Business', 'number': 2, 'total': 4}
    case.client.set(review.UPLOAD_PREFIX + case.task, raw({'publish_plan': frozen}))
    assignment, counter = review._series_keys(saved(case), frozen)
    case.client.set(assignment, '2'); case.client.set(counter, '2')
    assert review.validate_editorial_publication(saved(case), frozen)['receipt_id'] == case.task
    assert case.client.get(counter) == '2' and saved(case)['parent_id'] is None
    case.client.set(assignment, '3')
    with pytest.raises(review.EditorialReviewError): review.validate_editorial_publication(saved(case), frozen)


@pytest.mark.parametrize('marker', ['external_descriptor_id', 'external_provenance', 'editorial_review_id',
    'editorial_review_sha256', *review._EXPECTED])
def test_stripped_external_workflow_cannot_fall_back_to_generated_boolean(case, marker):
    source = deepcopy(case.source); source['spec'] = {}
    source['result'] = {'video_key': 'v.mp4', 'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False, marker: None}
    assert review.automated_quality_approved(source)
    assert not review.publication_quality_approved(source)


def test_legacy_generated_path_remains_unchanged():
    source = {'state': 'SUCCESS', 'kind': 'render', 'spec': {},
              'result': {'video_key': 'v.mp4', 'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}}
    assert review.publication_quality_approved(source) and review.automated_quality_approved(source)


def test_no_preexisting_upload_can_be_reauthorized(case):
    case.client.set(review.UPLOAD_PREFIX + case.task, raw({'state': 'uncertain'}))
    with pytest.raises(review.EditorialReviewError): approve(case)


def test_cas_race_rolls_back_both_review_and_job(case, monkeypatch):
    old_pipeline = case.client.pipeline
    def pipeline():
        pipe = old_pipeline(); old_execute = pipe.execute
        def execute(*args, **kwargs):
            case.client.set(review.ingest.AUTH_EPOCH_KEY, '16')
            return old_execute(*args, **kwargs)
        pipe.execute = execute
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(review.EditorialReviewError): approve(case)
    assert not case.client.exists(review.EDITORIAL_RECEIPT_PREFIX + case.task)
    assert saved(case)['result']['quality_disposition'] == 'manual_qa_preview'


def test_lost_success_reply_does_not_repeat_or_overwrite_receipt(case, monkeypatch):
    original = case.client.pipeline
    def pipeline():
        pipe = original(); execute = pipe.execute
        def uncertain(*args, **kwargs):
            execute(*args, **kwargs); raise OSError('lost reply')
        pipe.execute = uncertain; return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(review.EditorialReviewError): approve(case)
    monkeypatch.setattr(case.client, 'pipeline', original)
    assert approve(case)['receipt_id'] == case.task and len(case.calls) == 1


def test_oversized_or_duplicate_key_evidence_is_rejected(case):
    for pack in ('{"version":1,"version":1}', {'oversized': 'x' * review.MAX_EVIDENCE_BYTES}):
        with pytest.raises(review.EditorialReviewError): review.create_editorial_review(case.task, pack)
    assert not case.calls


def test_readonly_publication_cas_rejects_revocation_during_validation(case, monkeypatch):
    approve(case)
    original = case.client.pipeline
    def pipeline():
        pipe = original(); execute = pipe.execute
        def revoke(*args, **kwargs):
            case.client.set(review.ingest.AUTH_EPOCH_KEY, '16')
            return execute(*args, **kwargs)
        pipe.execute = revoke; return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    assert not review.publication_quality_approved(saved(case))


def test_real_local_master_decode_verifies_complete_pcm_and_storage_bytes(case, tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local FFmpeg tools unavailable')
    video, captions = tmp_path / 'real.mp4', tmp_path / 'real.srt'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'color=c=navy:s=720x1280:r=30:d=30', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=30', '-ac', '2', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-n', str(video)],
        check=True, capture_output=True, timeout=90)
    captions.write_bytes(b'placeholder')
    manifest = _manifest(video, captions)
    for row, text in zip(manifest['scenes'], NARRATION): row['narration'] = text
    captions.write_bytes(_captions(manifest['scenes']))
    manifest['files']['captions'].update(size=captions.stat().st_size,
                                      sha256=hashlib.sha256(captions.read_bytes()).hexdigest())
    descriptor = review.artifact.validate_staged_external_artifact(tmp_path, video, captions, manifest)
    pcm = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(video),
        '-map', '0:a:0', '-vn', '-sn', '-dn', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'],
        check=True, capture_output=True, timeout=60).stdout
    binding = {'sample_rate': 48000, 'channels': 2, 'sample_frames': len(pcm) // 4,
               'pcm_sha256': hashlib.sha256(pcm).hexdigest()}
    metadata = raw({key: value for key, value in descriptor.items() if key != 'files'}).encode()
    source = deepcopy(case.source)
    for name, field, suffix in (('video', 'video_key', 'master.mp4'), ('captions', 'caption_key', 'captions.srt')):
        source['result'][field] = 'external-masters/v1/' + manifest['files'][name]['sha256'] + '/' + suffix
    source['result']['metadata_key'] = 'external-masters/v1/' + hashlib.sha256(metadata).hexdigest() + '/metadata.json'
    source['result']['external_descriptor_id'] = descriptor['descriptor_id']
    objects = {source['result']['video_key']: video.read_bytes(), source['result']['caption_key']: captions.read_bytes(),
               source['result']['metadata_key']: metadata}
    def download(key, destination):
        Path(destination).write_bytes(objects[key])
    monkeypatch.setattr(review.ingest.storage, 'download_file', download)
    proof = REAL_STORAGE_PROOF(source, manifest, binding)
    assert proof['pcm_sha256'] == binding['pcm_sha256'] and proof['server_verified_stored_bytes_and_pcm'] is True
    assert proof['media_structure']['frame_count'] == 900
    with pytest.raises(review.EditorialReviewError):
        REAL_STORAGE_PROOF(source, manifest, {**binding, 'pcm_sha256': '0' * 64})
    objects[source['result']['video_key']] = b'Wrong bytes'
    with pytest.raises(review.artifact.ExternalArtifactValidationError): REAL_STORAGE_PROOF(source, manifest, binding)
