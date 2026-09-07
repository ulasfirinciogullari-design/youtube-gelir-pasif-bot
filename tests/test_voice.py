import base64
import ast
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace()
_previous_config_module = sys.modules.get('app.config')
sys.modules['app.config'] = config_stub

redis_stub = types.ModuleType('redis')
redis_stub.Redis = SimpleNamespace(from_url=lambda *_args, **_kwargs: None)
_previous_redis_module = sys.modules.get('redis')
sys.modules['redis'] = redis_stub

from app.services.voice import normalize_turkish_tts
from app.services.voice import _voice_speed
from app.services.voice import _use_turkish_short_preview_profile
from app.services.voice import _fit_duration
from app.services.voice import fit_existing_narration_candidate
from app.services.voice import _join_scene_narration
from app.services.voice import _scene_durations_from_alignment
from app.services.voice import _short_preview_audio_edit_plan
from app.services.voice import _deterministic_scene_seed
from app.services.voice import synthesize_scene_sequence
from app.services.voice import synthesize_voice_with_id
from app.services.voice import synthesize_voice_with_timestamps
import app.services.voice as voice_module

if _previous_config_module is None:
    sys.modules.pop('app.config', None)
else:
    sys.modules['app.config'] = _previous_config_module
if _previous_redis_module is None:
    sys.modules.pop('redis', None)
else:
    sys.modules['redis'] = _previous_redis_module
# Keep the local references above for these tests, but do not leak a module
# bound to the temporary config/Redis stubs into later test imports.
sys.modules.pop('app.services.voice', None)
services_package = sys.modules.get('app.services')
if services_package is not None and getattr(
    services_package,
    'voice',
    None,
) is voice_module:
    delattr(services_package, 'voice')


class _FakeVoiceResponse:
    content = b'voice-bytes'

    def raise_for_status(self):
        return None


class _FakeTimestampResponse(_FakeVoiceResponse):
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


_REAL_LONGFORM_LEGACY_CUES = [
    7.409062075492956,
    7.600263535774647,
    7.160244101408449,
    8.965989895211267,
    10.765759891830985,
    8.965989895211267,
    8.201183008450704,
    8.85374424338028,
    8.03729664,
    9.403021953802815,
    9.403021953802815,
    8.228498100281689,
    7.508503941408449,
    8.474328698591547,
    9.348392815774647,
    9.611722095774645,
    6.398425636056338,
    8.228498100281689,
    8.71717192112676,
    8.884046710985915,
    8.740645354366196
]


class TurkishVoiceNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(voice_module, 'settings', config_stub.settings))

    def test_numeric_separators_are_not_converted_to_speech_boundaries(self):
        for text in ('Maliyet 3,69 sent.', 'Maliyet 3.69 sent.',
                     'Bir milyon: 1.000.000 TL.', 'Tarih 07.01.2026.'):
            with self.subTest(text=text):
                self.assertEqual(normalize_turkish_tts(text), text)

    def test_ordinary_punctuation_still_gets_spacing_without_joining_numbers(self):
        self.assertEqual(normalize_turkish_tts('Evet,doğru.Yıl:2026!Tamam?Evet;son.'),
                         'Evet, doğru. Yıl: 2026! Tamam? Evet; son.')
        self.assertEqual(normalize_turkish_tts('Maliyet 3, 69 sent.'), 'Maliyet 3, 69 sent.')
        self.assertEqual(normalize_turkish_tts('Bölüm 3.Sonraki 69.'), 'Bölüm 3. Sonraki 69.')

    def test_explicit_legacy_mode_reproduces_only_the_prior_normalizer(self):
        self.assertEqual(normalize_turkish_tts('Maliyet 3,69 sent.', legacy_numeric_spacing=True),
                         'Maliyet 3, 69 sent.')
        self.assertEqual(normalize_turkish_tts('Tutar 1.000.000 TL', ensure_terminal=False,
                                              legacy_numeric_spacing=True), 'Tutar 1. 000. 000 TL')
        self.assertEqual(normalize_turkish_tts('Maliyet 3,69 sent.'), 'Maliyet 3,69 sent.')

    def test_temporary_voice_import_does_not_leak_to_later_tests(self):
        self.assertIsNot(sys.modules.get('app.services.voice'), voice_module)

    def test_short_preview_always_fits_content_before_reserved_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b'fitted')

            with (
                patch.object(
                    voice_module,
                    '_media_duration',
                    side_effect=[30.048, 29.52],
                ),
                patch.object(
                    voice_module.subprocess,
                    'run',
                    side_effect=fake_ffmpeg,
                ) as run,
            ):
                durations, before, after, rate = _fit_duration(
                    output,
                    [9.0, 6.0, 9.0, 6.0],
                    30.0,
                )

        self.assertEqual(before, 30.048)
        self.assertEqual(after, 29.52)
        self.assertAlmostEqual(rate, 30.048 / 29.5, places=6)
        self.assertAlmostEqual(
            sum(durations),
            30.0 * 29.52 / 30.048,
            places=6,
        )
        self.assertIn(
            f'atempo={rate:.6f}',
            run.call_args.args[0],
        )

    def test_short_preview_uses_natural_default_voice_speed(self):
        self.assertEqual(_voice_speed(30), 1.0)
        self.assertEqual(_voice_speed(40), 1.0)
        self.assertEqual(_voice_speed(60), 1.01)
        self.assertEqual(_voice_speed(None), 1.01)

    def test_turkish_flash_profile_route_is_narrow(self):
        self.assertTrue(_use_turkish_short_preview_profile('tr', 30.0))
        self.assertTrue(_use_turkish_short_preview_profile(' TR ', 40.0))
        self.assertTrue(_use_turkish_short_preview_profile('tr-TR', 30.0))
        self.assertTrue(_use_turkish_short_preview_profile('tr_TR', 30.0))
        for language, duration in (
            ('en', 30.0),
            ('tr', 60.0),
            ('trick', 30.0),
            (None, 30.0),
            ('tr', None),
        ):
            with self.subTest(language=language, duration=duration):
                self.assertFalse(
                    _use_turkish_short_preview_profile(language, duration)
                )

    def test_near_unity_short_preview_fit_uses_only_minimal_tempo_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b'fitted')

            with (
                patch.object(
                    voice_module,
                    '_media_duration',
                    side_effect=[29.56, 29.50],
                ),
                patch.object(
                    voice_module.subprocess,
                    'run',
                    side_effect=fake_ffmpeg,
                ) as run,
            ):
                durations, before, after, rate = _fit_duration(
                    output,
                    [10.0, 9.0, 10.56],
                    30.0,
                )

        self.assertEqual(before, 29.56)
        self.assertEqual(after, 29.50)
        self.assertAlmostEqual(rate, 29.56 / 29.5, places=6)
        self.assertLess(abs(rate - 1.0), 0.01)
        self.assertAlmostEqual(sum(durations), 29.50, places=6)
        self.assertIn(f'atempo={rate:.6f}', run.call_args.args[0])

    def test_short_preview_does_not_stretch_beyond_seven_percent_allowance(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')
            with (
                patch.object(
                    voice_module,
                    '_media_duration',
                    return_value=26.7,
                ),
                patch.object(voice_module.subprocess, 'run') as run,
            ):
                durations, before, after, rate = _fit_duration(
                    output,
                    [26.7],
                    30.0,
                )
                unchanged = output.read_bytes()

        run.assert_not_called()
        self.assertEqual(durations, [26.7])
        self.assertEqual((before, after, rate), (26.7, 26.7, 1.0))
        self.assertEqual(unchanged, b'raw')

    def test_recoverable_short_deficit_uses_minimal_bounded_slowdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b'fitted')

            with (
                patch.object(voice_module, '_media_duration', side_effect=[27.24, 28.75]),
                patch.object(voice_module.subprocess, 'run', side_effect=fake_ffmpeg) as run,
            ):
                durations, before, after, rate = _fit_duration(output, [10.0, 17.24], 30.0)
        self.assertEqual((before, after, rate), (27.24, 28.75, 0.947478))
        self.assertAlmostEqual(sum(durations), 28.75, places=8)
        self.assertEqual(rate, float(run.call_args.args[0][5].split('=')[1]))

    def test_already_valid_short_narration_is_not_slowed(self):
        for seconds in (28.7, 28.72, 28.75, 29.5):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'voice.mp3'
                output.write_bytes(b'raw')
                with (
                    patch.object(voice_module, '_media_duration', return_value=seconds),
                    patch.object(voice_module.subprocess, 'run') as run,
                ):
                    values = _fit_duration(output, [seconds], 30.0)
                run.assert_not_called()
                self.assertEqual(values, ([seconds], seconds, seconds, 1.0))

    def test_too_thin_audio_remains_unchanged_and_fails_existing_duration_qa(self):
        task_path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
        definition = next(
            node for node in ast.parse(task_path.read_text(encoding='utf-8')).body
            if isinstance(node, ast.FunctionDef) and node.name == '_short_preview_voice_duration_qc'
        )
        namespace = {'math': math}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(task_path), 'exec'), namespace)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')
            with (
                patch.object(voice_module, '_media_duration', return_value=25.0),
                patch.object(voice_module.subprocess, 'run') as run,
            ):
                values = _fit_duration(output, [25.0], 30.0)
        run.assert_not_called()
        self.assertEqual(values, ([25.0], 25.0, 25.0, 1.0))
        qa = namespace['_short_preview_voice_duration_qc']({'duration_after_fit': values[2]}, 30.0)
        self.assertFalse(qa['pass'])
        self.assertEqual(qa['minimum_seconds'], 28.7)
        self.assertEqual(qa['reason'], 'short_form_script_too_thin')

    def test_short_slowdown_boundary_and_long_form_fit_limits_are_preserved(self):
        for target, before, after, expected_rate in (
            (30.0, 27.025, 28.75, 0.94),
            (40.0, 36.8125, 38.75, 0.95),
            (60.0, 55.242, 59.4, 0.93),
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'voice.mp3'
                output.write_bytes(b'raw')

                def fake_ffmpeg(command, **_kwargs):
                    Path(command[-1]).write_bytes(b'fitted')

                with (
                    patch.object(voice_module, '_media_duration', side_effect=[before, after]),
                    patch.object(voice_module.subprocess, 'run', side_effect=fake_ffmpeg),
                ):
                    values = _fit_duration(output, [before], target)
                self.assertEqual(values[3], expected_rate)
                self.assertAlmostEqual(sum(values[0]), after)

    def test_existing_candidate_fit_is_repeat_safe_preserves_metadata_and_never_calls_tts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')
            candidate = {
                'path': str(output), 'scene_durations': [10.0, 17.24],
                'duration_before_fit': 27.24, 'duration_after_fit': 27.24,
                'tempo_rate': 1.0, 'spoken_texts': ['Same immutable', 'narration'],
                'audio_qc': {'pass': True}, 'audio_prosody_qc': {'pass': True},
            }

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b'fitted')

            with (
                patch.object(voice_module, '_media_duration', side_effect=[27.24, 28.776, 28.776]),
                patch.object(voice_module.subprocess, 'run', side_effect=fake_ffmpeg) as run,
                patch.object(voice_module, 'synthesize_voice_with_timestamps') as tts,
            ):
                result = fit_existing_narration_candidate(candidate, 30.0)
                repeated = fit_existing_narration_candidate(result, 30.0)
        tts.assert_not_called()
        run.assert_called_once()
        self.assertEqual(result, repeated)
        self.assertEqual(candidate['scene_durations'], [10.0, 17.24])
        self.assertEqual(candidate['tempo_rate'], 1.0)
        self.assertEqual(result['spoken_texts'], candidate['spoken_texts'])
        self.assertEqual(result['duration_before_fit'], 27.24)
        self.assertEqual(result['duration_after_fit'], 28.776)
        self.assertEqual(result['tempo_rate'], 0.947478)
        self.assertAlmostEqual(sum(result['scene_durations']), 28.776)
        self.assertNotIn('audio_qc', result)
        self.assertNotIn('audio_prosody_qc', result)

    def test_existing_candidate_cannot_compound_slowdown_beyond_seven_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')
            candidate = {
                'path': str(output), 'scene_durations': [27.24],
                'duration_before_fit': 26.6952, 'tempo_rate': 0.98,
            }
            with (
                patch.object(voice_module, '_media_duration', return_value=27.24),
                patch.object(voice_module.subprocess, 'run') as run,
            ):
                with self.assertRaisesRegex(voice_module.VoiceScriptFitError, 'cumulative tempo'):
                    fit_existing_narration_candidate(candidate, 30.0)
            self.assertEqual(output.read_bytes(), b'raw')
        run.assert_not_called()

    def test_long_form_real_21_chunk_overhang_is_corrected_only_in_visual_allocation(self):
        original = list(_REAL_LONGFORM_LEGACY_CUES)
        actual = 178.176
        self.assertEqual(len(original), 21)
        self.assertAlmostEqual(sum(original), 178.90581056901405, places=10)
        self.assertGreater(sum(original) - actual, 0.35)
        result = voice_module._scene_allocations_for_measured_audio(original, actual)
        self.assertEqual(original, _REAL_LONGFORM_LEGACY_CUES)
        self.assertEqual(sum(result), actual)
        self.assertAlmostEqual(math.fsum(result), actual, places=12)
        self.assertTrue(all(value > 0 for value in result))
        for old, new in zip(original[:-1], result[:-1]):
            self.assertAlmostEqual(new / old, actual / math.fsum(original), places=12)
        self.assertEqual(voice_module._scene_allocations_for_measured_audio(result, actual), result)

    def test_long_form_fit_binds_to_measured_final_audio_not_discrepant_chunk_sum(self):
        actual = 178.176
        chunks = [duration * 170.4 / actual for duration in _REAL_LONGFORM_LEGACY_CUES]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'original pre-fit bytes')

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b'existing fitted bytes, no additional edit')

            with (
                patch.object(voice_module, '_media_duration', side_effect=[170.4, actual]) as measure,
                patch.object(voice_module.subprocess, 'run', side_effect=fake_ffmpeg) as run,
                patch.object(voice_module, 'synthesize_voice_with_id') as tts,
                patch.object(voice_module, 'synthesize_voice_with_timestamps') as aligned_tts,
            ):
                durations, before, after, rate = _fit_duration(output, chunks, 180)
                self.assertEqual(output.read_bytes(), b'existing fitted bytes, no additional edit')
        self.assertEqual((before, after, rate), (170.4, actual, 0.956229))
        self.assertEqual(sum(durations), actual)
        self.assertEqual(len(durations), 21)
        self.assertEqual(measure.call_count, 2)
        run.assert_called_once()
        self.assertIn('atempo=0.956229', run.call_args.args[0])
        tts.assert_not_called()
        aligned_tts.assert_not_called()

    def test_long_form_no_fit_normalizes_visual_end_without_touching_audio_or_tempo(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'retained.mp3'
            original_bytes = b'unchanged complete retained audio'
            output.write_bytes(original_bytes)
            with (
                patch.object(voice_module, '_media_duration', return_value=178.176),
                patch.object(voice_module.subprocess, 'run') as run,
            ):
                durations, before, after, rate = _fit_duration(
                    output, list(_REAL_LONGFORM_LEGACY_CUES), 180)
            self.assertEqual(output.read_bytes(), original_bytes)
        self.assertEqual((before, after, rate), (178.176, 178.176, 1.0))
        self.assertEqual(sum(durations), 178.176)
        run.assert_not_called()

    def test_visual_allocation_rejects_invalid_weights_and_measurements(self):
        for weights, duration in (([], 10), ([0], 10), ([-1, 2], 10), ([True], 10),
                ([float('nan')], 10), ([float('inf')], 10), ([1], 0), ([1], True),
                ([1], float('nan')), ([1], float('inf'))):
            with self.subTest(weights=weights, duration=duration):
                with self.assertRaises(voice_module.VoiceQualityError):
                    voice_module._scene_allocations_for_measured_audio(weights, duration)

    def test_old_inconsistent_checkpoint_still_fails_the_unchanged_loader(self):
        from app.services import voice_candidate_recovery

        raw = {'spoken_texts': ['Unchanged narration.'] * 21,
            'scene_durations': list(_REAL_LONGFORM_LEGACY_CUES),
            'duration_before_fit': 170.4, 'duration_after_fit': 178.176,
            'tempo_rate': 0.956229, 'voice_profile': {}}
        with self.assertRaisesRegex(ValueError, 'Candidate timing is inconsistent'):
            voice_candidate_recovery._voice_result(raw, 21)
        self.assertEqual(raw['scene_durations'], _REAL_LONGFORM_LEGACY_CUES)

    def test_short_preview_rejects_excessive_compression(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')
            with (
                patch.object(
                    voice_module,
                    '_media_duration',
                    return_value=33.2,
                ),
                patch.object(voice_module.subprocess, 'run') as run,
            ):
                with self.assertRaisesRegex(RuntimeError, '1.125x'):
                    _fit_duration(output, [33.2], 30.0)
        run.assert_not_called()

    def test_intermediate_scene_does_not_gain_unwritten_terminal_pause(self):
        self.assertEqual(
            normalize_turkish_tts('Bir', ensure_terminal=False),
            'Bir',
        )
        self.assertEqual(normalize_turkish_tts('Son'), 'Son.')

    def test_continuous_narration_join_retains_exact_scene_spans(self):
        narration, spans = _join_scene_narration([
            'Bir.',
            'İki kelime.',
            'Son.',
        ])

        self.assertEqual(narration, 'Bir. İki kelime. Son.')
        self.assertEqual(spans, [(0, 4), (5, 16), (17, 21)])
        self.assertEqual(
            [narration[start:end] for start, end in spans],
            ['Bir.', 'İki kelime.', 'Son.'],
        )

    def test_source_alignment_yields_real_scene_durations(self):
        narration, spans = _join_scene_narration([
            'Bir.',
            'İki kelime.',
            'Son.',
        ])
        alignment = {
            'characters': list(narration),
            'character_end_times_seconds': [
                round((index + 1) * 0.1, 2)
                for index in range(len(narration))
            ],
        }

        durations = _scene_durations_from_alignment(
            narration,
            spans,
            alignment,
            2.30,
        )

        self.assertEqual(len(durations), 3)
        self.assertAlmostEqual(durations[0], 0.40, places=6)
        self.assertAlmostEqual(durations[1], 1.20, places=6)
        self.assertAlmostEqual(durations[2], 0.70, places=6)
        self.assertAlmostEqual(sum(durations), 2.30, places=6)

    def test_source_alignment_mismatch_fails_closed(self):
        narration, spans = _join_scene_narration(['Bir.', 'İki.'])
        alignment = {
            'characters': list('Bir. Iki.'),
            'character_end_times_seconds': [
                round((index + 1) * 0.1, 2)
                for index in range(len(narration))
            ],
        }

        with self.assertRaisesRegex(RuntimeError, 'does not match narration'):
            _scene_durations_from_alignment(
                narration,
                spans,
                alignment,
                1.0,
            )

    def test_short_preview_edit_plan_compacts_only_aligned_dead_air(self):
        narration, spans = _join_scene_narration([
            'Bir.',
            'İki.',
            'Son.',
        ])
        alignment = {
            'characters': list(narration),
            'character_start_times_seconds': [
                0.0, 0.1, 0.2, 0.3, 0.4,
                1.3, 1.4, 1.5, 1.6, 1.7,
                2.6, 2.7, 2.8, 2.9,
            ],
            'character_end_times_seconds': [
                0.1, 0.2, 0.3, 0.4, 0.45,
                1.4, 1.5, 1.6, 1.7, 1.75,
                2.7, 2.8, 2.9, 3.0,
            ],
        }

        plan = _short_preview_audio_edit_plan(
            narration,
            spans,
            alignment,
            3.6,
        )

        self.assertEqual(plan['interior_pause_count'], 2)
        self.assertTrue(plan['tail_trimmed'])
        self.assertEqual(len(plan['cuts']), 3)
        self.assertAlmostEqual(plan['cuts'][0][0], 0.48, places=6)
        self.assertAlmostEqual(plan['cuts'][0][1], 1.12, places=6)
        self.assertAlmostEqual(plan['cuts'][1][0], 1.78, places=6)
        self.assertAlmostEqual(plan['cuts'][1][1], 2.42, places=6)
        self.assertAlmostEqual(plan['cuts'][2][0], 3.10, places=6)
        self.assertAlmostEqual(sum(plan['scene_durations']), 1.82, places=6)
        self.assertAlmostEqual(
            plan['removed_silence_seconds'],
            1.78,
            places=6,
        )

    def test_short_preview_edit_plan_rejects_missing_start_alignment(self):
        narration, spans = _join_scene_narration(['Bir.', 'İki.'])
        alignment = {
            'characters': list(narration),
            'character_end_times_seconds': [
                round((index + 1) * 0.1, 2)
                for index in range(len(narration))
            ],
        }

        with self.assertRaisesRegex(RuntimeError, 'missing start timing'):
            _short_preview_audio_edit_plan(
                narration,
                spans,
                alignment,
                1.0,
            )

    @patch.object(voice_module.httpx, 'post', create=True)
    def test_selected_speed_is_sent_to_elevenlabs(self, post):
        config_stub.settings.elevenlabs_api_key = 'test-key'
        post.return_value = _FakeVoiceResponse()

        audio = synthesize_voice_with_id(
            'Elif telefonu cebine koyar.',
            'test-voice',
            speed=0.84,
        )

        self.assertEqual(audio, b'voice-bytes')
        request = post.call_args
        self.assertEqual(
            request.kwargs['json']['voice_settings']['speed'],
            0.84,
        )

    @patch.object(voice_module.httpx, 'post', create=True)
    def test_elevenlabs_request_profile_remains_fixed(self, post):
        config_stub.settings.elevenlabs_api_key = 'test-key'
        post.return_value = _FakeVoiceResponse()

        synthesize_voice_with_id(
            'Altmış iki konteyner.',
            'test-voice',
            previous_text='Fırtına başladı.',
            next_text='Parçalar kıyıya ulaştı.',
            speed=0.92,
        )

        request = post.call_args
        self.assertEqual(
            request.kwargs['json'],
            {
                'text': 'Altmış iki konteyner.',
                'model_id': 'eleven_multilingual_v2',
                'apply_text_normalization': 'on',
                'voice_settings': {
                    'stability': 0.40,
                    'similarity_boost': 0.80,
                    'style': 0.0,
                    'use_speaker_boost': True,
                    'speed': 0.92,
                },
                'previous_text': 'Fırtına başladı.',
                'next_text': 'Parçalar kıyıya ulaştı.',
            },
        )
        self.assertEqual(
            request.kwargs['params'],
            {'output_format': 'mp3_44100_128'},
        )
        self.assertEqual(request.kwargs['timeout'], 180)

    @patch.object(voice_module.httpx, 'post', create=True)
    def test_turkish_short_preview_uses_verified_flash_profile(self, post):
        config_stub.settings.elevenlabs_api_key = 'test-key'
        alignment = {
            'characters': list('Bir. İki.'),
            'character_start_times_seconds': [0.0] * 9,
            'character_end_times_seconds': [0.1] * 9,
        }
        post.return_value = _FakeTimestampResponse({
            'audio_base64': base64.b64encode(b'continuous-voice').decode(),
            'alignment': alignment,
        })

        synthesize_voice_with_timestamps(
            'Bir. İki.',
            'test-voice',
            speed=1.0,
            seed=123,
            turkish_short_preview=True,
        )

        request_body = post.call_args.kwargs['json']
        self.assertEqual(
            request_body,
            {
                'text': 'Bir. İki.',
                'model_id': 'eleven_flash_v2_5',
                'language_code': 'tr',
                'apply_text_normalization': 'on',
                'voice_settings': {
                    'stability': 0.50,
                    'similarity_boost': 0.75,
                    'speed': 1.0,
                },
                'seed': 123,
            },
        )
        self.assertNotIn('style', request_body['voice_settings'])
        self.assertNotIn('use_speaker_boost', request_body['voice_settings'])

    @patch.object(voice_module.httpx, 'post', create=True)
    def test_timestamp_request_is_single_and_keeps_voice_profile(self, post):
        config_stub.settings.elevenlabs_api_key = 'test-key'
        alignment = {
            'characters': list('Bir. İki.'),
            'character_start_times_seconds': [0.0] * 9,
            'character_end_times_seconds': [0.1] * 9,
        }
        post.return_value = _FakeTimestampResponse({
            'audio_base64': base64.b64encode(b'continuous-voice').decode(),
            'alignment': alignment,
        })

        audio, actual_alignment = synthesize_voice_with_timestamps(
            'Bir. İki.',
            'test-voice',
            speed=0.92,
            seed=123,
        )

        self.assertEqual(audio, b'continuous-voice')
        self.assertIs(actual_alignment, alignment)
        post.assert_called_once()
        request = post.call_args
        self.assertTrue(request.args[0].endswith(
            '/text-to-speech/test-voice/with-timestamps'
        ))
        self.assertEqual(
            request.kwargs['json'],
            {
                'text': 'Bir. İki.',
                'model_id': 'eleven_multilingual_v2',
                'apply_text_normalization': 'on',
                'voice_settings': {
                    'stability': 0.40,
                    'similarity_boost': 0.80,
                    'style': 0.0,
                    'use_speaker_boost': True,
                    'speed': 0.92,
                },
                'seed': 123,
            },
        )
        self.assertEqual(
            request.kwargs['params'],
            {'output_format': 'mp3_44100_128'},
        )
        self.assertEqual(request.kwargs['timeout'], 180)

    def test_short_preview_uses_one_continuous_timestamp_take(self):
        scenes = [
            {'narration': 'Bir.'},
            {'narration': 'İki.'},
        ]
        narration = 'Bir. İki.'
        alignment = {
            'characters': list(narration),
            'character_start_times_seconds': [
                0.0, 3.0, 6.0, 9.0,
                9.3,
                9.5, 16.0, 20.0, 29.3,
            ],
            'character_end_times_seconds': [
                3.0, 6.0, 9.0, 9.3,
                9.4,
                16.0, 20.0, 29.3, 29.35,
            ],
        }
        expected_seed = _deterministic_scene_seed(
            'voice-id',
            narration,
            0,
            3,
        )

        with tempfile.TemporaryDirectory() as tmp:
            real_path = Path

            def mapped_path(value):
                if str(value).replace('\\', '/') == '/tmp':
                    return real_path(tmp)
                return real_path(value)

            def fake_ffmpeg(command, **_kwargs):
                real_path(command[-1]).write_bytes(b'normalized')

            with (
                patch.object(
                    voice_module,
                    '_selected_voice_or_raise',
                    return_value={'voice_id': 'voice-id', 'name': 'Mustafa'},
                ),
                patch.object(
                    voice_module,
                    'synthesize_voice_with_timestamps',
                    return_value=(b'continuous', alignment),
                ) as timestamp_synthesis,
                patch.object(
                    voice_module,
                    'synthesize_voice_with_id',
                ) as segmented_synthesis,
                patch.object(
                    voice_module,
                    '_media_duration',
                    return_value=29.5,
                ),
                patch.object(
                    voice_module,
                    'Path',
                    side_effect=mapped_path,
                ),
                patch.object(
                    voice_module.subprocess,
                    'run',
                    side_effect=fake_ffmpeg,
                ) as run,
            ):
                result = synthesize_scene_sequence(
                    scenes,
                    'continuous-test',
                    30.0,
                    generation_attempt=3,
                )

        timestamp_synthesis.assert_called_once_with(
            narration,
            'voice-id',
            speed=1.0,
            seed=expected_seed,
        )
        segmented_synthesis.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result['spoken_texts'], ['Bir.', 'İki.'])
        self.assertEqual(result['scene_durations'], [9.25, 20.25])
        self.assertEqual(result['tempo_rate'], 1.0)
        self.assertEqual(result['removed_silence_seconds'], 0.0)
        self.assertEqual(result['compacted_boundary_pause_count'], 0)
        self.assertFalse(result['compacted_trailing_silence'])
        self.assertEqual(result['voice_model'], 'eleven_multilingual_v2')
        self.assertIsNone(result['voice_language_code'])

    def test_only_turkish_short_preview_routes_to_flash_profile(self):
        scenes = [{'narration': 'Bir.'}, {'narration': 'İki.'}]
        narration = 'Bir. İki.'
        alignment = {
            'characters': list(narration),
            'character_start_times_seconds': [
                index * 0.1 for index in range(len(narration))
            ],
            'character_end_times_seconds': [
                (index + 1) * 0.1 for index in range(len(narration))
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            real_path = Path

            def mapped_path(value):
                if str(value).replace('\\', '/') == '/tmp':
                    return real_path(tmp)
                return real_path(value)

            def fake_ffmpeg(command, **_kwargs):
                real_path(command[-1]).write_bytes(b'normalized')

            with (
                patch.object(
                    voice_module,
                    '_selected_voice_or_raise',
                    return_value={'voice_id': 'voice-id', 'name': 'Mustafa'},
                ),
                patch.object(
                    voice_module,
                    'synthesize_voice_with_timestamps',
                    return_value=(b'continuous', alignment),
                ) as timestamp_synthesis,
                patch.object(voice_module, '_media_duration', return_value=0.9),
                patch.object(voice_module, 'Path', side_effect=mapped_path),
                patch.object(
                    voice_module.subprocess,
                    'run',
                    side_effect=fake_ffmpeg,
                ),
            ):
                result = synthesize_scene_sequence(
                    scenes,
                    'turkish-short-test',
                    30.0,
                    language=' TR ',
                )

        timestamp_synthesis.assert_called_once()
        self.assertIs(
            timestamp_synthesis.call_args.kwargs['turkish_short_preview'],
            True,
        )
        self.assertEqual(result['voice_model'], 'eleven_flash_v2_5')
        self.assertEqual(result['voice_language_code'], 'tr')

    @patch.object(voice_module.httpx, 'post', create=True)
    def test_seed_is_sent_as_top_level_elevenlabs_request_field(self, post):
        config_stub.settings.elevenlabs_api_key = 'test-key'
        post.return_value = _FakeVoiceResponse()

        synthesize_voice_with_id(
            'Altmış iki konteyner.',
            'test-voice',
            seed=4_294_967_295,
        )

        request_body = post.call_args.kwargs['json']
        self.assertEqual(request_body['seed'], 4_294_967_295)
        self.assertNotIn('seed', request_body['voice_settings'])

    def test_scene_seed_is_stable_bounded_and_changes_per_retry(self):
        initial = _deterministic_scene_seed(
            'voice-id', 'Altmış iki konteyner.', 2, 0
        )
        repeated = _deterministic_scene_seed(
            'voice-id', 'Altmış iki konteyner.', 2, 0
        )
        next_attempt = _deterministic_scene_seed(
            'voice-id', 'Altmış iki konteyner.', 2, 1
        )
        next_scene = _deterministic_scene_seed(
            'voice-id', 'Altmış iki konteyner.', 3, 0
        )

        self.assertEqual(initial, repeated)
        self.assertNotEqual(initial, next_attempt)
        self.assertNotEqual(initial, next_scene)
        self.assertTrue(0 <= initial <= 4_294_967_295)

    def test_long_form_transient_retry_reuses_same_scene_seed(self):
        request = voice_module.httpx.Request(
            'POST',
            'https://voice.example.invalid',
        )
        response = voice_module.httpx.Response(
            429,
            request=request,
            headers={'Retry-After': '0'},
        )
        error = voice_module.httpx.HTTPStatusError(
            'rate limited',
            request=request,
            response=response,
        )
        source_texts = ['Birinci sahne.', 'İkinci sahne.', 'Üçüncü sahne.']
        with tempfile.TemporaryDirectory() as tmp:
            chunk = Path(tmp) / 'scene_001.mp3'
            with (
                patch.object(
                    voice_module,
                    'synthesize_voice_with_id',
                    side_effect=[error, b'voice-bytes'],
                ) as synthesize,
                patch.object(voice_module, '_media_duration', return_value=1.25),
                patch.object(voice_module.time, 'sleep') as sleep,
            ):
                result = voice_module._synthesize_long_form_scene(
                    1,
                    source_texts,
                    chunk,
                    'voice-id',
                    1.01,
                    2,
                )

        self.assertEqual(result, (1, 1.25))
        self.assertEqual(synthesize.call_count, 2)
        self.assertEqual(
            synthesize.call_args_list[0].kwargs['seed'],
            synthesize.call_args_list[1].kwargs['seed'],
        )
        sleep.assert_called_once_with(0.0)

    def test_long_form_scene_retry_is_bounded_and_nontransient_is_immediate(self):
        source_texts = ['Tek sahne.']
        request = voice_module.httpx.Request(
            'POST',
            'https://voice.example.invalid',
        )
        transient_response = voice_module.httpx.Response(
            503,
            request=request,
        )
        transient = voice_module.httpx.HTTPStatusError(
            'unavailable',
            request=request,
            response=transient_response,
        )
        auth_response = voice_module.httpx.Response(401, request=request)
        auth = voice_module.httpx.HTTPStatusError(
            'unauthorized',
            request=request,
            response=auth_response,
        )
        with tempfile.TemporaryDirectory() as tmp:
            chunk = Path(tmp) / 'scene.mp3'
            with (
                patch.object(
                    voice_module,
                    'synthesize_voice_with_id',
                    side_effect=[transient, transient, transient],
                ) as transient_synthesis,
                patch.object(voice_module.time, 'sleep') as transient_sleep,
            ):
                with self.assertRaises(voice_module.httpx.HTTPStatusError):
                    voice_module._synthesize_long_form_scene(
                        0, source_texts, chunk, 'voice-id', 1.01, 0
                    )
            self.assertEqual(transient_synthesis.call_count, 3)
            self.assertEqual(transient_sleep.call_count, 2)

            with (
                patch.object(
                    voice_module,
                    'synthesize_voice_with_id',
                    side_effect=auth,
                ) as auth_synthesis,
                patch.object(voice_module.time, 'sleep') as auth_sleep,
            ):
                with self.assertRaises(voice_module.httpx.HTTPStatusError):
                    voice_module._synthesize_long_form_scene(
                        0, source_texts, chunk, 'voice-id', 1.01, 0
                    )
            self.assertEqual(auth_synthesis.call_count, 1)
            auth_sleep.assert_not_called()

    def test_voice_retry_after_is_bounded(self):
        request = voice_module.httpx.Request(
            'POST',
            'https://voice.example.invalid',
        )
        response = voice_module.httpx.Response(
            429,
            request=request,
            headers={'Retry-After': '30'},
        )
        error = voice_module.httpx.HTTPStatusError(
            'rate limited',
            request=request,
            response=response,
        )
        self.assertEqual(
            voice_module.voice_http_retry_delay_seconds(error, 0),
            8.0,
        )

    def test_qr_code_phrases_do_not_duplicate_code_word(self):
        cases = {
            'QR kod kasada okunur': 'kare kod kasada okunur.',
            'QR kodu kasada okunur': 'kare kodu kasada okunur.',
            'QR kodunu kameraya gösterir': 'kare kodunu kameraya gösterir.',
            'QR kodunda çizik vardır': 'kare kodunda çizik vardır.',
            'QR koduyla ödeme yapar': 'kare koduyla ödeme yapar.',
            'QR kodları rafta görünür': 'kare kodları rafta görünür.',
            'QR kodlarla giriş yapılır': 'kare kodlarla giriş yapılır.',
            'QR kodlarında çizik vardır': 'kare kodlarında çizik vardır.',
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                actual = normalize_turkish_tts(source)
                self.assertEqual(actual, expected)
                self.assertEqual(
                    normalize_turkish_tts(actual),
                    expected,
                )
                self.assertNotIn('kare kod kod', actual)

    def test_supported_standalone_initialisms_remain_idempotent(self):
        cases = {
            'OLED ekran kararır': 'oled ekran kararır.',
            'GPS sinyali güçlenir': 'ci pi es sinyali güçlenir.',
            'QR kasada okunur': 'kare kod kasada okunur.',
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                actual = normalize_turkish_tts(source)
                self.assertEqual(actual, expected)
                self.assertEqual(
                    normalize_turkish_tts(actual),
                    expected,
                )


if __name__ == '__main__':
    unittest.main()
