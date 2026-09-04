"""Load a voice-only retry candidate, without granting story or media approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
from uuid import UUID

from app.services import audio_checkpoint, storage


_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_POINTER_FIELDS = {
    'version', 'status', 'qa_approved', 'requires_full_qa', 'audio_key',
    'metadata_key', 'audio_sha256', 'metadata_sha256', 'package_sha256', 'size',
}
_METADATA_FIELDS = {
    'version', 'status', 'qa_approved', 'requires_full_qa', 'source_task_id',
    'audio', 'package_sha256', 'package', 'voice',
}


class VoiceCandidateRecoveryError(RuntimeError):
    """A secret-safe failure; no automatic fresh synthesis is authorized."""


def _canonical_id(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Invalid retry identity')
    return value


def _work_directory(child_task_id: str, value: str | Path) -> Path:
    path = Path(value)
    root = audio_checkpoint._AUDIO_TEMP_ROOT.absolute()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid retry directory')
    relative = path.relative_to(root).as_posix()
    if not re.fullmatch(rf'youtube_factory/{re.escape(child_task_id)}_attempt_[0-9]{{1,2}}', relative):
        raise ValueError('Retry directory is not child-scoped')
    resolved = path.resolve(strict=True)
    if resolved != root.resolve(strict=True) / relative or not resolved.is_dir():
        raise ValueError('Invalid retry directory')
    return resolved


def _unapproved(record: dict) -> bool:
    return (
        type(record.get('version')) is int and record['version'] == 1
        and record.get('status') == 'unapproved_candidate'
        and record.get('qa_approved') is False
        and record.get('requires_full_qa') is True
    )


def _download_bounded(client, key: str, output: Path, maximum: int, *, expected_size: int | None = None) -> tuple[str, int]:
    response = client.get_object(Bucket=storage.settings.bucket, Key=key)
    body = response.get('Body')
    try:
        size = response.get('ContentLength')
        if type(size) is not int or not 1 <= size <= maximum or expected_size is not None and size != expected_size:
            raise ValueError('Recovery object size is invalid')
        checksum = hashlib.sha256()
        total = 0
        with output.open('xb') as stream:
            while chunk := body.read(min(64 * 1024, maximum - total + 1)):
                total += len(chunk)
                if total > maximum or total > size:
                    raise ValueError('Recovery object exceeds its bound')
                checksum.update(chunk)
                stream.write(chunk)
        if total != size:
            raise ValueError('Recovery object is incomplete')
        return checksum.hexdigest(), total
    finally:
        if body is not None:
            body.close()


def _voice_result(raw: object, scene_count: int) -> dict:
    required = {
        'spoken_texts', 'scene_durations', 'duration_before_fit',
        'duration_after_fit', 'tempo_rate', 'voice_profile',
    }
    optional = {'content_target_seconds', 'reserved_tail_seconds', 'removed_silence_seconds'}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - optional:
        raise ValueError('Invalid candidate voice schema')
    profile = raw['voice_profile']
    if not isinstance(profile, dict) or set(profile) - {'voice_name', 'voice_model', 'voice_language_code'}:
        raise ValueError('Invalid candidate voice profile')
    flattened = {key: value for key, value in raw.items() if key != 'voice_profile'}
    flattened.update(profile)
    if audio_checkpoint._candidate_voice(flattened, scene_count) != raw:
        raise ValueError('Candidate voice metadata changed')
    if not all(text.strip() for text in flattened['spoken_texts']):
        raise ValueError('Candidate spoken text is missing')
    if abs(sum(flattened['scene_durations']) - flattened['duration_after_fit']) > 0.35:
        raise ValueError('Candidate timing is inconsistent')
    return flattened


def load_voice_retry_candidate(
    source_task_id: str,
    child_task_id: str,
    checkpoint: dict,
    work_dir: str | Path,
) -> dict:
    """Load exact content-addressed audio and an *unapproved* frozen plan.

    The worker must separately verify failed-parent eligibility, frozen task
    spec and the one-shot retry claim before invoking this loader. It must
    independently recheck this story with immutable narration, fit the old
    audio only within its bound, and run all normal QA/publication gates.
    This helper does not create an approved-package or paid-media contract.
    """
    try:
        source_task_id = _canonical_id(source_task_id)
        child_task_id = _canonical_id(child_task_id)
        if source_task_id == child_task_id:
            raise ValueError('Recovery needs a distinct retry child')
        work = _work_directory(child_task_id, work_dir)
        if not isinstance(checkpoint, dict) or set(checkpoint) != _POINTER_FIELDS or not _unapproved(checkpoint):
            raise ValueError('Invalid candidate pointer')
        for field in ('audio_sha256', 'metadata_sha256', 'package_sha256'):
            if not isinstance(checkpoint[field], str) or not _SHA256.fullmatch(checkpoint[field]):
                raise ValueError('Invalid candidate fingerprint')
        size = checkpoint['size']
        if type(size) is not int or not 1024 <= size <= audio_checkpoint.MAX_AUDIO_CANDIDATE_BYTES:
            raise ValueError('Invalid candidate audio size')
        prefix = f"audio_candidates/{source_task_id}/{checkpoint['audio_sha256']}"
        if (
            checkpoint['audio_key'] != f'{prefix}/candidate.mp3'
            or checkpoint['metadata_key'] != f"{prefix}/metadata-{checkpoint['metadata_sha256']}.json"
        ):
            raise ValueError('Candidate keys are not bound to the source')
        destination = work / 'recovered_voice.mp3'
        # Do not overwrite a prior fitted candidate or another local result.
        if destination.exists() or destination.is_symlink():
            raise ValueError('Retry audio destination already exists')
        with tempfile.TemporaryDirectory(prefix='voice_candidate_', dir=work) as temporary:
            temporary = Path(temporary)
            metadata_path = temporary / 'metadata.json'
            client = storage._client()
            checksum, _size = _download_bounded(
                client, checkpoint['metadata_key'], metadata_path,
                audio_checkpoint.MAX_AUDIO_CANDIDATE_METADATA_BYTES,
            )
            if checksum != checkpoint['metadata_sha256']:
                raise ValueError('Candidate metadata fingerprint mismatch')
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            if (
                not isinstance(metadata, dict) or set(metadata) != _METADATA_FIELDS
                or not _unapproved(metadata) or metadata['source_task_id'] != source_task_id
                or metadata['package_sha256'] != checkpoint['package_sha256']
                or metadata['audio'] != {
                    'key': checkpoint['audio_key'], 'sha256': checkpoint['audio_sha256'], 'size': size,
                }
            ):
                raise ValueError('Candidate metadata binding is invalid')
            raw_package = metadata['package']
            if not isinstance(raw_package, dict):
                raise ValueError('Candidate package is missing')
            package = audio_checkpoint._candidate_package(raw_package)
            if package != raw_package:
                raise ValueError('Candidate package schema is invalid')
            canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
            if hashlib.sha256(canonical).hexdigest() != checkpoint['package_sha256']:
                raise ValueError('Candidate package fingerprint mismatch')
            voice_result = _voice_result(metadata['voice'], len(package['scenes']))
            package['narration'] = ' '.join(scene['narration'] for scene in package['scenes'])
            audio_path = temporary / 'candidate.mp3'
            checksum, _size = _download_bounded(
                client, checkpoint['audio_key'], audio_path,
                audio_checkpoint.MAX_AUDIO_CANDIDATE_BYTES, expected_size=size,
            )
            if checksum != checkpoint['audio_sha256']:
                raise ValueError('Candidate audio fingerprint mismatch')
            # Exclusive creation preserves an existing result even on a race.
            with audio_path.open('rb') as incoming, destination.open('xb') as outgoing:
                while chunk := incoming.read(64 * 1024):
                    outgoing.write(chunk)
        voice_result['path'] = str(destination)
        return {
            'source_task_id': source_task_id,
            'child_task_id': child_task_id,
            'audio_sha256': checksum,
            'status': 'unapproved_candidate',
            'qa_approved': False,
            'requires_full_qa': True,
            'package': package,
            'voice_result': voice_result,
        }
    except Exception:
        raise VoiceCandidateRecoveryError('Voice retry candidate unavailable') from None


def require_unchanged_voice_narration(candidate_package: dict, rechecked_package: dict) -> None:
    """Allow query/visual repairs, but never silently marry old audio to new words."""
    try:
        original = candidate_package.get('scenes')
        reviewed = rechecked_package.get('scenes')
        if not isinstance(original, list) or not original or not isinstance(reviewed, list) or len(original) != len(reviewed):
            raise ValueError('Narration scene count changed')
        for position, (before, after) in enumerate(zip(original, reviewed)):
            if not isinstance(before, dict) or not isinstance(after, dict):
                raise ValueError('Narration scene is invalid')
            text = before.get('narration')
            if not isinstance(text, str) or not text.strip() or after.get('narration') != text:
                raise ValueError('Narration changed')
            for scene in (before, after):
                if 'index' in scene and (type(scene['index']) is not int or scene['index'] != position):
                    raise ValueError('Narration scene order changed')
        joined = ' '.join(scene['narration'] for scene in original)
        if 'narration' in rechecked_package and rechecked_package['narration'] != joined:
            raise ValueError('Combined narration changed')
    except Exception:
        raise VoiceCandidateRecoveryError('Voice retry narration changed') from None
