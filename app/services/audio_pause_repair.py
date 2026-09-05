"""One bounded edit of proven internal dead air; never an audio approval."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from app.services.audio_qc import (
    _prosody_phrases_equivalent, _validate_prosody_review,
    _validated_prosody_timestamp_evidence,
)
from app.services.voice import _media_duration, fit_existing_narration_candidate


_MAX_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r'^[0-9a-f]{64}$')
_SILENCE_EVENT = re.compile(r'silence_(start|end):\s*([0-9]+(?:\.[0-9]+)?)')


def _number(value, *, zero=False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or not zero and value == 0:
        raise ValueError('Invalid audio repair timing')
    return float(value)


def _fingerprint(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or not 1024 <= path.stat().st_size <= _MAX_BYTES:
        raise ValueError('Invalid audio repair input')
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            if total == 0 and not (chunk.startswith(b'ID3') or len(chunk) >= 2 and chunk[0] == 0xFF and chunk[1] & 0xE0 == 0xE0):
                raise ValueError('Invalid audio repair format')
            total += len(chunk)
            if total > _MAX_BYTES:
                raise ValueError('Audio repair input too large')
            digest.update(chunk)
    if total < 1024:
        raise ValueError('Audio repair input incomplete')
    return digest.hexdigest()


def _silences(path: Path, duration: float) -> list[tuple[float, float]]:
    result = subprocess.run([
        'ffmpeg', '-hide_banner', '-nostats', '-protocol_whitelist', 'file,pipe',
        '-i', str(path), '-map', '0:a:0', '-af', 'silencedetect=noise=-38dB:d=0.08',
        '-vn', '-f', 'null', '-',
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45, check=True)
    intervals = []
    opened = None
    for kind, raw in _SILENCE_EVENT.findall(result.stderr):
        value = _number(float(raw), zero=True)
        if kind == 'start':
            if opened is not None or intervals and value < intervals[-1][1]:
                raise ValueError('Ambiguous silence evidence')
            opened = value
        else:
            if opened is None or value <= opened or value > duration + 0.1:
                raise ValueError('Invalid silence evidence')
            intervals.append((opened, min(value, duration)))
            opened = None
    if opened is not None or len(intervals) > 256:
        raise ValueError('Incomplete silence evidence')
    return intervals


def _text_windows(spoken: list[str], words: list[dict]) -> list[tuple[int, int, int]]:
    """Bind each source sentence to one contiguous, exhaustive STT window."""
    windows = []
    cursor = 0
    for scene_index, text in enumerate(spoken):
        for sentence in re.split(r'(?<=[.!?…;:])\s+', text.strip()):
            matches = [
                end for end in range(cursor + 1, len(words) + 1)
                if _prosody_phrases_equivalent(' '.join(word['text'] for word in words[cursor:end]), sentence)
            ]
            if len(matches) != 1:
                raise ValueError('Ambiguous source sentence timing')
            end = matches[0]
            windows.append((cursor, end, scene_index))
            cursor = end
    if cursor != len(words):
        raise ValueError('Source does not cover transcript')
    return windows


def _planned_cuts(voice: dict, duration: float, words: list[dict], review: dict,
                  silences: list[tuple[float, float]]) -> list[dict]:
    windows = _text_windows(voice['spoken_texts'], words)
    source_durations = voice['scene_durations']
    scale = duration / sum(source_durations)
    boundaries = [0.0]
    for value in source_durations:
        boundaries.append(boundaries[-1] + value * scale)
    cuts = []
    used_gaps = set()
    for issue in review['issues']:
        eligible = []
        for index, (left, right) in enumerate(zip(words, words[1:])):
            if left['start'] < issue['start_seconds'] - 0.002 or right['end'] > issue['end_seconds'] + 0.002:
                continue
            owners = [scene for start, end, scene in windows if start <= index and index + 1 < end]
            if len(owners) != 1:
                continue  # A sentence or scene boundary is not internal dead air.
            scene = owners[0]
            gap_start, gap_end = left['end'], right['start']
            if gap_start < boundaries[scene] or gap_end > boundaries[scene + 1]:
                continue
            matches = []
            for silence_start, silence_end in silences:
                start, end = max(gap_start, silence_start), min(gap_end, silence_end)
                if end - start < 0.8:
                    continue
                # Preserve both speech-side margins and at least .36s of the
                # original interword breath. Never cut an STT word interval.
                cut_start = max(silence_start + 0.06, gap_start + 0.18)
                cut_end = min(silence_end - 0.06, gap_end - 0.18)
                if cut_end > cut_start:
                    matches.append({'start_seconds': cut_start, 'end_seconds': cut_end,
                                    'removed_seconds': cut_end - cut_start, 'scene_index': scene})
            if len(matches) > 1:
                raise ValueError('Ambiguous waveform gap')
            if matches:
                eligible.append((index, matches[0]))
        if len(eligible) != 1 or eligible[0][0] in used_gaps:
            raise ValueError('Issue does not identify one unique internal gap')
        used_gaps.add(eligible[0][0])
        cuts.append(eligible[0][1])
    cuts.sort(key=lambda cut: cut['start_seconds'])
    if not 1 <= len(cuts) <= 3 or sum(cut['removed_seconds'] for cut in cuts) > 3:
        raise ValueError('Audio repair cut budget exceeded')
    if any(left['end_seconds'] >= right['start_seconds'] for left, right in zip(cuts, cuts[1:])):
        raise ValueError('Audio repair cuts overlap')
    return cuts


def _fit_is_bounded(duration: float, target: float, prior_rate: float) -> bool:
    minimum = target - 1.30
    desired = target - 1.25 if duration < minimum else target - 0.50
    needs_fit = duration < minimum or duration > desired + 0.015
    rate = round(duration / desired, 6) if needs_fit else 1.0
    return (0.94 <= rate <= 1.12 and 0.94 - 1e-12 <= prior_rate * rate <= 1.12 + 1e-12
            and minimum <= duration / rate <= target - 0.25)


def _cut_audio(source: Path, destination: Path, duration: float, cuts: list[dict]) -> None:
    ranges = []
    cursor = 0.0
    for cut in cuts:
        ranges.append((cursor, cut['start_seconds']))
        cursor = cut['end_seconds']
    ranges.append((cursor, duration))
    filters = [f'[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[k{i}]'
               for i, (start, end) in enumerate(ranges)]
    filters.append(''.join(f'[k{i}]' for i in range(len(ranges))) + f'concat=n={len(ranges)}:v=0:a=1[out]')
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(source),
        '-filter_complex', ';'.join(filters), '-map', '[out]', '-c:a', 'libmp3lame',
        '-b:a', '192k', str(destination),
    ], capture_output=True, timeout=45, check=True)


def repair_internal_pauses(voice_result: dict, target_seconds: float, *,
                          transcript_evidence: dict, prosody_review: dict) -> dict | None:
    """Edit only SHA-bound, reviewed internal silence, or leave bytes untouched.

    The caller supplies fresh STT evidence with audio_sha256 captured before
    that review and must run all audio QA again after success. This function
    performs no synthesis, storage, job/ledger writes or approval.
    """
    try:
        target = _number(target_seconds)
        if not 2 <= target <= 40 or not isinstance(voice_result, dict) or 'internal_pause_repair' in voice_result:
            return None
        voice = dict(voice_result)
        if voice.get('voice_language_code') != 'tr':
            return None
        path = Path(voice['path'])
        if not path.is_absolute() or path.suffix.lower() != '.mp3' or '..' in path.parts:
            return None
        source_sha = _fingerprint(path)
        if (not isinstance(transcript_evidence, dict)
                or not isinstance(transcript_evidence.get('audio_sha256'), str)
                or not _SHA.fullmatch(transcript_evidence['audio_sha256'])
                or transcript_evidence['audio_sha256'] != source_sha):
            return None
        evidence = _validated_prosody_timestamp_evidence(transcript_evidence)
        spoken = voice.get('spoken_texts')
        durations = voice.get('scene_durations')
        if (evidence is None or not isinstance(spoken, list) or not 1 <= len(spoken) <= 16
                or not all(isinstance(text, str) and text.strip() and len(text) <= 4000 for text in spoken)
                or not isinstance(durations, list) or len(durations) != len(spoken)):
            return None
        voice['scene_durations'] = [_number(value) for value in durations]
        duration = _number(_media_duration(path))
        if (duration > 40.1 or abs(duration - _number(voice['duration_after_fit'])) > 0.06
                or abs(sum(durations) - duration) > 0.10):
            return None
        prior_rate = _number(voice.get('tempo_rate'))
        if not 0.94 <= prior_rate <= 1.12:
            return None
        normalized = _validate_prosody_review(
            prosody_review, ' '.join(spoken), audio_duration_seconds=duration,
            transcript_evidence=transcript_evidence,
        )
        if (not isinstance(prosody_review, dict) or prosody_review.get('available') is not True
                or normalized is None or normalized['pass'] is not False
                or not 1 <= len(normalized['issues']) <= 3
                or any(issue['code'] != 'unnatural_internal_pause' for issue in normalized['issues'])):
            return None
        cuts = _planned_cuts(voice, duration, evidence[0], normalized, _silences(path, duration))
        removed = sum(cut['removed_seconds'] for cut in cuts)
        expected = duration - removed
        if not _fit_is_bounded(expected, target, prior_rate):
            return None
        with tempfile.TemporaryDirectory(prefix='internal_pause_', dir=path.parent, ignore_cleanup_errors=True) as temporary:
            source = Path(temporary) / 'source.mp3'
            output = Path(temporary) / 'candidate.mp3'
            shutil.copyfile(path, source)
            if _fingerprint(source) != source_sha:
                return None
            _cut_audio(source, output, duration, cuts)
            after_cut = _number(_media_duration(output))
            if abs(after_cut - expected) > 0.08:
                return None
            adjusted = [value * duration / sum(durations) for value in durations]
            for cut in cuts:
                adjusted[cut['scene_index']] -= cut['removed_seconds']
            voice.update(path=str(output), duration_after_fit=after_cut,
                         scene_durations=[value * after_cut / expected for value in adjusted])
            repaired = fit_existing_narration_candidate(voice, target)
            after = _number(_media_duration(output))
            if (abs(after - _number(repaired['duration_after_fit'])) > 0.06
                    or not target - 1.30 <= after <= target - 0.25
                    or not 0.94 - 1e-12 <= _number(repaired['tempo_rate']) <= 1.12 + 1e-12
                    or abs(sum(repaired['scene_durations']) - after) > 0.08
                    or any(_number(value) <= 0 for value in repaired['scene_durations'])):
                return None
            output_sha = _fingerprint(output)
            if output_sha == source_sha or _fingerprint(path) != source_sha:
                return None
            repaired['internal_pause_repair'] = {
                'version': 1, 'source_sha256': source_sha, 'output_sha256': output_sha,
                'cuts': cuts, 'removed_silence_seconds': removed,
                'duration_before_seconds': duration, 'duration_after_cut_seconds': after_cut,
                'duration_after_seconds': after, 'tempo_rate_before': prior_rate,
                'tempo_rate_after': repaired['tempo_rate'], 'requires_full_qa': True,
            }
            repaired['removed_silence_seconds'] = _number(voice_result.get('removed_silence_seconds', 0), zero=True) + removed
            for field in ('audio_qc', 'audio_duration_qc', 'audio_prosody_qc'):
                repaired.pop(field, None)
            repaired['path'] = str(path)
            output.replace(path)  # Only this final atomic step changes the original.
            return repaired
    except Exception:
        return None  # Failed repair is never permission to edit or approve audio.
