from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import documentary_word_contract as contract, director, commissioning_longform


def draft():
    phrase = 'Küçük atölyenin çalışanları yeni ürünleri müşterilerle birlikte deneyerek üretimi yeniden şekillendirdi.'
    assert director._word_count(phrase) == 11
    return {'title':'Atölyedeki karar','thumbnail_text':'Tek karar','description':'Kaynaklı belgesel.',
        'qc_summary':[], 'scenes':[{'index':index,
            'narration_words':dict(zip(contract.SLOTS,phrase.split(),strict=True)),
            'visual_queries':['workers inspecting products workshop'], 'ai_prompt':None,
            'pace':'normal','transition':'cut'}for index in range(30)]}


def test_joins_only_observed_ordered_words_and_keeps_every_other_field():
    value = draft(); before = deepcopy(value)
    for row in value['scenes']:row['narration_words']=dict(reversed(list(row['narration_words'].items())))
    result = contract.decode(value)
    assert value == before and sum(director._word_count(s['narration'])for s in result['scenes']) == 330
    for old,new in zip(before['scenes'],result['scenes'],strict=True):
        assert {k:v for k,v in old.items()if k!='narration_words'} == {k:v for k,v in new.items()if k!='narration'}
    assert not any('approved'in k for k in result)


@pytest.mark.parametrize('damage',['missing','extra','phrase','blank','number','competing','short_array'])
def test_invalid_slots_cannot_be_padded_or_silently_truncated(damage):
    value=draft();row=value['scenes'][0]
    if damage=='missing':row['narration_words'].pop('w11')
    if damage=='extra':row['narration_words']['w12']='extra'
    if damage=='phrase':row['narration_words']['w01']='two words'
    if damage=='blank':row['narration_words']['w01']=''
    if damage=='number':row['narration_words']['w01']=1
    if damage=='competing':row['narration']='another sentence'
    if damage=='short_array':value['scenes'].pop()
    before=deepcopy(value)
    with pytest.raises(ValueError):contract.decode(value)
    assert value==before


def test_only_explicit_turkish_long_correction_changes_wire_representation(monkeypatch):
    monkeypatch.setattr(commissioning_longform,'active',lambda:True)
    options={'content_plan_item_id':'item','mode':'production'}
    args=(options,3,'Turkish',30,(300,330),'A sourced documentary.')
    assert contract.eligible(*args,True)
    assert not contract.eligible(*args,False)
    assert not contract.eligible(options,3,'en',30,(345,375),'A sourced documentary.',True)
    assert not contract.eligible({},3,'Turkish',30,(300,330),'A sourced documentary.',True)
    monkeypatch.setattr(commissioning_longform,'active',lambda:False)
    assert not contract.eligible(*args,True)


def test_real_director_wire_and_decoder_keep_count_but_grant_no_approval(monkeypatch):
    from app.services import production_included_router as included, audience_strategy
    monkeypatch.setattr(director,'_studio_plan_provider',lambda:'abacus_included')
    monkeypatch.setattr(commissioning_longform,'active',lambda:True)
    monkeypatch.setattr(audience_strategy,'writer_rule',lambda *a:'')
    def generate(prompt,schema,*,purpose):
        assert purpose=='editorial' and contract.RULE in prompt
        row=schema['properties']['scenes']['items']
        assert 'narration'not in row['properties'] and set(row['properties']['narration_words']['required'])==set(contract.SLOTS)
        return draft()
    monkeypatch.setattr(included,'generate_text_json',generate)
    result=director._run_director(None,{'scenes':[],'current_word_count':293},'A sourced documentary.',
        'Turkish',3,315,300,330,30,{'content_plan_item_id':'item','mode':'production'},
        correction=True,exact_scene_count=True)
    assert sum(director._word_count(s['narration'])for s in result['scenes'])==330
    assert 'long_story_qc'not in result and 'short_story_qc'not in result


@pytest.mark.parametrize('text,language,valid',[
    ('Duration gate rejected script: 293 words for requested 3 min (target 300-330)','tr',True),
    ('Duration gate rejected script: 301 words for requested 3 min (target 300-330)','tr',False),
    ('Duration gate rejected script: 293 words for requested 3 min (target 300-330)','en',False),
    ('Duration gate rejected script: 340 words for requested 3.0 min (target 345-375)','en',True),
    ('provider request timed out','tr',False)])
def test_recovery_error_must_be_exact_local_duration_contract(text,language,valid):
    assert contract.duration_failure({'spec':{'language':language,'duration_minutes':3},'error':text})is valid
