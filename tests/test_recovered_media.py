import ast
import math
import re
import tempfile
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
)


class FinalVisualQualityError(RuntimeError):
    pass


def _load_recovery_boundary():
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding='utf-8'),
        filename=str(SOURCE_PATH),
    )
    constant_names = {
        '_RECOVERED_MEDIA_SOURCE_PATTERN',
        '_RECOVERED_MEDIA_KEY_PATTERN',
        '_RECOVERED_MEDIA_PROVIDERS',
        '_MAX_RECOVERED_VIDEO_BYTES',
    }
    function_names = {
        '_validated_recovered_generated_media',
        '_validate_recovered_generated_clip',
        '_require_recovered_media_coverage',
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
        )
        or (
            isinstance(node, ast.FunctionDef)
            and node.name in function_names
        )
    ]
    namespace = {
        'FinalVisualQualityError': FinalVisualQualityError,
        'Path': Path,
        'math': math,
        're': re,
        'media_duration': lambda _path: 6.0,
        'video_frame_count': lambda _path: 144,
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


class RecoveredGeneratedMediaTests(unittest.TestCase):
    def setUp(self):
        self.boundary = _load_recovery_boundary()
        self.source_task_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'

    def contract(self):
        return {
            'version': 1,
            'recovery_only': True,
            'source_task_id': self.source_task_id,
            'provider': 'gemini_veo_fast',
            'scenes': {
                '0': [
                    f'recovery/{self.source_task_id}/raw/clip-00.mp4'
                ],
                '6': [
                    f'recovery/{self.source_task_id}/raw/clip-01.mp4'
                ],
            },
        }

    def test_contract_normalizes_scene_indices_and_storage_keys(self):
        validate = self.boundary['_validated_recovered_generated_media']

        result = validate(self.contract(), 7)

        self.assertEqual(set(result['scenes']), {0, 6})
        self.assertTrue(result['recovery_only'])
        self.assertEqual(result['provider'], 'gemini_veo_fast')

    def test_foreign_prefix_or_extra_field_fails_closed(self):
        validate = self.boundary['_validated_recovered_generated_media']
        foreign = self.contract()
        foreign['scenes']['0'] = [
            'recovery/00000000-0000-0000-0000-000000000000/'
            'raw/clip-00.mp4'
        ]
        with self.assertRaises(FinalVisualQualityError):
            validate(foreign, 7)

        extra = self.contract()
        extra['download_url'] = 'https://example.invalid/signed'
        with self.assertRaises(FinalVisualQualityError):
            validate(extra, 7)

        duplicated_scene = self.contract()
        duplicated_scene['scenes'][0] = list(
            duplicated_scene['scenes']['0']
        )
        with self.assertRaises(FinalVisualQualityError):
            validate(duplicated_scene, 7)

        duplicated_key = self.contract()
        duplicated_key['scenes']['6'] = list(
            duplicated_key['scenes']['0']
        )
        with self.assertRaises(FinalVisualQualityError):
            validate(duplicated_key, 7)

    def test_missing_selected_scene_fails_before_paid_fallback(self):
        validate = self.boundary['_validated_recovered_generated_media']
        require = self.boundary['_require_recovered_media_coverage']
        contract = validate(self.contract(), 7)

        require(contract, [0, 6])
        with self.assertRaisesRegex(
            FinalVisualQualityError,
            'does not exactly match selected paid scenes',
        ):
            require(contract, [0, 2, 6])

        with self.assertRaisesRegex(
            FinalVisualQualityError,
            'does not exactly match selected paid scenes',
        ):
            require(contract, [0])

    def test_checkpoint_clip_requires_mp4_frames_and_duration(self):
        validate_clip = self.boundary['_validate_recovered_generated_clip']
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / 'clip.mp4'
            clip.write_bytes(b'\x00\x00\x00\x18ftyp' + b'0' * 2048)
            validate_clip(clip, 5.0)

            self.boundary['media_duration'] = lambda _path: 4.5
            with self.assertRaisesRegex(
                FinalVisualQualityError,
                'too short',
            ):
                validate_clip(clip, 5.0)

            self.boundary['media_duration'] = lambda _path: 6.0
            self.boundary['video_frame_count'] = lambda _path: 0
            with self.assertRaisesRegex(
                FinalVisualQualityError,
                'too short',
            ):
                validate_clip(clip, 5.0)

            clip.write_bytes(b'not-an-mp4' + b'0' * 2048)
            with self.assertRaisesRegex(
                FinalVisualQualityError,
                'valid MP4',
            ):
                validate_clip(clip, 5.0)


if __name__ == '__main__':
    unittest.main()

