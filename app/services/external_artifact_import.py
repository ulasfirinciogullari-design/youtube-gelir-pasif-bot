"""Validate a staged external master, never approve, publish or generate it.

Inputs are server-local uploads beneath an explicitly supplied staging root.
No URL fetching, archive extraction, executable loading, storage writes, job
mutation, media generation, or critic call occurs here. The returned descriptor
is private worker input, not a public Studio result or an authorization token.
All quality/provenance declarations remain unverified until a separate review.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import unicodedata
from urllib.parse import urlsplit

from app.services.source_evidence import normalize_evidence_sources


MAX_MANIFEST_BYTES = 64 * 1024
MAX_VIDEO_BYTES = 128 * 1024 * 1024
MAX_CAPTION_BYTES = 64 * 1024
MAX_PROBE_BYTES = 64 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_SECRET = re.compile(r'(?i)(?:\bbearer\s+\S+|\bsk-[\w-]{12,}|\bAIza[\w-]{20,}|'
                     r'\b(?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|'
                     r'client[_ -]?secret|password|secret)[\s\x22\x27]*[:=])')
_UNKNOWN_ORIGIN_FIELDS = {'model': 128, 'voice_provider': 64, 'voice_model': 128}


class ExternalArtifactValidationError(ValueError):
    """Sanitized structural rejection, never a claim about creative quality."""


def _require(value, code='external_artifact_invalid'):
    if not value:
        raise ExternalArtifactValidationError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _object(raw, *, limit=MAX_MANIFEST_BYTES):
    if type(raw) is dict:
        raw = _json(raw)
    if isinstance(raw, bytes):
        _require(len(raw) <= limit)
        raw = raw.decode('utf-8')
    _require(isinstance(raw, str) and 0 < len(raw.encode('utf-8')) <= limit)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, 'external_manifest_duplicate_field')
            value[key] = item
        return value
    def invalid(_):
        raise ExternalArtifactValidationError('external_manifest_nonfinite_number')
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    _require(type(value) is dict)
    return value


def _text(value, maximum):
    _require(type(value) is str and 1 <= len(value) <= maximum and value == value.strip()
             and not _SECRET.search(value) and not any(ord(c) < 32 and c not in '\n\t' for c in value)
             and not any(c in value for c in ('<', '>', '\x7f')), 'external_text_invalid')
    _require(not any(unicodedata.category(c) in {'Cf', 'Cs'} for c in value), 'external_text_invalid')
    return value


def _canonical_text(value):
    # No case folding, number conversion, punctuation removal or ASR waiver.
    return ' '.join(unicodedata.normalize('NFC', value).split())


def _source_url(value):
    _require(type(value) is str and 1 <= len(value) <= 2000 and not _SECRET.search(value)
             and not any(c.isspace() or ord(c) < 32 for c in value), 'external_source_invalid')
    parts = urlsplit(value)
    host = (parts.hostname or '').lower().rstrip('.')
    _require(parts.scheme in {'http', 'https'} and host and parts.username is None and parts.password is None
             and not parts.query and not parts.fragment and parts.port in (None, 80, 443), 'external_source_invalid')
    forbidden = ('localhost', 'local', 'internal', 'test', 'invalid', 'example', 'storageapi.dev',
                 'railway.app', 'amazonaws.com', 'blob.core.windows.net', 'googleusercontent.com')
    _require(not any(host == item or host.endswith('.' + item) for item in forbidden), 'external_source_invalid')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        numeric = all(re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)', item) for item in host.split('.'))
        _require('.' in host and re.fullmatch(r'[a-z0-9.-]+', host) and not numeric
                 and all(label and not label.startswith('-') and not label.endswith('-') for label in host.split('.')),
                 'external_source_invalid')
    else:
        _require(address.is_global, 'external_source_invalid')
    return value


def validate_external_manifest(manifest):
    """Validate versioned declarations without treating them as proof.

    V1 retains its original 30-second contract. V2 supports bounded TR/EN
    masters at their declared millisecond duration, still with six scenes.
    """
    try:
        value = _object(manifest)
        _require(set(value) == {'version', 'origin', 'language', 'format', 'duration_ms', 'title',
                               'scenes', 'sources', 'files'}, 'external_manifest_schema_invalid')
        _require(type(value['version']) is int and value['version'] in (1, 2)
                 and value['format'] == 'shorts' and type(value['duration_ms']) is int)
        if value['version'] == 1:
            _require(value['language'] in {'tr', 'en', 'de', 'es', 'ar'} and value['duration_ms'] == 30000)
        else:
            _require(value['language'] in {'tr', 'en'} and 15000 <= value['duration_ms'] <= 60000)
        _text(value['title'], 140)
        origin = value['origin']
        _require(type(origin) is dict and set(origin) == {'kind', 'provider', *_UNKNOWN_ORIGIN_FIELDS}
                 and origin['kind'] == 'external_master' and origin['provider'] in {'abacus', 'unknown'},
                 'external_origin_invalid')
        for key, maximum in _UNKNOWN_ORIGIN_FIELDS.items():
            if origin[key] is not None:
                _text(origin[key], maximum)
        scenes = value['scenes']
        _require(type(scenes) is list and len(scenes) == 6, 'external_scene_count_invalid')
        cursor = 0
        for index, scene in enumerate(scenes):
            _require(type(scene) is dict and set(scene) == {'index', 'start_ms', 'end_ms', 'narration', 'visual_intent'}
                     and type(scene['index']) is int and scene['index'] == index
                     and type(scene['start_ms']) is int and type(scene['end_ms']) is int
                     and scene['start_ms'] == cursor and 1000 <= scene['end_ms'] - cursor <= 10000,
                     'external_scene_timeline_invalid')
            _text(scene['narration'], 1000)
            _text(scene['visual_intent'], 1000)
            _require(not re.search(r'https?://', scene['narration'], re.I), 'external_narration_invalid')
            cursor = scene['end_ms']
        _require(cursor == value['duration_ms'], 'external_scene_coverage_invalid')
        _require(type(value['sources']) is list and 2 <= len(value['sources']) <= 5)
        for source in value['sources']:
            _require(type(source) is dict and set(source) == {'url', 'evidence'})
            _source_url(source['url'])
            _text(source['evidence'], 600)
        sources = normalize_evidence_sources(value['sources'], min_count=2, max_count=5)
        _require(sources == value['sources'], 'external_sources_not_canonical')
        files = value['files']
        _require(type(files) is dict and set(files) == {'video', 'captions'})
        for key, content_type, minimum, maximum in (
            ('video', 'video/mp4', 1024, MAX_VIDEO_BYTES),
            ('captions', 'application/x-subrip', 1, MAX_CAPTION_BYTES),
        ):
            item = files[key]
            _require(type(item) is dict and set(item) == {'sha256', 'size', 'content_type'}
                     and type(item['sha256']) is str and _SHA.fullmatch(item['sha256'])
                     and type(item['size']) is int and minimum <= item['size'] <= maximum
                     and item['content_type'] == content_type, 'external_file_declaration_invalid')
        return value
    except ExternalArtifactValidationError:
        raise
    except Exception:
        raise ExternalArtifactValidationError('external_manifest_invalid') from None


def _srt_time(value):
    matched = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})', value)
    _require(matched, 'external_caption_timestamp_invalid')
    h, m, s, ms = map(int, matched.groups())
    _require(m < 60 and s < 60, 'external_caption_timestamp_invalid')
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def validate_external_captions(data, manifest):
    """Verify actual SRT order/times and exact per-scene declared text.

    This proves only caption/manifest coherence, not that the audio says it.
    Cue gaps are allowed; overlaps, cross-scene cues and omitted words are not.
    """
    try:
        _require(isinstance(data, bytes) and 0 < len(data) <= MAX_CAPTION_BYTES)
        text = data.decode('utf-8-sig').replace('\r\n', '\n')
        _require('\r' not in text and text.strip(), 'external_caption_encoding_invalid')
        blocks = re.split(r'\n[ \t]*\n', text.strip())
        _require(6 <= len(blocks) <= 180, 'external_caption_count_invalid')
        by_scene, cues, previous_end = [[] for _ in range(6)], [], 0
        for number, block in enumerate(blocks, 1):
            lines = block.split('\n')
            _require(3 <= len(lines) <= 6 and lines[0] == str(number), 'external_caption_sequence_invalid')
            timing = re.fullmatch(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})', lines[1])
            _require(timing, 'external_caption_timestamp_invalid')
            start, end = map(_srt_time, timing.groups())
            _require(previous_end <= start < end <= manifest['duration_ms'], 'external_caption_overlap_or_range')
            caption = _canonical_text(_text('\n'.join(lines[2:]), 1000))
            _require(not re.search(r'\{\\|https?://', caption, re.I), 'external_caption_markup_invalid')
            matches = [scene['index'] for scene in manifest['scenes'] if scene['start_ms'] <= start < end <= scene['end_ms']]
            _require(len(matches) == 1, 'external_caption_crosses_scene')
            by_scene[matches[0]].append(caption)
            cues.append({'index': number, 'start_ms': start, 'end_ms': end, 'scene_index': matches[0], 'text': caption})
            previous_end = end
        for index, scene in enumerate(manifest['scenes']):
            _require(by_scene[index] and _canonical_text(' '.join(by_scene[index])) == _canonical_text(scene['narration']),
                     'external_caption_narration_mismatch')
        return {'cue_count': len(cues), 'cues': cues, 'coherence': 'exact_scene_text_and_timing_only',
                'audio_transcription_verified': False}
    except ExternalArtifactValidationError:
        raise
    except Exception:
        raise ExternalArtifactValidationError('external_captions_invalid') from None


def _no_link(path):
    details = path.lstat()
    _require(not stat.S_ISLNK(details.st_mode)
             and not (getattr(details, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)),
             'external_path_link_not_allowed')
    return details


def _staged_path(root, value, suffix):
    _require(isinstance(value, (str, Path)) and '://' not in str(value)
             and not str(value).startswith(('\\\\', '//')), 'external_local_path_required')
    candidate = Path(value)
    _require('..' not in candidate.parts, 'external_path_outside_staging')
    candidate = candidate if candidate.is_absolute() else root / candidate
    # Reject links before resolving so a link cannot hide behind its target.
    lexical = Path(os.path.abspath(candidate))
    _require(lexical.is_relative_to(root) and lexical != root, 'external_path_outside_staging')
    walk = lexical
    while walk != root:
        _no_link(walk)
        walk = walk.parent
    resolved = lexical.resolve(strict=True)
    _require(resolved.is_relative_to(root) and resolved.suffix.lower() == suffix, 'external_file_type_invalid')
    info = _no_link(resolved)
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'external_regular_file_required')
    return resolved


def _fingerprint(path, expected, *, return_bytes=False):
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    before_path = _no_link(path)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                 and before.st_size == expected['size'], 'external_file_size_changed')
        digest, size, parts = hashlib.sha256(), 0, []
        while chunk := stream.read(min(64 * 1024, expected['size'] - size + 1)):
            size += len(chunk)
            _require(size <= expected['size'], 'external_file_size_changed')
            digest.update(chunk)
            if return_bytes:
                parts.append(chunk)
        after = os.fstat(stream.fileno())
        _require(all(getattr(before, k) == getattr(after, k) for k in fields)
                 and size == expected['size'] and digest.hexdigest() == expected['sha256'], 'external_file_hash_changed')
    current = _no_link(path)
    # Windows can expose different creation/change-time semantics through
    # fstat and lstat. Compare ctime only within the same API, while binding
    # the opened file to its path with device, inode, size and exact mtime.
    _require(all(getattr(before_path, k) == getattr(current, k) for k in fields)
             and all(getattr(after, k) == getattr(current, k) for k in fields[:-1])
             and current.st_nlink == 1, 'external_file_changed')
    return (b''.join(parts) if return_bytes else None), tuple(getattr(current, k) for k in fields)


def _positive_number(value):
    _require(type(value) in (str, int, float) and not isinstance(value, bool))
    number = float(value)
    _require(math.isfinite(number) and number > 0, 'external_probe_number_invalid')
    return number


def _probe_mp4(path, *, expected_duration=30):
    _require(type(expected_duration) in (int, float) and math.isfinite(expected_duration)
             and 15 <= expected_duration <= 60, 'external_expected_duration_invalid')
    with path.open('rb') as stream:
        header = stream.read(32)
    _require(len(header) == 32 and header[4:8] == b'ftyp'
             and 16 <= int.from_bytes(header[:4], 'big') <= 65536, 'external_mp4_header_invalid')
    command = ['ffprobe', '-v', 'error', '-f', 'mov', '-protocol_whitelist', 'file', '-enable_drefs', '0',
               '-use_absolute_path', '0', '-count_frames', '-show_entries',
               'format=format_name,duration,size:stream=index,codec_type,codec_name,width,height,pix_fmt,'
               'avg_frame_rate,r_frame_rate,duration,nb_read_frames,sample_rate,channels:'
               'stream_disposition=attached_pic:stream_tags=rotate:stream_side_data=rotation',
               '-of', 'json', str(path)]
    completed = subprocess.run(command, capture_output=True, check=False, timeout=45,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _require(completed.returncode == 0 and isinstance(completed.stdout, bytes)
             and 0 < len(completed.stdout) <= MAX_PROBE_BYTES, 'external_probe_failed')
    value = _object(completed.stdout, limit=MAX_PROBE_BYTES)
    streams, container = value.get('streams'), value.get('format')
    _require(type(streams) is list and len(streams) == 2 and all(type(s) is dict for s in streams)
             and type(container) is dict and 'mp4' in str(container.get('format_name', '')).split(','),
             'external_streams_invalid')
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    _require(len(videos) == len(audios) == 1, 'external_streams_invalid')
    video, audio = videos[0], audios[0]
    _require(video.get('codec_name') == 'h264' and video.get('pix_fmt') in {'yuv420p', 'yuvj420p'}
             and audio.get('codec_name') == 'aac', 'external_codec_profile_unsupported')
    width, height = video.get('width'), video.get('height')
    _require(type(width) is int and type(height) is int and 720 <= width <= 2160 and 1280 <= height <= 3840
             and width % 2 == height % 2 == 0 and abs(width / height - 9 / 16) <= .002,
             'external_portrait_canvas_invalid')
    _require((video.get('disposition') or {}).get('attached_pic', 0) == 0
             and (video.get('tags') or {}).get('rotate', '0') in ('0', 0)
             and all(item.get('rotation', 0) == 0 for item in video.get('side_data_list', [])),
             'external_rotation_or_cover_art_invalid')
    rate = video.get('avg_frame_rate')
    _require(type(rate) is str and re.fullmatch(r'[0-9]+/[0-9]+', rate), 'external_frame_rate_invalid')
    fps = float(Fraction(rate))
    duration = _positive_number(video.get('duration'))
    container_duration = _positive_number(container.get('duration'))
    audio_duration = _positive_number(audio.get('duration'))
    frames = video.get('nb_read_frames')
    _require(type(frames) is str and re.fullmatch(r'[1-9][0-9]*', frames)
             and 23 <= fps <= 60 and abs(duration - expected_duration) <= .100
             and abs(container_duration - expected_duration) <= .250
             and abs(int(frames) - fps * duration) <= 1.1, 'external_duration_or_frames_invalid')
    _require(1 <= audio_duration <= duration + .250
             and str(audio.get('sample_rate')) in {'44100', '48000'}
             and type(audio.get('channels')) is int and audio['channels'] in {1, 2}, 'external_audio_stream_invalid')
    _require(str(container.get('size')) == str(path.stat().st_size), 'external_probe_size_mismatch')
    return {'video_codec': 'h264', 'audio_codec': 'aac', 'width': width, 'height': height,
            'pixel_format': video['pix_fmt'], 'frame_rate': rate, 'frame_count': int(frames),
            'video_duration_seconds': duration, 'audio_duration_seconds': audio_duration,
            'container_duration_seconds': container_duration,
            'sample_rate': int(audio['sample_rate']), 'audio_channels': audio['channels']}


def validate_staged_external_artifact(staging_root, video_path, captions_path, manifest):
    """Return only an unapproved, hash-bound descriptor of existing files.

    Subsequent readers must recheck these hashes; this read-only function does
    not lock the filesystem forever. Local paths belong only in private worker
    state, never in public metadata. No caller QA flag is accepted by the schema.
    """
    try:
        value = validate_external_manifest(manifest)
        _require(isinstance(staging_root, (str, Path)) and '://' not in str(staging_root)
                 and not str(staging_root).startswith(('\\\\', '//')), 'external_staging_root_invalid')
        supplied_root = Path(staging_root)
        _require(supplied_root.is_absolute() and stat.S_ISDIR(_no_link(supplied_root).st_mode), 'external_staging_root_invalid')
        root = supplied_root.resolve(strict=True)
        video = _staged_path(root, video_path, '.mp4')
        captions = _staged_path(root, captions_path, '.srt')
        _require(video != captions, 'external_files_must_differ')
        _, video_identity = _fingerprint(video, value['files']['video'])
        caption_bytes, caption_identity = _fingerprint(captions, value['files']['captions'], return_bytes=True)
        caption_contract = validate_external_captions(caption_bytes, value)
        media = (_probe_mp4(video) if value['version'] == 1
                 else _probe_mp4(video, expected_duration=value['duration_ms'] / 1000))
        # Probe and caption parsing cannot certify bytes subsequently replaced.
        _require(_staged_path(root, video, '.mp4') == video and _staged_path(root, captions, '.srt') == captions)
        _require(_fingerprint(video, value['files']['video'])[1] == video_identity
                 and _fingerprint(captions, value['files']['captions'])[1] == caption_identity, 'external_file_changed')
        manifest_sha = hashlib.sha256(_json(value).encode('utf-8')).hexdigest()
        identity = {'manifest_sha256': manifest_sha, 'video_sha256': value['files']['video']['sha256'],
                    'captions_sha256': value['files']['captions']['sha256']}
        return {'version': 1, 'status': 'unapproved_external_candidate',
                'descriptor_id': hashlib.sha256(_json(identity).encode('utf-8')).hexdigest(), **identity,
                'manifest': value, 'provenance_status': 'caller_declared_unverified',
                'files': {'video': {**value['files']['video'], 'local_path': str(video)},
                          'captions': {**value['files']['captions'], 'local_path': str(captions)}},
                'media_structure': media, 'caption_contract': caption_contract,
                'qa_approved': False, 'publish_eligible': False, 'media_generation_authorized': False,
                'requires_fresh_story_qa': True, 'requires_fresh_audio_qa': True,
                'requires_fresh_visual_qa': True, 'requires_current_episode_and_oauth_binding': True,
                'source_evidence_verified': False, 'audio_transcription_verified': False}
    except ExternalArtifactValidationError:
        raise
    except Exception:
        raise ExternalArtifactValidationError('external_artifact_validation_unavailable') from None
