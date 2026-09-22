from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock

import pytest

from app.services import commissioning_longform as longform, longform_editorial_feedback as feedback
from app.services import director, included_research_sources as sources, included_source_passages as passages
from app.services import production_included_router as included


GOOD = 'A printed banknote begins with artists carefully preparing detailed designs for engravers.'
BAD = 'A printed banknote begins with several months of detailed artistic engraving work.'


@pytest.fixture
def draft(monkeypatch):
    monkeypatch.setattr(longform, 'active', lambda: True)
    page = {'url': 'https://www.bep.gov/currency/how-money-is-made', 'text': GOOD,
            'text_sha256': hashlib.sha256(GOOD.encode()).hexdigest()}
    monkeypatch.setattr(sources, 'fetch_page', lambda url: deepcopy(page))
    scene = {'narration': GOOD, 'visual_queries': ['artist drawing an engraving design'],
             'ai_prompt': None, 'pace': 'normal', 'transition': 'cut'}
    scenes = [deepcopy(scene) for _ in range(30)]; scenes[0]['narration'] = BAD
    package = {'title': 'The journey of a banknote', 'thumbnail_text': 'The journey',
        'description': 'An evidenced documentary.', 'scenes': scenes,
        'narration': ' '.join(row['narration'] for row in scenes), 'sources': [{'url': page['url']}],
        'target_scene_count': 30, 'target_word_range': [345, 375],
        'studio_options': {'content_plan_item_id': 'queued-documentary', 'mode': 'production', 'format': 'landscape'}}
    seen = []
    def critic(prompt, schema, *, purpose):
        assert purpose == 'story_review'
        rows = json.loads(prompt.split('EXACT FINAL NARRATION TO AUDIT:\n', 1)[1]); seen.extend(rows)
        return {'editorial_review': {k: True for k in schema['properties']['editorial_review']['required']},
            'factual_audit': {'sentences': [{**row, 'assessment': 'uncertain' if 'months' in row['narration'] else 'supported',
                'reason': 'The exact source describes preparing designs but supplies no duration in months.',
                'quotations': [{'passage_id': passages.catalogue([page])[0]['passage_id']}]}
                for row in rows]}}
    monkeypatch.setattr(included, 'generate_text_json', critic)
    return package, page, seen


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_actual_negative_review_drives_revision_and_every_scene_is_reviewed_again(draft, monkeypatch, repair_succeeds):
    package, page, seen = draft; original = deepcopy(package)
    def revise(client, compact, topic, language, duration, target, minimum, maximum, scenes, options, **kwargs):
        assert compact['narration_quality_issues'][0]['position'] == 0
        assert 'duration' in compact['narration_quality_issues'][0]['reason']
        assert compact['retrieved_reference_data'] == [{'url': page['url'], 'text': GOOD}]
        assert (duration, target, minimum, maximum, scenes) == (3, 360, 345, 375, 30)
        assert kwargs == {'correction': True, 'exact_scene_count': True}
        out = deepcopy(compact)
        if repair_succeeds: out['scenes'][0]['narration'] = GOOD
        return out
    writer = Mock(side_effect=revise); monkeypatch.setattr(director, '_run_director', writer)
    if repair_succeeds:
        result = longform.review_story(package, 'Banknote', 'en')
        assert result['narration_word_count'] == 360 and len(seen) == 60
        assert result['longform_story_qc']['accepted'] is True
        assert len(result['longform_story_qc']['revision_history']) == 1
        assert result['longform_story_qc']['revision_history'][0]['findings'][0]['assessment'] == 'uncertain'
        assert len(result['longform_story_qc']['reviews']) == 3
        writer.assert_called_once()
    else:
        with pytest.raises(director.ProductionContentError): longform.review_story(package, 'Banknote', 'en')
        assert writer.call_count == 3 and len(seen) == 120
    assert package == original


@pytest.mark.parametrize('damage', ['missing_scene', 'word_budget', 'paid_ai'])
def test_revision_cannot_evade_duration_scene_or_media_contract(draft, monkeypatch, damage):
    package, page, seen = draft; revised = deepcopy(package)
    if damage == 'missing_scene': revised['scenes'].pop()
    if damage == 'word_budget': revised['scenes'][0]['narration'] = 'Too short.'
    if damage == 'paid_ai': revised['scenes'][0]['ai_prompt'] = 'Generate this scene.'
    if damage == 'word_budget':
        for row in revised['scenes']: row['narration'] = 'Too short.'
    writer = Mock(return_value=revised); monkeypatch.setattr(director, '_run_director', writer)
    with pytest.raises(director.ProductionContentError): longform.review_story(package, 'Banknote', 'en')
    assert writer.call_count == (3 if damage == 'word_budget' else 1)
    assert len(seen) == 30


def test_shortened_factual_revision_is_repaired_using_its_measured_length_then_fully_reviewed(draft, monkeypatch):
    package, page, seen = draft; original = deepcopy(package)
    shortened = deepcopy(package)
    for row in shortened['scenes']: row['narration'] = 'Artists carefully prepare detailed banknote designs for engravers.'
    corrected = deepcopy(package)
    for row in corrected['scenes']: row['narration'] = GOOD
    writer = Mock(side_effect=[shortened, corrected]); monkeypatch.setattr(director, '_run_director', writer)
    result = longform.review_story(package, 'Banknote', 'en')
    compact = writer.call_args.args[1]
    assert compact['current_word_count'] == 240 and compact['scene_word_counts'] == [8] * 30
    assert compact['length_correction_attempt'] == 1 and '240 words' in compact['measured_length_issue']
    assert compact['narration_quality_issues'][0]['position'] == 0
    assert compact['retrieved_reference_data'] == [{'url': page['url'], 'text': GOOD}]
    assert result['narration_word_count'] == 360 and result['longform_story_qc']['accepted'] is True
    assert len(seen) == 60 and writer.call_count == 2 and package == original


@pytest.mark.parametrize('voice', ['_recovered_voice','voice_candidate_reuse','voice_replacement','audio_candidate_checkpoint'])
def test_existing_speech_is_never_rewritten(draft, monkeypatch, voice):
    package, _, seen = draft; package[voice] = {'existing': True}
    writer = Mock(); monkeypatch.setattr(director, '_run_director', writer)
    with pytest.raises(director.ProductionContentError): longform.review_story(package, 'Banknote', 'en')
    writer.assert_not_called(); assert len(seen) == 30


def test_explicit_immutable_voice_review_never_repairs_even_a_complete_eligible_draft(draft, monkeypatch):
    package, _, seen = draft; writer = Mock(); monkeypatch.setattr(director, '_run_director', writer)
    with pytest.raises(director.ProductionContentError):
        longform.review_story(package, 'Banknote', 'en', allow_revisions=False)
    writer.assert_not_called(); assert len(seen) == 30
