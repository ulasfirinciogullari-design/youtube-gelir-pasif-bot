import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

import httpx

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
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name == '_final_pexels_rescue_queries'
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
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == '_TRANSIENT_PEXELS_HTTP_STATUS_CODES'
                for target in node.targets
            )
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name == '_is_transient_pexels_provider_error'
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
        'httpx': httpx,
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


def _load_outage_allocation_boundary():
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding='utf-8'),
        filename=str(SOURCE_PATH),
    )
    definition = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_allocate_short_preview_provider_outage_runway'
    )
    namespace = {
        'SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP': 2,
    }
    exec(
        compile(
            ast.Module(body=[definition], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_allocate_short_preview_provider_outage_runway']


def _retry_call(retry_bad_scene, **kwargs):
    return retry_bad_scene(
        2,
        ['specific subject action', 'specific subject close-up'],
        set(),
        Path('/tmp/test-broll-fallback'),
        [],
        **kwargs,
    )


def _network_error(message='provider unavailable'):
    return httpx.ConnectError(
        message,
        request=httpx.Request('GET', 'https://api.pexels.com/videos/search'),
    )


def _http_status_error(status_code):
    request = httpx.Request('GET', 'https://api.pexels.com/videos/search')
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f'HTTP {status_code}',
        request=request,
        response=response,
    )


class ShortPreviewBrollFallbackTests(unittest.TestCase):
    def test_recovered_provider_rescue_uses_original_outage_scene_queries(self):
        namespace = _load_retry_boundary()
        recovered_spec = {
            'path': '/tmp/recovered-stock.mp4',
            'start_fraction': 0.35,
            'source_duration': 9.0,
        }
        download_candidates = Mock(return_value=[recovered_spec])
        namespace['_download_ranked_broll_candidates'] = download_candidates
        queries = namespace['_final_pexels_rescue_queries'](
            {
                'visual_queries': [
                    'volunteer collecting plastic beach',
                    'hands sorting plastic on sand',
                    'this third query must stay out',
                ]
            },
            {},
            provider_outage_stock_fallback=True,
        )

        replacements = namespace['_retry_bad_scene'](
            2,
            queries,
            set(),
            Path('/tmp/test-recovered-provider-rescue'),
            [],
            file_prefix='final_qc_rescue',
            tolerate_pexels_failure=True,
        )

        self.assertEqual(
            queries,
            [
                'volunteer collecting plastic beach',
                'hands sorting plastic on sand',
            ],
        )
        self.assertEqual(replacements, [recovered_spec])
        self.assertEqual(
            download_candidates.call_args.args[1],
            queries,
        )

    def test_persistent_outage_rescue_remains_empty_and_unaccepted(self):
        namespace = _load_retry_boundary()
        typed_outage = namespace['PexelsRetryError'](
            'bounded provider outage persists'
        )
        download_candidates = Mock(side_effect=typed_outage)
        namespace['_download_ranked_broll_candidates'] = download_candidates
        queries = namespace['_final_pexels_rescue_queries'](
            {'visual_queries': ['plastic cleanup beach']},
            {},
            provider_outage_stock_fallback=True,
        )

        replacements = namespace['_retry_bad_scene'](
            2,
            queries,
            set(),
            Path('/tmp/test-persistent-provider-outage'),
            [],
            file_prefix='final_qc_rescue',
            tolerate_pexels_failure=True,
        )

        self.assertEqual(queries, ['plastic cleanup beach'])
        self.assertEqual(replacements, [])
        self.assertFalse(bool(replacements))
        download_candidates.assert_called_once()

    def test_non_outage_missing_review_does_not_borrow_visual_queries(self):
        namespace = _load_retry_boundary()

        queries = namespace['_final_pexels_rescue_queries'](
            {'visual_queries': ['plastic cleanup beach']},
            {},
            provider_outage_stock_fallback=False,
        )

        self.assertEqual(queries, [])

    def test_outage_scene_keeps_final_review_queries_when_present(self):
        namespace = _load_retry_boundary()

        queries = namespace['_final_pexels_rescue_queries'](
            {'visual_queries': ['original broad query']},
            {'retry_queries': ['critic exact query']},
            provider_outage_stock_fallback=True,
        )

        self.assertEqual(queries, ['critic exact query'])

    def test_provider_outage_stock_scene_is_the_only_extra_paid_candidate(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': 0, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 1, 'has_visual': True, 'stock_score': 42},
            {'scene_index': 3, 'has_visual': True, 'stock_score': 91},
            {
                'scene_index': 2,
                'has_visual': True,
                'stock_score': 58,
                'provider_outage_stock_fallback': True,
            },
        ]

        selected, required_base, missing, cap_exceeded = allocate(
            ranked, 3, {2}, 86
        )

        self.assertEqual([item['scene_index'] for item in required_base], [0, 1])
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 3, 2])
        self.assertEqual(
            [item['scene_index'] for item in selected[3:]],
            [2],
        )
        self.assertEqual(missing, [])
        self.assertFalse(cap_exceeded)

    def test_multiple_provider_outages_each_receive_one_bounded_extra_slot(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': 0, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 1, 'has_visual': True, 'stock_score': 40},
            {'scene_index': 2, 'has_visual': True, 'stock_score': 90},
            {'scene_index': 4, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 5, 'has_visual': True, 'stock_score': 30},
        ]

        selected, required_base, missing, cap_exceeded = allocate(
            ranked, 3, {4, 5}, 86
        )

        self.assertEqual(len(required_base), 2)
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 2, 4, 5])
        self.assertEqual(
            {item['scene_index'] for item in selected[3:]},
            {4, 5},
        )
        self.assertEqual(missing, [])
        self.assertFalse(cap_exceeded)

    def test_outage_allocation_exposes_incomplete_base_or_missing_outage(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': index, 'has_visual': False, 'stock_score': -1}
            for index in range(4)
        ]

        selected, required_base, missing, cap_exceeded = allocate(
            ranked, 3, {9}, 86
        )

        self.assertEqual(len(selected), 3)
        self.assertEqual(len(required_base), 4)
        self.assertEqual(missing, [9])
        self.assertFalse(cap_exceeded)

    def test_more_than_two_provider_outages_never_allocate_a_third_extra_slot(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': index, 'has_visual': False, 'stock_score': -1}
            for index in range(5)
        ]

        selected, required_base, missing, cap_exceeded = allocate(
            ranked, 2, {2, 3, 4}, 86
        )

        self.assertEqual([item['scene_index'] for item in required_base], [0, 1])
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 2, 3])
        self.assertEqual(missing, [])
        self.assertTrue(cap_exceeded)

    def test_provider_outage_quarantines_low_score_stock_incumbent(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')
        contract_start = source.index('stock_contract_candidates = [')
        catch_start = source.index('except PexelsRetryError:', contract_start)
        frozen_pool_start = source.index('frozen_pool: list[dict]', catch_start)
        quarantine = source[catch_start:frozen_pool_start]

        self.assertIn('incumbent_specs = []', quarantine)
        self.assertIn('incumbent = None', quarantine)
        self.assertIn('scene_visuals[scene_idx] = []', quarantine)
        self.assertIn("'score': -1", quarantine)
        self.assertIn("'spec': None", quarantine)
        self.assertNotIn('except Exception', quarantine)

        generation_start = source.index('for candidate in selected_runway:')
        generation_end = source.index('# Re-review the exact clips', generation_start)
        generation = source[generation_start:generation_end]
        self.assertIn('stock_fallback = list(scene_visuals[scene_idx])', generation)
        self.assertIn(
            'scene_visuals[scene_idx] = [runway_spec, *stock_fallback][:3]',
            generation,
        )

    def test_emergency_cap_is_global_and_present_in_preflight_diagnostics(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')

        self.assertIn(
            'SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP = 2',
            source,
        )
        self.assertIn("'provider_outage_emergency_cap': (", source)
        self.assertIn('or outage_cap_exceeded', source)

    def test_parallel_search_burst_gets_one_sequential_second_chance(self):
        namespace = _load_search_boundary()
        find_broll = Mock(side_effect=[
            _network_error('parallel disconnect one'),
            _network_error('parallel disconnect two'),
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
        find_broll = Mock(side_effect=[
            _network_error(f'provider unavailable {index}')
            for index in range(4)
        ])
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
        self.assertIsInstance(caught.exception.__cause__, httpx.NetworkError)

    def test_transient_http_status_gets_one_bounded_search_retry(self):
        namespace = _load_search_boundary()
        find_broll = Mock(side_effect=[_http_status_error(429), []])
        namespace['find_broll'] = find_broll

        result = namespace['_download_ranked_broll_candidates'](
            2,
            ['container ship at sea'],
            set(),
            Path('/tmp/test-pexels-http-retry'),
            [],
            file_prefix='stock',
            selected_by='test',
            max_candidates=3,
            search_limit=18,
        )

        self.assertEqual(result, [])
        self.assertEqual(find_broll.call_count, 2)

    def test_non_transient_search_failures_propagate_without_retry(self):
        for failure in (
            _http_status_error(401),
            _http_status_error(403),
            _http_status_error(404),
            ValueError('invalid provider JSON'),
            RuntimeError('PEXELS_API_KEY is not configured'),
            OSError('local cache failure'),
        ):
            with self.subTest(failure=type(failure).__name__, detail=str(failure)):
                namespace = _load_search_boundary()
                find_broll = Mock(side_effect=failure)
                namespace['find_broll'] = find_broll

                with self.assertRaises(type(failure)) as raised:
                    namespace['_download_ranked_broll_candidates'](
                        2,
                        ['container ship at sea'],
                        set(),
                        Path('/tmp/test-pexels-non-transient'),
                        [],
                        file_prefix='stock',
                        selected_by='test',
                        max_candidates=3,
                        search_limit=18,
                    )

                self.assertIs(raised.exception, failure)
                self.assertEqual(find_broll.call_count, 1)

    def test_transient_download_failure_is_wrapped_at_actual_boundary(self):
        namespace = _load_search_boundary()
        namespace['find_broll'] = Mock(return_value=[{'pexels_id': 10}])
        namespace['_select_ranked_broll_candidates'] = Mock(return_value=[
            (
                'container ship at sea',
                {'pexels_id': 10, 'duration': 9.0},
            )
        ])
        download_error = _network_error('cdn unavailable')
        namespace['download_broll'] = Mock(side_effect=download_error)

        with self.assertRaises(namespace['PexelsRetryError']) as raised:
            namespace['_download_ranked_broll_candidates'](
                2,
                ['container ship at sea'],
                set(),
                Path('/tmp/test-pexels-download-transient'),
                [],
                file_prefix='stock',
                selected_by='test',
                max_candidates=1,
                search_limit=18,
            )

        self.assertIs(raised.exception.__cause__, download_error)

    def test_local_download_failure_propagates_and_cannot_authorize_fallback(self):
        namespace = _load_search_boundary()
        namespace['find_broll'] = Mock(return_value=[{'pexels_id': 10}])
        namespace['_select_ranked_broll_candidates'] = Mock(return_value=[
            (
                'container ship at sea',
                {'pexels_id': 10, 'duration': 9.0},
            )
        ])
        local_error = PermissionError('output directory is read-only')
        download_broll = Mock(side_effect=local_error)
        namespace['download_broll'] = download_broll

        with self.assertRaises(PermissionError) as raised:
            namespace['_download_ranked_broll_candidates'](
                2,
                ['container ship at sea'],
                set(),
                Path('/tmp/test-pexels-download-local'),
                [],
                file_prefix='stock',
                selected_by='test',
                max_candidates=1,
                search_limit=18,
            )

        self.assertIs(raised.exception, local_error)
        download_broll.assert_called_once()

    def test_classifier_status_allowlist_is_fail_closed(self):
        namespace = _load_search_boundary()
        classifier = namespace['_is_transient_pexels_provider_error']

        for status_code in (408, 425, 429, 500, 503, 599):
            with self.subTest(status_code=status_code):
                self.assertTrue(classifier(_http_status_error(status_code)))
        for status_code in (400, 401, 403, 404, 409, 422):
            with self.subTest(status_code=status_code):
                self.assertFalse(classifier(_http_status_error(status_code)))

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

    def test_pexels_failure_tolerance_opt_ins_remain_narrowly_scoped(self):
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

        self.assertEqual(len(tolerant_values), 3)
        self.assertTrue(any(
            isinstance(value, ast.Constant) and value.value is True
            for value in tolerant_values
        ))
        self.assertTrue(any(
            isinstance(value, ast.Name)
            and value.id == 'is_short_preview_authored_ai'
            for value in tolerant_values
        ))
        self.assertTrue(any(
            isinstance(value, ast.Compare)
            and isinstance(value.ops[0], ast.In)
            and isinstance(value.comparators[0], ast.Name)
            and value.comparators[0].id == 'provider_outage_stock_scenes'
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
        self.assertEqual(stock_contract.count('except PexelsRetryError'), 1)
        self.assertIn(
            'provider_outage_stock_scenes.add(scene_idx)',
            stock_contract,
        )
        self.assertNotIn('except Exception', stock_contract)


if __name__ == '__main__':
    unittest.main()

