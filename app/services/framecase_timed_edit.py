"""Fit animated shots around the accepted performance, without editing audio.

Cut only in actual, independently transcribed inter-word gaps. Preserve every
spoken character, the original voice asset and original media allowance. A new
editorial package is reviewed before any image/video request; its cuts persist
so resuming the episode never buys a different timeline around old receipts.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from app.services import audio_qc as qc
from app.services.production_spend import SpendBlocked

VERSION = 'framecase-accepted-voice-cuts-v1'
_WORDS = re.compile(r'\S+')  # Same spoken-word budget as validate_package.


def _require(value, code='framecase_timed_edit_unverified'):
    if not value:
        raise SpendBlocked(code)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def fits(durations, effective):
    if not durations or not all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in durations):
        return False
    tail = max(0, effective - sum(durations))
    return all(math.ceil(seconds * 30) / 30 + (tail if i == len(durations)-1 else 0) <= 8
               for i, seconds in enumerate(durations))


def _word_gaps(narration, evidence, seconds):
    _require(evidence.get('available') is True and evidence.get('pass') is True)
    words = evidence.get('word_timestamps')
    _require(type(words) is list and 1 <= len(words) <= 1500)
    try:
        comparison = qc._require_word_timing_evidence(qc.compare_transcript(
            narration, evidence.get('transcript', ''), words=words,
            provider=evidence.get('provider'), comparison_language='en'), 'Framecase')
    except qc.AudioQCError as exc:
        raise SpendBlocked('framecase_timed_edit_unverified') from exc
    _require(comparison['pass'] is True)
    expected = qc._comparison_units(narration, 'en')
    heard = qc._comparison_units(evidence['transcript'], 'en')
    _require([row[0] for row in expected] == [row[0] for row in heard])
    surface = []; previous_start = previous_end = 0.0
    for index, row in enumerate(comparison['word_timestamps']):
        start, end = row['start'], row['end']
        _require(type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end)
            and previous_start <= start <= end <= seconds + .03 and previous_end <= end)
        tokens = qc._comparison_lexical_tokens(row['text'], 'en'); _require(tokens)
        surface.extend((token, index, start, min(end, seconds)) for token in tokens)
        previous_start, previous_end = start, end
    _require([token for _, sources in heard for token in sources] == [row[0] for row in surface])
    units = []; offset = 0
    for _, sources in heard:
        _require(sources and offset + len(sources) <= len(surface))
        left, right = surface[offset], surface[offset + len(sources)-1]
        units.append((left[1], right[1], left[2], right[3])); offset += len(sources)
    matches = list(_WORDS.finditer(narration)); gaps = []
    for count in range(1, len(matches)):
        position = matches[count].start()
        prefix = qc._comparison_units(narration[:position], 'en')
        cursor = len(prefix)
        # A normalized number/group must remain wholly on one side of the cut.
        if not 0 < cursor < len(units) or [row[0] for row in prefix] != [row[0] for row in expected[:cursor]]:
            continue
        left, right = units[cursor-1], units[cursor]
        if left[1] >= right[0] or left[3] > right[2]:
            continue
        first, last = math.ceil(left[3]*30), math.floor(right[2]*30)
        for frame in range(first, last+1):
            if 0 < frame < seconds*30:
                gaps.append((count, position, frame))
    return matches, gaps


def balanced_cuts(package, voice, transcript, effective, *, longform=False):
    try:
        return _balanced_cuts(package, voice, transcript, effective, longform=longform)
    except SpendBlocked as error:
        if longform or str(error) != 'framecase_timed_edit_infeasible':
            raise
    # A complete, well-performed 33-second narration cannot fit four native
    # eight-second clips. A fifth shot stays inside the existing six-request
    # allowance and leaves one correction slot. No accepted media is rebound:
    # prepare() still requires an empty media journal and a fresh story review.
    return _balanced_cuts(package, voice, transcript, effective, longform=False, shot_count=5)


def _balanced_cuts(package, voice, transcript, effective, *, longform, shot_count=None):
    narration = package['narration']; scenes = package['scenes']
    _require(narration == ' '.join(voice['spoken_texts'])
        and narration == ' '.join(row['narration'] for row in scenes))
    seconds = voice['duration_after_fit']
    _require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 < seconds <= 239
        and type(effective) in (int, float) and math.isfinite(effective)
        and seconds <= effective <= seconds+1 and len(scenes) == (30 if longform else 4))
    matches, gaps = _word_gaps(narration, transcript, seconds)
    lower, upper = (10, 20) if longform else (8, 23)
    total = len(matches); original_counts = []; cursor = 0
    for scene in scenes[:-1]:
        cursor += len(_WORDS.findall(scene['narration'])); original_counts.append(cursor)
    old_frames = []; cursor = 0.0
    for duration in voice['scene_durations'][:-1]:
        cursor += duration; old_frames.append(cursor*30)
    _require(len(old_frames) == len(original_counts))
    count_scenes = len(scenes) if shot_count is None else shot_count
    _require(count_scenes == len(scenes) or not longform and count_scenes == 5)
    if count_scenes != len(scenes):
        original_counts = [total * i / count_scenes for i in range(1, count_scenes)]
        old_frames = [seconds * 30 * i / count_scenes for i in range(1, count_scenes)]
    # Dynamic programming minimizes moved words, then favors sentence ends and
    # cuts close to the original edit. Every candidate is an actual silence gap.
    layer = {(0, 0): (0.0, [])}
    for stage in range(count_scenes-1):
        following = count_scenes-stage-1; next_layer = {}
        for count, position, frame in gaps:
            if not lower*following <= total-count <= upper*following:
                continue
            original_shot_count = count_scenes == len(scenes)
            penalty = (100 if original_shot_count else 1)*(count-original_counts[stage])**2
            penalty += (0 if re.search(r'[.!?][\"\'’”)]*\s*$', narration[:position])
                        else 10 if original_shot_count else 1000)
            penalty += ((frame-old_frames[stage])/30)**2
            choices = [(score+penalty, path+[(count, position, frame)])
                for (old_count, old_frame), (score, path) in layer.items()
                if lower <= count-old_count <= upper and 0 < frame-old_frame <= 240]
            if choices:
                next_layer[(count, frame)] = min(choices, key=lambda row: (row[0], row[1]))
        layer = next_layer
        _require(layer, 'framecase_timed_edit_infeasible')
    endings = []
    for (count, frame), (score, path) in layer.items():
        if not lower <= total-count <= upper:
            continue
        starts = [0, *[row[2] for row in path]]
        durations = [(end-start)/30 for start, end in zip(starts, starts[1:])]
        durations.append(seconds-frame/30)
        if fits(durations, effective):
            endings.append((score, path, durations))
    _require(endings, 'framecase_timed_edit_infeasible')
    _, path, durations = min(endings, key=lambda row: (row[0], row[1]))
    positions = [0, *[row[1] for row in path], len(narration)]
    spoken = [narration[start:end].strip() for start, end in zip(positions, positions[1:])]
    _require(' '.join(spoken) == narration and len(durations) == count_scenes
        and abs(sum(durations)-seconds) < 1e-8 and fits(durations, effective))
    return {'scene_narrations': spoken, 'scene_durations': durations,
            'cut_frames': [row[2] for row in path], 'audio_edited': False,
            'timing_source': 'accepted_audio_word_gaps'}


def prepare(package, voice, audio_qc, effective, *, longform, checkpoint, task, client, draft):
    if fits(voice['scene_durations'], effective):
        _require('timed_edit' not in checkpoint, 'framecase_timed_edit_changed')
        return deepcopy(package), list(voice['scene_durations'])
    basis = {'version': VERSION, 'original_package_sha256': _sha(package),
        'voice_asset_sha256': voice['asset']['sha256'], 'voice_sha256': _sha(voice),
        'transcript_sha256': _sha(audio_qc['transcript']), 'effective_seconds': effective}
    cuts = balanced_cuts(package, voice, audio_qc['transcript'], effective, longform=longform)
    saved = checkpoint.get('timed_edit')
    if saved is None:
        # A different cut cannot be invented around an accepted/unknown media
        # request. These records are never erased to make this condition true.
        _require(not checkpoint.get('clips') and not checkpoint.get('creates')
            and not checkpoint.get('keyframes') and not checkpoint.get('cast_reference')
            and not client.exists('youtube_studio:commissioning:v1:framecase_image:'+task,
                                  'youtube_studio:commissioning:v1:video:'+task),
            'framecase_timed_edit_media_already_bound')
        candidate = draft(cuts['scene_narrations'])
        _require([row['narration'] for row in candidate['scenes']] == cuts['scene_narrations']
                 and candidate['narration'] == package['narration'])
        saved = {**basis, **cuts, 'package': deepcopy(candidate)}
    _require(all(saved.get(k) == v for k, v in {**basis, **cuts}.items()), 'framecase_timed_edit_changed')
    from app.services.framecase_pipeline import CHECKS
    candidate = saved['package']
    _require([row['narration'] for row in candidate['scenes']] == cuts['scene_narrations']
        and candidate['narration'] == package['narration']
        and all(candidate.get('fiction_review', {}).get(k) is True for k in CHECKS)
        and candidate['fiction_review'].get('findings') == [])
    if 'timed_edit' not in checkpoint:
        checkpoint['timed_edit'] = deepcopy(saved)
    return deepcopy(candidate), list(cuts['scene_durations'])
