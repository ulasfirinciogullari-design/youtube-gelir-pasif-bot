import ast
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx

from app.services.visual_identity import manufactured_replica_guardrail


class FinalVisualQualityError(RuntimeError):
    pass


class PreRunwayRetryableError(RuntimeError):
    pass


class FinalAudioQualityError(RuntimeError):
    pass


class AudioQCError(RuntimeError):
    pass


class VoiceQualityError(RuntimeError):
    pass


class VoiceScriptFitError(VoiceQualityError):
    pass


class UnsupportedLanguageError(ValueError):
    pass


def _load_prompt_functions():
    source_path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(
        source_path.read_text(encoding='utf-8'),
        filename=str(source_path),
    )
    names = {
        '_visual_path',
        '_truncate_utf16',
        '_sanitize_provider_visual_text',
        '_identity_proof_clause',
        '_runway_prompt_for_scene',
        '_apply_visual_review',
        '_runway_generation_seconds',
        '_runway_failure_diagnostic',
        '_runway_failure_payload',
        '_runway_single_pass_supported',
        '_validate_runway_single_pass_candidates',
        '_preflight_runway_candidates_before_paid',
        '_generated_visual_spec',
        '_render_target_duration',
        '_max_runway_scenes',
        '_short_preview_voice_duration_qc',
        '_strict_short_preview_render_qc',
        '_synthesize_voice_candidate',
        '_verify_audio_narration_with_retry',
        'normalize_pipeline_language',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        're': re,
        'math': math,
        'json': json,
        'Path': Path,
        'FinalVisualQualityError': FinalVisualQualityError,
        'PreRunwayRetryableError': PreRunwayRetryableError,
        'FinalAudioQualityError': FinalAudioQualityError,
        'AudioQCError': AudioQCError,
        'VoiceQualityError': VoiceQualityError,
        'VoiceScriptFitError': VoiceScriptFitError,
        'UnsupportedLanguageError': UnsupportedLanguageError,
        'SUPPORTED_PIPELINE_LANGUAGES': frozenset({
            'tr', 'en', 'de', 'es', 'ar',
        }),
        'httpx': httpx,
        'time': SimpleNamespace(sleep=lambda _seconds: None),
        'is_transient_voice_http_error': lambda exc: bool(
            isinstance(exc, httpx.RequestError)
            or (
                isinstance(exc, httpx.HTTPStatusError)
                and (
                    exc.response.status_code in {408, 425, 429}
                    or exc.response.status_code >= 500
                )
            )
        ),
        'voice_http_retry_delay_seconds': lambda _exc, _retry_index: 0.0,
        'MAX_AUDIO_GENERATION_ATTEMPTS': 3,
        'AUDIO_QC_PROVIDER_ATTEMPTS': 2,
        'AUDIO_QC_PROVIDER_RETRY_DELAY_SECONDS': 1.0,
        'synthesize_scene_sequence': None,
        'verify_audio_narration': None,
        'manufactured_replica_guardrail': manufactured_replica_guardrail,
        'preview_paid_ai_limit': lambda options, scene_count, duration: (
            min(
                1
                if (options.get('visual_mix') or 'balanced') == 'real_first'
                else 6
                if (options.get('visual_mix') or 'balanced') == 'ai_first'
                else 4,
                scene_count,
            )
            if options.get('mode') == 'preview' and duration <= 0.6
            else None
        ),
    }
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(source_path),
            'exec',
        ),
        namespace,
    )
    return (
        namespace['_runway_prompt_for_scene'],
        namespace['_apply_visual_review'],
        namespace['_runway_generation_seconds'],
        namespace['_runway_failure_diagnostic'],
        namespace['_runway_failure_payload'],
        namespace['_runway_single_pass_supported'],
        namespace['_validate_runway_single_pass_candidates'],
        namespace['_preflight_runway_candidates_before_paid'],
        namespace['_generated_visual_spec'],
        namespace['_render_target_duration'],
        namespace['_max_runway_scenes'],
        namespace['_short_preview_voice_duration_qc'],
        namespace['_strict_short_preview_render_qc'],
        namespace['_synthesize_voice_candidate'],
        namespace['_verify_audio_narration_with_retry'],
        namespace['normalize_pipeline_language'],
    )


(
    runway_prompt,
    apply_visual_review,
    runway_generation_seconds,
    runway_failure_diagnostic,
    runway_failure_payload,
    runway_single_pass_supported,
    validate_runway_single_pass_candidates,
    preflight_runway_candidates_before_paid,
    generated_visual_spec,
    render_target_duration,
    max_runway_scenes,
    short_preview_voice_duration_qc,
    strict_short_preview_render_qc,
    synthesize_voice_candidate,
    verify_audio_narration_with_retry,
    normalize_pipeline_language,
) = (
    _load_prompt_functions()
)


class RunwayPromptTests(unittest.TestCase):
    def test_pipeline_language_is_validated_before_provider_work(self):
        for language in ('tr', 'en', 'de', 'es', 'ar', ' TR '):
            with self.subTest(language=language):
                self.assertIn(
                    normalize_pipeline_language(language),
                    {'tr', 'en', 'de', 'es', 'ar'},
                )
        for language in ('fr', 'xx', 'en-US', ''):
            with self.subTest(language=language):
                with self.assertRaises(UnsupportedLanguageError):
                    normalize_pipeline_language(language)

    def test_short_preview_voice_duration_gate_rejects_thin_script(self):
        self.assertTrue(short_preview_voice_duration_qc(
            {'duration_after_fit': 29.5},
            30.0,
        )['pass'])
        natural_ending_hold = short_preview_voice_duration_qc(
            {'duration_after_fit': 28.728},
            30.0,
        )
        self.assertTrue(natural_ending_hold['pass'])
        self.assertEqual(natural_ending_hold['minimum_seconds'], 28.7)
        rejected = short_preview_voice_duration_qc(
            {'duration_after_fit': 27.0},
            30.0,
        )
        self.assertFalse(rejected['pass'])
        self.assertEqual(rejected['reason'], 'short_form_script_too_thin')
        self.assertFalse(rejected['retryable'])
        self.assertEqual(rejected['minimum_seconds'], 28.7)

        near_boundary = short_preview_voice_duration_qc(
            {'duration_after_fit': 28.5},
            30.0,
        )
        self.assertFalse(near_boundary['pass'])
        self.assertTrue(near_boundary['retryable'])

    def test_short_preview_duration_policy_integrates_final_render_tail(self):
        rendered = {
            'duration': 30.0,
            'frame_count': 900,
            # 30.000 - 28.728 = 1.272 seconds of intentional final hold.
            'ending_silence_seconds': 1.272,
        }
        accepted = strict_short_preview_render_qc(
            rendered,
            30.0,
            28.728,
        )
        self.assertTrue(accepted['pass'])
        self.assertEqual(accepted['expected_frames'], 900)
        self.assertLessEqual(
            accepted['ending_silence_seconds'],
            accepted['maximum_ending_silence_seconds'],
        )

        encoder_and_voice_tail = strict_short_preview_render_qc(
            {**rendered, 'ending_silence_seconds': 1.5},
            30.0,
            28.728,
        )
        self.assertTrue(encoder_and_voice_tail['pass'])

        excessive_tail = strict_short_preview_render_qc(
            {**rendered, 'ending_silence_seconds': 1.56},
            30.0,
            28.7,
        )
        self.assertFalse(excessive_tail['pass'])
        self.assertEqual(
            excessive_tail['reason'],
            'final_ending_silence_out_of_bounds',
        )

        wrong_frame_count = strict_short_preview_render_qc(
            {**rendered, 'frame_count': 899},
            30.0,
            28.728,
        )
        self.assertFalse(wrong_frame_count['pass'])
        self.assertEqual(
            wrong_frame_count['reason'],
            'final_frame_count_mismatch',
        )

    def test_voice_synthesis_quality_retries_use_three_distinct_seeds(self):
        calls = []

        def synthesize(
            _scenes, _job_id, _target, *, generation_attempt, language=None
        ):
            calls.append(generation_attempt)
            if generation_attempt < 2:
                raise VoiceQualityError('alignment defect')
            return {'path': 'voice.mp3'}

        globals_dict = synthesize_voice_candidate.__globals__
        previous = globals_dict['synthesize_scene_sequence']
        globals_dict['synthesize_scene_sequence'] = synthesize
        try:
            result = synthesize_voice_candidate([], 'job', 30.0)
        finally:
            globals_dict['synthesize_scene_sequence'] = previous

        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(result['_generation_attempt'], 2)
        self.assertEqual(result['_generation_attempts_used'], 3)
        self.assertEqual(len(result['_synthesis_quality_errors']), 2)

    def test_voice_synthesis_quality_exhaustion_is_terminal(self):
        calls = []

        def synthesize(
            _scenes, _job_id, _target, *, generation_attempt, language=None
        ):
            calls.append(generation_attempt)
            raise VoiceQualityError('deterministic fit defect')

        globals_dict = synthesize_voice_candidate.__globals__
        previous = globals_dict['synthesize_scene_sequence']
        globals_dict['synthesize_scene_sequence'] = synthesize
        try:
            with self.assertRaises(FinalAudioQualityError):
                synthesize_voice_candidate([], 'job', 30.0)
        finally:
            globals_dict['synthesize_scene_sequence'] = previous

        self.assertEqual(calls, [0, 1, 2])

    def test_voice_structural_fit_error_is_terminal_without_seed_retry(self):
        calls = []

        def synthesize(
            _scenes, _job_id, _target, *, generation_attempt, language=None
        ):
            calls.append(generation_attempt)
            raise VoiceScriptFitError('script is structurally too long')

        globals_dict = synthesize_voice_candidate.__globals__
        previous = globals_dict['synthesize_scene_sequence']
        globals_dict['synthesize_scene_sequence'] = synthesize
        try:
            with self.assertRaises(FinalAudioQualityError):
                synthesize_voice_candidate([], 'job', 30.0)
        finally:
            globals_dict['synthesize_scene_sequence'] = previous

        self.assertEqual(calls, [0])

    def test_transient_voice_provider_error_consumes_same_three_attempt_budget(self):
        calls = []
        request = httpx.Request('POST', 'https://voice.example.invalid')
        response = httpx.Response(429, request=request)

        def synthesize(
            _scenes, _job_id, _target, *, generation_attempt, language=None
        ):
            calls.append(generation_attempt)
            raise httpx.HTTPStatusError(
                'rate limited',
                request=request,
                response=response,
            )

        globals_dict = synthesize_voice_candidate.__globals__
        previous = globals_dict['synthesize_scene_sequence']
        globals_dict['synthesize_scene_sequence'] = synthesize
        try:
            with self.assertRaises(FinalAudioQualityError):
                synthesize_voice_candidate([], 'job', 30.0)
        finally:
            globals_dict['synthesize_scene_sequence'] = previous

        self.assertEqual(calls, [0, 1, 2])

    def test_long_form_exhausted_scene_retry_never_restarts_full_batch(self):
        calls = []
        request = httpx.Request('POST', 'https://voice.example.invalid')
        response = httpx.Response(429, request=request)

        def synthesize(
            _scenes, _job_id, _target, *, generation_attempt, language=None
        ):
            calls.append(generation_attempt)
            raise httpx.HTTPStatusError(
                'rate limited',
                request=request,
                response=response,
            )

        globals_dict = synthesize_voice_candidate.__globals__
        previous = globals_dict['synthesize_scene_sequence']
        globals_dict['synthesize_scene_sequence'] = synthesize
        try:
            with self.assertRaises(FinalAudioQualityError):
                synthesize_voice_candidate([], 'job', 60.0)
        finally:
            globals_dict['synthesize_scene_sequence'] = previous

        self.assertEqual(calls, [0])

    def test_voice_candidate_forwards_pipeline_language_to_synthesis(self):
        calls = []

        def synthesize(
            _scenes, _job_id, _target, *, generation_attempt, language=None
        ):
            calls.append((generation_attempt, language))
            return {'path': 'voice.mp3'}

        globals_dict = synthesize_voice_candidate.__globals__
        previous = globals_dict['synthesize_scene_sequence']
        globals_dict['synthesize_scene_sequence'] = synthesize
        try:
            synthesize_voice_candidate([], 'job', 30.0, language='tr')
        finally:
            globals_dict['synthesize_scene_sequence'] = previous

        self.assertEqual(calls, [(0, 'tr')])

    def test_toy_replica_prompt_keeps_manufactured_identity_guardrail(self):
        prompt = runway_prompt(
            {
                'narration': 'Bu Lego ahtapotu kıyıda bulundu.',
                'ai_prompt': (
                    'Small vintage orange plastic toy octopus on wet sand, '
                    'no humans. ' * 30
                ),
                'visual_queries': ['orange Lego octopus toy Cornwall beach'],
            },
            {'retry_queries': ['weathered orange toy on wet sand']},
        )

        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)
        self.assertIn('MANUFACTURED IDENTITY', prompt)
        self.assertIn('Require 2+ clear manufactured cues', prompt)
        self.assertIn('Never a live or dead biological original', prompt)
        self.assertIn('no person, human or hand', prompt)

    def test_real_animal_scene_does_not_receive_replica_guardrail(self):
        prompt = runway_prompt(
            {
                'narration': 'Canlı ahtapot kayalıkların arasında yüzüyor.',
                'ai_prompt': 'Wild octopus swimming through a natural reef.',
                'visual_queries': ['wild octopus underwater reef'],
            },
            None,
        )

        self.assertNotIn('MANUFACTURED IDENTITY', prompt)

    def test_scene_duration_buys_enough_single_pass_footage(self):
        self.assertEqual(runway_generation_seconds(9.108), 10)
        self.assertEqual(runway_generation_seconds(8.986), 10)
        self.assertEqual(runway_generation_seconds(6.565), 7)
        self.assertEqual(runway_generation_seconds(2.0), 5)
        self.assertTrue(runway_single_pass_supported(9.108))
        self.assertFalse(runway_single_pass_supported(9.61))

    def test_preview_and_production_mode_route_render_safely(self):
        self.assertEqual(render_target_duration({'mode': 'preview'}, 30), 30.0)
        self.assertIsNone(
            render_target_duration({'mode': 'production'}, 600)
        )
        self.assertTrue(generated_visual_spec('preview.mp4')['forbid_loop'])
        self.assertTrue(generated_visual_spec('production.mp4')['forbid_loop'])
        self.assertEqual(
            generated_visual_spec(
                'gemini.mp4',
                provider='gemini_veo',
                provider_attempts=2,
            )['generation_provider'],
            'gemini_veo',
        )
        self.assertEqual(
            generated_visual_spec(
                'gemini.mp4',
                provider='gemini_veo',
                provider_attempts=2,
            )['generation_provider_attempts'],
            2,
        )

    def test_short_generation_prompt_is_vertical_and_long_form_stays_wide(self):
        scene = {
            'narration': 'Telefonu açık komodine bırak.',
            'ai_prompt': 'A black phone cooling on an open nightstand.',
            'visual_queries': ['phone on open nightstand'],
        }

        short_prompt = runway_prompt(scene, None, '9:16')
        long_prompt = runway_prompt(scene, None)

        self.assertIn('9:16 vertical documentary shot', short_prompt)
        self.assertIn('central safe area', short_prompt)
        self.assertIn(
            'No UI, @handle, caption, text, logo, watermark, border, '
            'letterbox or collage.',
            short_prompt,
        )
        self.assertNotIn('YouTube Shorts', short_prompt)
        self.assertNotIn('16:9 documentary shot', short_prompt)
        self.assertIn('16:9 documentary shot', long_prompt)
        self.assertNotIn('YouTube Shorts', long_prompt)

    def test_provider_prompt_removes_social_presentation_bait(self):
        scene = {
            'narration': (
                'For YouTube Shorts, the black toy dragon rises from the sand '
                'beside @seeldfft_bwers.'
            ),
            'ai_prompt': (
                'TikTok style close-up of the same black toy dragon with a '
                'subscribe button.'
            ),
            'visual_queries': ['unused fallback'],
        }
        prompt = runway_prompt(
            scene,
            {
                'retry_queries': [
                    'Instagram Reel style with @seeldfft_bwers share icon; black toy '
                    'dragon standing on wet sand',
                ],
            },
            '9:16',
        )

        lowered = prompt.casefold()
        self.assertIn('black toy dragon', lowered)
        self.assertIn('wet sand', lowered)
        self.assertIn('central safe area', lowered)
        for bait in (
            'youtube shorts',
            'tiktok',
            'instagram reel',
            '@seeldfft_bwers',
            'subscribe button',
            'share icon',
        ):
            with self.subTest(bait=bait):
                self.assertNotIn(bait, lowered)

        factual_prompt = runway_prompt(
            {
                'narration': (
                    'TikTok changed music discovery while YouTube Shorts '
                    'policy changed creator strategy.'
                ),
                'ai_prompt': (
                    'Exterior of TikTok headquarters beside a generic creator '
                    'workspace discussing YouTube Shorts policy.'
                ),
                'visual_queries': ['TikTok headquarters documentary exterior'],
            },
            None,
        )
        self.assertIn('TikTok headquarters', factual_prompt)
        self.assertIn('YouTube Shorts policy', factual_prompt)

        wide_prompt = runway_prompt(
            {
                'narration': 'A musician records a new song.',
                'ai_prompt': 'TikTok-style close-up of a musician recording.',
                'visual_queries': ['musician recording in a studio'],
            },
            None,
            '16:9',
        )
        self.assertIn('16:9 documentary shot', wide_prompt)
        self.assertNotIn('TikTok', wide_prompt)
        self.assertNotIn('vertical short-form', wide_prompt)

    def test_identity_gate_failures_protect_comparable_subject_close_view(self):
        scene = {
            'narration': (
                'The same tiny notched-tail toy dragon is tossed inside '
                'the storm-struck shipping container.'
            ),
            'ai_prompt': (
                'Inside the storm-tossed container, show the exact same '
                'tiny matte-black molded plastic toy dragon at original '
                'LEGO scale with a distinctive notch in its tail, no '
                'humans or hands. ' * 20
            ),
            'visual_queries': [
                'tiny matte-black notched-tail toy dragon in storm container'
            ],
        }
        prompt = runway_prompt(
            scene,
            {
                'retry_queries': [
                    'close unobstructed view of the same tiny matte-black '
                    'molded plastic toy dragon at original LEGO scale with '
                    'its tail notch visible inside the storm container ' * 8,
                ],
                'subject_visible': False,
                'recurring_identity_continuity_applicable': True,
                'recurring_identity_continuity_matches': False,
                'authored_identity_or_material_conflict_visible': True,
            },
            '9:16',
        )

        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)
        self.assertIn(
            'Raw-camera 9:16 documentary shot. No UI, @handle, caption, text, '
            'logo, watermark, border, letterbox or collage.',
            prompt,
        )
        identity_clause = (
            'IDENTITY PROOF: exact subject large in frame, never physically '
            'enlarged; authored scale; visible comparable close-up; no '
            'substitute.'
        )
        manufactured_clause = manufactured_replica_guardrail(scene)
        closing = 'Keep one clean, stable, unbranded documentary frame.'
        self.assertIn(identity_clause, prompt)
        self.assertIn(manufactured_clause, prompt)
        self.assertIn('Core shot direction: close unobstructed view', prompt)
        self.assertTrue(prompt.endswith(closing), repr(prompt[-180:]))
        self.assertLess(
            prompt.index(identity_clause),
            prompt.index('Core shot direction:'),
        )
        self.assertLess(
            prompt.index('Core shot direction:'),
            prompt.index(manufactured_clause),
        )
        self.assertLess(prompt.index(manufactured_clause), prompt.index(closing))

    def test_identity_repair_keeps_nonempty_core_for_non_replica_scene(self):
        prompt = runway_prompt(
            {
                'narration': 'Water visibly rises inside a clear glass.',
                'ai_prompt': 'Close documentary view of water filling a glass.',
                'visual_queries': ['water filling a clear glass'],
            },
            {
                'retry_queries': [
                    'clear glass stays visible while the water level rises'
                ],
                'subject_visible': False,
            },
            '9:16',
        )

        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)
        self.assertIn(
            'Core shot direction: clear glass stays visible while the water '
            'level rises',
            prompt,
        )
        self.assertNotIn('MANUFACTURED IDENTITY:', prompt)
        self.assertTrue(
            prompt.endswith(
                'Keep one clean, stable, unbranded documentary frame.'
            ),
            repr(prompt[-180:]),
        )

    def test_clean_identity_review_does_not_change_general_prompt(self):
        prompt = runway_prompt(
            {
                'narration': 'A glass fills with clear water.',
                'ai_prompt': 'Close documentary view of water filling a glass.',
                'visual_queries': ['water filling a clear glass'],
            },
            {
                'retry_queries': ['water visibly rises inside the same glass'],
                'subject_visible': True,
                'recurring_identity_continuity_applicable': True,
                'recurring_identity_continuity_matches': True,
                'authored_identity_or_material_conflict_visible': False,
            },
            '9:16',
        )

        self.assertNotIn('IDENTITY PROOF:', prompt)

    def test_each_identity_hard_gate_independently_requires_proof(self):
        scene = {
            'narration': 'The same black toy dragon crosses wet sand.',
            'ai_prompt': 'The same matte-black notched-tail toy dragon.',
            'visual_queries': ['matte-black notched-tail toy dragon'],
        }
        failing_reviews = (
            {'subject_visible': False},
            {'authored_identity_or_material_conflict_visible': True},
            {
                'recurring_identity_continuity_applicable': True,
                'recurring_identity_continuity_matches': False,
            },
        )

        for review in failing_reviews:
            with self.subTest(review=review):
                prompt = runway_prompt(scene, review, '9:16')
                self.assertIn('IDENTITY PROOF:', prompt)

        non_applicable = runway_prompt(
            scene,
            {
                'subject_visible': True,
                'recurring_identity_continuity_applicable': False,
                'recurring_identity_continuity_matches': False,
                'authored_identity_or_material_conflict_visible': False,
            },
            '9:16',
        )
        self.assertNotIn('IDENTITY PROOF:', non_applicable)

    def test_non_dict_review_does_not_mutate_general_prompt(self):
        prompt = runway_prompt(
            {
                'narration': 'Water visibly rises inside a clear glass.',
                'ai_prompt': 'Close documentary view of water filling a glass.',
                'visual_queries': ['water filling a clear glass'],
            },
            ['invalid-review-shape'],
            '9:16',
        )

        self.assertNotIn('IDENTITY PROOF:', prompt)
        self.assertIn('water filling a clear glass', prompt)

    def test_portrait_toy_prompt_keeps_complete_artifact_ban(self):
        prompt = runway_prompt(
            {
                'narration': 'A black toy dragon is recovered from wet sand.',
                'ai_prompt': (
                    'The same manufactured black toy dragon on wet Cornwall '
                    'sand, no humans or hands. ' * 20
                ),
                'visual_queries': ['black toy dragon wet sand no humans'],
            },
            {
                'retry_queries': [
                    'weathered black toy dragon with molded seams and part '
                    'edges on wet Cornwall sand, no humans ' * 8,
                ],
            },
            '9:16',
        )

        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)
        self.assertIn(
            'No UI, @handle, caption, text, logo, watermark, border, '
            'letterbox or collage.',
            prompt,
        )

    def test_real_first_short_preview_caps_initial_runway_spend_at_one_scene(self):
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'preview', 'visual_mix': 'real_first'},
                5,
                0.5,
            ),
            1,
        )
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'preview', 'visual_mix': 'balanced'},
                5,
                0.5,
            ),
            4,
        )
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                5,
                0.5,
            ),
            5,
        )
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'preview', 'visual_mix': 'real_first'},
                5,
                0.61,
            ),
            0,
        )
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'production', 'visual_mix': 'real_first'},
                20,
                10.0,
            ),
            2,
        )

    def test_all_single_pass_candidates_are_preflighted_before_paid_calls(self):
        paid_provider = Mock()
        with self.assertRaises(FinalVisualQualityError):
            validate_runway_single_pass_candidates(
                [0, 1, 2],
                [6.0, 7.0, 11.0],
            )
            paid_provider()
        paid_provider.assert_not_called()

        with self.assertRaises(PreRunwayRetryableError):
            preflight_runway_candidates_before_paid(
                [0],
                [11.0],
                None,
            )
        with self.assertRaises(FinalVisualQualityError):
            preflight_runway_candidates_before_paid(
                [0],
                [11.0],
                {'scenes': [{}]},
            )

        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        initial_preflight = source.index(
            '_preflight_runway_candidates_before_paid(\n'
            '            [int(item'
        )
        initial_loop = source.index('for candidate in selected_runway:')
        repair_preflight = source.index(
            '_preflight_runway_candidates_before_paid(\n'
            '            [int(index)'
        )
        repair_loop = source.index(
            'for scene_idx in final_runway_repair_candidates:'
        )
        self.assertLess(initial_preflight, initial_loop)
        self.assertLess(repair_preflight, repair_loop)

    def test_audio_transcript_gate_runs_before_any_paid_runway_submission(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')

        audio_gate = source.index(
            'audio_qc = _verify_audio_narration_with_retry('
        )
        prosody_gate = source.index('audio_prosody_qc = verify_audio_prosody(')
        audio_rejection = source.index(
            'raise FinalAudioQualityError(',
            audio_gate,
        )
        initial_runway_loop = source.index(
            'for candidate in selected_runway:'
        )

        self.assertLess(audio_gate, audio_rejection)
        self.assertLess(prosody_gate, audio_rejection)
        self.assertLess(audio_rejection, initial_runway_loop)
        self.assertIn('MAX_AUDIO_GENERATION_ATTEMPTS = 3', source)
        self.assertIn('AUDIO_QC_PROVIDER_ATTEMPTS = 2', source)
        self.assertIn('except AudioQCError:', source)
        self.assertIn('bounded same-audio retry before paid media', source)
        self.assertIn('generation_attempt=generation_attempt', source)
        self.assertIn("'audio_generation_attempts': audio_generation_attempts", source)
        self.assertIn("'audio_qc': audio_qc", source)
        self.assertIn("'audio_prosody_qc': audio_prosody_qc", source)
        self.assertIn("'audio_qc_retry_history': audio_qc_retry_history", source)
        self.assertIn(
            "str(language or '').lower().startswith('tr')",
            source,
        )
        self.assertIn(
            "'reason': 'not_applicable_non_turkish_or_long_form'",
            source,
        )
        self.assertIn(
            "audio_duration_qc.get('retryable') is True",
            source,
        )
        self.assertIn(
            "and audio_qc.get('available') is True",
            source,
        )
        self.assertIn(
            "and audio_duration_qc.get('available') is True",
            source,
        )
        self.assertIn(
            "'selected_audio_generation_attempt': (",
            source,
        )
        self.assertIn("audio_mismatch.get('operations')", source)
        self.assertNotIn(
            "(audio_qc.get('mismatch_details') or [])[:6]",
            source,
        )

    def test_audio_provider_outage_retries_the_same_audio_once(self):
        expected_result = {
            'available': True,
            'pass': True,
            'provider': 'gemini',
        }
        verifier = Mock(side_effect=[
            AudioQCError('temporary provider outage'),
            expected_result,
        ])
        sleep = Mock()
        function_globals = verify_audio_narration_with_retry.__globals__
        previous_verifier = function_globals['verify_audio_narration']
        previous_time = function_globals['time']
        function_globals['verify_audio_narration'] = verifier
        function_globals['time'] = SimpleNamespace(sleep=sleep)
        try:
            result = verify_audio_narration_with_retry(
                'immutable-voice.mp3',
                'Beklenen anlatım',
                language='tr',
            )
        finally:
            function_globals['verify_audio_narration'] = previous_verifier
            function_globals['time'] = previous_time

        self.assertEqual(result, expected_result)
        self.assertEqual(verifier.call_count, 2)
        self.assertEqual(
            [item.args for item in verifier.call_args_list],
            [
                ('immutable-voice.mp3', 'Beklenen anlatım'),
                ('immutable-voice.mp3', 'Beklenen anlatım'),
            ],
        )
        self.assertEqual(
            [item.kwargs for item in verifier.call_args_list],
            [{'language': 'tr'}, {'language': 'tr'}],
        )
        sleep.assert_called_once_with(1.0)

    def test_exhausted_audio_provider_outage_is_terminal_and_secret_safe(self):
        secret = 'never-expose-provider-detail'
        verifier = Mock(side_effect=[
            AudioQCError(secret),
            AudioQCError(secret),
        ])
        sleep = Mock()
        function_globals = verify_audio_narration_with_retry.__globals__
        previous_verifier = function_globals['verify_audio_narration']
        previous_time = function_globals['time']
        function_globals['verify_audio_narration'] = verifier
        function_globals['time'] = SimpleNamespace(sleep=sleep)
        try:
            with self.assertRaises(FinalAudioQualityError) as caught:
                verify_audio_narration_with_retry(
                    'immutable-voice.mp3',
                    'Beklenen anlatım',
                    language='tr',
                )
        finally:
            function_globals['verify_audio_narration'] = previous_verifier
            function_globals['time'] = previous_time

        self.assertEqual(verifier.call_count, 2)
        sleep.assert_called_once_with(1.0)
        self.assertIn('bounded same-audio retry', str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))

    def test_initial_and_repair_generation_never_use_fixed_five_seconds(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        self.assertNotIn('generate_scene(prompt_candidates[scene_idx], duration=5)', source)
        self.assertNotIn('generate_scene(repair_prompt, duration=5)', source)
        self.assertEqual(source.count("'forbid_loop': True"), 1)
        self.assertNotIn('five-second photorealistic', source)

    def test_runway_failure_diagnostic_never_serializes_exception_message(self):
        secret = (
            'sk-live-secret-value '
            'https://media.example/video.mp4?X-Amz-Signature=secret '
            'PROMPT: private authored scene'
        )
        diagnostic = runway_failure_diagnostic(
            'initial_generation',
            2,
            RuntimeError(secret),
        )

        self.assertEqual(
            diagnostic,
            {
                'stage': 'initial_generation',
                'scene_index': 2,
                'exception_class': 'RuntimeError',
            },
        )
        serialized = json.dumps(diagnostic)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('sk-live-secret-value', serialized)
        self.assertNotIn('X-Amz-Signature', serialized)
        self.assertNotIn('private authored scene', serialized)

    def test_runway_failure_diagnostic_allows_only_safe_reason_codes(self):
        safe_error = RuntimeError('secret provider response')
        safe_error.reason_code = 'invalid_model'
        unsafe_error = RuntimeError('secret provider response')
        unsafe_error.reason_code = 'sk_live_secret_value'

        safe = runway_failure_diagnostic('initial_generation', 1, safe_error)
        unsafe = runway_failure_diagnostic('initial_generation', 2, unsafe_error)

        self.assertEqual(safe['reason_code'], 'invalid_model')
        self.assertNotIn('reason_code', unsafe)
        self.assertNotIn('secret', json.dumps([safe, unsafe]))

    def test_public_runway_failure_payload_filters_fields_and_scenes(self):
        secret = 'key=super-secret&url=https://private.example/generated.mp4'
        payload = runway_failure_payload(
            3,
            [1],
            [
                {
                    **runway_failure_diagnostic(
                        'final_repair',
                        1,
                        TimeoutError(secret),
                    ),
                    'reason_code': 'invalid_model',
                    'message': secret,
                    'url': 'https://private.example/generated.mp4',
                    'prompt': 'private authored scene',
                },
                runway_failure_diagnostic(
                    'initial_generation',
                    2,
                    RuntimeError(secret),
                ),
                {
                    'stage': 'final_repair',
                    'scene_index': 1,
                    'exception_class': secret,
                    'reason_code': secret,
                },
            ],
        )

        self.assertEqual(payload['attempts'], 3)
        self.assertEqual(payload['failed_scenes'], [1])
        self.assertEqual(
            payload['failures'],
            [
                {
                    'stage': 'final_repair',
                    'scene_index': 1,
                    'exception_class': 'TimeoutError',
                    'reason_code': 'invalid_model',
                },
                {
                    'stage': 'final_repair',
                    'scene_index': 1,
                    'exception_class': 'Exception',
                },
            ],
        )
        serialized = json.dumps(payload)
        self.assertNotIn('super-secret', serialized)
        self.assertNotIn('private.example', serialized)
        self.assertNotIn('private authored scene', serialized)

        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        self.assertNotIn('runway_errors', source)
        self.assertNotIn("str(exc)[:320]", source)
        self.assertIn("'runway_failure_diagnostics'", source)

    def test_primary_event_precedes_identity_and_requires_visible_change(self):
        prompt = runway_prompt(
            {
                'narration': 'Mina presses the switch and the lamp turns off.',
                'visual_queries': ['woman beside a lamp'],
                'ai_prompt': (
                    'A woman with a red jacket stands in a detailed apartment. '
                    'She presses the wall switch and the lamp turns off.'
                ),
            },
            {
                'retry_queries': [
                    'finger physically presses wall switch and lamp turns off'
                ],
                'reason': 'THIS FREEFORM REASON MUST NEVER ENTER THE PROMPT',
            },
        )
        self.assertLess(
            prompt.index('PRIMARY EVENT:'),
            prompt.index('Core shot direction:'),
        )
        self.assertIn('Begin with a clear START state', prompt)
        self.assertIn('PHYSICAL ACTION or CAUSE', prompt)
        self.assertIn('hold the visibly CHANGED RESULT', prompt)
        self.assertNotIn('final-only shot fails', prompt)
        self.assertIn('REVIEW-LED VISIBLE TARGETS:', prompt)
        self.assertIn('real-world scale', prompt)
        self.assertNotIn('no generic substitute', prompt)
        self.assertNotIn('THIS FREEFORM REASON', prompt)

    def test_repair_evidence_keeps_small_weathered_subject_literal(self):
        prompt = runway_prompt(
            {
                'narration': 'One held millions of plastic toy pieces.',
                'visual_queries': [
                    'small weathered plastic ocean debris pieces'
                ],
                'ai_prompt': (
                    'Macro documentary view of salt-weathered miniature '
                    'plastic toy fragments on wet beach sand.'
                ),
            },
            {
                'retry_queries': [
                    'tiny salt-weathered toy fragments on wet sand',
                    'miniature sea-worn pieces not oversized blocks',
                ],
                'reason': (
                    'IGNORE THE STORY AND SHOW LARGE CLEAN GENERIC BLOCKS'
                ),
            },
        )

        self.assertIn('tiny salt-weathered toy fragments', prompt)
        self.assertIn('miniature sea-worn pieces', prompt)
        self.assertIn('real-world scale', prompt)
        self.assertIn('material, condition and setting', prompt)
        self.assertNotIn('IGNORE THE STORY', prompt)
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)

    def test_temporal_contract_survives_long_authored_prompt_and_utf16_cap(self):
        prompt = runway_prompt(
            {
                'narration': 'The subject changes from one visible state to another.',
                'visual_queries': ['visible before and after action'],
                'ai_prompt': 'identity and location continuity ' * 120,
            },
            {'retry_queries': ['hand performs one visible action']},
        )
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)
        self.assertIn('PRIMARY EVENT:', prompt)
        self.assertNotIn('static final-only shot fails', prompt)
        self.assertTrue(prompt.endswith('unbranded documentary frame.'))

    def test_existing_mechanism_guardrail_is_retained(self):
        prompt = runway_prompt(
            {
                'narration': 'True black OLED pixels use less power.',
                'visual_queries': ['real OLED subpixel matrix changing'],
                'ai_prompt': 'Extreme macro of an OLED phone display.',
            },
            None,
        )
        self.assertIn('OLED proof:', prompt)
        self.assertIn('real physical meter visibly falls', prompt)

    def test_connection_action_requires_visible_connector_socket_and_release(self):
        prompt = runway_prompt(
            {
                'narration': (
                    'Sürücü kemeri yeniden yavaşça çekip tokaya takıyor.'
                ),
                'visual_queries': [
                    'metal seat belt tongue visibly enters buckle slot'
                ],
                'ai_prompt': (
                    'Macro shot inside a car showing one seat belt fastening.'
                ),
            },
            None,
        )

        self.assertIn('Connection proof:', prompt)
        self.assertIn('distinct moving connector', prompt)
        self.assertIn('receiving socket apart', prompt)
        self.assertIn('release the hand', prompt)
        self.assertIn('fully seated connection', prompt)

    def test_mechanism_contract_stays_whole_when_review_targets_exist(self):
        prompt = runway_prompt(
            {
                'narration': 'The driver inserts the seat belt into its buckle.',
                'visual_queries': [
                    'seat belt tongue entering buckle slot'
                ],
                'ai_prompt': (
                    'One continuous close-up of a seat belt fastening.'
                ),
            },
            {
                'retry_queries': [
                    'metal tongue visibly enters buckle and stays connected'
                ],
            },
        )

        self.assertIn(
            'metal tongue visibly enters buckle and stays connected',
            prompt,
        )
        self.assertIn('Connection proof:', prompt)
        self.assertIn(
            'hold the fully seated connection in clear view.',
            prompt,
        )
        self.assertNotIn('REVIEW-LED', prompt)
        self.assertTrue(prompt.endswith('unbranded documentary frame.'))
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)

    def test_long_event_and_mechanism_keep_authored_contract_and_closing(self):
        authored = (
            ('same person same wardrobe same location ' * 7)
            + 'AUTHORED_CONTINUITY_SENTINEL '
            + ('specific physical composition ' * 40)
        )
        prompt = runway_prompt(
            {
                'narration': 'OLED true black uses less power ' * 20,
                'visual_queries': ['real OLED subpixels change visibly ' * 8],
                'ai_prompt': authored,
            },
            {'retry_queries': ['physical meter falls after OLED pixels turn off ' * 8]},
        )
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 900)
        self.assertIn('AUTHORED_CONTINUITY_SENTINEL', prompt)
        self.assertIn('OLED proof:', prompt)
        self.assertTrue(prompt.endswith('unbranded documentary frame.'))

    def test_generated_clip_keeps_zero_start_after_qc_ranking(self):
        visuals = [[{
            'path': 'runway_repair.mp4',
            'start_fraction': 0.0,
            'preserve_start_fraction': True,
            'forbid_loop': True,
        }]]
        apply_visual_review(
            visuals,
            0,
            {'best_candidate_index': 0, 'best_start_fraction': 0.82},
        )
        self.assertEqual(visuals[0][0]['start_fraction'], 0.0)
        self.assertTrue(visuals[0][0]['forbid_loop'])

    def test_stock_clip_still_uses_qc_selected_start(self):
        visuals = [[{'path': 'stock.mp4', 'start_fraction': 0.25}]]
        apply_visual_review(
            visuals,
            0,
            {'best_candidate_index': 0, 'best_start_fraction': 0.82},
        )
        self.assertEqual(visuals[0][0]['start_fraction'], 0.82)


if __name__ == '__main__':
    unittest.main()

