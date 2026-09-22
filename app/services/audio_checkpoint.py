"""Persist existing, explicitly unapproved narration before temp cleanup.

This module neither generates audio nor verifies it. A checkpoint is only a
recovery candidate; normal source, narration, prosody and final-video QA must
run before any later reuse or publication.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import stat
import tempfile
from uuid import UUID
from urllib.parse import urlsplit

from app.services.storage import upload_file


MAX_AUDIO_CANDIDATE_BYTES = 14 * 1024 * 1024
MAX_AUDIO_CANDIDATE_METADATA_BYTES = 512 * 1024
_AUDIO_TEMP_ROOT = Path('/tmp')
_NON_CITATION_HOST_SUFFIXES = (
    'localhost', 'local', 'internal', 'test', 'invalid', 'example',
    'storageapi.dev', 'amazonaws.com', 'blob.core.windows.net',
    'railway.app', 'elevenlabs.io', 'api.openai.com',
    'generativelanguage.googleapis.com', 'runwayml.com', 'replicate.delivery',
)
_PRIVATE_TEXT = re.compile(
    r'https?://|\bBearer\s+\S+|\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})'
    r'|\b(?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|'
    r'client[_ -]?secret|password)\s*[:=]',
    re.IGNORECASE,
)


class AudioCandidateCheckpointError(RuntimeError):
    """A deliberately non-sensitive checkpoint failure."""


def _plain_text(value: object, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or _PRIVATE_TEXT.search(value):
        raise ValueError('Invalid candidate text')
    return value


def _number(value: object, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Invalid candidate timing')
    number = float(value)
    if not math.isfinite(number) or not (0 <= number <= 86400):
        raise ValueError('Invalid candidate timing')
    if not allow_zero and number == 0:
        raise ValueError('Invalid candidate timing')
    return number


def _public_sources(value: object) -> list[dict]:
    """Keep public citation references, never asset links or credentials.

    This validates URL shape only and does not fetch a source or assert that
    its contents have passed factual QA. Invalid references are omitted so
    they cannot prevent preservation of otherwise valid paid narration.
    """
    result = []
    seen = set()
    if not isinstance(value, list):
        return result
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        try:
            url = item.get('url')
            if not isinstance(url, str) or not 1 <= len(url) <= 2000 or any(ord(char) <= 32 for char in url):
                continue
            parsed = urlsplit(url)
            hostname = (parsed.hostname or '').casefold().rstrip('.')
            if (
                parsed.scheme not in {'http', 'https'} or not hostname
                or parsed.username is not None or parsed.password is not None
                or (parsed.query and not _public_entry_query(parsed))
                or parsed.fragment or parsed.port not in (None, 80, 443)
                or any(hostname == suffix or hostname.endswith('.' + suffix) for suffix in _NON_CITATION_HOST_SUFFIXES)
            ):
                continue
            try:
                if not ipaddress.ip_address(hostname).is_global:
                    continue
            except ValueError:
                if '.' not in hostname or not re.fullmatch(r'[a-z0-9.-]+', hostname):
                    continue
                if re.fullmatch(r'[0-9.]+', hostname) or hostname.isdigit():
                    continue
            evidence = _plain_text(item.get('evidence'), 600)
            if len(evidence.strip()) < 12 or url in seen:
                continue
        except (TypeError, ValueError):
            continue
        result.append({'url': url, 'evidence': evidence})
        seen.add(url)
        if len(result) == 5:
            break
    return result


def _public_entry_query(parsed) -> bool:
    """An encyclopedia entry selector is public source identity, not a token.

    Only this documented public route and bounded identifier are retained.
    Additional, encoded, duplicate or credential parameters remain excluded.
    """
    return bool(parsed.scheme == 'https' and parsed.hostname == 'www.okhistory.org'
        and parsed.path == '/publications/enc/entry'
        and re.fullmatch(r'entry=[A-Z]{2}[0-9]{3}', parsed.query))


def _candidate_package(package: dict) -> dict:
    scenes = package.get('scenes')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 256:
        raise ValueError('Invalid candidate scenes')
    result = {}
    for field, limit in (('title', 500), ('thumbnail_text', 500)):
        if package.get(field) is not None:
            result[field] = _plain_text(package[field], limit)
    result['scenes'] = []
    for position, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise ValueError('Invalid candidate scene')
        item = {'index': position, 'narration': _plain_text(scene.get('narration'), 4000)}
        if not item['narration'].strip():
            raise ValueError('Missing candidate narration')
        for field, limit in (('ai_prompt', 12000), ('pace', 80), ('transition', 80)):
            if scene.get(field) is not None:
                item[field] = _plain_text(scene[field], limit)
            elif field == 'ai_prompt':
                item[field] = None
        if 'visual_queries' in scene:
            queries = scene['visual_queries']
            if not isinstance(queries, list) or len(queries) > 8:
                raise ValueError('Invalid candidate queries')
            item['visual_queries'] = [_plain_text(query, 600) for query in queries]
        result['scenes'].append(item)
    result['sources'] = _public_sources(package.get('sources'))
    if 'spoken_word_budget' in package:
        from app.services.director import validate_spoken_word_budget

        # Keep only the fixed planning profile, never an approval. It is part
        # of the content-addressed package; reuse still needs exact job/spec
        # binding, immutable story review, and every normal audio/media gate.
        result['spoken_word_budget'] = validate_spoken_word_budget(package['spoken_word_budget'])
    return result


def _candidate_voice(voice_result: dict, scene_count: int) -> dict:
    spoken = voice_result.get('spoken_texts')
    durations = voice_result.get('scene_durations')
    if (
        not isinstance(spoken, list) or not isinstance(durations, list)
        or len(spoken) != scene_count or len(durations) != scene_count
    ):
        raise ValueError('Invalid candidate voice scenes')
    result = {
        'spoken_texts': [_plain_text(text, 4000) for text in spoken],
        'scene_durations': [_number(value) for value in durations],
        'duration_before_fit': _number(voice_result.get('duration_before_fit')),
        'duration_after_fit': _number(voice_result.get('duration_after_fit')),
        'tempo_rate': _number(voice_result.get('tempo_rate')),
        'voice_profile': {},
    }
    for field in ('content_target_seconds', 'reserved_tail_seconds', 'removed_silence_seconds'):
        if voice_result.get(field) is not None:
            result[field] = _number(voice_result[field], allow_zero=True)
    for field in ('voice_name', 'voice_model', 'voice_language_code'):
        if voice_result.get(field) is not None:
            result['voice_profile'][field] = _plain_text(voice_result[field], 200)
    return result


def _local_candidate_path(task_id: str, voice_result: dict) -> Path:
    raw = voice_result.get('path')
    if not isinstance(raw, (str, Path)) or '://' in str(raw):
        raise ValueError('Invalid candidate path')
    path = Path(raw)
    root = _AUDIO_TEMP_ROOT.absolute()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid candidate path')
    relative = path.relative_to(root).as_posix()
    expected = (
        rf'{re.escape(task_id)}(?:_audio_retry_[0-9]{{1,2}})?\.mp3'
        rf'|{re.escape(task_id)}(?:_audio_retry_[0-9]{{1,2}})?_voice/joined\.mp3'
        rf'|youtube_factory/{re.escape(task_id)}_attempt_[0-9]{{1,2}}/recovered_voice\.mp3'
    )
    if re.fullmatch(expected, relative) is None:
        raise ValueError('Candidate is outside task audio paths')
    resolved = path.resolve(strict=True)
    # A same-named symlink must not expose another job or an arbitrary file.
    if resolved != root.resolve(strict=True) / relative or not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError('Invalid candidate file')
    if not 1024 <= resolved.stat().st_size <= MAX_AUDIO_CANDIDATE_BYTES:
        raise ValueError('Invalid candidate size')
    return resolved


def persist_audio_candidate_checkpoint(
    task_id: str,
    package: dict,
    voice_result: dict,
) -> dict:
    """Return one job field after storing the existing MP3 and safe manifest.

    Callers may catch AudioCandidateCheckpointError and record a fixed
    unavailable status. Storage responses and exception details are never
    copied into the checkpoint, returned to the caller, or logged here.
    """
    try:
        canonical_id = str(UUID(task_id))
        if canonical_id != task_id or not isinstance(package, dict) or not isinstance(voice_result, dict):
            raise ValueError('Invalid candidate inputs')
        candidate_package = _candidate_package(package)
        candidate_voice = _candidate_voice(voice_result, len(candidate_package['scenes']))
        source = _local_candidate_path(canonical_id, voice_result)
        package_bytes = json.dumps(candidate_package, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        package_sha256 = hashlib.sha256(package_bytes).hexdigest()

        with tempfile.TemporaryDirectory(prefix='audio_checkpoint_', dir=_AUDIO_TEMP_ROOT) as temporary:
            audio_path = Path(temporary) / 'candidate.mp3'
            audio_hash = hashlib.sha256()
            total = 0
            with source.open('rb') as incoming, audio_path.open('wb') as outgoing:
                while chunk := incoming.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_AUDIO_CANDIDATE_BYTES:
                        raise ValueError('Candidate grew beyond its size cap')
                    if total == len(chunk) and not (
                        chunk.startswith(b'ID3')
                        or (len(chunk) >= 2 and chunk[0] == 0xFF and chunk[1] & 0xE0 == 0xE0)
                    ):
                        raise ValueError('Candidate is not MP3 audio')
                    outgoing.write(chunk)
                    audio_hash.update(chunk)
            if total < 1024:
                raise ValueError('Candidate audio is incomplete')
            checksum = audio_hash.hexdigest()
            prefix = f'audio_candidates/{canonical_id}/{checksum}'
            audio_key = f'{prefix}/candidate.mp3'
            metadata = {
                'version': 1,
                'status': 'unapproved_candidate',
                'qa_approved': False,
                'requires_full_qa': True,
                'source_task_id': canonical_id,
                'audio': {'key': audio_key, 'sha256': checksum, 'size': total},
                'package_sha256': package_sha256,
                'package': candidate_package,
                'voice': candidate_voice,
            }
            metadata_bytes = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            if len(metadata_bytes) > MAX_AUDIO_CANDIDATE_METADATA_BYTES:
                raise ValueError('Candidate metadata is too large')
            metadata_sha256 = hashlib.sha256(metadata_bytes).hexdigest()
            metadata_key = f'{prefix}/metadata-{metadata_sha256}.json'
            metadata_path = Path(temporary) / 'metadata.json'
            metadata_path.write_bytes(metadata_bytes)
            upload_file(audio_path, audio_key, 'audio/mpeg')
            upload_file(metadata_path, metadata_key, 'application/json')
        return {'audio_candidate_checkpoint': {
            'version': 1,
            'status': 'unapproved_candidate',
            'qa_approved': False,
            'requires_full_qa': True,
            'audio_key': audio_key,
            'metadata_key': metadata_key,
            'audio_sha256': checksum,
            'metadata_sha256': metadata_sha256,
            'package_sha256': package_sha256,
            'size': total,
        }}
    except Exception:
        raise AudioCandidateCheckpointError('Audio candidate checkpoint unavailable') from None
