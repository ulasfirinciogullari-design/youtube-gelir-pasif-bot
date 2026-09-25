"""Exercise the real final worker gate with original audio, never a looser cap."""
import ast
import hashlib
import json

import pytest

from test_framecase_sound import source_voice, narration_master
from test_production_short_effective_duration import helpers, PIPELINE, SOURCE


class Rejected(RuntimeError):
    def __init__(self, message, *, code): super().__init__(message)


def check(rendered, voice, *, scheduled=True, seconds=2.4):
    block = next(node for node in ast.walk(PIPELINE) if isinstance(node, ast.If)
        and any(isinstance(item, ast.Assign) and any(isinstance(t, ast.Name)
            and t.id == 'final_render_qc' for t in item.targets) for item in node.body))
    ns = helpers()
    ns.update(rendered=rendered, effective_edit_target_seconds=3, final_audio_path=voice,
        voice_result={'duration_after_fit': seconds}, strict_short_preview_duration=False,
        options={'mode':'production','format':'shorts','production_scheduled':scheduled},
        duration_minutes=.5, json=json, ProductionContentError=Rejected)
    exec(compile(ast.Module(body=[block],type_ignores=[]),str(SOURCE),'exec'),ns)
    return ns['final_render_qc']


def test_worker_measures_natural_source_pause_without_changing_master_or_voice(tmp_path):
    voice=source_voice(tmp_path,2.05)
    master=narration_master(tmp_path,2.05)
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (voice,tmp_path/'final.mp4')}
    with pytest.raises(Rejected,match='breathing-room'): check(master,voice,scheduled=False)
    result=check(master,voice)
    assert result['pass'] is True and result['timing_basis']=='measured_original_voice_tail'
    assert result['maximum_ending_silence_seconds']<=1.55
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


@pytest.mark.parametrize('failure',['extra_frame','truncated_master','excessive_tail','wrong_source'])
def test_worker_still_rejects_real_timing_failures(tmp_path,failure):
    voice=source_voice(tmp_path,1.3 if failure=='excessive_tail' else 2.05)
    master=narration_master(tmp_path,1.3 if failure=='excessive_tail' else 1.7 if failure=='truncated_master' else 2.05)
    if failure=='extra_frame': master['frame_count']+=1
    with pytest.raises(Rejected,match='gate rejected'):
        check(master,voice,seconds=2.8 if failure=='wrong_source' else 2.4)
