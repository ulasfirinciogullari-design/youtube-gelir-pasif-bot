"""Keep chronological provider input without renumbering existing moment IDs."""

import ast
import base64
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'services' / 'visual_qc.py'
EXPECTED = [(3, 0.06), (0, 0.18), (1, 0.50), (2, 0.82), (4, 0.94)]


@pytest.fixture
def reviewer(tmp_path):
    # Execute the real module with configuration and provider entry points
    # isolated, so the tests need no credentials, media tools or network.
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    settings = SimpleNamespace(
        studio_plan_provider='gemini', openai_api_key='fixture-openai-key',
        openai_model='fixture-openai-model', gemini_api_key='fixture-gemini-key',
        gemini_model='fixture-gemini-model',
    )
    namespace = {
        'settings': settings,
        'GEMINI_DEFAULT_MODEL': 'fixture-gemini-model',
        'GeminiGenerationError': type('GeminiGenerationError', (RuntimeError,), {}),
        'GeminiProtocolError': type('GeminiProtocolError', (RuntimeError,), {}),
        'manufactured_replica_required': lambda _scene: False,
        'routed_open_air_cooling_temporal_required': lambda _scene: False,
    }
    exec(compile(tree, str(SOURCE), 'exec'), namespace)
    sampled = []

    def frame(path, output, fraction):
        sampled.append((path, fraction))
        output.write_bytes(b'\xff\xd8\xff' + f'fraction={fraction:.2f}'.encode() + b'\xff\xd9')
        return output

    verdict = {
        'scene_index': 0, 'best_candidate_index': 0, 'best_moment_index': 1,
        'score': 92, 'reason': 'The named subject is visible with useful motion.',
        'retry_queries': [], 'evidence_moment_indices': [moment for moment, _ in EXPECTED],
        **{field: False for field in (
            *namespace['_EVIDENCE_BOOLEAN_FIELDS'],
            *namespace['_MANUAL_QA_VISUAL_BOOLEAN_FIELDS'],
            *namespace['_IDENTITY_BOOLEAN_FIELDS'],
        )},
        'subject_visible': True, 'spoken_action_visible': True,
    }
    gemini = Mock(return_value={'reviews': [verdict]})
    openai_client = SimpleNamespace(responses=SimpleNamespace(
        create=Mock(return_value=SimpleNamespace(output_text=json.dumps({'reviews': [verdict]}))),
    ))
    openai = Mock(return_value=openai_client)
    namespace.update(_frame=frame, generate_gemini_multimodal_json=gemini, OpenAI=openai)
    return SimpleNamespace(namespace=namespace, settings=settings, sampled=sampled,
                           gemini=gemini, openai=openai, client=openai_client, work=tmp_path)


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_single_candidate_frames_are_chronological_with_stable_moment_ids(reviewer, provider):
    reviewer.settings.studio_plan_provider = provider
    result = reviewer.namespace['review_scene_visuals'](
        [{'index': 7, 'narration': 'A hand holds cotton.', 'ai_prompt': None}],
        [[{'path': 'selected-stock.mp4', 'source_type': 'stock', 'stock_provider': 'pexels'}]],
        reviewer.work, _missing_review_attempts=0, _score_reason_consistency_attempts=0,
    )
    assert reviewer.sampled == [('selected-stock.mp4', fraction) for _, fraction in EXPECTED]
    if provider == 'gemini':
        reviewer.gemini.assert_called_once()
        reviewer.openai.assert_not_called()
        parts = reviewer.gemini.call_args.args[0]
    else:
        reviewer.gemini.assert_not_called()
        reviewer.openai.assert_called_once()
        reviewer.client.responses.create.assert_called_once()
        parts = reviewer.client.responses.create.call_args.kwargs['input'][0]['content']

    observed = []
    for index, part in enumerate(parts):
        label = part.get('text', '')
        match = re.fullmatch(r'CANDIDATE 0 — MOMENT ([0-4]) — approximately ([0-9]+)% into clip', label)
        if match is None:
            continue
        image = parts[index + 1]
        raw = (image['image_bytes'] if provider == 'gemini'
               else base64.b64decode(image['image_url'].split(',', 1)[1]))
        moment, percent = map(int, match.groups())
        fraction = percent / 100
        assert f'fraction={fraction:.2f}'.encode() in raw
        observed.append((moment, fraction))
    assert observed == EXPECTED
    # ID 1 must remain the 50% editorial choice even though it is now emitted
    # third; provider order must never change downstream fraction mapping.
    assert result['reviews'][0]['best_moment_index'] == 1
    assert result['reviews'][0]['best_start_fraction'] == 0.50
    assert result['reviews'][0]['score'] == 92
