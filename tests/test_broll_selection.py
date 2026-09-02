import ast
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import tempfile
import time
import unittest


def _load_task_helpers():
    source_path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'), filename=str(source_path))
    wanted = {
        '_select_ranked_broll_candidates',
        '_download_ranked_broll_candidates',
        '_is_transient_pexels_provider_error',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    if {node.name for node in definitions} != wanted:
        raise AssertionError('candidate helper definitions are missing from app/tasks.py')
    namespace = {
        'Path': Path,
        'ThreadPoolExecutor': ThreadPoolExecutor,
        'as_completed': as_completed,
        're': re,
        'find_broll': None,
        'download_broll': None,
        'httpx': httpx,
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source_path), 'exec'), namespace)
    return namespace


HELPERS = _load_task_helpers()
select_candidates = HELPERS['_select_ranked_broll_candidates']
download_candidates = HELPERS['_download_ranked_broll_candidates']


def candidate(pexels_id, *, duration=10, width=1920, height=1080):
    return {
        'pexels_id': pexels_id,
        'download_url': f'https://videos.example/{pexels_id}.mp4',
        'duration': duration,
        'width': width,
        'height': height,
        'creator_name': f'creator-{pexels_id}',
    }


class RankedBrollSelectionTests(unittest.TestCase):
    def test_round_robin_preserves_query_relevance_order(self):
        selected = select_candidates(
            [
                ('first query', [candidate(1), candidate(2), candidate(5)]),
                ('second query', [candidate(3), candidate(4), candidate(6)]),
            ],
            set(),
            5,
            allow_seen_fallback=False,
        )

        self.assertEqual(
            [item['pexels_id'] for _query, item in selected],
            [1, 3, 2, 4, 5],
        )

    def test_duplicate_seen_short_and_portrait_candidates_are_skipped(self):
        selected = select_candidates(
            [
                ('first', [
                    candidate(1),
                    candidate(2, duration=2),
                    candidate(3, width=720, height=1280),
                ]),
                ('second', [candidate(1), candidate(4)]),
            ],
            {4},
            5,
            allow_seen_fallback=False,
            allow_short_fallback=False,
        )

        self.assertEqual(
            [item['pexels_id'] for _query, item in selected],
            [1],
        )

    def test_portrait_selection_keeps_vertical_candidates_only(self):
        selected = select_candidates(
            [
                ('vertical story', [
                    candidate(1),
                    candidate(2, width=720, height=1280),
                    candidate(3, width=1080, height=1920),
                ]),
            ],
            set(),
            3,
            allow_seen_fallback=False,
            orientation='portrait',
        )

        self.assertEqual(
            [item['pexels_id'] for _query, item in selected],
            [2, 3],
        )

    def test_network_completion_order_cannot_reorder_downloaded_candidates(self):
        observed_orientations = []

        def fake_find(query, _limit, *, orientation):
            observed_orientations.append(orientation)
            if query == 'first':
                time.sleep(0.04)
                return [candidate(1), candidate(2)]
            time.sleep(0.001)
            return [candidate(3), candidate(4)]

        def fake_download(item, _path):
            time.sleep({1: 0.04, 3: 0.01}.get(item['pexels_id'], 0.001))

        globals_dict = download_candidates.__globals__
        old_find = globals_dict['find_broll']
        old_download = globals_dict['download_broll']
        globals_dict['find_broll'] = fake_find
        globals_dict['download_broll'] = fake_download
        try:
            with tempfile.TemporaryDirectory() as temporary:
                credits = []
                seen_ids = set()
                replacements = download_candidates(
                    4,
                    ['first', 'second'],
                    seen_ids,
                    Path(temporary),
                    credits,
                    file_prefix='deterministic',
                    selected_by='test',
                    max_candidates=3,
                    search_limit=24,
                )
        finally:
            globals_dict['find_broll'] = old_find
            globals_dict['download_broll'] = old_download

        self.assertEqual(
            [Path(spec['path']).name for spec in replacements],
            [
                'deterministic_s04_00.mp4',
                'deterministic_s04_01.mp4',
                'deterministic_s04_02.mp4',
            ],
        )
        self.assertEqual([entry['query'] for entry in credits], ['first', 'second', 'first'])
        self.assertEqual([entry['pexels_id'] for entry in credits], [1, 3, 2])
        self.assertEqual(seen_ids, {1, 2, 3})
        self.assertEqual(observed_orientations, ['landscape', 'landscape'])

    def test_failed_download_is_not_marked_seen(self):
        def fake_find(query, _limit, *, orientation):
            self.assertEqual(orientation, 'landscape')
            return [
                candidate(1),
                candidate(2),
                candidate(3),
                candidate(4),
            ] if query == 'only' else []

        def fake_download(item, _path):
            if item['pexels_id'] == 2:
                raise httpx.TimeoutException('simulated download failure')

        globals_dict = download_candidates.__globals__
        old_find = globals_dict['find_broll']
        old_download = globals_dict['download_broll']
        globals_dict['find_broll'] = fake_find
        globals_dict['download_broll'] = fake_download
        try:
            with tempfile.TemporaryDirectory() as temporary:
                credits = []
                seen_ids = set()
                replacements = download_candidates(
                    5,
                    ['only'],
                    seen_ids,
                    Path(temporary),
                    credits,
                    file_prefix='failure',
                    selected_by='test',
                    max_candidates=3,
                    search_limit=24,
                )
        finally:
            globals_dict['find_broll'] = old_find
            globals_dict['download_broll'] = old_download

        self.assertEqual(
            [Path(spec['path']).name for spec in replacements],
            [
                'failure_s05_00.mp4',
                'failure_s05_02.mp4',
                'failure_s05_03.mp4',
            ],
        )
        self.assertEqual([entry['pexels_id'] for entry in credits], [1, 3, 4])
        self.assertEqual(seen_ids, {1, 3, 4})
        self.assertNotIn(2, seen_ids)

    def test_portrait_retry_search_and_selection_stay_vertical(self):
        observed_orientations = []

        def fake_find(_query, _limit, *, orientation):
            observed_orientations.append(orientation)
            return [
                candidate(1),
                candidate(2, width=720, height=1280),
            ]

        globals_dict = download_candidates.__globals__
        old_find = globals_dict['find_broll']
        old_download = globals_dict['download_broll']
        globals_dict['find_broll'] = fake_find
        globals_dict['download_broll'] = lambda _item, _path: None
        try:
            with tempfile.TemporaryDirectory() as temporary:
                replacements = download_candidates(
                    6,
                    ['vertical story'],
                    set(),
                    Path(temporary),
                    [],
                    file_prefix='portrait',
                    selected_by='test',
                    max_candidates=2,
                    search_limit=24,
                    orientation='portrait',
                )
        finally:
            globals_dict['find_broll'] = old_find
            globals_dict['download_broll'] = old_download

        self.assertEqual(observed_orientations, ['portrait'])
        self.assertEqual(len(replacements), 1)
        self.assertIn('portrait_s06_00.mp4', replacements[0]['path'])


if __name__ == '__main__':
    unittest.main()
