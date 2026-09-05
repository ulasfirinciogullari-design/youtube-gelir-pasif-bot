"""Hash-bound, server-authored stock recovery; never an editorial approval.

Preparation retrieves existing Pexels media only. Loading uses private Storage
only. Neither path mutates jobs/claims/budgets or calls a generative provider.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.services import audio_checkpoint, pexels, storage, studio_state
from app.services.qa_workprint import _digest as _file_digest, _file, _put_immutable
from app.services.qa_workprint_access import validated_pointer
from app.services.voice_candidate_recovery import _download_bounded, _work_directory
from app.services.visual_allocation_checkpoint import _text


MAX_MANIFEST_BYTES = 128 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_CLIP_BYTES = 100 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_STOCK_INDICES = {0, 1, 2, 4, 5}
_POINTER_FIELDS = {'version', 'source_task_id', 'key', 'sha256', 'size'}
_MANIFEST_FIELDS = {
    'version', 'status', 'qa_approved', 'requires_full_qa', 'source_task_id',
    'source_spec_sha256', 'package_sha256', 'asset_source_task_id', 'voice_sha256',
    'source_workprint_metadata_sha256', 'paid_scene_indices', 'replacement_scene_indices', 'scenes',
}
_ENTRY_FIELDS = {'pexels_id', 'key', 'sha256', 'size', 'start_fraction', 'attribution',
                 'source_duration', 'width', 'height'}
_ATTRIBUTION_FIELDS = {'source', 'creator_name', 'creator_url', 'page_url', 'license_url'}


class CuratedStockError(RuntimeError):
    """Fixed, secret-safe failure. Never fall back to generating replacements."""


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Invalid curated source')
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError('Invalid curated fingerprint')
    return value


def _number(value: object, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid curated timing')
    return float(value)


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value: object) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _json(raw: bytes) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate curated field')
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError('Nonfinite curated value')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _private_json(key: str, checksum: str, size: int, maximum: int) -> dict:
    _sha(checksum)
    if type(size) is not int or not 1 <= size <= maximum:
        raise ValueError('Invalid private manifest size')
    response = storage._client().get_object(Bucket=storage.settings.bucket, Key=key)
    body = response.get('Body')
    try:
        if type(response.get('ContentLength')) is not int or response['ContentLength'] != size:
            raise ValueError('Private manifest size changed')
        raw = body.read(size + 1)
    finally:
        if body is not None:
            body.close()
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError('Private manifest fingerprint changed')
    parsed = _json(raw)
    if not isinstance(parsed, dict):
        raise ValueError('Invalid private manifest')
    return parsed


def _bindings(source_job: dict, package: dict) -> tuple[dict, dict]:
    source_id = _uuid(source_job.get('task_id'))
    spec = source_job.get('spec')
    if (source_job.get('kind') != 'render' or source_job.get('state') != 'FAILURE'
            or source_job.get('failure_stage') not in {'final_visual_qc', 'final_visual_qc_rescue'}
            or type(source_job.get('paid_create_slots_used')) is not int or source_job['paid_create_slots_used'] != 0
            or not isinstance(spec, dict) or spec.get('mode') != 'production' or spec.get('format') != 'shorts'
            or type(spec.get('duration_minutes')) not in (int, float) or spec['duration_minutes'] != 0.5
            or spec.get('music') != 'off' or not isinstance(package, dict)
            or not isinstance(package.get('scenes'), list) or len(package['scenes']) != 6):
        raise ValueError('Curated recovery is outside its scope')
    material = {key: value for key, value in package.items() if key not in {'_recovered_voice', '_recovered_generated_media'}}
    package_sha = _digest(material)
    media, voice = package.get('_recovered_generated_media'), package.get('_recovered_voice')
    if (not isinstance(media, dict) or type(media.get('version')) is not int or media['version'] != 3
            or media.get('recovery_only') is not True or not isinstance(voice, dict)
            or type(voice.get('version')) is not int or voice['version'] != 1
            or not isinstance(media.get('scenes'), dict) or set(media['scenes']) != {'3'}
            or media.get('package_sha256') != package_sha or voice.get('package_sha256') != package_sha
            or _uuid(media.get('source_task_id')) != _uuid(voice.get('source_task_id'))
            or media['source_task_id'] == source_id):
        raise ValueError('Curated recovery requires paired immutable v3 assets')
    voice_sha = _sha(voice.get('sha256'))
    durations = voice.get('scene_durations')
    if not isinstance(durations, list) or len(durations) != 6:
        raise ValueError('Curated voice timing is unavailable')
    durations = [_number(value, 0.1, 20) for value in durations]
    _number(sum(durations), 28.7, 30.08)
    checkpoint = source_job.get('audio_candidate_checkpoint')
    if (not isinstance(checkpoint, dict) or checkpoint.get('audio_sha256') != voice_sha
            or checkpoint.get('size') != voice.get('size')
            or checkpoint.get('package_sha256') != _digest(audio_checkpoint._candidate_package(package))):
        raise ValueError('Curated voice identity differs')
    pointer = validated_pointer(source_job, source_id)
    if pointer is None:
        raise ValueError('Private source workprint is unavailable')
    metadata = _private_json(pointer['metadata_key'], pointer['metadata_sha256'], pointer['metadata_size'], MAX_METADATA_BYTES)
    if (type(metadata.get('version')) is not int or metadata['version'] != 1 or metadata.get('status') != 'qa_workprint'
            or metadata.get('task_id') != source_id or metadata.get('qa_approved') is not False
            or metadata.get('publish_eligible') is not False or metadata.get('reusable') is not False
            or metadata.get('voice', {}).get('sha256') != voice_sha
            or metadata.get('video_sha256') != pointer['sha256'] or metadata.get('video_size') != pointer['size']
            or not isinstance(metadata.get('scenes'), list) or len(metadata['scenes']) != 6):
        raise ValueError('Workprint metadata binding differs')
    entries = media['scenes']['3']
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError('Curated paid coverage is ambiguous')
    for index, row in enumerate(metadata['scenes']):
        if (not isinstance(row, dict) or type(row.get('scene_index')) is not int or row['scene_index'] != index
                or row.get('narration') != package['scenes'][index].get('narration')
                or not isinstance(row.get('selection'), dict)):
            raise ValueError('Workprint storyboard differs')
        selected = row['selection']
        if _number(row.get('duration_seconds'), 0.1, 20) != durations[index]:
            raise ValueError('Workprint narration timing differs')
        _sha(selected.get('sha256'))
        if type(selected.get('size')) is not int or not 1024 <= selected['size'] <= MAX_CLIP_BYTES:
            raise ValueError('Workprint source is outside the size bound')
        _number(selected.get('start_fraction'), 0, 0.95)
        if index == 3:
            if selected['sha256'] != entries[0].get('sha256') or selected['size'] != entries[0].get('size'):
                raise ValueError('Workprint paid source differs')
        elif (selected.get('source_type') != 'stock' or selected.get('stock_provider') != 'pexels'
              or type(selected.get('pexels_id')) is not int or selected['pexels_id'] <= 0):
            raise ValueError('Workprint stock source is ambiguous')
    return {'source_task_id': source_id, 'source_spec_sha256': _digest(spec), 'package_sha256': package_sha,
            'asset_source_task_id': media['source_task_id'], 'voice_sha256': voice_sha,
            'source_workprint_metadata_sha256': pointer['metadata_sha256']}, metadata


def _public_pexels_url(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 1500 or any(ord(char) <= 32 for char in value):
        return False
    parsed = urlsplit(value)
    return bool(parsed.scheme == 'https' and parsed.hostname in {'www.pexels.com', 'pexels.com'}
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment and parsed.path.startswith('/'))


def _entry(entry: object, source_id: str) -> dict:
    if (not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS
            or type(entry.get('pexels_id')) is not int or entry['pexels_id'] <= 0
            or type(entry.get('size')) is not int or not 1024 <= entry['size'] <= MAX_CLIP_BYTES
            or entry.get('key') != f'curated_stock/{source_id}/raw/{_sha(entry.get("sha256"))}.mp4'):
        raise ValueError('Invalid curated media entry')
    _number(entry.get('start_fraction'), 0, 0.95)
    _number(entry.get('source_duration'), 1, 1800)
    if any(type(entry.get(key)) is not int or not 240 <= entry[key] <= 8192 for key in ('width', 'height')):
        raise ValueError('Invalid curated media dimensions')
    attribution = entry.get('attribution')
    if (not isinstance(attribution, dict) or set(attribution) != _ATTRIBUTION_FIELDS
            or attribution.get('source') != 'Pexels' or attribution.get('license_url') != 'https://www.pexels.com/license/'
            or attribution.get('page_url') != f'https://www.pexels.com/video/{entry["pexels_id"]}/'
            or not isinstance(attribution.get('creator_name'), str) or not attribution['creator_name']
            or _text(attribution['creator_name'], 200) != attribution['creator_name']
            or attribution.get('creator_url') is not None and not _public_pexels_url(attribution['creator_url'])):
        raise ValueError('Invalid curated attribution')
    return entry


def validate_curated_stock_manifest(pointer: dict, *, source_job: dict, approved_package: dict) -> dict:
    """Read-only validation; callers still own claim/CAS and independent QA."""
    try:
        bindings, metadata = _bindings(source_job, approved_package)
        if (not isinstance(pointer, dict) or set(pointer) != _POINTER_FIELDS
                or type(pointer.get('version')) is not int or pointer['version'] != 1
                or pointer.get('source_task_id') != bindings['source_task_id']
                or pointer.get('key') != f'curated_stock/{bindings["source_task_id"]}/{_sha(pointer.get("sha256"))}.json'):
            raise ValueError('Invalid curated pointer')
        manifest = _private_json(pointer['key'], pointer['sha256'], pointer['size'], MAX_MANIFEST_BYTES)
        if (set(manifest) != _MANIFEST_FIELDS or type(manifest.get('version')) is not int or manifest['version'] != 1
                or manifest.get('status') != 'unapproved_curated_stock' or manifest.get('qa_approved') is not False
                or manifest.get('requires_full_qa') is not True or any(manifest.get(key) != value for key, value in bindings.items())
                or manifest.get('paid_scene_indices') != [3] or manifest.get('replacement_scene_indices') != [4]
                or any(type(index) is not int for index in manifest['paid_scene_indices'] + manifest['replacement_scene_indices'])
                or not isinstance(manifest.get('scenes'), dict) or set(manifest['scenes']) != {str(index) for index in _STOCK_INDICES}):
            raise ValueError('Curated manifest bindings differ')
        seen = set()
        for index in sorted(_STOCK_INDICES):
            entry = _entry(manifest['scenes'][str(index)], bindings['source_task_id'])
            minimum_duration = max(5.0, approved_package['_recovered_voice']['scene_durations'][index] + 0.35)
            if entry['source_duration'] < minimum_duration:
                raise ValueError('Curated source is shorter than its narration')
            if entry['pexels_id'] in seen:
                raise ValueError('Curated stock must retain unique final identities')
            seen.add(entry['pexels_id'])
            original = metadata['scenes'][index]['selection']
            if index != 4 and any(entry[key] != original[key] for key in ('pexels_id', 'sha256', 'size', 'start_fraction')):
                raise ValueError('An unchanged curated source was replaced')
            if index == 4 and entry['pexels_id'] == original['pexels_id']:
                raise ValueError('Curated replacement did not change the rejected source')
        return deepcopy(manifest)
    except Exception:
        raise CuratedStockError('Curated stock manifest unavailable; no replacement was generated') from None


def _bounded_http(url: str, *, headers: dict | None, output: Path | None, maximum: int) -> bytes | tuple[str, int]:
    with httpx.stream('GET', url, headers={**(headers or {}), 'Accept-Encoding': 'identity'}, timeout=60, follow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError('Curated source request failed')
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('Curated source transfer encoding differs')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or not 1 <= int(length) <= maximum):
            raise ValueError('Curated source exceeds its bound')
        body = io.BytesIO() if output is None else output.open('xb')
        digest, total = hashlib.sha256(), 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > maximum:
                    raise ValueError('Curated source exceeds its bound')
                body.write(chunk)
                digest.update(chunk)
            if not total or length is not None and total != int(length):
                raise ValueError('Curated source is incomplete')
            return body.getvalue() if output is None else (digest.hexdigest(), total)
        finally:
            body.close()


def _probe(path: Path) -> dict:
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-show_entries', 'stream=codec_type,width,height:format=duration', '-of', 'json', str(path),
    ], text=True, stderr=subprocess.DEVNULL, timeout=30)
    if len(raw) > 64 * 1024:
        raise ValueError('Invalid curated source probe')
    data = _json(raw.encode('utf-8'))
    streams = [item for item in data.get('streams', []) if item.get('codec_type') == 'video']
    if len(streams) != 1:
        raise ValueError('Curated source has no unique picture track')
    dimensions = {key: streams[0].get(key) for key in ('width', 'height')}
    if any(type(value) is not int or not 240 <= value <= 8192 for value in dimensions.values()):
        raise ValueError('Invalid curated source dimensions')
    duration = _number(float(data['format']['duration']), 1, 1800)
    return {**dimensions, 'source_duration': duration}


def _pexels_by_id(pexels_id: int, output: Path) -> tuple[dict, dict]:
    if type(pexels_id) is not int or not 1 <= pexels_id <= 10**12:
        raise ValueError('Invalid Pexels identity')
    payload = _json(_bounded_http(f'https://api.pexels.com/v1/videos/videos/{pexels_id}',
                                 headers=pexels._headers(), output=None, maximum=MAX_METADATA_BYTES))
    if not isinstance(payload, dict) or type(payload.get('id')) is not int or payload['id'] != pexels_id:
        raise ValueError('Pexels identity changed')
    chosen = pexels._pick_file(payload, orientation='portrait')
    if not isinstance(chosen, dict) or chosen.get('file_type') != 'video/mp4':
        raise ValueError('Pexels MP4 source is unavailable')
    url = chosen.get('link')
    if not isinstance(url, str) or len(url) > 2000 or any(ord(char) <= 32 for char in url):
        raise ValueError('Invalid Pexels source location')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'videos.pexels.com' or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.startswith('/video-files/') or not parsed.path.endswith('.mp4')
            or '%' in parsed.path or '..' in parsed.path):
        raise ValueError('Pexels source is outside its CDN')
    checksum, size = _bounded_http(url, headers=None, output=output, maximum=MAX_CLIP_BYTES)
    _file(output, output.parent, MAX_CLIP_BYTES)
    dimensions = _probe(output)
    creator = payload.get('user') or {}
    name = _text(creator.get('name'), 200)
    if not name or name == '[redacted]':
        raise ValueError('Pexels attribution is unavailable')
    attribution = {'source': 'Pexels', 'creator_name': name,
                   'creator_url': creator.get('url') if _public_pexels_url(creator.get('url')) else None,
                   'page_url': f'https://www.pexels.com/video/{pexels_id}/',
                   'license_url': 'https://www.pexels.com/license/'}
    return {'pexels_id': pexels_id, 'sha256': checksum, 'size': size, **dimensions}, attribution


def prepare_curated_stock(leaf_task_id: str, original_receipt_pointer: dict,
                         replacements: dict[int, dict], work_dir: str | Path) -> dict:
    """Prepare an immutable candidate; only explicit scene 4 may be replaced."""
    try:
        from app.services import paid_render_recovery
        leaf_id = _uuid(leaf_task_id)
        receipt = paid_render_recovery._load_original_receipt(original_receipt_pointer)
        origin, leaf, fingerprint = paid_render_recovery._continuation_state(receipt['source_task_id'], leaf_id, studio_state._client())
        paid_render_recovery._validate_continuation_package(receipt, origin, leaf)
        bindings, metadata = _bindings(leaf, receipt['approved_package'])
        path = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', path.name)
        if not match or _uuid(match[1]) in {leaf_id, receipt['source_task_id']}:
            raise ValueError('Curated preparation directory is not distinct')
        work = _work_directory(match[1], path)
        if any(work.iterdir()):
            raise ValueError('Curated preparation directory is not fresh')
        if (not isinstance(replacements, dict) or set(replacements) != {4} or type(next(iter(replacements))) is not int
                or not isinstance(replacements[4], dict) or set(replacements[4]) != {'pexels_id', 'start_fraction'}):
            raise ValueError('Only the explicit rejected scene may change')
        candidate = replacements[4]
        if type(candidate.get('pexels_id')) is not int or not 1 <= candidate['pexels_id'] <= 10**12:
            raise ValueError('Invalid explicit replacement identity')
        _number(candidate.get('start_fraction'), 0, 0.95)
        rows = {}
        staged = []
        seen = set()
        for index in sorted(_STOCK_INDICES):
            old = metadata['scenes'][index]['selection']
            request = candidate if index == 4 else old
            pexels_id = request['pexels_id']
            if pexels_id in seen or index == 4 and pexels_id == old['pexels_id']:
                raise ValueError('Curated stock identity is repeated')
            seen.add(pexels_id)
            output = work / f'curated_source_{index:02d}.mp4'
            entry, attribution = _pexels_by_id(pexels_id, output)
            if _file_digest(output, MAX_CLIP_BYTES) != (entry['sha256'], entry['size']):
                raise ValueError('Curated source changed after retrieval')
            if index != 4 and any(entry[key] != old[key] for key in ('sha256', 'size')):
                raise ValueError('Previously selected Pexels source changed')
            if entry['source_duration'] < max(5.0, receipt['approved_package']['_recovered_voice']['scene_durations'][index] + 0.35):
                raise ValueError('Curated source is shorter than its narration')
            entry.update(key=f'curated_stock/{leaf_id}/raw/{entry["sha256"]}.mp4',
                         start_fraction=request['start_fraction'], attribution=attribution)
            _entry(entry, leaf_id)
            rows[str(index)] = entry
            staged.append((output, entry))
        if paid_render_recovery._continuation_state(receipt['source_task_id'], leaf_id, studio_state._client())[2] != fingerprint:
            raise ValueError('Curated source lineage changed')
        manifest = {'version': 1, 'status': 'unapproved_curated_stock', 'qa_approved': False,
                    'requires_full_qa': True, **bindings, 'paid_scene_indices': [3],
                    'replacement_scene_indices': [4], 'scenes': rows}
        raw = _bytes(manifest)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('Curated manifest exceeds its bound')
        checksum = hashlib.sha256(raw).hexdigest()
        key = f'curated_stock/{leaf_id}/{checksum}.json'
        client = storage._client()
        for output, entry in staged:
            with output.open('rb') as body:
                _put_immutable(client, entry['key'], body, entry['size'], entry['sha256'], 'video/mp4')
        _put_immutable(client, key, io.BytesIO(raw), len(raw), checksum, 'application/json')
        return {'curated_stock_manifest': {'version': 1, 'source_task_id': leaf_id,
                                           'key': key, 'sha256': checksum, 'size': len(raw)}}
    except Exception:
        raise CuratedStockError('Curated stock preparation unavailable; no replacement was generated') from None


def load_curated_stock_manifest(pointer: dict, *, source_job: dict, approved_package: dict,
                               child_task_id: str, work_dir: str | Path) -> dict:
    """Load exact private stock candidates; never import old scores as approval."""
    try:
        child_id = _uuid(child_task_id)
        manifest = validate_curated_stock_manifest(pointer, source_job=source_job, approved_package=approved_package)
        if child_id in {manifest['source_task_id'], manifest['asset_source_task_id']}:
            raise ValueError('Curated child is not distinct')
        work = _work_directory(child_id, work_dir)
        visuals, credits = {}, []
        for raw_index, entry in sorted(manifest['scenes'].items(), key=lambda item: int(item[0])):
            index = int(raw_index)
            output = work / f'curated_stock_{index:02d}.mp4'
            if output.exists() or output.is_symlink():
                raise ValueError('Curated destination already exists')
            checksum, _size = _download_bounded(storage._client(), entry['key'], output, MAX_CLIP_BYTES, expected_size=entry['size'])
            if checksum != entry['sha256']:
                raise ValueError('Curated source fingerprint changed')
            _file(output, work, MAX_CLIP_BYTES)
            probed = _probe(output)
            if (any(probed[key] != entry[key] for key in ('width', 'height'))
                    or abs(probed['source_duration'] - entry['source_duration']) > 0.04
                    or probed['source_duration'] < max(5.0, approved_package['_recovered_voice']['scene_durations'][index] + 0.35)):
                raise ValueError('Curated source probe changed')
            visuals[index] = [{'path': str(output), 'pexels_id': entry['pexels_id'],
                               'start_fraction': entry['start_fraction'], 'preserve_start_fraction': True,
                               'source_duration': entry['source_duration'], 'source_type': 'stock',
                               'stock_provider': 'pexels', 'generated': False}]
            credits.append({**entry['attribution'], 'scene_index': index,
                            'pexels_id': entry['pexels_id'], 'selected_by': 'curated_stock_manifest'})
        return {'scene_visuals': visuals, 'credits': credits}
    except Exception:
        raise CuratedStockError('Curated stock recovery unavailable; no replacement was generated') from None
