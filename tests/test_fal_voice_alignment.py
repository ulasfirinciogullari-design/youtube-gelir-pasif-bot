from copy import deepcopy
import pytest

from app.services import fal_voice_alignment as alignment
from app.services.fal_voice_adapter import FalVoiceError


def fixture():
    spoken = ['The story continues.']*7 + ['The brand’s last word.']
    text=' '+' '.join(spoken).replace('’',"'")+' '
    values={'characters':list(text),'character_start_times_seconds':[i*.04 for i in range(len(text))],
        'character_end_times_seconds':[(i+1)*.04 for i in range(len(text))]}
    return spoken,[{k:v[:80]for k,v in values.items()},{k:v[80:]for k,v in values.items()}],len(text)*.04+.1


def test_actual_chunk_shape_global_times_keep_whole_performance_and_apostrophe_equivalence():
    spoken,chunks,seconds=fixture();before=deepcopy(chunks)
    plan=alignment.edit_plan(spoken,chunks,seconds)
    assert sum(plan['scene_durations'])==pytest.approx(seconds)
    assert plan['cuts']==[] and plan['audio_edited']is False
    assert plan['timing_source']=='fal_elevenlabs_native_character_alignment'
    assert 'pass'not in plan and chunks==before


@pytest.mark.parametrize('change',['missing_tail','repeated_chunk','changed_word','relative_chunk_times',
    'non_finite','short_audio','missing_start','overlap'])
def test_partial_or_unbound_alignment_cannot_supply_scene_cuts(change):
    spoken,chunks,seconds=fixture()
    if change=='missing_tail':chunks.pop()
    if change=='repeated_chunk':chunks.append(deepcopy(chunks[-1]))
    if change=='changed_word':chunks[0]['characters'][1]='X'
    if change=='relative_chunk_times':
        chunks[1]['character_start_times_seconds']=[v-3.2 for v in chunks[1]['character_start_times_seconds']]
        chunks[1]['character_end_times_seconds']=[v-3.2 for v in chunks[1]['character_end_times_seconds']]
    if change=='non_finite':chunks[0]['character_start_times_seconds'][1]=float('nan')
    if change=='short_audio':seconds=1
    if change=='missing_start':chunks[0].pop('character_start_times_seconds')
    if change=='overlap':chunks[0]['character_start_times_seconds'][2]=0
    with pytest.raises(FalVoiceError):alignment.edit_plan(spoken,chunks,seconds)
