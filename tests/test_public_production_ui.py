"""Exercise the actual pure profile renderer without network/service imports."""
import ast
from html import escape
from pathlib import Path

import pytest


@pytest.fixture
def render_profile():
    path = Path(__file__).resolve().parents[1] / 'app' / 'youtube_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_profile_form')
    namespace = {'escape': escape, '_production_status_text': lambda *_: 'Planlama bekliyor',
                 '_produce_now_form': lambda *_: ''}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_profile_form']


@pytest.mark.parametrize(('mode', 'label'), [
    ('private', 'Otomatik yükleme · gizli'),
    ('public', 'Kalite geçerse otomatik yayında'),
    ('scheduled', 'Kalite geçerse otomatik planlanır'),
])
def test_publication_policy_visible_before_collapsed_settings(render_profile, mode, label):
    html = render_profile({'id': 'channel'}, {'auto_publish': True, 'release_mode': mode})
    assert label in html.split('<details', 1)[0]
    assert f'<option value="{mode}" selected>' in html


def test_disabled_profile_never_claims_automatic_publication(render_profile):
    html = render_profile({'id': 'channel'}, {'auto_publish': False, 'release_mode': 'public'})
    assert 'Otomatik yükleme kapalı' in html.split('<details', 1)[0]
    assert 'Kalite geçerse otomatik yayında' not in html


def test_format_parallel_and_disclosure_limits_are_explicit(render_profile):
    html = render_profile({'id': 'channel'}, None)
    for text in ('30 saniyelik Shorts', '3 dakikalık yatay video',
                 'Aynı kanalın bölümleri sırayla', 'iki farklı kanal',
                 'gerekli içerik bildirimleri', 'Başlık, açıklama ve etiketler otomatik'):
        assert text in html
    assert 'garanti' not in html
