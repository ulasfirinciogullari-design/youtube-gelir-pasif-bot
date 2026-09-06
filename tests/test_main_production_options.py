import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field, StrictBool, ValidationError
import pytest


def _api():
    path = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                   and node.name in {'JobCreate', 'create_job'}]
    for node in definitions:
        node.decorator_list = []
    queued = []
    bindings = []

    def bind(mode, enabled, channel, language):
        bindings.append((mode, enabled, channel, language))
        return {'publish_after_render': enabled is True and mode == 'production'}

    namespace = {
        'BaseModel': BaseModel, 'Field': Field, 'StrictBool': StrictBool,
        'Literal': Literal, 'Header': Header, 'HTTPException': HTTPException,
        'UnsupportedLanguageError': ValueError,
        'normalize_pipeline_language': lambda language: language,
        '_require_factory_token': lambda _token: None,
        '_production_publish_options': bind,
        'run_video_pipeline': SimpleNamespace(delay=lambda *args: queued.append(args) or SimpleNamespace(id='queued')),
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace, queued, bindings


def test_api_default_does_not_enable_production_upload():
    namespace, queued, bindings = _api()
    payload = namespace['JobCreate'](topic='Kabin ışıkları')
    namespace['create_job'](payload)
    assert payload.publish_after_render is False
    assert queued[0][4]['publish_after_render'] is False
    assert bindings[0][1] is False


def test_api_supports_explicit_production_shorts_and_drops_server_only_markers():
    namespace, queued, bindings = _api()
    payload = namespace['JobCreate'](
        topic='Kabin ışıkları', duration_minutes=0.5, mode='production',
        format='shorts', publish_after_render=True,
        production_channel_id='UC_selected', production_scheduled=True,
        production_connection_id='client-forged',
    )
    namespace['create_job'](payload)
    assert queued[0][4] == {'mode': 'production', 'format': 'shorts', 'publish_after_render': True}
    assert bindings == [('production', True, 'UC_selected', 'tr')]
    assert not hasattr(payload, 'production_scheduled')
    assert not hasattr(payload, 'production_connection_id')


@pytest.mark.parametrize('invalid', ['true', 'false', 1, 0])
def test_api_publish_opt_in_requires_a_json_boolean(invalid):
    namespace, _, _ = _api()
    with pytest.raises(ValidationError):
        namespace['JobCreate'](topic='Kabin ışıkları', publish_after_render=invalid)


def test_api_preview_cannot_auto_upload_even_when_requested():
    namespace, queued, _ = _api()
    payload = namespace['JobCreate'](
        topic='Kabin ışıkları', mode='preview', publish_after_render=True,
    )
    namespace['create_job'](payload)
    assert queued[0][4]['publish_after_render'] is False
