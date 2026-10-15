from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import audio_proper_name_review as names, audio_qc as qc
from app.services import production_included_router as router


@pytest.fixture
def actual():
    value = json.loads((Path(__file__).parent/'fixtures/fal_full_english_name_spelling.json').read_text())
    prior = value['comparison']
    comparison = qc.compare_transcript(value['text'], prior['transcript'], words=prior['word_timestamps'],
        provider='elevenlabs', comparison_language='en')
    qc._require_word_timing_evidence(comparison, 'Scribe')
    return value['text'], comparison


def accepted(found):
    return {'pass': True, 'summary': 'Complete natural delivery; the name is recognizable.',
        'scores': {k:10 if k=='roboticness' else 95 for k in qc._PROSODY_SCORE_FIELDS}, 'issues': [],
        'name_checks': [{'id':r['id'], 'verdict':'matching_pronunciation',
            'heard_pronunciation':'God-fred', 'detail':'The heard name matches the full named person.'} for r in found]}


def test_actual_complete_record_only_identifies_four_observed_name_spans(actual):
    text, comparison = actual
    found = names.candidates(text, comparison)
    assert comparison['pass'] is False and comparison['mismatch_details']['exact_match'] is False
    assert len(found)==4 and {r['expected'] for r in found}=={'godtfred'}
    assert {r['full_name'] for r in found}=={'Godtfred Kirk Christiansen'}
    for row in found:
        stamp = comparison['word_timestamps'][row['word_index']]
        assert (row['start_seconds'], row['end_seconds'])==(stamp['start'],stamp['end'])
        assert row['transcribed']=='godfred'


@pytest.mark.parametrize('before,after',[
    ('Godtfred', 'godtfred'), ('Godtfred Kirk Christiansen', 'Godtfred'),
    ('Christiansen', 'Christenson'), ('nineteen forty-seven', 'nineteen forty-eight'),
    ('completely stop', 'never stop'), ('six essential', 'seven essential'),
    ('traditional Danish', 'traditional Swedish'), ('global trajectory.', 'global trajectory. Extra words.'),
])
def test_ordinary_identity_number_negation_or_missing_words_never_enter(actual,before,after):
    text, comparison = actual
    assert names.candidates(text.replace(before,after), comparison) is None


def test_bad_timestamps_or_unrelated_recognizer_never_enter(actual):
    text, comparison=actual
    comparison['word_timestamps'][81]['end']=comparison['word_timestamps'][81]['start']
    assert names.candidates(text,comparison)is None
    comparison['provider']='openai'
    assert names.candidates(text,comparison)is None


@pytest.mark.parametrize('mutation',['uncertain','mispronounced','missing','reordered','empty_explanation','bad_scores','negative_delivery'])
def test_every_occurrence_and_full_performance_need_positive_actual_evidence(actual,mutation):
    text, comparison=actual;found=names.candidates(text,comparison);out=accepted(found)
    if mutation in {'uncertain','mispronounced'}:out['name_checks'][2]['verdict']=mutation
    elif mutation=='missing':out['name_checks'].pop()
    elif mutation=='reordered':out['name_checks'].reverse()
    elif mutation=='empty_explanation':out['name_checks'][0]['detail']=' '
    elif mutation=='bad_scores':out['scores']['pronunciation']=10
    else:out['pass']=False
    assert names.validate(out,text,comparison,found,164.7)is None


def test_composite_verdict_preserves_raw_negative_transcript_score_and_timestamps(actual,monkeypatch):
    text, comparison=actual;before=deepcopy(comparison);found=names.candidates(text,comparison)
    monkeypatch.setattr(names,'_fal_context',lambda text:(SimpleNamespace(),{'kind':'long'}))
    monkeypatch.setattr(names,'prepared',lambda *a:SimpleNamespace(audio={'decoded_samples':7905600,'decoded_sample_rate':48000}))
    generate=Mock(return_value=accepted(found));monkeypatch.setattr(router,'_generate',generate)
    router._LAST_OBSERVED.set({'purpose':'prosody','context':{'kind':'long'},'evidence':{'captured':True}})
    result=names.reassess(b'whole-audio',text,comparison)
    assert result['pass']is True and result['blind_transcript_pass']is False
    assert result['mismatch_details']['exact_match']is False
    assert result['verification_basis']=='blind_transcript_with_independent_name_pronunciation'
    assert comparison==before
    for field in ('transcript','word_timestamps','score','mismatch_details'):
        assert result[field]==comparison[field]
    assert generate.call_count==1


def test_no_fal_authority_or_unknown_listener_cannot_silently_pass(actual,monkeypatch):
    text, comparison=actual;generate=Mock(side_effect=RuntimeError('outcome unknown'))
    monkeypatch.setattr(router,'_generate',generate)
    monkeypatch.setattr(names,'_fal_context',lambda text:None)
    assert names.reassess(b'audio',text,comparison)==comparison
    generate.assert_not_called()
    monkeypatch.setattr(names,'_fal_context',lambda text:(None,{}))
    monkeypatch.setattr(names,'prepared',lambda *a:None)
    with pytest.raises(RuntimeError,match='outcome unknown'):names.reassess(b'audio',text,comparison)


def test_old_failed_qualification_is_preserved_by_bound_reassessment():
    from app.services import fal_voice_trial as trial, kie_voice_ledger as kie
    old={'pass':False, **{k:k for k in ('body','spoken_scenes','grant_sha256','media_sha256','audio_sha256','timing','asr')}}
    new={**old,'pass':True,'supersedes_qualification_sha256':kie.sha(kie.raw(old))}
    rows={'qualification':old,'reassessment':new}
    assert trial.qualifying_record(rows)==new and rows['qualification']['pass']is False
    rows['reassessment']['audio_sha256']='different-audio'
    with pytest.raises(trial.api.FalVoiceError):trial.qualifying_record(rows)
