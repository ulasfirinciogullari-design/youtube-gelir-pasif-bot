"""Retrieve six already-created operations; never create, approve or dispatch."""
import hashlib
import io
import json
import logging
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

logging.disable(logging.CRITICAL)

import httpx
from botocore.exceptions import ClientError
from app.config import settings
from app.services import storage, studio_state


TASK = 'cb476d47-8ccc-4a55-8da8-e82ca8db3272'
PREFIX = f'recovery/{TASK}/'
METADATA_SHA = '6e828c8d528963d287503edf109fba88369d51f392da4bfce6dbe72e9ac12dd2'
METADATA_KEY = f'qa_workprints/{TASK}/{METADATA_SHA}.json'
METADATA_SIZE = 12802
VOICE_SHA = 'b98f0388c9fd30e0370f71fce743bc7d33f7868f01d96dd5d0b161eb2d9266ce'
VOICE_SIZE = 691820
# These hashes identify original selected raw files, never the workprint edit.
SELECTED = {
    0: ('45a3a884311b53aaa6ed6e138220ae0e4fe9549e5428fb9345ac429bc05db700', 2367477, 40),
    1: ('37745f0f576ae5dd8eb493dcb71ad6603bea22ae16df70155b2ba04a0ce5e895', 3102488, 95),
    2: ('eb07e3ddfb228b8792af9533a9a5330b4c27df1978551a46337f97a9039dd17f', 1368667, 92),
    3: ('449e4cb9e6faeace3ed5389eb7b608f6d9187af39ec2d4bc4c23d91b2ce06523', 1669242, 90),
    4: ('e6507387dba89a592bdc8023ca29d37e87bc482d64c457d23335cfdc753eeb64', 1451822, 92),
    5: ('c727528701f29fd10e947936b1a8306356f3fcb0102c465905f21a7da13e6f63', 14652547, 78),
}
MAX_BYTES = 16 * 1024 * 1024
MAX_JSON = 1024 * 1024
HOST = 'generativelanguage.googleapis.com'
OPERATION = re.compile(r'models/veo-3\.1(?:-lite|-fast)?-generate-preview/operations/[A-Za-z0-9_-]{1,128}')
FILE_PATH = re.compile(r'/v1beta/files/[A-Za-z0-9_-]{1,128}:download')
SOURCE_FIELDS = ('task_id', 'kind', 'state', 'stage', 'failure_stage', 'spec',
                 'audio_candidate_checkpoint', 'qa_workprint', 'paid_create_slots_used',
                 'preview_total_paid_create_cap', 'retry_child_task_id', 'retry_claimed',
                 'repair_claimed', 'repair_available')


def require(condition):
    if not condition:
        raise ValueError('Preservation evidence is invalid')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def checksum(value):
    return hashlib.sha256(value).hexdigest()


def operation_paths(arguments):
    require(len(arguments) == 6)
    paths = [value.removeprefix('/v1beta/') for value in arguments]
    require(len(set(paths)) == 6 and all(OPERATION.fullmatch(value) for value in paths))
    return sorted(paths)


def read_storage(client, key, *, expected_size, expected_sha):
    response = client.get_object(Bucket=settings.bucket, Key=key)
    body = response['Body']
    try:
        require(type(response.get('ContentLength')) is int
                and response['ContentLength'] == expected_size and expected_size <= MAX_BYTES)
        raw = body.read(expected_size + 1)
    finally:
        body.close()
    require(len(raw) == expected_size and checksum(raw) == expected_sha)
    return raw


def source_proof():
    source = studio_state.get_job(TASK)
    require(isinstance(source, dict) and source.get('task_id') == TASK
            and source.get('state') == 'FAILURE' and source.get('kind') == 'render'
            and source.get('failure_stage') == 'final_visual_qc_rescue'
            and source.get('paid_create_slots_used') == 6
            and source.get('preview_total_paid_create_cap') == 6
            and not any(source.get(field) for field in ('retry_child_task_id', 'retry_claimed',
                                                       'repair_claimed', 'repair_available')))
    spec = source['spec']
    require(spec.get('mode') == 'production' and spec.get('format') == 'shorts'
            and spec.get('duration_minutes') == 0.5 and spec.get('music') == 'off')
    ledger = studio_state._client().hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + TASK)
    require(ledger == {'cap': '6', 'used': '6'})
    pointer = source['qa_workprint']
    require(pointer.get('version') == 1 and pointer.get('task_id') == TASK
            and pointer.get('status') == 'qa_workprint' and pointer.get('qa_approved') is False
            and pointer.get('reusable') is False and pointer.get('publish_eligible') is False
            and pointer.get('metadata_key') == METADATA_KEY
            and pointer.get('metadata_sha256') == METADATA_SHA
            and pointer.get('metadata_size') == METADATA_SIZE)
    audio = source['audio_candidate_checkpoint']
    require(audio.get('audio_sha256') == VOICE_SHA and audio.get('size') == VOICE_SIZE
            and audio.get('qa_approved') is False and audio.get('requires_full_qa') is True)
    proof = {'source': {key: source.get(key) for key in SOURCE_FIELDS}, 'ledger': ledger}
    return source, checksum(encoded(proof))


def validate_metadata(raw):
    data = json.loads(raw)
    require(data.get('version') == 1 and data.get('task_id') == TASK
            and data.get('status') == 'qa_workprint' and data.get('qa_approved') is False
            and data.get('reusable') is False and data.get('publish_eligible') is False
            and data.get('voice', {}).get('sha256') == VOICE_SHA
            and data['voice'].get('size') == VOICE_SIZE
            and data['voice'].get('existing_voice_quality_passed') is True)
    rows = data.get('scenes')
    require(isinstance(rows, list) and len(rows) == 6)
    for index, row in enumerate(rows):
        require(row.get('scene_index') == index)
        selected, review = row.get('selection', {}), row.get('review', {})
        expected_sha, expected_size, expected_score = SELECTED[index]
        # The exact selected pool slot can be 1 (or another bounded slot).
        # It is not the scene index, nor necessarily review.best_candidate_index
        # after an earlier pool collapse. The trusted metadata SHA and raw-file
        # SHA/size bind the actual choice; preserve its original slot unchanged.
        pool_index = selected.get('selected_spec_index')
        require(selected.get('sha256') == expected_sha and selected.get('size') == expected_size
                and type(pool_index) is int and 0 <= pool_index < 24
                and review.get('score') == expected_score)
        if index < 5:
            require(selected.get('generation_provider') == 'gemini_veo'
                    and selected.get('start_fraction') == 0.0 and selected.get('forbid_loop') is True)
        else:
            require(selected.get('stock_provider') == 'pexels' and selected.get('pexels_id') == 38052460
                    and selected.get('start_fraction') == 0.5)
    return data


def http_bytes(client, url, limit):
    # Only exact configured Google API endpoints receive its credential.
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname == HOST and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment)
    with client.stream('GET', url, headers={'x-goog-api-key': settings.gemini_api_key}) as response:
        response.raise_for_status()
        require(response.status_code == 200)
        length = response.headers.get('content-length')
        require(length is None or (length.isdigit() and int(length) <= limit))
        chunks, size = [], 0
        for chunk in response.iter_bytes(64 * 1024):
            size += len(chunk)
            require(size <= limit)
            chunks.append(chunk)
        require(size > 0)
        return b''.join(chunks)


def retrieve(client, operation):
    payload = json.loads(http_bytes(client, f'https://{HOST}/v1beta/{operation}', MAX_JSON))
    require(payload.get('name') == operation and payload.get('done') is True and not payload.get('error'))
    samples = payload['response']['generateVideoResponse']['generatedSamples']
    require(isinstance(samples, list) and len(samples) == 1)
    uri = samples[0]['video']['uri']
    require(isinstance(uri, str))
    parsed = urlsplit(uri)
    require(parsed.scheme == 'https' and parsed.hostname == HOST and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and FILE_PATH.fullmatch(parsed.path) and parsed.query in ('', 'alt=media'))
    return http_bytes(client, uri, MAX_BYTES)


def probe(path):
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-count_frames',
        '-show_entries', 'stream=codec_type,codec_name,width,height,nb_read_frames:format=duration',
        '-of', 'json', str(path),
    ], text=True, stderr=subprocess.DEVNULL, timeout=45)
    require(len(raw) <= 65536)
    data = json.loads(raw)
    videos = [row for row in data.get('streams', []) if row.get('codec_type') == 'video']
    duration = float(data.get('format', {}).get('duration', 'nan'))
    require(len(videos) == 1 and videos[0].get('codec_name') == 'h264'
            and (videos[0].get('width'), videos[0].get('height')) in {(720, 1280), (1080, 1920)}
            and math.isfinite(duration) and 5.0 <= duration <= 8.1)
    frames = int(videos[0].get('nb_read_frames', 0))
    require(120 <= frames <= 1000)
    return {'duration_seconds': duration, 'video_frames': frames}


def put_immutable(client, key, raw, content_type):
    require(key.startswith(PREFIX))
    digest = checksum(raw)
    try:
        client.put_object(Bucket=settings.bucket, Key=key, Body=io.BytesIO(raw),
                          ContentLength=len(raw), ContentType=content_type,
                          CacheControl='private, no-store', Metadata={'sha256': digest}, IfNoneMatch='*')
    except ClientError as exc:
        if str(exc.response.get('Error', {}).get('Code')) not in {'412', 'PreconditionFailed'}:
            raise
        # The old object's actual bytes must match; metadata alone is not proof.
        read_storage(client, key, expected_size=len(raw), expected_sha=digest)


def main(arguments):
    stage, downloaded, stored = 'validate_arguments', 0, 0
    try:
        paths = operation_paths(arguments)
        stage = 'validate_source'
        source, fingerprint = source_proof()
        client = storage._client()
        stage = 'validate_workprint_metadata'
        metadata = validate_metadata(read_storage(client, METADATA_KEY,
                                                  expected_size=METADATA_SIZE, expected_sha=METADATA_SHA))
        by_hash = {value[0]: index for index, value in SELECTED.items() if index < 5}
        rows, clips, matched, seen_hashes = [], [], set(), set()
        # Fresh local directory; preserved copies are never input to the renderer.
        work = Path(tempfile.mkdtemp(prefix='cb476-paid-preserve-'))
        with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0)) as http:
            for ordinal, operation in enumerate(paths):
                stage = 'retrieve_existing_operation_and_file'
                raw = retrieve(http, operation)
                downloaded += 1
                require(1024 <= len(raw) <= MAX_BYTES and raw[4:8] == b'ftyp')
                digest = checksum(raw)
                require(digest not in seen_hashes)
                seen_hashes.add(digest)
                index = by_hash.get(digest)
                if index is not None:
                    require(len(raw) == SELECTED[index][1] and index not in matched)
                    matched.add(index)
                stage = 'validate_local_existing_clip'
                path = work / f'operation-{ordinal:02d}.mp4'
                path.write_bytes(raw)
                measurements = probe(path)
                raw_key = (f'{PREFIX}raw/scene-{index:02d}-initial.mp4' if index in {1, 2, 3, 4}
                           else f'{PREFIX}diagnostic/{digest}.mp4')
                rows.append({'operation_name': operation, 'provider': 'gemini_veo',
                             'matched_scene_index': index, 'clip_key': raw_key,
                             'clip_sha256': digest, 'clip_size': len(raw), **measurements,
                             'role': 'preserved_raw_candidate' if index in {1, 2, 3, 4} else 'diagnostic_only',
                             'qa_approved': False, 'reusable': False})
                clips.append((raw_key, path))
        require(matched == {0, 1, 2, 3, 4} and len(rows) == 6
                and sum(row['matched_scene_index'] is None for row in rows) == 1)
        stage = 'recheck_source'
        require(source_proof()[1] == fingerprint)
        audio = source['audio_candidate_checkpoint']
        manifest = {'version': 1, 'source_task_id': TASK, 'status': 'unapproved_preservation',
                    'diagnostic_only': True, 'qa_approved': False, 'reusable': False,
                    'requires_full_qa': True, 'source_state_sha256': fingerprint,
                    'workprint_metadata_key': METADATA_KEY, 'workprint_metadata_sha256': METADATA_SHA,
                    'workprint_metadata_size': METADATA_SIZE, 'voice_sha256': VOICE_SHA, 'voice_size': VOICE_SIZE,
                    'source_audio_metadata_sha256': audio['metadata_sha256'],
                    'source_audio_package_sha256': audio['package_sha256'],
                    'source_spec_sha256': checksum(encoded(source['spec'])),
                    'source_paid_create_slots_used': 6, 'source_paid_create_cap': 6,
                    'new_paid_create_requests': 0, 'new_tts_requests': 0,
                    'preserved_raw_scene_indices': [1, 2, 3, 4],
                    'rejected_selected_scene_indices': [0, 5],
                    'stock_scene_five_not_downloaded': True,
                    'voice_not_downloaded': True, 'operations': rows,
                    'selection_proof': [{'scene_index': row['scene_index'], 'selection': row['selection']}
                                        for row in metadata['scenes']]}
        manifest_bytes = encoded(manifest)
        require(len(manifest_bytes) <= 65536)
        manifest_sha = checksum(manifest_bytes)
        manifest_key = f'{PREFIX}provider_retrieval_v1-{manifest_sha}.json'
        stage = 'preserve_private_existing_clips'
        for key, path in clips:
            put_immutable(client, key, path.read_bytes(), 'video/mp4')
            stored += 1
        stage = 'recheck_before_manifest'
        require(source_proof()[1] == fingerprint)
        stage = 'preserve_private_manifest'
        put_immutable(client, manifest_key, manifest_bytes, 'application/json')
        print(json.dumps({'status': 'preserved_unapproved', 'source_task_id': TASK,
                          'qa_approved': False, 'reusable': False,
                          'downloaded_existing_operations': downloaded, 'stored_existing_clips': stored,
                          'matched_selected_scene_indices': sorted(matched),
                          'preserved_raw_scene_indices': [1, 2, 3, 4], 'unmapped_diagnostic_count': 1,
                          'manifest_key': manifest_key, 'manifest_sha256': manifest_sha,
                          'manifest_size': len(manifest_bytes), 'new_paid_create_requests': 0,
                          'new_tts_requests': 0}, separators=(',', ':')))
    except Exception as exc:
        name = type(exc).__name__
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None)
        print(json.dumps({'status': 'stopped', 'stage': stage,
                          'error_class': name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', name) else 'Error',
                          'http_status': status if type(status) is int and 100 <= status <= 599 else None,
                          'downloaded_existing_operations': downloaded, 'stored_existing_clips': stored,
                          'qa_approved': False, 'reusable': False,
                          'new_paid_create_requests': 0, 'new_tts_requests': 0}, separators=(',', ':')))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main(sys.argv[1:])
