import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import re
import tempfile
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
        and node.name == '_allocate_short_preview_forced_stock_runway'
    )
    namespace = {
        'SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP': 2,
        'SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP': 1,
    }
    exec(
        compile(
            ast.Module(body=[definition], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_allocate_short_preview_forced_stock_runway']


def _load_manual_qa_boundary():
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding='utf-8'),
        filename=str(SOURCE_PATH),
    )
    constant_names = {
        'MANUAL_QA_PREVIEW_STOCK_FLOOR',
        'MANUAL_QA_PREVIEW_GENERATED_FLOOR',
        'MANUAL_QA_PUBLISH_QUALITY_THRESHOLD',
        '_MANUAL_QA_CLEAR_VISUAL_FIELDS',
        '_MANUAL_QA_DIAGNOSTIC_BOOLEAN_FIELDS',
    }
    function_names = {
        '_is_generated_visual_spec',
        '_reviewed_visual_spec',
        '_manual_qa_visual_source_type',
        '_manual_qa_preview_passes',
        '_manual_qa_preview_record',
        '_manual_qa_visual_identity',
        '_manual_qa_failure_diagnostic',
        '_manual_qa_preview_decisions',
        '_generated_visual_spec',
    }
    definitions = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in constant_names
                for target in node.targets
            )
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name in function_names
        )
    ]
    namespace = {'Path': Path, 'hashlib': hashlib}
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


def _manual_review(score=68, **overrides):
    review = {
        'best_candidate_index': 0,
        'score': score,
        'reason': 'The exact subject and narrated action are visible.',
        'retry_queries': ['closer literal action query'],
        'evidence_gate_passed': True,
        'editorial_gate_passed': True,
        'subject_visible': True,
        'spoken_action_visible': True,
        'unexplained_reset': False,
        'prominent_readable_text_or_logo_visible': False,
        'major_visual_artifact_visible': False,
        'effectively_static_or_frozen': False,
    }
    review.update(overrides)
    return review


def _manual_options(**overrides):
    options = {
        'mode': 'preview',
        'quality_threshold': 86,
    }
    options.update(overrides)
    return options


def _stock_spec(path='/tmp/real-stock.mp4'):
    return {
        'path': path,
        'source_duration': 9.0,
        'source_type': 'stock',
        'stock_provider': 'pexels',
    }


class ShortPreviewBrollFallbackTests(unittest.TestCase):
    def test_manual_qa_source_floors_and_positive_provenance_are_exact(self):
        namespace = _load_manual_qa_boundary()
        passes = namespace['_manual_qa_preview_passes']
        scene = {'ai_prompt': ''}
        stock_spec = _stock_spec()
        generated = namespace['_generated_visual_spec']('/tmp/runway.mp4')

        for spec, score, expected in (
            (stock_spec, 59, False),
            (stock_spec, 60, True),
            (stock_spec, 85, True),
            (generated, 64, False),
            (generated, 65, True),
            (generated, 85, True),
            (generated, 86, False),
        ):
            with self.subTest(score=score):
                self.assertIs(
                    passes(
                        _manual_options(),
                        0.5,
                        scene,
                        _manual_review(score),
                        spec,
                    ),
                    expected,
                )

        self.assertFalse(
            passes(
                _manual_options(),
                0.5,
                scene,
                _manual_review(75),
                '/tmp/opaque.mp4',
            )
        )
        self.assertFalse(
            passes(
                _manual_options(),
                0.5,
                scene,
                _manual_review(75),
                {'path': '/tmp/unproven.mp4'},
            )
        )
        self.assertFalse(
            passes(
                _manual_options(),
                0.5,
                scene,
                _manual_review(75),
                {'path': '/tmp/partial-generated.mp4', 'forbid_loop': True},
            )
        )

    def test_manual_qa_requires_explicit_hard_and_editorial_evidence(self):
        namespace = _load_manual_qa_boundary()
        passes = namespace['_manual_qa_preview_passes']
        scene = {'ai_prompt': ''}
        stock_spec = _stock_spec()

        for field, bad_value in (
            ('evidence_gate_passed', False),
            ('editorial_gate_passed', False),
            ('subject_visible', False),
            ('spoken_action_visible', False),
            ('unexplained_reset', True),
            ('prominent_readable_text_or_logo_visible', True),
            ('major_visual_artifact_visible', True),
            ('effectively_static_or_frozen', True),
        ):
            with self.subTest(field=field):
                self.assertFalse(
                    passes(
                        _manual_options(),
                        0.5,
                        scene,
                        _manual_review(**{field: bad_value}),
                        stock_spec,
                    )
                )

        for missing_field in (
            'evidence_gate_passed',
            'editorial_gate_passed',
            'subject_visible',
            'spoken_action_visible',
            'unexplained_reset',
            'prominent_readable_text_or_logo_visible',
            'major_visual_artifact_visible',
            'effectively_static_or_frozen',
        ):
            with self.subTest(missing_field=missing_field):
                review = _manual_review()
                review.pop(missing_field)
                self.assertFalse(
                    passes(
                        _manual_options(),
                        0.5,
                        scene,
                        review,
                        stock_spec,
                    )
                )

        retry_review = _manual_review(
            68,
            retry_queries=['literal storm ship', 'container vessel waves'],
        )
        self.assertTrue(
            passes(
                _manual_options(), 0.5, scene, retry_review, stock_spec
            )
        )
        record = namespace['_manual_qa_preview_record'](
            1, retry_review, stock_spec
        )
        self.assertEqual(
            record['retry_queries'],
            ['literal storm ship', 'container vessel waves'],
        )

    def test_manual_qa_identity_binds_content_cut_and_source_contract(self):
        namespace = _load_manual_qa_boundary()
        identity = namespace['_manual_qa_visual_identity']
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / 'first.mp4'
            second = Path(tmp) / 'second.mp4'
            first.write_bytes(b'exact-private-preview-clip')
            second.write_bytes(b'exact-private-preview-clip')
            first_spec = _stock_spec(str(first))
            first_spec['start_fraction'] = 0.35
            same_spec = _stock_spec(str(second))
            same_spec['start_fraction'] = 0.35

            self.assertEqual(identity(first_spec), identity(same_spec))

            changed_cut = dict(same_spec, start_fraction=0.36)
            self.assertNotEqual(identity(first_spec), identity(changed_cut))
            second.write_bytes(b'different-private-preview-clip')
            self.assertNotEqual(identity(first_spec), identity(same_spec))

    def test_manual_qa_failure_diagnostic_is_exact_and_secret_safe(self):
        namespace = _load_manual_qa_boundary()
        diagnostic = namespace['_manual_qa_failure_diagnostic']
        secret = (
            'sk-never-leak '
            'https://storage.example/private.mp4?X-Amz-Signature=never'
        )
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / 'stock.mp4'
            clip.write_bytes(b'video-bytes')
            review = _manual_review(
                40,
                raw_score=92,
                best_moment_index=2,
                evidence_moment_indices=[0, 1, 2],
                location_continuity_applicable=True,
                location_continuity_matches=False,
                evidence_gate_passed=False,
                reason=secret,
                arbitrary_secret=secret,
                path=secret,
            )
            result = diagnostic(4, review, _stock_spec(str(clip)))

        serialized = repr(result)
        self.assertEqual(result['scene_index'], 4)
        self.assertEqual(result['source_type'], 'stock')
        self.assertEqual(result['manual_qa_floor'], 60)
        self.assertEqual(result['score'], 40)
        self.assertEqual(result['raw_score'], 92)
        self.assertIs(result['location_continuity_matches'], False)
        self.assertEqual(result['evidence_moment_indices'], [0, 1, 2])
        self.assertNotIn(secret, serialized)
        self.assertNotIn('reason', result)
        self.assertNotIn('path', result)
        self.assertNotIn('arbitrary_secret', result)

    def test_manual_qa_allows_exact_generated_but_excludes_non_preview_modes(self):
        namespace = _load_manual_qa_boundary()
        passes = namespace['_manual_qa_preview_passes']
        generated = namespace['_generated_visual_spec']('/tmp/runway.mp4')
        review = _manual_review(68)
        stock_spec = _stock_spec()

        self.assertTrue(
            passes(_manual_options(), 0.5, {'ai_prompt': 'hero shot'}, review, stock_spec)
        )
        self.assertTrue(
            passes(_manual_options(), 0.5, {'ai_prompt': ''}, review, generated)
        )
        self.assertFalse(
            passes(
                _manual_options(mode='production'),
                0.5,
                {'ai_prompt': ''},
                review,
                stock_spec,
            )
        )
        self.assertFalse(
            passes(_manual_options(), 0.50001, {'ai_prompt': ''}, review, stock_spec)
        )
        self.assertFalse(
            passes(
                _manual_options(quality_threshold=87),
                0.5,
                {'ai_prompt': ''},
                review,
                stock_spec,
            )
        )

    def test_forced_generated_and_recovered_stock_use_their_source_floors(self):
        namespace = _load_manual_qa_boundary()
        passes = namespace['_manual_qa_preview_passes']
        generated = namespace['_generated_visual_spec']('/tmp/runway.mp4')
        stock_spec = _stock_spec('/tmp/recovered-pexels.mp4')
        args = (
            _manual_options(),
            0.5,
            {'ai_prompt': ''},
        )

        self.assertTrue(
            passes(*args, _manual_review(65), generated)
        )
        self.assertTrue(
            passes(*args, _manual_review(60), stock_spec)
        )

    def test_live_seven_scene_matrix_rejects_only_gate_failed_scene_two(self):
        namespace = _load_manual_qa_boundary()
        decide = namespace['_manual_qa_preview_decisions']
        generated_spec = namespace['_generated_visual_spec']
        scores = [75, 65, 40, 68, 60, 65, 67]
        generated_indices = {0, 3, 6}
        scenes = [
            {'ai_prompt': 'authored hero'} if index in generated_indices
            else {'ai_prompt': ''}
            for index in range(len(scores))
        ]
        visuals = [
            [generated_spec(f'/tmp/generated-{index}.mp4')]
            if index in generated_indices
            else [_stock_spec(f'/tmp/stock-{index}.mp4')]
            for index in range(len(scores))
        ]
        reviews = {
            index: _manual_review(
                score,
                evidence_gate_passed=(index != 2),
                subject_visible=(index != 2),
            )
            for index, score in enumerate(scores)
        }

        accepted, rejected = decide(
            _manual_options(),
            0.5,
            scenes,
            reviews,
            visuals,
            set(range(len(scores))),
        )

        self.assertEqual(sorted(accepted), [0, 1, 3, 4, 5, 6])
        self.assertEqual(rejected, [2])
        self.assertNotIn(2, accepted)
        self.assertEqual(
            {index: record['source_type'] for index, record in accepted.items()},
            {
                0: 'generated',
                1: 'stock',
                3: 'generated',
                4: 'stock',
                5: 'stock',
                6: 'generated',
            },
        )

        repair = Mock()
        render = Mock()
        if rejected:
            repair(rejected)
        else:
            render()
        repair.assert_called_once_with([2])
        render.assert_not_called()

    def test_accepted_generated_floor_skips_repair_and_reaches_render(self):
        namespace = _load_manual_qa_boundary()
        decide = namespace['_manual_qa_preview_decisions']
        generated = namespace['_generated_visual_spec']('/tmp/runway.mp4')
        accepted, rejected = decide(
            _manual_options(),
            0.5,
            [{'ai_prompt': 'hero'}],
            {0: _manual_review(65)},
            [[generated]],
            [0],
        )
        repair = Mock()
        render = Mock()
        if rejected:
            repair(rejected)
        else:
            render()
        self.assertEqual(list(accepted), [0])
        repair.assert_not_called()
        render.assert_called_once_with()

    def test_manual_prepass_final_failure_is_terminal_before_any_repair(self):
        namespace = _load_manual_qa_boundary()
        passes = namespace['_manual_qa_preview_passes']
        scene = {'ai_prompt': ''}
        stock_spec = _stock_spec()

        self.assertTrue(
            passes(
                _manual_options(), 0.5, scene, _manual_review(68), stock_spec
            )
        )
        self.assertFalse(
            passes(
                _manual_options(),
                0.5,
                scene,
                _manual_review(68, subject_visible=False),
                stock_spec,
            )
        )

        source = SOURCE_PATH.read_text(encoding='utf-8')
        terminal = source.index(
            'Manual-QA preview failed exact final revalidation'
        )
        repair = source.index('final_runway_repair_candidates =', terminal)
        self.assertLess(terminal, repair)

    def test_manual_prepass_disagreement_gets_one_bounded_blind_vote(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')
        final_review = source.index("work / 'final_visual_qc'")
        adjudication = source.index("work / 'manual_qa_final_adjudication'")
        terminal = source.index(
            'Manual-QA preview failed exact final revalidation'
        )

        self.assertLess(final_review, adjudication)
        self.assertLess(adjudication, terminal)
        self.assertIn('len(terminal_manual_qa_candidates) <= 2', source)
        self.assertIn(
            '_manual_qa_visual_identity(selected_spec)\n'
            '                == manual_qa_prepass_identities.get(scene_idx)',
            source,
        )
        self.assertIn("final_review.get('subject_visible') is True", source)
        self.assertIn("final_review.get('spoken_action_visible') is True", source)
        self.assertIn("_missing_review_attempts=0", source[adjudication:terminal])
        self.assertIn(
            'scene_idx not in manual_qa_preserve_exact_cut_scenes',
            source,
        )

    def test_manual_and_forced_sets_are_explicitly_disjoint(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')

        self.assertIn(
            'manual_prepass_forced_overlap = manual_qa_preview_scenes & (',
            source,
        )
        self.assertIn(
            'final_manual_forced_overlap = manual_qa_preview_scenes & (',
            source,
        )
        self.assertIn('provider_outage_stock_scenes.discard(scene_idx)', source)
        self.assertIn('stock_quality_fallback_scenes.discard(scene_idx)', source)

    def test_manual_qa_metadata_contract_is_explicit(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')

        self.assertIn("'manual_qa_preview'", source)
        self.assertIn("'quality_disposition': quality_disposition", source)
        self.assertIn("'manual_qa_required': manual_qa_required", source)
        self.assertIn("'manual_qa_floor': MANUAL_QA_PREVIEW_STOCK_FLOOR", source)
        self.assertIn("'manual_qa_floors': manual_qa_floors", source)
        self.assertIn("'manual_qa_scene_indices': manual_qa_scene_indices", source)
        self.assertIn("'manual_qa_scene_scores': manual_qa_scene_scores", source)
        self.assertIn(
            "'manual_qa_scene_source_types': manual_qa_scene_source_types",
            source,
        )
        self.assertIn(
            "'manual_qa_scene_retry_queries': manual_qa_scene_retry_queries",
            source,
        )
        self.assertIn(
            "'manual_qa_scene_reasons': manual_qa_scene_reasons",
            source,
        )
        self.assertIn(
            "'manual_qa_scene_reviews': manual_qa_scene_reviews",
            source,
        )
        self.assertIn(
            "'publish_quality_threshold': (",
            source,
        )

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
            forced_stock_fallback=True,
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

    def test_stock_quality_fallback_uses_same_bounded_final_rescue(self):
        namespace = _load_retry_boundary()
        recovered_spec = {
            'path': '/tmp/recovered-quality-stock.mp4',
            'start_fraction': 0.35,
            'source_duration': 9.0,
        }
        download_candidates = Mock(return_value=[recovered_spec])
        namespace['_download_ranked_broll_candidates'] = download_candidates
        queries = namespace['_final_pexels_rescue_queries'](
            {
                'visual_queries': [
                    'cargo ship rough storm sea',
                    'large freight ship ocean waves',
                    'third query is outside the bound',
                ]
            },
            {},
            forced_stock_fallback=True,
        )

        replacements = namespace['_retry_bad_scene'](
            1,
            queries,
            set(),
            Path('/tmp/test-stock-quality-final-rescue'),
            [],
            file_prefix='final_qc_rescue',
            tolerate_pexels_failure=True,
        )

        self.assertEqual(len(queries), 2)
        self.assertEqual(replacements, [recovered_spec])
        self.assertEqual(download_candidates.call_args.args[1], queries)

        source = SOURCE_PATH.read_text(encoding='utf-8')
        final_rescue = source[source.index('# Give every still-rejected clip'):]
        self.assertGreaterEqual(
            final_rescue.count('scene_idx in stock_quality_fallback_scenes'),
            2,
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
            forced_stock_fallback=True,
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
            forced_stock_fallback=False,
        )

        self.assertEqual(queries, [])

    def test_outage_scene_keeps_final_review_queries_when_present(self):
        namespace = _load_retry_boundary()

        queries = namespace['_final_pexels_rescue_queries'](
            {'visual_queries': ['original broad query']},
            {'retry_queries': ['critic exact query']},
            forced_stock_fallback=True,
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

        (
            selected, required_base, missing, missing_quality,
            cap_exceeded, quality_cap_exceeded, overlap,
        ) = allocate(ranked, 3, {2}, set(), 86)

        self.assertEqual([item['scene_index'] for item in required_base], [0, 1])
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 3, 2])
        self.assertEqual(
            [item['scene_index'] for item in selected[3:]],
            [2],
        )
        self.assertEqual(missing, [])
        self.assertEqual(missing_quality, [])
        self.assertFalse(cap_exceeded)
        self.assertFalse(quality_cap_exceeded)
        self.assertEqual(overlap, [])

    def test_multiple_provider_outages_each_receive_one_bounded_extra_slot(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': 0, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 1, 'has_visual': True, 'stock_score': 40},
            {'scene_index': 2, 'has_visual': True, 'stock_score': 90},
            {'scene_index': 4, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 5, 'has_visual': True, 'stock_score': 30},
        ]

        (
            selected, required_base, missing, missing_quality,
            cap_exceeded, quality_cap_exceeded, overlap,
        ) = allocate(ranked, 3, {4, 5}, set(), 86)

        self.assertEqual(len(required_base), 2)
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 2, 4, 5])
        self.assertEqual(
            {item['scene_index'] for item in selected[3:]},
            {4, 5},
        )
        self.assertEqual(missing, [])
        self.assertEqual(missing_quality, [])
        self.assertFalse(cap_exceeded)
        self.assertFalse(quality_cap_exceeded)
        self.assertEqual(overlap, [])

    def test_outage_allocation_exposes_incomplete_base_or_missing_outage(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': index, 'has_visual': False, 'stock_score': -1}
            for index in range(4)
        ]

        (
            selected, required_base, missing, missing_quality,
            cap_exceeded, quality_cap_exceeded, overlap,
        ) = allocate(ranked, 3, {9}, set(), 86)

        self.assertEqual(len(selected), 3)
        self.assertEqual(len(required_base), 4)
        self.assertEqual(missing, [9])
        self.assertEqual(missing_quality, [])
        self.assertFalse(cap_exceeded)
        self.assertFalse(quality_cap_exceeded)
        self.assertEqual(overlap, [])

    def test_more_than_two_provider_outages_never_allocate_a_third_extra_slot(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': index, 'has_visual': False, 'stock_score': -1}
            for index in range(5)
        ]

        (
            selected, required_base, missing, missing_quality,
            cap_exceeded, quality_cap_exceeded, overlap,
        ) = allocate(ranked, 2, {2, 3, 4}, set(), 86)

        self.assertEqual([item['scene_index'] for item in required_base], [0, 1])
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 2, 3])
        self.assertEqual(missing, [])
        self.assertEqual(missing_quality, [])
        self.assertTrue(cap_exceeded)
        self.assertFalse(quality_cap_exceeded)
        self.assertEqual(overlap, [])

    def test_one_stock_quality_failure_gets_exactly_one_extra_paid_slot(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': 0, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 1, 'has_visual': True, 'stock_score': 40},
            {'scene_index': 2, 'has_visual': True, 'stock_score': 91},
            {
                'scene_index': 3,
                'has_visual': False,
                'stock_score': 68,
                'stock_quality_fallback': True,
            },
        ]

        (
            selected, required_base, missing_outage, missing_quality,
            outage_cap_exceeded, quality_cap_exceeded, overlap,
        ) = allocate(ranked, 3, set(), {3}, 86)

        self.assertEqual([item['scene_index'] for item in required_base], [0, 1])
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 2, 3])
        self.assertEqual([item['scene_index'] for item in selected[3:]], [3])
        self.assertEqual(missing_outage, [])
        self.assertEqual(missing_quality, [])
        self.assertFalse(outage_cap_exceeded)
        self.assertFalse(quality_cap_exceeded)
        self.assertEqual(overlap, [])

    def test_two_stock_quality_failures_exceed_cap_before_selection(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': 0, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 2, 'has_visual': False, 'stock_score': 68},
            {'scene_index': 3, 'has_visual': False, 'stock_score': 61},
        ]

        (
            selected, _required_base, _missing_outage, missing_quality,
            _outage_cap_exceeded, quality_cap_exceeded, _overlap,
        ) = allocate(ranked, 1, set(), {2, 3}, 86)

        self.assertEqual([item['scene_index'] for item in selected], [0, 2])
        self.assertEqual(missing_quality, [])
        self.assertTrue(quality_cap_exceeded)

        source = SOURCE_PATH.read_text(encoding='utf-8')
        cap_check = source.index(
            'len(semantic_stock_quality_failures)\n'
            '                > SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP'
        )
        quarantine = source.index(
            'for failure in semantic_stock_quality_failures:',
            cap_check,
        )
        paid_generation = source.index('for candidate in selected_runway:', quarantine)
        self.assertLess(cap_check, quarantine)
        self.assertLess(quarantine, paid_generation)
        self.assertIn('or quality_cap_exceeded', source)
        self.assertIn('unroutable_stock_failures', source)

    def test_provider_outage_and_quality_fallback_caps_combine_without_using_base(self):
        allocate = _load_outage_allocation_boundary()
        ranked = [
            {'scene_index': 0, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 1, 'has_visual': True, 'stock_score': 50},
            {'scene_index': 2, 'has_visual': True, 'stock_score': 90},
            {'scene_index': 3, 'has_visual': False, 'stock_score': -1},
            {'scene_index': 4, 'has_visual': False, 'stock_score': 68},
        ]

        (
            selected, required_base, missing_outage, missing_quality,
            outage_cap_exceeded, quality_cap_exceeded, overlap,
        ) = allocate(ranked, 3, {3}, {4}, 86)

        self.assertEqual([item['scene_index'] for item in required_base], [0, 1])
        self.assertEqual([item['scene_index'] for item in selected], [0, 1, 2, 3, 4])
        self.assertEqual([item['scene_index'] for item in selected[3:]], [3, 4])
        self.assertEqual(missing_outage, [])
        self.assertEqual(missing_quality, [])
        self.assertFalse(outage_cap_exceeded)
        self.assertFalse(quality_cap_exceeded)
        self.assertEqual(overlap, [])

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

    def test_stock_quality_fallback_quarantines_incumbent_after_full_tournament(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')
        contract_start = source.index('stock_contract_candidates = [')
        tournament_start = source.index(
            'for round_index, (candidate_start, candidate_end)',
            contract_start,
        )
        quarantine_start = source.index(
            'for failure in semantic_stock_quality_failures:',
            tournament_start,
        )
        ranking_start = source.index(
            'def rank_runway_candidates()',
            quarantine_start,
        )
        quarantine = source[quarantine_start:ranking_start]

        self.assertLess(tournament_start, quarantine_start)
        self.assertIn('scene_visuals[scene_idx] = []', quarantine)
        self.assertIn('stock_quality_fallback_scenes.add(scene_idx)', quarantine)
        self.assertIn(
            "'stage': 'pre_runway_stock_quality_fallback'",
            quarantine,
        )
        self.assertNotIn('quality_threshold =', quarantine)

    def test_stock_quality_fallback_is_short_preview_only_and_keeps_final_gate(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')
        bounded_preview = source.index('if is_bounded_short_preview:')
        quality_fallback = source.index(
            'stock_quality_fallback_scenes.add(scene_idx)',
            bounded_preview,
        )
        ranking = source.index('def rank_runway_candidates()', quality_fallback)
        final_gate = source.index(
            "int(final_reviews[idx].get('score', 0)) < quality_threshold",
            ranking,
        )

        self.assertLess(bounded_preview, quality_fallback)
        self.assertLess(quality_fallback, ranking)
        self.assertLess(ranking, final_gate)
        self.assertIn(
            'options.get(\'mode\') == \'preview\'\n'
            '            and duration_minutes <= 0.6',
            source,
        )
        self.assertIn(
            "if has_visual and score >= quality_threshold:",
            source,
        )
        self.assertIn("if bool(failure.get('has_visual'))", source)
        self.assertIn("and int(failure.get('stock_score', -1)) >= 0", source)
        self.assertIn("and int(failure['scene_index']) in current_reviews", source)

    def test_emergency_cap_is_global_and_present_in_preflight_diagnostics(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')

        self.assertIn(
            'SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP = 2',
            source,
        )
        self.assertIn(
            'SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP = 1',
            source,
        )
        self.assertIn("'provider_outage_emergency_cap': (", source)
        self.assertIn("'stock_quality_emergency_cap': (", source)
        self.assertIn('or outage_cap_exceeded', source)
        self.assertIn('or quality_cap_exceeded', source)
        self.assertIn("else 'stock_quality_fallback'", source)
        self.assertIn("'stock_quality_fallback_scene_indices': sorted(", source)

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
            isinstance(node, ast.Compare)
            and isinstance(node.ops[0], ast.In)
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == 'provider_outage_stock_scenes'
            for value in tolerant_values
            for node in ast.walk(value)
        ))
        self.assertTrue(any(
            isinstance(node, ast.Compare)
            and isinstance(node.ops[0], ast.In)
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == 'stock_quality_fallback_scenes'
            for value in tolerant_values
            for node in ast.walk(value)
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

