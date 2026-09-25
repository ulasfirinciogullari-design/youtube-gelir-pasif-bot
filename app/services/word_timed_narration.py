"""Scene cuts from real blind-ASR words, without invented character timing.

Keep the complete audio performance. Match every canonical spoken unit and
place visual boundaries only between complete words belonging to neighboring
scenes. Speech-number normalization comes from the same strict audio QC rules.
"""
import math

from app.services import audio_qc as qc
from app.services.voice import VoiceQualityError


def require(value):
    if not value:
        raise VoiceQualityError('Blind speech timing does not match the scene narration')


def edit_plan(spoken, evidence, media_seconds, *, language):
    require(type(spoken) is list and 1 <= len(spoken) <= 60
        and all(type(x) is str and x.strip() for x in spoken)
        and type(media_seconds) in (int, float) and math.isfinite(media_seconds)
        and 0 < media_seconds <= 240 and language in {'tr', 'en'})
    narration = ' '.join(spoken)
    comparison = qc.compare_transcript(
        narration, evidence['text'], words=evidence['words'],
        language_code=evidence.get('language'), provider='openai',
        comparison_language=language)
    # A captured transcript missing speech is a bounded synthesis defect.
    # Diagnose it before timestamp validity so the existing take limit can
    # recover; unknown ASR requests still fail closed before this function.
    require(comparison['pass'] is True)
    comparison = qc._require_word_timing_evidence(comparison, 'OpenAI')
    expected = qc._comparison_units(narration, language)
    scenes = [qc._comparison_units(scene, language) for scene in spoken]
    # Never split a number or combine a semantic unit across two scenes.
    require(all(scenes) and [u[0] for scene in scenes for u in scene] == [u[0] for u in expected])
    heard = qc._comparison_units(evidence['text'], language,
        expected_apostrophe_suffixes=qc._expected_apostrophe_suffixes(narration) if language == 'tr' else frozenset())
    require([u[0] for u in heard] == [u[0] for u in expected])
    surface = []
    previous_start = previous_end = 0.0
    for word_index, word in enumerate(comparison['word_timestamps']):
        start, end = word['start'], word['end']
        require(type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end)
            and previous_start <= start <= end <= media_seconds + 0.03
            and previous_end <= end)
        tokens = qc._comparison_lexical_tokens(word['text'], language)
        require(tokens)
        surface.extend((token, word_index, start, min(end, media_seconds)) for token in tokens)
        previous_start, previous_end = start, end
    # Exact surface binding is mandatory even when QC tolerates a display-only
    # ASR percent/decimal difference. Such a case needs a real alignment result.
    require([token for _, sources in heard for token in sources] == [r[0] for r in surface])
    units, offset = [], 0
    for _, sources in heard:
        require(sources and offset + len(sources) <= len(surface))
        first, last = surface[offset], surface[offset + len(sources) - 1]
        units.append((first[1], last[1], first[2], last[3]))
        offset += len(sources)
    boundaries, cursor = [], 0
    for scene in scenes[:-1]:
        cursor += len(scene)
        left, right = units[cursor - 1], units[cursor]
        # A word containing tokens from both scenes cannot be split safely.
        require(left[1] < right[0] and left[3] <= right[2] + 0.02)
        boundary = (left[3] + right[2]) / 2
        require(0 < boundary < media_seconds)
        boundaries.append(boundary)
    boundaries.append(float(media_seconds))
    starts = [0.0, *boundaries[:-1]]
    durations = [end - start for start, end in zip(starts, boundaries)]
    require(all(x > 0 for x in durations) and abs(sum(durations) - media_seconds) < 1e-6)
    return {'cuts': [], 'scene_durations': durations, 'expected_duration': float(media_seconds),
        'removed_silence_seconds': 0.0, 'interior_pause_count': 0, 'tail_trimmed': False,
        'timing_source': 'blind_asr_word_boundaries', 'audio_edited': False}
