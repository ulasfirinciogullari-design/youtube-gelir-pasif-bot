from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import countable_stock_narration as words, director
from app.services import production_included_router as included
from app.services.abacus_router_adapter import _matches_schema
from test_countable_stock_narration import package, encoded


def director_reply():
    candidate = package()
    return {'title': candidate['title'], 'thumbnail_text': 'A business choice',
        'description': 'Unapproved draft', 'qc_summary': ['Unreviewed writer claim'],
        'scenes': [{**{k: v for k, v in row.items() if k != 'position'},
            'index': row['position'], 'pace': 'normal', 'transition': 'cut'}
            for row in encoded(candidate)['scenes']]}


@pytest.mark.parametrize('correction', [False, True])
def test_initial_director_and_whole_story_repair_share_real_countable_contract(monkeypatch, correction):
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'abacus_included')
    candidate = package();reply = director_reply();before = deepcopy(reply)
    call = Mock(return_value=reply);monkeypatch.setattr(included, 'generate_text_json', call)
    actual = director._run_director(None, candidate, 'Explain this business', 'English', .5,
        65, 62, 66, 6, {'content_style': 'documentary'}, correction=correction, fresh_scheduled=True)
    prompt, schema = call.call_args.args
    assert _matches_schema(reply, schema) and call.call_count == 1
    assert 'COUNTABLE ENGLISH NARRATION' in prompt
    assert [s['narration'] for s in actual['scenes']] == [s['narration'] for s in candidate['scenes']]
    assert sum(director._word_count(s['narration']) for s in actual['scenes']) == 66
    assert reply == before and actual['qc_summary'] == before['qc_summary']
    assert _matches_schema(actual, director._director_json_schema(6))
    assert 'short_story_qc' not in actual and 'qa_approved' not in actual


@pytest.mark.parametrize('damage', ['language', 'duration', 'fresh', 'word_range', 'scene_count', 'ai_scene'])
def test_countable_director_only_applies_to_authorized_fresh_english_stock(damage):
    args = [True, 'English', .5, 6, 62, 66, package(), 'Unapproved business story']
    if damage == 'language': args[1] = 'Turkish'
    elif damage == 'duration': args[2] = 1
    elif damage == 'fresh': args[0] = False
    elif damage == 'word_range': args[5] = 70
    elif damage == 'scene_count': args[6]['scenes'].pop()
    else: args[6]['scenes'][0]['ai_prompt'] = 'An authored generated scene'
    assert words.director_eligible(*args) is False


def test_explicit_owner_narration_never_enters_countable_rewriting():
    topic = 'Spoken narration must be exactly: "Keep every one of these owner supplied words."'
    assert director._exact_narration_lock_from_brief(topic) is not None
    assert words.director_eligible(True, 'English', .5, 6, 62, 66, package(), topic) is False


@pytest.mark.parametrize('damage', ['extra_word', 'two_words', 'ambiguous_narration', 'ai_scene'])
def test_countable_director_cannot_silently_discard_spoken_words_or_generated_routes(damage):
    reply = director_reply()
    if damage == 'extra_word': reply['scenes'][0]['narration_words']['w12'] = 'extra'
    elif damage == 'two_words': reply['scenes'][0]['narration_words']['w01'] = 'two words'
    elif damage == 'ambiguous_narration': reply['scenes'][0]['narration'] = 'different words'
    else: reply['scenes'][0]['ai_prompt'] = 'a generated clip'
    before = deepcopy(reply)
    with pytest.raises(ValueError): words.decode_director(reply)
    assert reply == before
