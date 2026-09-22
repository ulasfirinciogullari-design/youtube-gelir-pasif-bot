"""Private raw candidates, never an approval, repair claim or render input."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
from uuid import UUID

from botocore.exceptions import ClientError
from app.services import storage
from app.services.audio_checkpoint import _candidate_package, _candidate_voice, _local_candidate_path
from app.services.visual_allocation_checkpoint import _work_path


MAX_RAW_BYTES = 100 * 1024 * 1024
MAX_AUDIO_BYTES = 14 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
PROVIDERS = frozenset({'runway', 'fal_seedance_2_fast', 'gemini_omni', 'gemini_veo',
                       'gemini_veo_fast', 'gemini_veo_standard', 'gemini_image_motion'})
PHASES = frozenset({'initial_generation', 'final_repair'})


class GeneratedAssetCheckpointError(RuntimeError):
    """The only failure exposed by preservation; no provider or path text."""


def _require(condition):
    if not condition:
        raise ValueError('Invalid raw candidate')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _snapshot(source: Path, output: Path, maximum: int, *, audio=False):
    digest, size = hashlib.sha256(), 0
    with source.open('rb') as incoming, output.open('xb') as outgoing:
        while chunk := incoming.read(64 * 1024):
            if not size:
                _require((chunk.startswith(b'ID3') or (len(chunk) >= 2 and chunk[0] == 0xff
                          and chunk[1] & 0xe0 == 0xe0)) if audio else chunk[4:8] == b'ftyp')
            size += len(chunk)
            _require(size <= maximum)
            digest.update(chunk)
            outgoing.write(chunk)
    _require(size >= 1024)
    return digest.hexdigest(), size


def _verify_existing(client, key, checksum, size, content_type):
    response = client.get_object(Bucket=storage.settings.bucket, Key=key)
    body = response['Body']
    try:
        _require(response.get('ContentLength') == size and response.get('ContentType') == content_type)
        digest, total = hashlib.sha256(), 0
        while chunk := body.read(min(64 * 1024, size + 1 - total)):
            total += len(chunk)
            _require(total <= size)
            digest.update(chunk)
        _require(total == size and digest.hexdigest() == checksum)
    finally:
        body.close()


def _put_immutable(client, key, body, checksum, size, content_type):
    try:
        response = client.put_object(
            Bucket=storage.settings.bucket, Key=key, Body=body, ContentLength=size,
            ContentType=content_type, CacheControl='private, no-store',
            Metadata={'sha256': checksum}, IfNoneMatch='*',
        )
        _require(isinstance(response, dict)
                 and response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200)
    except ClientError as exc:
        if str(exc.response.get('Error', {}).get('Code')) not in {'412', 'PreconditionFailed'}:
            raise
        # A forged metadata hash must not validate a changed existing object.
        _verify_existing(client, key, checksum, size, content_type)


def persist_generated_asset_candidate(
    task_id: str, work_dir: str | Path, *, package: dict, voice_result: dict,
    visual_spec: dict, scene_index: int, phase: str, options: dict,
    duration_minutes: float,
) -> dict:
    """Synchronously preserve the existing raw and voice before another create.

    Only private Storage is touched. A returned pointer attests bytes stored,
    not visual or audio quality. No job, claim, profile or publisher is read or
    changed, and no generation/reviewer/renderer/YouTube API is called.
    """
    try:
        longform = bool(isinstance(options, dict) and options.get('content_plan_item_id')
            and options.get('format') == 'landscape' and duration_minutes == 3)
        if longform:
            from app.services.commissioning_longform import active
            _require(active())
        _require(isinstance(task_id, str) and str(UUID(task_id)) == task_id
                 and isinstance(options, dict) and options.get('mode') == 'production'
                 and (longform or options.get('format') == 'shorts' and duration_minutes == 0.5)
                 and type(duration_minutes) in (int, float)
                 and isinstance(package, dict) and isinstance(voice_result, dict)
                 and isinstance(visual_spec, dict) and phase in PHASES)
        clean_package = _candidate_package(package)
        count = len(clean_package['scenes'])
        _require(6 <= count <= (32 if longform else 12) and type(scene_index) is int and 0 <= scene_index < count)
        clean_voice = _candidate_voice(voice_result, count)
        full_package = {key: value for key, value in package.items()
                        if key not in {'_recovered_generated_media', '_recovered_voice'}}
        package_bytes = _json(full_package)
        _require(len(package_bytes) <= MAX_MANIFEST_BYTES)
        package_sha = hashlib.sha256(package_bytes).hexdigest()
        candidate_package_sha = hashlib.sha256(_json(clean_package)).hexdigest()
        work = _work_path(task_id, work_dir)
        raw_path = visual_spec.get('path')
        _require(isinstance(raw_path, (str, Path)) and '://' not in str(raw_path))
        raw_path = Path(raw_path)
        expected_name = (f'runway_s{scene_index:02d}.mp4' if phase == 'initial_generation'
                         else f'runway_repair_s{scene_index:02d}.mp4')
        _require(raw_path.is_absolute() and '..' not in raw_path.parts
                 and raw_path == work / expected_name and not raw_path.is_symlink()
                 and raw_path.resolve(strict=True) == work / expected_name
                 and stat.S_ISREG(raw_path.stat().st_mode)
                 and 1024 <= raw_path.stat().st_size <= MAX_RAW_BYTES)
        provider = visual_spec.get('generation_provider')
        attempts = visual_spec.get('generation_provider_attempts')
        _require(visual_spec.get('generated') is True and visual_spec.get('source_type') == 'generated'
                 and provider in PROVIDERS and type(attempts) is int and 1 <= attempts <= 10
                 and visual_spec.get('start_fraction') == 0.0
                 and visual_spec.get('preserve_start_fraction') is True
                 and visual_spec.get('forbid_loop') is True)
        voice_path = _local_candidate_path(task_id, voice_result)
        if voice_path.name == 'recovered_voice.mp3':
            _require(voice_path.parent == work)
        flags = {'version': 1, 'source_task_id': task_id, 'status': 'preserved_candidate',
                 'diagnostic_only': True, 'qa_approved': False, 'reusable': False,
                 'requires_full_qa': True}
        with tempfile.TemporaryDirectory(prefix='generated_checkpoint_', dir=work) as temporary:
            copied_raw, copied_voice = Path(temporary) / 'raw.mp4', Path(temporary) / 'voice.mp3'
            raw_sha, raw_size = _snapshot(raw_path, copied_raw, MAX_RAW_BYTES)
            voice_sha, voice_size = _snapshot(voice_path, copied_voice, MAX_AUDIO_BYTES, audio=True)
            prefix = f'generated_candidates/{task_id}/'
            raw_key, voice_key = f'{prefix}raw/{raw_sha}.mp4', f'{prefix}voice/{voice_sha}.mp3'
            manifest = {**flags, 'scene_index': scene_index, 'phase': phase,
                        'package_sha256': package_sha, 'candidate_package_sha256': candidate_package_sha,
                        'package': clean_package, 'voice': clean_voice,
                        'audio': {'key': voice_key, 'sha256': voice_sha, 'size': voice_size},
                        'raw': {'key': raw_key, 'sha256': raw_sha, 'size': raw_size,
                                'provider': provider, 'provider_attempts': attempts,
                                'start_fraction': 0.0, 'forbid_loop': True,
                                'synthetic_motion_only': visual_spec.get('synthetic_motion_only') is True}}
            payload = _json(manifest)
            _require(len(payload) <= MAX_MANIFEST_BYTES)
            manifest_sha = hashlib.sha256(payload).hexdigest()
            manifest_key = f'{prefix}manifests/{manifest_sha}.json'
            client = storage._client()
            with copied_raw.open('rb') as incoming:
                _put_immutable(client, raw_key, incoming, raw_sha, raw_size, 'video/mp4')
            with copied_voice.open('rb') as incoming:
                _put_immutable(client, voice_key, incoming, voice_sha, voice_size, 'audio/mpeg')
            _put_immutable(client, manifest_key, io.BytesIO(payload), manifest_sha, len(payload), 'application/json')
        return {**flags, 'scene_index': scene_index, 'phase': phase,
                'provider': provider, 'raw_key': raw_key, 'raw_sha256': raw_sha, 'raw_size': raw_size,
                'audio_sha256': voice_sha, 'package_sha256': package_sha,
                'manifest_key': manifest_key, 'manifest_sha256': manifest_sha, 'manifest_size': len(payload)}
    except Exception:
        raise GeneratedAssetCheckpointError('generated_asset_preservation_unavailable') from None
