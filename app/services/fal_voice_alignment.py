"""Use the actual chunked ElevenLabs character alignment, never as blind ASR.

Fal returns global times for each chunk. Only extra exterior whitespace may
be removed from the alignment arrays; the complete audio remains unchanged.
"""
import math

from app.services.fal_voice_adapter import require

FIELDS = ('characters', 'character_start_times_seconds', 'character_end_times_seconds')


def _typography(text):
    # A one-character apostrophe substitution preserves every source index.
    return text.replace('’', "'")


def exact(narration, chunks, seconds):
    require(type(narration) is str and narration.strip() == narration and narration
        and type(chunks) is list and 1 <= len(chunks) <= 100
        and type(seconds) in (int,float) and math.isfinite(seconds) and 0 < seconds <= 240,
        'fal_voice_alignment_invalid')
    aligned = {key:[] for key in FIELDS}
    for chunk in chunks:
        require(type(chunk) is dict and set(chunk) == set(FIELDS)
            and all(type(chunk[k]) is list for k in FIELDS)
            and 0 < len(chunk[FIELDS[0]]) == len(chunk[FIELDS[1]]) == len(chunk[FIELDS[2]]))
        for key in FIELDS: aligned[key].extend(chunk[key])
    require(len(aligned['characters']) <= 10000
        and all(type(c) is str and len(c) == 1 for c in aligned['characters']))
    text = ''.join(aligned['characters'])
    require(_typography(text.strip()) == _typography(narration), 'fal_voice_alignment_text_mismatch')
    starts, ends = aligned[FIELDS[1]], aligned[FIELDS[2]]
    previous_start = previous_end = 0.
    for start,end in zip(starts,ends):
        require(type(start) in (int,float) and type(end) in (int,float)
            and math.isfinite(start) and math.isfinite(end)
            and 0 <= start <= end <= seconds + .05
            and start + 1e-6 >= previous_start and end + 1e-6 >= previous_end
            and start + .02 >= previous_end, 'fal_voice_alignment_timing_invalid')
        previous_start, previous_end = start,end
    offset = len(text) - len(text.lstrip())
    return {key:values[offset:offset + len(narration)] for key,values in aligned.items()}


def edit_plan(spoken, chunks, seconds):
    from app.services.voice import _join_scene_narration, _scene_durations_from_alignment
    require(type(spoken) is list and 8 <= len(spoken) <= 32
        and all(type(s) is str and s.strip() for s in spoken))
    narration,spans = _join_scene_narration(spoken)
    aligned = exact(narration,chunks,seconds)
    # Full source/end-time and scene-span validation from the established path.
    canonical = {**aligned, 'characters':list(_typography(''.join(aligned['characters'])))}
    _scene_durations_from_alignment(_typography(narration),spans,canonical,seconds)
    boundaries = []
    for left,right in zip(spans,spans[1:]):
        end = next(i for i in range(left[1]-1,left[0]-1,-1) if narration[i].isalnum())
        start = next(i for i in range(right[0],right[1]) if narration[i].isalnum())
        a,b = aligned[FIELDS[2]][end],aligned[FIELDS[1]][start]
        require(a <= b + .02, 'fal_voice_scene_overlap')
        boundary = (a+b)/2
        require(0 < boundary < seconds and (not boundaries or boundary > boundaries[-1]))
        boundaries.append(boundary)
    boundaries.append(float(seconds))
    durations = [end-start for start,end in zip([0.,*boundaries[:-1]],boundaries)]
    require(all(d>0 for d in durations) and abs(sum(durations)-seconds)<1e-6)
    return {'cuts':[],'scene_durations':durations,'expected_duration':float(seconds),
        'removed_silence_seconds':0.,'interior_pause_count':0,'tail_trimmed':False,
        'audio_edited':False,'timing_source':'fal_elevenlabs_native_character_alignment'}
