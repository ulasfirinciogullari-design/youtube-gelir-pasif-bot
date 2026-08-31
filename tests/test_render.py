import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.render as render_module


class RenderQualityTests(unittest.TestCase):
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
        self.assertIn('setpts=N/(30*TB)', video_filter)
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
                patch.object(render_module, 'normalize_clip', side_effect=fake_normalize),
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
        silent_filter = silent_command[silent_command.index('-vf') + 1]
        self.assertTrue(silent_filter.startswith('setpts=PTS-STARTPTS,'))
        self.assertIn('tpad=stop_mode=clone:stop_duration=0.700', silent_filter)
        self.assertIn('trim=end_frame=900', silent_filter)
        self.assertEqual(silent_command[silent_command.index('-frames:v') + 1], '900')
        self.assertNotIn('-shortest', mux_command)
        audio_filter = mux_command[mux_command.index('-af') + 1]
        self.assertIn('apad=whole_dur=30.000', audio_filter)
        self.assertIn('atrim=duration=30.000', audio_filter)
        self.assertEqual(mux_command[mux_command.index('-frames:v') + 1], '900')
        self.assertEqual(result['frame_count'], 900)
        self.assertEqual(result['ending_silence_seconds'], 0.5)

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
        silent_filter = commands[0][commands[0].index('-vf') + 1]
        self.assertIn('tpad=stop_mode=clone:stop_duration=0.700', silent_filter)
        self.assertNotIn('stop_duration=30.', silent_filter)

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

