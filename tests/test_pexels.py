import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def _load_pexels_module():
    source_path = (
        Path(__file__).resolve().parents[1]
        / 'app'
        / 'services'
        / 'pexels.py'
    )
    spec = importlib.util.spec_from_file_location(
        'test_pexels_service_module',
        source_path,
    )
    if spec is None or spec.loader is None:
        raise AssertionError('Unable to load app/services/pexels.py')
    module = importlib.util.module_from_spec(spec)
    config_stub = ModuleType('app.config')
    config_stub.settings = SimpleNamespace(pexels_api_key='configured')
    with patch.dict(sys.modules, {'app.config': config_stub}):
        spec.loader.exec_module(module)
    return module


pexels = _load_pexels_module()


def _video_with_landscape_and_portrait_files() -> dict:
    return {
        'id': 42,
        'duration': 12,
        'url': 'https://www.pexels.com/video/42/',
        'user': {
            'name': 'Creator',
            'url': 'https://www.pexels.com/@creator/',
        },
        'video_files': [
            {
                'link': 'https://media.example/landscape.mp4',
                'width': 1920,
                'height': 1080,
                'file_type': 'video/mp4',
            },
            {
                'link': 'https://media.example/portrait.mp4',
                'width': 1080,
                'height': 1920,
                'file_type': 'video/mp4',
            },
        ],
    }


class PexelsOrientationTests(unittest.TestCase):
    def test_search_payload_normalizes_supported_portrait_orientation(self):
        response = Mock()
        response.json.return_value = {'videos': []}

        with (
            patch.object(
                pexels,
                '_headers',
                return_value={'Authorization': 'redacted'},
            ),
            patch.object(pexels.httpx, 'get', return_value=response) as get,
        ):
            result = pexels.search_videos(
                'vertical documentary',
                per_page=200,
                orientation=' PORTRAIT ',
            )

        self.assertEqual(result, [])
        self.assertEqual(
            get.call_args.kwargs['params'],
            {
                'query': 'vertical documentary',
                'per_page': 80,
                'orientation': 'portrait',
            },
        )
        response.raise_for_status.assert_called_once_with()

    def test_invalid_orientation_is_rejected_before_network_without_echo(self):
        canary = 'private-api-value-canary'
        invalid_orientation = f'portrait?token={canary}'

        with patch.object(pexels.httpx, 'get') as get:
            with self.assertRaises(ValueError) as raised:
                pexels.search_videos(
                    'query',
                    orientation=invalid_orientation,
                )

        get.assert_not_called()
        self.assertNotIn(canary, str(raised.exception))
        self.assertNotIn(invalid_orientation, str(raised.exception))

    def test_find_broll_defaults_to_landscape_and_picks_landscape_file(self):
        video = _video_with_landscape_and_portrait_files()

        with patch.object(
            pexels,
            'search_videos',
            return_value=[video],
        ) as search:
            result = pexels.find_broll('documentary', per_page=5)

        search.assert_called_once_with(
            'documentary',
            per_page=5,
            orientation='landscape',
        )
        self.assertEqual(result[0]['width'], 1920)
        self.assertEqual(result[0]['height'], 1080)
        self.assertEqual(
            result[0]['download_url'],
            'https://media.example/landscape.mp4',
        )

    def test_find_broll_passes_portrait_and_picks_portrait_file(self):
        video = _video_with_landscape_and_portrait_files()

        with patch.object(
            pexels,
            'search_videos',
            return_value=[video],
        ) as search:
            result = pexels.find_broll(
                'vertical documentary',
                per_page=5,
                orientation='portrait',
            )

        search.assert_called_once_with(
            'vertical documentary',
            per_page=5,
            orientation='portrait',
        )
        self.assertEqual(result[0]['width'], 1080)
        self.assertEqual(result[0]['height'], 1920)
        self.assertEqual(
            result[0]['download_url'],
            'https://media.example/portrait.mp4',
        )

    def test_find_broll_rejects_invalid_orientation_before_search(self):
        with patch.object(pexels, 'search_videos') as search:
            with self.assertRaises(ValueError):
                pexels.find_broll('query', orientation='diagonal')

        search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
