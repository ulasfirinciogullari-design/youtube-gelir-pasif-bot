from __future__ import annotations

import json
import importlib
import sys
import types

import pytest

if 'app.config' not in sys.modules:
    config_module = types.ModuleType('app.config')
    config_module.settings = types.SimpleNamespace(
        redis_url='redis://test',
        app_encryption_key='',
        factory_api_token='',
        google_client_id='',
        google_client_secret='',
        google_redirect_uri='',
    )
    sys.modules['app.config'] = config_module

automation = importlib.import_module('app.services.youtube_automation')


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.sets = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def eval(self, script, numkeys, *values):
        keys = list(values[:numkeys])
        args = list(values[numkeys:])
        if script == automation._SAVE_PROFILE:
            current = self.values.get(keys[0], '')
            if current != args[0]:
                return 0
            self.values[keys[0]] = args[1]
            self.sets.setdefault(keys[1], set()).add(args[2])
            return 1
        if script == automation._RESERVE_SERIES_NUMBER:
            current = self.values.get(keys[1])
            if current is not None:
                return int(current)
            number = int(self.values.get(keys[0], 0)) + 1
            maximum = int(args[0])
            if maximum > 0 and number > maximum:
                return -1
            self.values[keys[0]] = str(number)
            self.values[keys[1]] = str(number)
            return number
        raise AssertionError('unexpected script')


def _source(*, quality='automated_qc_pass', manual=False, label='merak-tr'):
    return {
        'state': 'SUCCESS',
        'kind': 'render',
        'spec': {
            'topic': 'Uçak kabin ışıkları neden kısılır?',
            'language': 'tr',
            'channel_id': label,
        },
        'result': {
            'video_key': 'videos/source/final.mp4',
            'title': 'Kabin Işıkları Neden Kısılır?',
            'quality_disposition': quality,
            'manual_qa_required': manual,
            'publish_metadata': {
                'title': 'Kabin Işıkları Neden Kısılır?',
                'description': 'Kalkış ve inişteki küçük değişikliğin güvenlik nedenini anlatıyoruz.',
                'tags': ['havacılık'],
                'hashtags': ['Uçak'],
                'sources': ['FAA passenger safety guidance'],
            },
        },
    }


def _profile(channel_id='UC_channel_alpha', **updates):
    value = {
        'schema_version': 1,
        'channel_id': channel_id,
        'profile_revision': 'revision-one',
        'channel_identity': 'Türkçe kısa havacılık belgeselleri',
        'route_label': 'merak-tr',
        'topic_keywords': ['uçak', 'havacılık'],
        'languages': ['tr'],
        'default_language': 'tr',
        'default_tags': ['bilim', 'merak'],
        'hashtags': ['Bilim', 'Shorts'],
        'category_id': '28',
        'description_footer': 'Her hafta yeni bir açıklama.',
        'series_id': 'ucak-sirlari',
        'series_name': 'Uçak Sırları',
        'series_total': 8,
        'auto_publish': True,
        'release_mode': 'public',
        'schedule_delay_minutes': 60,
        'require_thumbnail': False,
    }
    value.update(updates)
    return value


def test_quality_gate_requires_explicit_automated_pass():
    assert automation.automated_quality_approved(_source()) is True
    assert automation.automated_quality_approved(
        _source(quality='manual_qa_preview', manual=True)
    ) is False
    unknown = _source()
    unknown['result'].pop('quality_disposition')
    assert automation.automated_quality_approved(unknown) is False
    failed = _source()
    failed['state'] = 'FAILURE'
    assert automation.automated_quality_approved(failed) is False


def test_routing_is_language_topic_and_identity_scoped_and_fails_on_tie():
    selected = automation.select_channel_profile(
        _source(),
        [
            _profile(),
            _profile(
                'UC_channel_english',
                route_label='merak-en',
                languages=['en'],
            ),
        ],
        connected_channel_ids={'UC_channel_alpha', 'UC_channel_english'},
    )
    assert selected['channel_id'] == 'UC_channel_alpha'

    ambiguous = automation.select_channel_profile(
        _source(label=''),
        [
            _profile('UC_channel_one', route_label='', topic_keywords=[]),
            _profile('UC_channel_two', route_label='', topic_keywords=[]),
        ],
    )
    assert ambiguous is None


def test_series_number_is_atomic_unique_and_idempotent(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    first = automation.reserve_series_number(
        'UC_channel_alpha', 'ucak-sirlari', 'source-task-one', total=2
    )
    replay = automation.reserve_series_number(
        'UC_channel_alpha', 'ucak-sirlari', 'source-task-one', total=2
    )
    second = automation.reserve_series_number(
        'UC_channel_alpha', 'ucak-sirlari', 'source-task-two', total=2
    )
    assert (first, replay, second) == (1, 1, 2)
    with pytest.raises(automation.SeriesExhaustedError):
        automation.reserve_series_number(
            'UC_channel_alpha', 'ucak-sirlari', 'source-task-three', total=2
        )


def test_publish_plan_freezes_full_metadata_and_series(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    plan = automation.build_publish_plan(
        'source-task-plan',
        _source(),
        _profile(),
    )
    assert plan['target_channel_id'] == 'UC_channel_alpha'
    assert plan['title'].endswith('(1/8)')
    assert plan['series']['number'] == 1
    assert plan['series']['total'] == 8
    assert plan['tags'] == ['havacılık', 'bilim', 'merak', 'uçak']
    assert '#Uçak #Bilim #Shorts' in plan['description']
    assert plan['quality_snapshot'] == {
        'quality_disposition': 'automated_qc_pass',
        'manual_qa_required': False,
    }
    assert 'connection' not in json.dumps(plan).casefold()


def test_metadata_failure_happens_before_series_number_is_consumed(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    source = _source()
    source['result']['publish_metadata']['description'] = ''
    with pytest.raises(automation.MetadataValidationError):
        automation.build_publish_plan(
            'source-task-invalid',
            source,
            _profile(),
        )
    assert not any(key.startswith(automation.SERIES_COUNTER_PREFIX) for key in client.values)


def test_profile_write_is_revision_safe(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    saved = automation.save_channel_profile(
        'UC_channel_alpha',
        _profile(),
    )
    assert saved['auto_publish'] is True
    with pytest.raises(automation.ProfileConflictError):
        automation.save_channel_profile(
            'UC_channel_alpha',
            _profile(channel_identity='Changed'),
            expected_revision='stale-revision',
        )
    updated = automation.save_channel_profile(
        'UC_channel_alpha',
        _profile(channel_identity='Changed'),
        expected_revision=saved['profile_revision'],
    )
    assert updated['channel_identity'] == 'Changed'
    assert updated['profile_revision'] != saved['profile_revision']


def test_production_profile_defaults_and_partial_update_preserve_existing_fields(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    saved = automation.save_channel_profile('UC_channel_alpha', _profile())
    assert saved['production_enabled'] is False
    assert saved['production_topics'] == []
    assert saved['production_interval_hours'] == 24
    updated = automation.save_channel_profile(
        'UC_channel_alpha',
        {'production_enabled': True, 'production_topics': 'Kabin ışıkları\nMotorlar, kanatlar\n'},
        expected_revision=saved['profile_revision'],
    )
    assert updated['production_topics'] == ['Kabin ışıkları', 'Motorlar, kanatlar']
    assert updated['series_id'] == saved['series_id']
    assert updated['auto_publish'] is True
    with pytest.raises(automation.ProfileConflictError):
        automation.save_channel_profile(
            'UC_channel_alpha', {'production_enabled': False},
            expected_revision=saved['profile_revision'],
        )
    assert automation.get_channel_profile('UC_channel_alpha')['production_enabled'] is True


@pytest.mark.parametrize('fields', [
    {'production_enabled': 'false'}, {'production_enabled': 1},
    {'production_interval_hours': True}, {'production_interval_hours': '24'},
    {'production_interval_hours': 5}, {'production_interval_hours': 169},
    {'production_topics': ['a'] * 61}, {'production_topics': ['a' * 241]},
    {'production_topics': [1]}, {'production_topics': {'topic': 'wrong shape'}},
])
def test_production_profile_rejects_invalid_bounds_and_types(fields):
    with pytest.raises(ValueError):
        automation._profile_payload('UC_channel_alpha', fields)


def test_production_profile_accepts_topic_and_interval_boundaries():
    payload = automation._profile_payload('UC_channel_alpha', {
        'production_topics': [f'{index} ' + 'x' * 237 for index in range(60)],
        'production_interval_hours': 6,
    })
    assert len(payload['production_topics']) == 60
    assert payload['production_interval_hours'] == 6
    assert automation._profile_payload('UC_channel_alpha', {
        'production_interval_hours': 168,
    })['production_interval_hours'] == 168


def test_explicit_production_channel_never_falls_back_to_another_profile():
    source = _source()
    source['spec']['production_channel_id'] = 'UC_channel_chosen'
    chosen = _profile('UC_channel_chosen', route_label='different', topic_keywords=['space'])
    assert automation.select_channel_profile(source, [_profile(), chosen]) is chosen
    assert automation.select_channel_profile(source, [_profile()]) is None
    assert automation.select_channel_profile(source, [dict(chosen, auto_publish=False)]) is None
    source['spec']['production_scheduled'] = True
    assert automation.select_channel_profile(source, [chosen]) is None
    assert automation.select_channel_profile(source, [dict(chosen, production_enabled=True)])


def test_publish_metadata_uses_actual_source_language_before_profile_default(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    source = _source()
    source['result']['language'] = 'en'
    profile = _profile(default_language='tr', languages=['tr', 'en'])
    plan = automation.build_publish_plan('source-language', source, profile)
    assert plan['default_language'] == 'en'
    source['result'].pop('language')
    source['spec']['language'] = 'en'
    assert automation.build_publish_plan('source-language-two', source, profile)['default_language'] == 'en'


@pytest.mark.parametrize('language', ['de', 'not a language'])
def test_disallowed_source_language_blocks_before_consuming_series_number(monkeypatch, language):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    source = _source()
    source['result']['language'] = language
    with pytest.raises(automation.MetadataValidationError):
        automation.build_publish_plan('source-language-wrong', source, _profile())
    assert client.values == {}
