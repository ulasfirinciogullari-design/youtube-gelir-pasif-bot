import json

import pytest

from app.services.research import _parse_json_payload, _research_json_schema
from app.services.abacus_router_adapter import _matches_schema


@pytest.mark.parametrize('assessment', [True, False])
def test_optional_writer_self_assessment_is_not_a_story_or_visual_approval(assessment):
    raw = {'title': 'A sourced business decision', 'description': 'An explanation.',
        'thumbnail_text': 'Why stop?', 'sources': [
            {'url': 'https://global.toyota/en/company/vision-and-philosophy/production-system/',
             'evidence': 'Toyota describes stopping the line to resolve an abnormality.'},
            {'url': 'https://global.toyota/en/company/plant-tours/production-system/',
             'evidence': 'Work resumes after the problem has been resolved.'}],
        'scenes': [{'narration': 'Operators can stop the assembly line when an abnormality occurs.',
                    'visual_queries': ['automotive assembly line', 'car manufacturing plant'],
                    'ai_prompt': None, 'visual_queries_match_narrative': assessment} for _ in range(5)]}
    assert _matches_schema(raw, _research_json_schema(6))
    parsed = _parse_json_payload(json.dumps(raw))
    assert len(parsed['scenes']) == 5
    assert all('visual_queries_match_narrative' not in scene for scene in parsed['scenes'])
    assert not {'stock_scene_qc', 'short_story_qc', 'qa_approved'} & set(parsed)
    assert all(scene['narration'] == raw['scenes'][i]['narration'] for i, scene in enumerate(parsed['scenes']))
    raw['scenes'][0]['visual_queries_match_narrative'] = 'true'
    assert not _matches_schema(raw, _research_json_schema(6))
    raw['scenes'][0]['visual_queries_match_narrative'] = assessment
    raw['scenes'][0]['qa_approved'] = True
    assert not _matches_schema(raw, _research_json_schema(6))
