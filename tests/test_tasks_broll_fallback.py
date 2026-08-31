import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

from app.services.visual_routing import should_rank_runway_candidate


SOURCE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'


def _load_retry_boundary():
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding='utf-8'),
        filename=str(SOURCE_PATH),
    )
    definitions = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ClassDef)
            and node.name == 'PexelsRetryError'
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name == '_retry_bad_scene'
        )
    ]
    namespace = {
        'Path': Path,
        're': re,
        '_download_ranked_broll_candidates': Mock(),
    }
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace


def _load_search_boundary():
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding='utf-8'),
        filename=str(SOURCE_PATH),
    )
    definitions = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ClassDef)
            and node.name == 'PexelsRetryError'
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name == '_download_ranked_broll_candidates'
        )
    ]
    namespace = {
        'Path': Path,
        're': re,
        'ThreadPoolExecutor': ThreadPoolExecutor,
        'as_completed': as_completed,
        'find_broll': Mock(),
        '_select_ranked_broll_candidates': Mock(return_value=[]),
        'download_broll': Mock(),
    }
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace


def _retry_call(retry_bad_scene, **kwargs):
    return retry_bad_scene(
        2,
        ['specific subject action', 'specific subject close-up'],
        set(),
        Path('/tmp/test-broll-fallback'),
        [],
        **kwargs,
    )


class ShortPreviewBrollFallbackTests(unittest.TestCase):
    def test_parallel_search_burst_gets_one_sequential_second_chance(self):
        namespace = _load_search_boundary()
        find_broll = Mock(side_effect=[
            RuntimeError('parallel disconnect one'),
            RuntimeError('parallel disconnect two'),
            [],
            [],
        ])
        namespace['find_broll'] = find_broll

        result = namespace['_download_ranked_broll_candidates'](
            2,
            ['container ship at sea', 'cargo vessel ocean aerial'],
            set(),
            Path('/tmp/test-pexels-search-fallback'),
            [],
            file_prefix='stock',
            selected_by='test',
            max_candidates=3,
            search_limit=18,
        )

        self.assertEqual(result, [])
        self.assertEqual(find_broll.call_count, 4)
        namespace['_select_ranked_broll_candidates'].assert_called_once()

    def test_persistent_parallel_and_sequential_search_failure_stays_strict(self):
        namespace = _load_search_boundary()
        find_broll = Mock(side_effect=RuntimeError('provider unavailable'))
        namespace['find_broll'] = find_broll

        with self.assertRaises(namespace['PexelsRetryError']) as caught:
            namespace['_download_ranked_broll_candidates'](
                2,
                ['container ship at sea', 'cargo vessel ocean aerial'],
                set(),
                Path('/tmp/test-pexels-search-fallback'),
                [],
                file_prefix='stock',
                selected_by='test',
                max_candidates=3,
                search_limit=18,
            )

        self.assertEqual(find_broll.call_count, 4)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_empty_authored_ai_scene_tolerates_pexels_evidence_failure(self):
        namespace = _load_retry_boundary()
        pexels_error = namespace['PexelsRetryError'](
            'bounded search failed'
        )
        download_candidates = Mock(side_effect=pexels_error)
        namespace['_download_ranked_broll_candidates'] = download_candidates

        replacements = _retry_call(
            namespace['_retry_bad_scene'],
            file_prefix='duration_refill',
            allow_short_fallback=False,
            tolerate_pexels_failure=True,
        )

        self.assertEqual(replacements, [])
        download_candidates.assert_called_once()
        self.assertTrue(
            should_rank_runway_candidate(
                {'mode': 'preview', 'visual_mix': 'balanced'},
                0.6,
                authored_ai_prompt=True,
                has_visual=bool(replacements),
                stock_score=-1,
                quality_threshold=86,
            )
        )

    def test_low_scoring_authored_ai_keeps_incumbent_for_runway_fallback(self):
        namespace = _load_retry_boundary()
        namespace['_download_ranked_broll_candidates'] = Mock(
            side_effect=namespace['PexelsRetryError']('download failed')
        )
        incumbent = {
            'path': '/tmp/incumbent.mp4',
            'start_fraction': 0.65,
            'source_duration': 8.0,
        }

        replacements = _retry_call(
            namespace['_retry_bad_scene'],
            tolerate_pexels_failure=True,
        )
        selected_specs = [*replacements, incumbent][:3]

        self.assertEqual(selected_specs, [incumbent])
        self.assertTrue(
            should_rank_runway_candidate(
                {'mode': 'preview', 'visual_mix': 'balanced'},
                0.5,
                authored_ai_prompt=True,
                has_visual=True,
                stock_score=35,
                quality_threshold=86,
            )
        )

    def test_stock_retry_remains_fail_closed_by_default(self):
        namespace = _load_retry_boundary()
        pexels_error = namespace['PexelsRetryError'](
            'stock evidence unavailable'
        )
        namespace['_download_ranked_broll_candidates'] = Mock(
            side_effect=pexels_error
        )

        with self.assertRaises(namespace['PexelsRetryError']) as raised:
            _retry_call(namespace['_retry_bad_scene'])

        self.assertIs(raised.exception, pexels_error)

    def test_tolerant_ai_boundary_does_not_hide_unrelated_runtime_errors(self):
        namespace = _load_retry_boundary()
        unexpected_error = RuntimeError('unexpected local invariant failure')
        namespace['_download_ranked_broll_candidates'] = Mock(
            side_effect=unexpected_error
        )

        with self.assertRaises(RuntimeError) as raised:
            _retry_call(
                namespace['_retry_bad_scene'],
                tolerate_pexels_failure=True,
            )

        self.assertIs(raised.exception, unexpected_error)

    def test_only_short_preview_authored_ai_retries_opt_into_tolerance(self):
        tree = ast.parse(
            SOURCE_PATH.read_text(encoding='utf-8'),
            filename=str(SOURCE_PATH),
        )
        pipeline = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'run_video_pipeline'
        )
        retry_calls = [
            node
            for node in ast.walk(pipeline)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == '_retry_bad_scene'
        ]
        tolerant_values = [
            keyword.value
            for call in retry_calls
            for keyword in call.keywords
            if keyword.arg == 'tolerate_pexels_failure'
        ]

        self.assertEqual(len(tolerant_values), 2)
        self.assertTrue(any(
            isinstance(value, ast.Constant) and value.value is True
            for value in tolerant_values
        ))
        self.assertTrue(any(
            isinstance(value, ast.Name)
            and value.id == 'is_short_preview_authored_ai'
            for value in tolerant_values
        ))

        source = SOURCE_PATH.read_text(encoding='utf-8')
        low_score_retry = source.index(
            'tolerate_pexels_failure=is_short_preview_authored_ai'
        )
        incumbent_restore = source.index(
            'scene_visuals[scene_idx] = [*replacements, best_spec][:3]',
            low_score_retry,
        )
        runway_ranking = source.index(
            'def rank_runway_candidates()', incumbent_restore
        )
        self.assertLess(low_score_retry, incumbent_restore)
        self.assertLess(incumbent_restore, runway_ranking)

        stock_contract_start = source.index(
            'stock_contract_candidates = ['
        )
        stock_contract_end = source.index(
            'def rank_runway_candidates()', stock_contract_start
        )
        stock_contract = source[stock_contract_start:stock_contract_end]
        self.assertIn(
            "file_prefix='pre_runway_stock_tournament'",
            stock_contract,
        )
        self.assertNotIn('tolerate_pexels_failure', stock_contract)
        self.assertNotIn('except PexelsRetryError', stock_contract)


if __name__ == '__main__':
    unittest.main()
