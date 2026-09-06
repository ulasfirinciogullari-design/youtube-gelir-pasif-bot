"""Owner-delegated editorial acceptance, never automated QA or a series repair.

Only an owner-authenticated review route may call create_editorial_review.
Retained provider records and visual/source/rights observations are explicitly
owner-supplied evidence, not independently fetched or machine-vision verified.
The server verifies stored bytes, captions, complete PCM and record consistency.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
from uuid import UUID

import redis

from app.services import external_artifact_import as artifact, external_master_ingest as ingest
from app.services.studio_state import JOB_PREFIX, JOB_TTL_SECONDS
from app.services.youtube_publish_state import UPLOAD_PREFIX
from app.services.youtube_automation import automated_quality_approved, SERIES_ASSIGNMENT_PREFIX, SERIES_COUNTER_PREFIX


EDITORIAL_RECEIPT_PREFIX = 'youtube_studio:external_editorial_review:v1:'
MAX_EVIDENCE_BYTES = 1024 * 1024
_LIMITATIONS = {'word_timing': 'unverified', 'human_listened': False,
                'provider_evidence_origin': 'owner_supplied_retained_run'}
_EXPECTED = ('expected_video_sha256', 'expected_caption_sha256', 'expected_video_size', 'expected_caption_size')
_RESULT_BINDING = ('video_key', 'caption_key', 'metadata_key', 'external_descriptor_id', 'external_provenance',
                   'quality_disposition', 'manual_qa_required', 'qa_approved', 'audio_transcription_verified',
                   'word_timing_verified', 'contains_synthetic_media', 'new_media_generated',
                   'editorial_review_id', 'publish_metadata', *_EXPECTED)


class EditorialReviewError(ValueError):
    """Fixed safe rejection; never includes evidence, credentials or filenames."""


def _require(condition):
    if not condition:
        raise EditorialReviewError('external_editorial_review_invalid')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _sha(ingest._json(value).encode('utf-8'))


def _object(raw, limit=2 * MAX_EVIDENCE_BYTES):
    return artifact._object(raw, limit=limit)


def _exact(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _hash(value):
    _require(type(value) is str and artifact._SHA.fullmatch(value))


def _text(value):
    return artifact._text(value, 3000)


def _words(text):
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold())


def _evidence(pack):
    pack = _object(pack, MAX_EVIDENCE_BYTES)
    _require(not artifact._SECRET.search(ingest._json(pack)))
    _exact(pack, ('version', 'manifest', 'asr_provider_evidence_json', 'asr_attempt_json',
                  'prosody_result_json', 'prosody_attempt_json', 'frames', 'scenes',
                  'sources', 'rights_basis', 'limitations', 'blocking_issues', 'publish_metadata'))
    _require(type(pack['version']) is int and pack['version'] == 1
             and type(pack['limitations']) is dict and set(pack['limitations']) == {*_LIMITATIONS, 'notes'}
             and all(type(pack['limitations'].get(k)) is type(v) and pack['limitations'].get(k) == v
                     for k, v in _LIMITATIONS.items())
             and pack['blocking_issues'] == [])
    _require(type(pack['limitations']['notes']) is list and 1 <= len(pack['limitations']['notes']) <= 10)
    for note in pack['limitations']['notes']:
        _text(note)
    manifest = artifact.validate_external_manifest(pack['manifest'])
    _require(manifest['language'] == 'en')
    metadata = pack['publish_metadata']
    _exact(metadata, ('title', 'description', 'tags', 'hashtags', 'sources', 'contains_synthetic_media'))
    _require(metadata['contains_synthetic_media'] is True
             and metadata['sources'] == [row['url'] for row in manifest['sources']])
    for key, maximum in (('title', 100), ('description', 4000)):
        artifact._text(metadata[key], maximum)
        _require(not re.search(r'\b\d+\s*/\s*\d+\b', metadata[key]))
    for key, maximum, length in (('tags', 30, 100), ('hashtags', 10, 60)):
        _require(type(metadata[key]) is list and 1 <= len(metadata[key]) <= maximum)
        for item in metadata[key]:
            artifact._text(item, length)
            if key == 'hashtags':
                _require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', item))
    authored = ' '.join([metadata['title'], metadata['description'], *metadata['tags'], *metadata['hashtags']])
    _require(all(not c.isalpha() or 'LATIN' in unicodedata.name(c, '') for c in authored))
    expected = ' '.join(scene['narration'] for scene in manifest['scenes'])
    asr, asr_attempt, prosody, prosody_attempt = [_object(pack[key]) for key in (
        'asr_provider_evidence_json', 'asr_attempt_json', 'prosody_result_json', 'prosody_attempt_json')]
    _exact(asr, ('binding', 'unapproved_provider_evidence'))
    binding, provider = asr['binding'], asr['unapproved_provider_evidence']
    _exact(binding, ('version', 'video_sha256', 'video_size', 'manifest_sha256', 'captions_sha256',
                     'pcm_sha256', 'wav_sha256', 'sample_rate', 'channels', 'sample_width_bytes',
                     'sample_frames', 'duration_seconds', 'conversion', 'review_code_sha256'))
    _require(type(binding['version']) is int and binding['version'] == 1
             and type(binding['video_size']) is int and 1024 <= binding['video_size'] <= artifact.MAX_VIDEO_BYTES
             and type(binding['sample_rate']) is int and binding['sample_rate'] in (44100, 48000)
             and type(binding['channels']) is int and binding['channels'] in (1, 2)
             and type(binding['sample_frames']) is int and binding['sample_frames'] > 0
             and type(binding['duration_seconds']) in (float, int)
             and 29.75 <= binding['duration_seconds'] <= 30.35
             and abs(binding['sample_frames'] / binding['sample_rate'] - binding['duration_seconds']) < .001
             and type(binding['review_code_sha256']) is dict and 1 <= len(binding['review_code_sha256']) <= 8)
    for name, digest in binding['review_code_sha256'].items():
        _require(type(name) is str and re.fullmatch(r'[a-z_]{1,40}', name)); _hash(digest)
    _exact(provider, ('language', 'model', 'payload', 'provider'))
    _require(provider['provider'] == 'openai' and provider['model'] == 'whisper-1' and provider['language'] == 'en')
    _exact(provider['payload'], ('language', 'text', 'words'))
    payload = provider['payload']
    _require(payload['language'] in {'en', 'english'} and payload['text'] == expected
             and type(payload['words']) is list and len(payload['words']) == len(_words(expected)) == 65)
    unknown = []
    for index, word in enumerate(payload['words']):
        _exact(word, ('word', 'start', 'end'))
        _text(word['word'])
        _require(all(type(word[k]) in (float, int) and math.isfinite(word[k]) for k in ('start', 'end'))
                 and 0 <= word['start'] <= word['end'] <= 30.35)
        if word['start'] == word['end']:
            unknown.append(index)
    _require([token for row in payload['words'] for token in _words(row['word'])] == _words(expected))
    _require(prosody.get('binding') == binding and prosody.get('component') == 'prosody')
    for attempt, component, provider_name, model in ((asr_attempt, 'asr', 'openai', 'whisper-1'),
                                                    (prosody_attempt, 'prosody', 'gemini', None)):
        _require(all(attempt.get(k) == v for k, v in binding.items())
                 and attempt.get('component') == component and attempt.get('provider') == provider_name
                 and attempt.get('maximum_paid_requests') == 1 and attempt.get('state') == 'reserved_before_request')
        _text(attempt.get('model'))
        if model:
            _require(attempt['model'] == model)
    for key in ('video_sha256', 'manifest_sha256', 'pcm_sha256', 'wav_sha256', 'captions_sha256'):
        _hash(binding.get(key))
    _require(binding['captions_sha256'] == manifest['files']['captions']['sha256']
             and binding.get('conversion') == 'decode_complete_master_audio_to_pcm_s16le_no_other_filters'
             and binding.get('sample_width_bytes') == 2)
    result = prosody.get('result', {})
    _require(type(result) is dict and set(result) <= {
        'asr_approved', 'audio_sha256', 'authorized_total_paid_requests', 'available', 'combined_audio_approved',
        'diagnostic_code_sha256', 'diagnostic_only', 'diagnostic_status', 'issues', 'pass', 'preflight', 'provider',
        'publish_eligible', 'qa_approved', 'raw_response_sha256', 'reason', 'review_attempts', 'scores', 'summary',
        'timestamp_provider', 'timestamp_source', 'word_timing_verified'})
    _require(result.get('available') is True and result.get('pass') is True and result.get('provider') == 'gemini'
             and result.get('diagnostic_only') is True and result.get('review_attempts') == 1
             and result.get('audio_sha256') == binding['wav_sha256'] and result.get('issues') == []
             and result.get('diagnostic_status') == 'audible_prosody_component_pass'
             and all(result.get(k) is False for k in ('asr_approved', 'combined_audio_approved', 'word_timing_verified',
                                                     'qa_approved', 'publish_eligible'))
             and all(prosody.get(k) is False for k in ('qa_approved', 'publish_eligible', 'media_generation_authorized'))
             and prosody.get('started_at') == prosody_attempt.get('started_at'))
    preflight = result.get('preflight', {})
    _require(preflight.get('provider_evidence_sha256') == _sha(pack['asr_provider_evidence_json'].encode('utf-8'))
             and preflight.get('text_exact') is True and preflight.get('text_comparison_score') == 100
             and preflight.get('word_timing_verified') is False and preflight.get('asr_approved') is False)
    scores = result.get('scores')
    _exact(scores, ('pronunciation', 'naturalness', 'pacing', 'sentence_flow', 'emphasis', 'roboticness'))
    _require(all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 100 for v in scores.values())
             and all(v >= 86 for k, v in scores.items() if k != 'roboticness') and scores['roboticness'] <= 30)
    urls = {row['url'] for row in manifest['sources']}
    _require(type(pack['sources']) is list and len(pack['sources']) == len(urls))
    for row in pack['sources']:
        _exact(row, ('url', 'content_sha256', 'evidence', 'observation'))
        _require(row['url'] in urls)
        _hash(row['content_sha256'])
        _text(row['evidence']); _text(row['observation'])
    _require({row['url'] for row in pack['sources']} == urls)
    _require(type(pack['frames']) is list and 6 <= len(pack['frames']) <= 40)
    frames = {}
    for row in pack['frames']:
        _exact(row, ('frame_index', 'sha256', 'observation'))
        i = row['frame_index']
        _require(type(i) is int and 0 <= i < 900 and i not in frames)
        _hash(row['sha256']); _text(row['observation'])
        frames[i] = row
    _require(type(pack['scenes']) is list and len(pack['scenes']) == 6)
    for i, row in enumerate(pack['scenes']):
        _exact(row, ('index', 'frame_indices', 'visual_observation', 'alignment_observation', 'source_urls', 'claim_note'))
        _require(type(row['index']) is int and row['index'] == i and type(row['frame_indices']) is list
                 and row['frame_indices'] and len(row['frame_indices']) <= 10
                 and all(type(n) is int and n in frames and manifest['scenes'][i]['start_ms'] <= n * 1000 / 30
                         < manifest['scenes'][i]['end_ms'] for n in row['frame_indices'])
                 and type(row['source_urls']) is list and row['source_urls'] and set(row['source_urls']) <= urls)
        for key in ('visual_observation', 'alignment_observation', 'claim_note'):
            _text(row[key])
    _require(type(pack['rights_basis']) is list and len(pack['rights_basis']) == 3)
    for row in pack['rights_basis']:
        _exact(row, ('asset_type', 'basis', 'evidence_ref', 'observation'))
        _require(row['basis'] in {'original_work', 'verified_license', 'owner_authorized_export'})
        _hash(row['evidence_ref']); _text(row['observation'])
    _require({row['asset_type'] for row in pack['rights_basis']} == {'graphics', 'fonts', 'voice'})
    return pack, manifest, binding, unknown


def _contract(source):
    spec, result = source.get('spec', {}), source.get('result', {})
    _require(source.get('kind') == 'render' and source.get('state') == 'SUCCESS' and not source.get('error')
             and source.get('parent_id') is None and not any(source.get(k) for k in
                 ('retry_child_task_id', 'retry_dispatch', 'retry_of', 'retry_source_task_id'))
             and spec.get('workflow') == 'external_import' and spec.get('mode') == 'production'
             and spec.get('production_scheduled') is False and spec.get('duration_minutes') == .5
             and spec.get('format') == 'shorts' and spec.get('language') == 'en'
             and not any(k in spec for k in ('production_topic_index', 'series_id', 'series_number'))
             and result.get('qa_approved') is False and result.get('audio_transcription_verified') is False
             and result.get('contains_synthetic_media') is True and result.get('new_media_generated') is False)
    for key in ('production_channel_id', 'production_connection_id', 'production_profile_revision'):
        _require(type(spec.get(key)) is str and ingest._ID.fullmatch(spec[key]))
    return spec, result


def _projection(source):
    _contract(source)
    return {'task_id': source['task_id'], 'spec': source['spec'],
            'result': {key: source['result'].get(key) for key in _RESULT_BINDING}}


def _context(reader, source):
    spec, _ = _contract(source)
    raw = ingest._snapshot(reader, spec['production_channel_id'], spec['production_connection_id'],
                           spec['production_profile_revision'], spec['language'])
    profile = _object(raw[ingest.PROFILE_PREFIX + spec['production_channel_id']])
    _require(profile.get('release_mode') == 'public' and profile.get('auto_publish') is True)
    channel = _object(raw[ingest.CHANNEL_PREFIX + spec['production_channel_id']])
    # Analytics refreshes are not an authorization change; identity/bio are.
    raw[ingest.CHANNEL_PREFIX + spec['production_channel_id']] = {
        k: channel.get(k) for k in ('id', 'connection_id', 'requires_reconnect', 'title', 'description')}
    return _digest(raw)


def _storage_proof(source, manifest, binding):
    """Read own immutable Storage objects; no provider call or Storage write."""
    result = source['result']
    with tempfile.TemporaryDirectory(prefix='editorial_verify_') as directory:
        root = Path(directory).resolve(strict=True)
        paths = {}
        for name, field, suffix in (('video', 'video_key', 'master.mp4'), ('captions', 'caption_key', 'captions.srt')):
            expected = manifest['files'][name]
            _require(result[field] == 'external-masters/v1/' + expected['sha256'] + '/' + suffix)
            paths[name] = root / suffix
            ingest.storage.download_file(result[field], paths[name])
        metadata_key = result['metadata_key']
        _require(type(metadata_key) is str and re.fullmatch(r'external-masters/v1/[0-9a-f]{64}/metadata.json', metadata_key))
        metadata_path = root / 'metadata.json'
        ingest.storage.download_file(metadata_key, metadata_path)
        _require(metadata_path.stat().st_size <= 512 * 1024)
        raw = metadata_path.read_bytes()
        _require(_sha(raw) == metadata_key.split('/')[2])
        metadata = _object(raw)
        descriptor = artifact.validate_staged_external_artifact(root, paths['video'], paths['captions'], manifest)
        _require(metadata['manifest'] == manifest and metadata['descriptor_id'] == descriptor['descriptor_id']
                 == result['external_descriptor_id'] and descriptor['media_structure']['frame_count'] == 900
                 and descriptor['media_structure']['frame_rate'] == '30/1')
        pcm = root / 'decoded.pcm'
        completed = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-max_alloc', '134217728',
            '-threads', '1', '-protocol_whitelist', 'file', '-enable_drefs', '0', '-use_absolute_path', '0',
            '-f', 'mov', '-i', str(paths['video']), '-map', '0:a:0', '-vn', '-sn', '-dn',
            '-c:a', 'pcm_s16le', '-f', 's16le', '-n', str(pcm)], stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        media = descriptor['media_structure']
        _require(completed.returncode == 0 and len(completed.stderr) <= 65536
                 and pcm.stat().st_size <= 8 * 1024 * 1024
                 and binding.get('sample_rate') == media['sample_rate'] and binding.get('channels') == media['audio_channels']
                 and pcm.stat().st_size == binding.get('sample_frames', -1) * binding['channels'] * 2
                 and _sha(pcm.read_bytes()) == binding['pcm_sha256'])
        return {'video_sha256': descriptor['video_sha256'], 'captions_sha256': descriptor['captions_sha256'],
                'manifest_sha256': descriptor['manifest_sha256'], 'pcm_sha256': binding['pcm_sha256'],
                'server_verified_stored_bytes_and_pcm': True, 'media_structure': media}


def _keys(source):
    spec, _ = _contract(source)
    task, channel = source['task_id'], spec['production_channel_id']
    _require(str(UUID(task)) == task)
    return [EDITORIAL_RECEIPT_PREFIX + task, JOB_PREFIX + task, ingest.RESERVATION_PREFIX + task,
            ingest.CHANNEL_INDEX_KEY, ingest.CHANNEL_PREFIX + channel, ingest.CREDENTIAL_PREFIX + channel,
            ingest.PROFILE_PREFIX + channel, ingest.AUTH_EPOCH_KEY, UPLOAD_PREFIX + task]


def _series_keys(source, frozen_plan):
    series = (frozen_plan or {}).get('series')
    if series is None:
        return []
    _exact(series, ('id', 'name', 'number', 'total'))
    _require(type(series['id']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', series['id']))
    scope = source['spec']['production_channel_id'] + ':' + series['id']
    return [SERIES_ASSIGNMENT_PREFIX + scope + ':' + source['task_id'], SERIES_COUNTER_PREFIX + scope]


def _validate(reader, source, frozen_plan=None):
    keys = _keys(source)
    current, receipt = _object(reader.get(keys[1])), _object(reader.get(keys[0]))
    result = source['result']
    _require(receipt.get('reviewer_type') == 'delegated_editorial_agent'
             and receipt.get('receipt_id') == source['task_id'] and receipt.get('version') == 1
             and receipt.get('quality_disposition') == 'editorial_review_pass'
             and receipt.get('receipt_sha256') == _digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
             and result.get('editorial_review_id') == receipt['receipt_id']
             and result.get('editorial_review_sha256') == receipt['receipt_sha256']
             and current['result'].get('editorial_review_sha256') == receipt['receipt_sha256']
             and receipt.get('source_sha256') == _digest(_projection(source)) == _digest(_projection(current))
             and receipt.get('authorization_sha256') == _context(reader, current)
             and result.get('quality_disposition') == 'editorial_review_pass' and result.get('manual_qa_required') is False
             and result.get('word_timing_verified') is False and source['spec'].get('publish_after_render') is True)
    reservation = _object(reader.get(keys[2]))
    _require(reservation.get('status') == 'complete' and reservation.get('task_id') == source['task_id']
             and reservation.get('job_sha256') == receipt.get('original_job_sha256'))
    if frozen_plan is not None:
        ledger = _object(reader.get(keys[8]))
        _require(ledger.get('publish_plan') == frozen_plan)
        snapshot = frozen_plan.get('quality_snapshot', {})
        _require(frozen_plan.get('source_task_id') == source['task_id']
                 and frozen_plan.get('target_channel_id') == source['spec']['production_channel_id']
                 and frozen_plan.get('profile_revision') == source['spec']['production_profile_revision']
                 and frozen_plan.get('contains_synthetic_media') is True
                 and all(snapshot.get(k) == result.get(k) for k in
                         ('quality_disposition', 'manual_qa_required', 'editorial_review_id', 'editorial_review_sha256')))
        profile = _object(reader.get(ingest.PROFILE_PREFIX + source['spec']['production_channel_id']))
        series = frozen_plan.get('series')
        if profile.get('series_id'):
            series_keys = _series_keys(source, frozen_plan)
            _require(series_keys and series['id'] == profile['series_id']
                     and series['name'] == ' '.join(str(profile.get('series_name') or '').split())[:100]
                     and type(series['number']) is int and type(series['total']) is int
                     and series['total'] == int(profile.get('series_total') or 0)
                     and 1 <= series['number'] <= (series['total'] or 10000)
                     and reader.get(series_keys[0]) == str(series['number'])
                     and int(reader.get(series_keys[1]) or 0) >= series['number'])
        else:
            _require(series is None)
    return receipt


def create_editorial_review(source_task_id, evidence_pack):
    """Trusted owner-review action. One immutable receipt; CAS changes only its job."""
    try:
        client = ingest._redis()
        _require(type(source_task_id) is str and str(UUID(source_task_id)) == source_task_id)
        source = _object(client.get(JOB_PREFIX + source_task_id))
        _require(source.get('task_id') == source_task_id)
        pack, manifest, binding, unknown = _evidence(evidence_pack)
        keys = _keys(source)
        if client.get(keys[0]) is not None:
            receipt = validate_editorial_publication(source)
            _require(receipt['evidence_sha256'] == _digest(pack))
            return receipt
        _require(source['result'].get('quality_disposition') == 'manual_qa_preview'
                 and source['result'].get('manual_qa_required') is True and source['spec'].get('publish_after_render') is False)
        proof = _storage_proof(source, manifest, binding)
        provenance = source['result']['external_provenance']
        _require(all(provenance.get(k) == proof[k] for k in ('video_sha256', 'captions_sha256', 'manifest_sha256')))
        with client.pipeline() as pipe:
            pipe.watch(*keys)
            _require(pipe.get(keys[0]) is None and _object(pipe.get(keys[1])) == source and pipe.get(keys[8]) is None)
            reservation = _object(pipe.get(keys[2]))
            _require(reservation.get('status') == 'complete' and reservation.get('task_id') == source_task_id
                     and reservation.get('descriptor_id') == source['result']['external_descriptor_id']
                     and reservation.get('job_sha256') == _digest(source))
            updated = deepcopy(source)
            updated['spec']['publish_after_render'] = True
            updated['result'].update(quality_disposition='editorial_review_pass', manual_qa_required=False,
                word_timing_verified=False, editorial_review_id=source_task_id, publish_metadata=pack['publish_metadata'],
                **dict(zip(_EXPECTED, (manifest['files']['video']['sha256'], manifest['files']['captions']['sha256'],
                                       manifest['files']['video']['size'], manifest['files']['captions']['size']))))
            receipt = {'version': 1, 'receipt_id': source_task_id, 'reviewer_type': 'delegated_editorial_agent',
                'quality_disposition': 'editorial_review_pass', 'created_at': datetime.now(timezone.utc).isoformat(),
                'evidence_sha256': _digest(pack), 'evidence': pack, 'server_proof': proof,
                'publish_metadata_sha256': _digest(pack['publish_metadata']),
                'unverified_word_indices': unknown, 'word_timing_verified': False, 'automated_qa_approved': False,
                'observations_provenance': 'owner_authenticated_editorial_attestation_not_backend_vision_or_source_fetch',
                'authorization_sha256': _context(pipe, source), 'source_sha256': _digest(_projection(updated)),
                'original_job_sha256': reservation['job_sha256']}
            receipt['receipt_sha256'] = _digest(receipt)
            updated['result']['editorial_review_sha256'] = receipt['receipt_sha256']
            pipe.multi()
            pipe.set(keys[0], ingest._json(receipt))
            pipe.setex(keys[1], JOB_TTL_SECONDS, ingest._json(updated))
            pipe.execute()
        return receipt
    except Exception:
        raise EditorialReviewError('external_editorial_review_unavailable_or_invalid') from None


def validate_editorial_publication(source, frozen_plan=None):
    """No writes. The publisher MUST additionally hash its actual downloaded files."""
    try:
        client = ingest._redis()
        with client.pipeline() as pipe:
            pipe.watch(*_keys(source), *_series_keys(source, frozen_plan))
            receipt = _validate(pipe, source, frozen_plan)
            pipe.multi()
            pipe.execute()  # Empty EXEC still rejects concurrent revocation/job replacement.
            return receipt
    except Exception:
        raise EditorialReviewError('external_editorial_publication_unavailable_or_invalid') from None


def publication_quality_approved(source):
    if not isinstance(source, dict):
        return False
    spec, result = source.get('spec'), source.get('result')
    if not isinstance(spec, dict) or not isinstance(result, dict):
        return False
    if (spec.get('workflow') == 'external_import' or result.get('quality_disposition') == 'editorial_review_pass'
            or any(k in result for k in ('external_descriptor_id', 'external_provenance',
                                         'editorial_review_id', 'editorial_review_sha256', *_EXPECTED))):
        try:
            validate_editorial_publication(source)
            return True
        except EditorialReviewError:
            return False
    return automated_quality_approved(source)
