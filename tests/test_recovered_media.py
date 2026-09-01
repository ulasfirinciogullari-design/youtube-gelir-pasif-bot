import ast
import hashlib
import json
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


class FinalAudioQualityError(RuntimeError):
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
        '_REPAIR_RECOVERED_MEDIA_PROVIDERS',
        '_MAX_RECOVERED_VIDEO_BYTES',
        '_MAX_RECOVERED_AUDIO_BYTES',
        '_RECOVERED_VOICE_KEY_PATTERN',
        '_SHA256_PATTERN',
    }
    function_names = {
        '_file_sha256',
        '_recovery_package_sha256',
        '_validated_recovered_generated_media',
        '_validate_recovered_generated_clip',
        '_validated_recovered_voice',
        '_stage_scene_repair_artifacts',
        '_persist_scene_repair_checkpoint',
        '_prepare_package',
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
        'FinalAudioQualityError': FinalAudioQualityError,
        'Path': Path,
        'hashlib': hashlib,
        'json': json,
        'math': math,
        're': re,
        'media_duration': lambda _path: 6.0,
        'video_frame_count': lambda _path: 144,
        'upload_file': lambda *_args, **_kwargs: None,
        'save_repair_checkpoint': lambda *_args, **_kwargs: None,
        'update_job': lambda *_args, **_kwargs: None,
        'set_stage': lambda *_args, **_kwargs: None,
        'short_story_package_is_approved': lambda *_args, **_kwargs: True,
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

    def repair_contract(self):
        checksum = 'a' * 64
        return {
            'version': 2,
            'repair_only': True,
            'source_task_id': self.source_task_id,
            'package_sha256': 'b' * 64,
            'repair_scene_indices': [2],
            'scenes': {
                '0': [{
                    'key': (
                        f'recovery/{self.source_task_id}/raw/'
                        'scene-00-initial.mp4'
                    ),
                    'sha256': checksum,
                    'size': 4096,
                    'provider': 'gemini_image_motion',
                    'provider_attempts': 1,
                    'synthetic_motion_only': True,
                    'motion_recipe_version': 'center-push-v1',
                    'source_media_type': 'image',
                }],
                '6': [{
                    'key': (
                        f'recovery/{self.source_task_id}/raw/'
                        'scene-06-repair-01.mp4'
                    ),
                    'sha256': 'c' * 64,
                    'size': 8192,
                    'provider': 'gemini_veo_fast',
                    'provider_attempts': 1,
                    'synthetic_motion_only': False,
                    'motion_recipe_version': None,
                    'source_media_type': 'video',
                }],
            },
        }

    def voice_contract(self):
        return {
            'version': 1,
            'source_task_id': self.source_task_id,
            'package_sha256': 'b' * 64,
            'key': f'recovery/{self.source_task_id}/raw/voice.mp3',
            'sha256': 'd' * 64,
            'size': 4096,
            'scene_durations': [3.0, 3.0],
            'spoken_texts': ['Birinci cümle', 'İkinci cümle'],
            'duration_before_fit': 6.0,
            'duration_after_fit': 6.0,
            'tempo_rate': 1.0,
            'content_target_seconds': 5.5,
            'reserved_tail_seconds': 0.5,
        }

    def test_contract_normalizes_scene_indices_and_storage_keys(self):
        validate = self.boundary['_validated_recovered_generated_media']

        result = validate(self.contract(), 7)

        self.assertEqual(set(result['scenes']), {0, 6})
        self.assertTrue(result['recovery_only'])
        self.assertEqual(result['provider'], 'gemini_veo_fast')

    def test_private_image_motion_and_unknown_providers_are_not_recoverable(self):
        validate = self.boundary['_validated_recovered_generated_media']
        image_contract = self.contract()
        image_contract['provider'] = 'gemini_image_motion'
        with self.assertRaises(FinalVisualQualityError):
            validate(image_contract, 7)
        unknown = self.contract()
        unknown['provider'] = 'untrusted_generator'
        with self.assertRaises(FinalVisualQualityError):
            validate(unknown, 7)

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

    def test_repair_contract_binds_hashes_and_exact_repair_coverage(self):
        validate = self.boundary['_validated_recovered_generated_media']
        require = self.boundary['_require_recovered_media_coverage']

        result = validate(self.repair_contract(), 7, 'b' * 64)

        self.assertEqual(set(result['scenes']), {0, 6})
        self.assertEqual(result['repair_scene_indices'], [2])
        self.assertEqual(
            result['scenes'][0][0]['provider'],
            'gemini_image_motion',
        )
        require(result, [0, 2, 6])
        with self.assertRaisesRegex(
            FinalVisualQualityError,
            'does not exactly match selected paid scenes',
        ):
            require(result, [0, 6])

    def test_repair_contract_rejects_overlap_tampering_and_wrong_story(self):
        validate = self.boundary['_validated_recovered_generated_media']

        overlap = self.repair_contract()
        overlap['repair_scene_indices'] = [0]
        with self.assertRaises(FinalVisualQualityError):
            validate(overlap, 7, 'b' * 64)

        tampered = self.repair_contract()
        tampered['scenes']['0'][0]['sha256'] = 'not-a-checksum'
        with self.assertRaises(FinalVisualQualityError):
            validate(tampered, 7, 'b' * 64)

        with self.assertRaises(FinalVisualQualityError):
            validate(self.repair_contract(), 7, 'e' * 64)

    def test_consecutive_scene_repair_preserves_original_package_hash(self):
        prepare = self.boundary['_prepare_package']
        package_hash = self.boundary['_recovery_package_sha256']
        validate = self.boundary['_validated_recovered_generated_media']
        original_options = {
            'mode': 'preview',
            'workflow': 'auto',
            'visual_mix': 'balanced',
        }
        approved = {
            'title': 'Locked repair story',
            'scenes': [{}, {}, {}, {}, {}, {}, {}],
            'studio_options': original_options,
        }
        expected_hash = package_hash(approved)
        contract = self.repair_contract()
        contract['package_sha256'] = expected_hash
        approved['_recovered_generated_media'] = contract
        approved['_recovered_voice'] = {
            'version': 1,
            'source_task_id': self.source_task_id,
            'package_sha256': expected_hash,
        }

        prepared = prepare(
            object(),
            '11111111-1111-4111-8111-111111111111',
            'Locked repair story',
            0.5,
            'tr',
            {
                **original_options,
                'workflow': 'scene_repair',
            },
            approved,
        )

        assert prepared['studio_options'] == original_options
        assert package_hash(prepared) == expected_hash
        validate(contract, 7, package_hash(prepared))

    def test_checkpoint_clip_verifies_exact_size_and_checksum(self):
        validate_clip = self.boundary['_validate_recovered_generated_clip']
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / 'clip.mp4'
            clip.write_bytes(b'\x00\x00\x00\x18ftyp' + b'0' * 2048)
            checksum = hashlib.sha256(clip.read_bytes()).hexdigest()
            validate_clip(
                clip,
                5.0,
                expected_size=clip.stat().st_size,
                expected_sha256=checksum,
            )
            with self.assertRaisesRegex(
                FinalVisualQualityError,
                'checksum',
            ):
                validate_clip(
                    clip,
                    5.0,
                    expected_size=clip.stat().st_size,
                    expected_sha256='f' * 64,
                )

    def test_voice_contract_is_bound_to_story_and_exact_scene_timing(self):
        validate_voice = self.boundary['_validated_recovered_voice']

        result = validate_voice(self.voice_contract(), 2, 'b' * 64)

        self.assertEqual(result['scene_durations'], [3.0, 3.0])
        with self.assertRaises(FinalAudioQualityError):
            validate_voice(self.voice_contract(), 2, 'e' * 64)
        inconsistent = self.voice_contract()
        inconsistent['scene_durations'] = [1.0, 1.0]
        with self.assertRaisesRegex(
            FinalAudioQualityError,
            'inconsistent',
        ):
            validate_voice(inconsistent, 2, 'b' * 64)

    def test_final_rejection_persists_only_accepted_generated_scenes(self):
        persist = self.boundary['_persist_scene_repair_checkpoint']
        saved = []
        uploads = []
        updates = []
        self.boundary['upload_file'] = (
            lambda path, key, content_type: uploads.append(
                (Path(path).name, key, content_type)
            )
        )
        self.boundary['save_repair_checkpoint'] = (
            lambda task_id, checkpoint: saved.append(
                (task_id, checkpoint)
            )
        )
        self.boundary['update_job'] = (
            lambda task_id, **fields: updates.append((task_id, fields))
        )
        with tempfile.TemporaryDirectory() as tmp:
            accepted = Path(tmp) / 'accepted.mp4'
            rejected = Path(tmp) / 'rejected.mp4'
            voice = Path(tmp) / 'voice.mp3'
            accepted.write_bytes(b'\x00\x00\x00\x18ftyp' + b'a' * 2048)
            rejected.write_bytes(b'\x00\x00\x00\x18ftyp' + b'r' * 2048)
            voice.write_bytes(b'ID3' + b'v' * 2048)
            package = {'title': 'Locked', 'scenes': [{}, {}]}
            voice_result = {
                'path': str(voice),
                'spoken_texts': ['Bir', 'İki'],
                'duration_before_fit': 6.0,
                'duration_after_fit': 6.0,
                'tempo_rate': 1.0,
                'content_target_seconds': 5.5,
                'reserved_tail_seconds': 0.5,
            }
            specs = {
                0: [{
                    'path': str(accepted),
                    'generation_provider': 'gemini_veo_fast',
                    'generation_provider_attempts': 1,
                }],
                1: [{
                    'path': str(rejected),
                    'generation_provider': 'gemini_veo_fast',
                    'generation_provider_attempts': 1,
                }],
            }

            result = persist(
                task_id=self.source_task_id,
                package=package,
                voice_result=voice_result,
                scene_durations=[3.0, 3.0],
                generated_checkpoint_specs=specs,
                rejected_scene_indices=[1],
            )

        self.assertTrue(result)
        self.assertEqual(len(saved), 1)
        approved = saved[0][1]['approved_package']
        media = approved['_recovered_generated_media']
        self.assertEqual(set(media['scenes']), {'0'})
        self.assertEqual(media['repair_scene_indices'], [1])
        self.assertTrue(approved['_recovered_voice']['sha256'])
        self.assertEqual(len(uploads), 2)
        self.assertEqual(updates[0][1]['repair_scene_indices'], [1])
        self.assertIn('yalnızca reddedilen', updates[0][1]['repair_message'])


if __name__ == '__main__':
    unittest.main()

