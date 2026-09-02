import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.render as render_module


class RenderQualityTests(unittest.TestCase):
    def test_preview_mode_is_portrait_and_production_remains_widescreen(self):
        self.assertEqual(
            render_module.resolution_for_mode('preview'),
            '1080x1920',
        )
        self.assertEqual(
            render_module.resolution_for_mode('production'),
            '1920x1080',
        )

    def test_portrait_normalizer_uses_short_canvas_and_safe_center_reframe(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=270),
        ):
            render_module.normalize_clip(
                {'path': 'generated.mp4', 'forbid_loop': True},
                'normalized.mp4',
                9.0,
                1,
                output_resolution=render_module.SHORTS_RESOLUTION,
            )

        self.assertEqual(len(commands), 1)
        video_filter = commands[0][commands[0].index('-vf') + 1]
        self.assertIn(
            'scale=1153:2050:force_original_aspect_ratio=increase',
            video_filter,
        )
        self.assertIn(
            'crop=1080:1920:(iw-1080)*0.42:(ih-1920)/2',
            video_filter,
        )

    def test_invalid_output_resolution_fails_before_render_work(self):
        with patch.object(render_module, 'media_duration') as duration:
            with self.assertRaisesRegex(ValueError, 'Output resolution'):
                render_module.render_video(
                    'voice.wav',
                    ['visual.mp4'],
                    'Narration.',
                    'final.mp4',
                    output_resolution='720x720',
                )
        duration.assert_not_called()

    def test_horizontal_letterbox_detector_uses_both_frame_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'letterboxed.mp4'
            media.write_bytes(b'video')
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='',
                stderr=(
                    'black_start:4.366667 black_end:7.933333 '
                    'black_duration:3.566667'
                ),
            )
            with patch.object(
                render_module.subprocess,
                'run',
                return_value=completed,
            ) as run:
                duration = render_module.max_horizontal_letterbox_duration(
                    media
                )

        self.assertAlmostEqual(duration, 3.566667)
        command = run.call_args.args[0]
        filter_complex = command[command.index('-filter_complex') + 1]
        self.assertIn('[top]crop=iw:24:0:0[top_band]', filter_complex)
        self.assertIn(
            '[bottom]crop=iw:24:0:ih-24[bottom_band]',
            filter_complex,
        )
        self.assertIn('[top_band][bottom_band]vstack=inputs=2', filter_complex)

    def test_horizontal_letterbox_detector_fails_closed_on_probe_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'unreadable.mp4'
            media.write_bytes(b'video')
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout='',
                stderr='decoder failure',
            )
            with patch.object(
                render_module.subprocess,
                'run',
                return_value=completed,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'letterbox inspection failed',
                ):
                    render_module.max_horizontal_letterbox_duration(media)

    def test_symmetric_letterbox_crop_requires_repeated_full_width_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'letterboxed.mp4'
            media.write_bytes(b'video')
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='',
                stderr='\n'.join([
                    'crop=1280:540:0:90',
                    'crop=1280:540:0:90',
                    'crop=1280:540:0:90',
                    'crop=1280:540:0:90',
                    # One transient dark edge must not override the stable bars.
                    'crop=1100:500:80:110',
                ]),
            )
            with (
                patch.object(
                    render_module.subprocess,
                    'check_output',
                    return_value='1280x720\n',
                ),
                patch.object(
                    render_module.subprocess,
                    'run',
                    return_value=completed,
                ) as run,
            ):
                crop = render_module.detect_symmetric_letterbox_crop(
                    media,
                    start_seconds=1.25,
                )

        self.assertEqual(crop, (540, 90))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-ss') + 1], '1.250')
        self.assertIn(
            'cropdetect=limit=24:round=2:reset=0',
            command[command.index('-vf') + 1],
        )

    def test_symmetric_letterbox_crop_rejects_single_dark_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'dark-frame.mp4'
            media.write_bytes(b'video')
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='',
                stderr='crop=1280:540:0:90',
            )
            with (
                patch.object(
                    render_module.subprocess,
                    'check_output',
                    return_value='1280x720\n',
                ),
                patch.object(
                    render_module.subprocess,
                    'run',
                    return_value=completed,
                ),
            ):
                crop = render_module.detect_symmetric_letterbox_crop(media)

        self.assertIsNone(crop)

    def test_fixed_master_never_silently_truncates_overlong_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            voice = work / 'voice.wav'
            visual = work / 'visual.mp4'
            voice.write_bytes(b'voice')
            visual.write_bytes(b'visual')
            with patch.object(
                render_module,
                'media_duration',
                return_value=30.20,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'Narration exceeds the fixed master duration',
                ):
                    render_module.render_video(
                        voice,
                        [str(visual)],
                        'Complete narration.',
                        work / 'final.mp4',
                        target_duration=30.0,
                    )

    def test_generated_clip_is_single_pass_and_short_source_fails_closed(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=270),
        ):
            render_module.normalize_clip(
                {'path': 'generated.mp4', 'forbid_loop': True},
                'normalized.mp4',
                9.0,
                0,
            )

        self.assertEqual(len(commands), 1)
        self.assertNotIn('-stream_loop', commands[0])
        video_filter = commands[0][commands[0].index('-vf') + 1]
        self.assertIn('setpts=(PTS-STARTPTS)/1.008', video_filter)
        self.assertIn('trim=end_frame=270', video_filter)
        self.assertNotIn('setpts=N/(30*TB)', video_filter)
        self.assertNotIn('tpad=', video_filter)
        self.assertEqual(commands[0][commands[0].index('-frames:v') + 1], '270')
        self.assertNotIn('-t', commands[0])

        with (
            patch.object(render_module, 'media_duration', return_value=5.0),
            patch.object(render_module, '_run') as run,
        ):
            with self.assertRaisesRegex(RuntimeError, 'single-pass'):
                render_module.normalize_clip(
                    {'path': 'generated.mp4', 'forbid_loop': True},
                    'normalized.mp4',
                    9.0,
                    0,
                )
        run.assert_not_called()

    def test_generated_decode_shortfall_fails_instead_of_freezing_tail(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=269),
        ):
            with self.assertRaisesRegex(RuntimeError, 'frame gate'):
                render_module.normalize_clip(
                    {'path': 'generated.mp4', 'forbid_loop': True},
                    'normalized.mp4',
                    9.0,
                    0,
                )

        self.assertEqual(len(commands), 1)
        video_filter = commands[0][commands[0].index('-vf') + 1]
        self.assertNotIn('tpad=', video_filter)

    def test_letterboxed_clip_gets_one_bounded_stronger_overscan(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=270),
            patch.object(
                render_module,
                'max_horizontal_letterbox_duration',
                side_effect=[3.5, 0.0],
            ),
        ):
            render_module.normalize_clip(
                {'path': 'generated.mp4', 'forbid_loop': True},
                'normalized.mp4',
                9.0,
                0,
            )

        self.assertEqual(len(commands), 2)
        first_filter = commands[0][commands[0].index('-vf') + 1]
        retry_filter = commands[1][commands[1].index('-vf') + 1]
        self.assertIn('scale=2050:1153:', first_filter)
        self.assertIn('scale=2304:1296:', retry_filter)
        self.assertNotIn('-stream_loop', commands[0])
        self.assertNotIn('-stream_loop', commands[1])

    def test_persistent_letterbox_fails_closed_after_one_retry(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=270),
            patch.object(
                render_module,
                'max_horizontal_letterbox_duration',
                side_effect=[3.5, 1.2],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, 'letterbox gate'):
                render_module.normalize_clip(
                    {'path': 'generated.mp4', 'forbid_loop': True},
                    'normalized.mp4',
                    9.0,
                    0,
                )

        self.assertEqual(len(commands), 2)

    def test_persistent_letterbox_uses_measured_crop_then_rechecks_gate(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=270),
            patch.object(
                render_module,
                'max_horizontal_letterbox_duration',
                side_effect=[3.5, 1.2, 0.0],
            ),
            patch.object(
                render_module,
                'detect_symmetric_letterbox_crop',
                return_value=(540, 90),
            ) as detect_crop,
        ):
            render_module.normalize_clip(
                {'path': 'generated.mp4', 'forbid_loop': True},
                'normalized.mp4',
                9.0,
                0,
            )

        self.assertEqual(len(commands), 3)
        crop_filter = commands[2][commands[2].index('-vf') + 1]
        self.assertTrue(crop_filter.startswith('crop=iw:540:0:90,'))
        self.assertIn('scale=2050:1153:', crop_filter)
        detect_crop.assert_called_once_with(
            'generated.mp4',
            start_seconds=0.0,
            sample_seconds=2.5,
        )

    def test_measured_crop_does_not_bypass_failed_output_inspection(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(render_module, 'video_frame_count', return_value=270),
            patch.object(
                render_module,
                'max_horizontal_letterbox_duration',
                side_effect=[
                    3.5,
                    1.2,
                    RuntimeError('Horizontal letterbox inspection failed'),
                ],
            ),
            patch.object(
                render_module,
                'detect_symmetric_letterbox_crop',
                return_value=(540, 90),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'letterbox inspection failed',
            ):
                render_module.normalize_clip(
                    {'path': 'generated.mp4', 'forbid_loop': True},
                    'normalized.mp4',
                    9.0,
                    0,
                )

        self.assertEqual(len(commands), 3)

    def test_measured_crop_still_enforces_exact_frame_gate(self):
        commands = []
        with (
            patch.object(render_module, 'media_duration', return_value=10.0),
            patch.object(render_module, '_run', side_effect=commands.append),
            patch.object(
                render_module,
                'video_frame_count',
                side_effect=[270, 270, 269],
            ),
            patch.object(
                render_module,
                'max_horizontal_letterbox_duration',
                side_effect=[3.5, 1.2],
            ),
            patch.object(
                render_module,
                'detect_symmetric_letterbox_crop',
                return_value=(540, 90),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, 'frame gate'):
                render_module.normalize_clip(
                    {'path': 'generated.mp4', 'forbid_loop': True},
                    'normalized.mp4',
                    9.0,
                    0,
                )

        self.assertEqual(len(commands), 3)

    @unittest.skipUnless(
        shutil.which('ffmpeg') and shutil.which('ffprobe'),
        'ffmpeg and ffprobe are required',
    )
    def test_fractional_segment_target_keeps_all_124_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = work / 'source.mp4'
            output = work / 'normalized.mp4'
            try:
                subprocess.run([
                    'ffmpeg', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x360:rate=24:duration=6',
                    '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', str(source),
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self.skipTest('ffmpeg execution is blocked by the local sandbox')

            render_module.normalize_clip(
                {
                    'path': str(source),
                    'forbid_loop': True,
                    'start_fraction': 0.0,
                },
                output,
                124 / render_module.FPS,
                0,
            )

            self.assertEqual(render_module.video_frame_count(output), 124)
            self.assertAlmostEqual(
                render_module.media_duration(output),
                124 / render_module.FPS,
                places=3,
            )

    def test_timeline_uses_cumulative_rounding_without_frame_drift(self):
        timeline = [
            ('a.mp4', 4.2137, 'cut', 0),
            ('b.mp4', 4.2137, 'cut', 1),
            ('c.mp4', 4.2137, 'cut', 2),
            ('d.mp4', 4.2137, 'cut', 3),
            ('e.mp4', 4.2137, 'cut', 4),
            ('f.mp4', 4.2137, 'cut', 5),
            ('g.mp4', 4.2138, 'cut', 6),
        ]

        counts = render_module._timeline_frame_counts(timeline, 29.496)

        self.assertEqual(sum(counts), 885)
        self.assertTrue(all(count > 0 for count in counts))

    def test_master_command_pins_frames_and_pads_audio_without_shortest(self):
        commands = []

        def fake_normalize(_spec, output, *_args, **_kwargs):
            Path(output).write_bytes(b'clip')
            return str(output)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            voice = work / 'voice.wav'
            visual = work / 'visual.mp4'
            voice.write_bytes(b'voice')
            visual.write_bytes(b'visual')
            with (
                patch.object(render_module, 'media_duration', side_effect=[29.5, 30.0]),
                patch.object(
                    render_module,
                    'normalize_clip',
                    side_effect=fake_normalize,
                ) as normalize,
                patch.object(render_module, '_run', side_effect=commands.append),
                patch.object(render_module, 'video_frame_count', return_value=900),
                patch.object(render_module, 'ending_silence_duration', return_value=0.5),
                patch.object(render_module, 'max_freeze_duration', return_value=0.0),
            ):
                result = render_module.render_video(
                    voice,
                    [str(visual)],
                    'Test narration.',
                    work / 'final.mp4',
                    target_duration=30.0,
                )

        silent_command, mux_command = commands
        silent_filter = silent_command[
            silent_command.index('-filter_complex') + 1
        ]
        self.assertIn(
            'trim=end_frame=885,settb=expr=1/30,setpts=N',
            silent_filter,
        )
        self.assertIn('tpad=stop_mode=clone:stop=15', silent_filter)
        self.assertIn('trim=end_frame=900', silent_filter)
        self.assertNotIn('stop_duration=', silent_filter)
        self.assertNotIn('stop=-1', silent_filter)
        self.assertNotIn('setpts=N/(30*TB)', silent_filter)
        self.assertEqual(silent_command[silent_command.index('-frames:v') + 1], '900')
        self.assertNotIn('-shortest', mux_command)
        audio_filter = mux_command[mux_command.index('-af') + 1]
        self.assertIn('apad=whole_dur=30.000', audio_filter)
        self.assertIn('atrim=duration=30.000', audio_filter)
        self.assertIn('loudnorm=I=-15:TP=-1.0:LRA=7', audio_filter)
        self.assertGreater(
            audio_filter.rfind('aresample=48000'),
            audio_filter.index('loudnorm='),
        )
        self.assertEqual(mux_command[mux_command.index('-frames:v') + 1], '900')
        self.assertEqual(result['frame_count'], 900)
        self.assertEqual(result['ending_silence_seconds'], 0.5)
        self.assertEqual(result['resolution'], '1920x1080')
        self.assertEqual(
            normalize.call_args.args[5],
            render_module.LANDSCAPE_RESOLUTION,
        )

    def test_portrait_master_routes_resolution_to_every_normalized_shot(self):
        commands = []

        def fake_normalize(_spec, output, *_args, **_kwargs):
            Path(output).write_bytes(b'clip')
            return str(output)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            voice = work / 'voice.wav'
            visual = work / 'visual.mp4'
            voice.write_bytes(b'voice')
            visual.write_bytes(b'visual')
            with (
                patch.object(
                    render_module,
                    'media_duration',
                    side_effect=[29.5, 30.0],
                ),
                patch.object(
                    render_module,
                    'normalize_clip',
                    side_effect=fake_normalize,
                ) as normalize,
                patch.object(render_module, '_run', side_effect=commands.append),
                patch.object(render_module, 'video_frame_count', return_value=900),
                patch.object(render_module, 'ending_silence_duration', return_value=0.5),
                patch.object(render_module, 'max_freeze_duration', return_value=0.0),
            ):
                result = render_module.render_video(
                    voice,
                    [str(visual)],
                    'Test narration.',
                    work / 'final.mp4',
                    target_duration=30.0,
                    output_resolution=render_module.SHORTS_RESOLUTION,
                )

        self.assertEqual(len(commands), 2)
        self.assertEqual(result['resolution'], '1080x1920')
        self.assertEqual(
            normalize.call_args.args[5],
            render_module.SHORTS_RESOLUTION,
        )

    def test_short_concat_fails_before_mux_instead_of_becoming_long_freeze(self):
        commands = []

        def fake_normalize(_spec, output, *_args, **_kwargs):
            Path(output).write_bytes(b'clip')
            return str(output)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            voice = work / 'voice.wav'
            visual = work / 'visual.mp4'
            voice.write_bytes(b'voice')
            visual.write_bytes(b'visual')
            with (
                patch.object(render_module, 'media_duration', return_value=29.5),
                patch.object(render_module, 'normalize_clip', side_effect=fake_normalize),
                patch.object(render_module, '_run', side_effect=commands.append),
                patch.object(render_module, 'video_frame_count', return_value=792),
            ):
                with self.assertRaisesRegex(RuntimeError, 'Silent master frame gate'):
                    render_module.render_video(
                        voice,
                        [str(visual)],
                        'Test narration.',
                        work / 'final.mp4',
                        target_duration=30.0,
                    )

        self.assertEqual(len(commands), 1)
        silent_filter = commands[0][
            commands[0].index('-filter_complex') + 1
        ]
        self.assertIn('tpad=stop_mode=clone:stop=15', silent_filter)
        self.assertNotIn('stop_duration=', silent_filter)

    @unittest.skipUnless(
        shutil.which('ffmpeg') and shutil.which('ffprobe'),
        'ffmpeg and ffprobe are required',
    )
    def test_30_second_master_has_900_frames_and_natural_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            voice = work / 'voice.wav'
            try:
                subprocess.run([
                    'ffmpeg', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=29.5',
                    '-c:a', 'pcm_s16le', str(voice),
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except PermissionError:
                self.skipTest('ffmpeg execution is blocked by the local sandbox')

            def make_normalized(_spec, output, duration, *_args, **_kwargs):
                subprocess.run([
                    'ffmpeg', '-y', '-f', 'lavfi', '-i',
                    f'testsrc2=size=64x64:rate=30:duration={duration:.3f}',
                    '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', str(output),
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return str(output)

            output = work / 'final.mp4'
            with patch.object(
                render_module,
                'normalize_clip',
                side_effect=make_normalized,
            ):
                result = render_module.render_video(
                    voice,
                    [{'path': 'generated.mp4', 'forbid_loop': True}],
                    'Test narration.',
                    output,
                    scenes=[{'narration': 'Test narration.'}],
                    scene_durations=[29.5],
                    scene_visual_paths=[[
                        {'path': 'generated.mp4', 'forbid_loop': True}
                    ]],
                    target_duration=30.0,
                )

            self.assertEqual(result['frame_count'], 900)
            self.assertAlmostEqual(result['duration'], 30.0, places=2)
            self.assertGreaterEqual(result['ending_silence_seconds'], 0.30)
            self.assertLessEqual(result['ending_silence_seconds'], 0.80)
            srt_text = Path(result['srt']).read_text(encoding='utf-8')
            self.assertIn('00:00:29,500', srt_text)


if __name__ == '__main__':
    unittest.main()
