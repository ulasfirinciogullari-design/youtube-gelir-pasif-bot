import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest

from app.services.production_editorial import choose_production_editorial


@pytest.mark.parametrize('topic', [
    'Kasadaki ilk barkod bir sakız paketiydi: 26 Haziran 1974, Troy. Modern kasa görüntüsünü arşiv diye sunma.',
    'Eurodaki köprü hangi şehirde? Tasarımlar belirli gerçek köprülerin resmi değil.',
    'Türkiye bir gecede altı sıfırı nasıl sildi? Bir milyon eski TL bir YTL oldu; bu tek başına zenginleşme değildi.',
    'Bir sent neden kendinden pahalı? Üretim maliyeti 3,69 sent. Eski paralar geçerli; üretim ve dolaşımı karıştırma.',
    'Banka kapalıyken para çekmek: ilk ATM, Enfield, 27 Haziran 1967. Modern görüntüyü arşiv diye sunma.',
    'Market arabası fikri: Sylvan Goldman ve tekerlekli sepet. Güncel market görüntüsüyle anlat.',
    'Doların üzerindeki yıl basıldığı yıl mı? Seri yılı tasarım onayı veya imza değişimini gösterir.',
    'Silgi grafiti nasıl toplar?',
    'What does the date on a dollar bill mean?',
    'Warum sind die Brücken auf den Euro-Banknoten fiktiv?',
    '¿Qué significa el año en un billete?',
    'ماذا تعني السنة المطبوعة على الورقة النقدية؟',
])
def test_single_curiosity_and_current_seven_topics_stay_short(topic):
    decision = choose_production_editorial(topic)
    assert decision['format'] == 'shorts'
    assert decision['duration_minutes'] == 0.5
    assert decision['reason_code'] == 'focused_or_unspecified_scope'


@pytest.mark.parametrize(('topic', 'reason', 'signals'), [
    ('Elektrikli ve benzinli otomobilleri maliyet, performans ve güvenlik açısından karşılaştır.',
     'multi_dimension_comparison', ['cost', 'performance', 'risk']),
    ('Compare rail and air travel by cost, speed, safety and environmental effects.',
     'multi_dimension_comparison', ['cost', 'impact', 'performance', 'risk']),
    ('Enflasyonun nedenlerini, tarihini ve sonuçlarını örneklerle ayrıntılı anlat.',
     'multi_part_explanation', ['history', 'impact', 'mechanism']),
    ('An in-depth explanation of batteries: history, mechanism and production.',
     'multi_part_explanation', ['history', 'mechanism', 'production']),
    ('Bu konuyu yatay video olarak işle.', 'explicit_long_form_direction', ['long_form_direction']),
    ('Format: landscape. Explain this topic.', 'explicit_long_form_direction', ['long_form_direction']),
])
def test_explicit_richer_scope_selects_bounded_normal_video(topic, reason, signals):
    decision = choose_production_editorial(topic)
    assert decision == {
        'version': 1, 'format': 'landscape', 'duration_minutes': 3.0,
        'reason_code': reason, 'reason': decision['reason'], 'scope_signals': signals,
    }
    assert decision == choose_production_editorial(topic)


@pytest.mark.parametrize('topic', [
    'Bir sent neden kendinden pahalı? https://example.org/comparison/cost-speed-risk/in-depth',
    'Bölüm 1/8: Doların seri yılı neyi anlatıyor?',
    'Belgesel değil; doların tek bir ayrıntısı.',
    'Detaylı karşılaştırma istemiyorum: maliyet, performans ve güvenlik.',
    'Do not compare cost, speed and safety; explain one small detail.',
    'Long-form video değil; bir banknot ayrıntısı.',
    'Doların tasarımını karşılaştır; tek bir maliyet farkı yeterli.',
    'Maliyet, performans ve güvenlik sözcükleri bu terimler listesinde geçiyor.',
    'A detailed answer to one question about cost.',
    'A long video appeared on the phone.',
    'Seri 2/8. https://example.org/a;in-depth;history;mechanism;production',
])
def test_urls_negation_series_numbers_and_keyword_mentions_do_not_expand_scope(topic):
    assert choose_production_editorial(topic)['format'] == 'shorts'


@pytest.mark.parametrize('identity', [
    'Yalnızca Shorts', 'Shorts kanalı', 'Sadece kısa videolar', 'Shorts only',
])
def test_explicit_channel_short_constraint_takes_precedence(identity):
    decision = choose_production_editorial(
        'Maliyet, performans ve güvenlik açısından ayrıntılı karşılaştır.', identity,
    )
    assert decision['format'] == 'shorts'
    assert decision['reason_code'] == 'explicit_short_direction'


def test_channel_theme_does_not_supply_missing_breadth():
    decision = choose_production_editorial(
        'Bir banknotun tasarımını anlat.',
        'Ayrıntılı maliyet, performans, güvenlik karşılaştırmaları yapan kanal.',
    )
    assert decision['format'] == 'shorts'


@pytest.mark.parametrize('direction', [
    '30 saniyelik Shorts:', 'Shorts:', 'Kısa video olarak', 'As a short-form video:',
])
def test_explicit_topic_short_format_precedes_multi_dimension_scope(direction):
    decision = choose_production_editorial(
        direction + ' maliyet, performans ve güvenlik açısından karşılaştır.',
    )
    assert decision['format'] == 'shorts'
    assert decision['duration_minutes'] == 0.5
    assert decision['reason_code'] == 'explicit_short_direction'


@pytest.mark.parametrize('direction', [
    '30 saniyelik Shorts istemiyorum', 'Kısa video olarak değil', 'Shorts are not wanted',
])
def test_negated_short_direction_does_not_override_explicit_broader_scope(direction):
    decision = choose_production_editorial(
        direction + '; maliyet, performans ve güvenlik açısından karşılaştır.',
    )
    assert decision['format'] == 'landscape'
    assert decision['duration_minutes'] == 3.0


@pytest.mark.parametrize(('topic', 'identity'), [
    ('', ''), ('  ', ''), (None, ''), ({'topic': 'x'}, ''), ('x' * 241, ''),
    ('Valid topic', None), ('Valid topic', 'x' * 241),
])
def test_invalid_editorial_inputs_fail_closed(topic, identity):
    with pytest.raises(ValueError, match='Invalid scheduled editorial brief'):
        choose_production_editorial(topic, identity)


@pytest.fixture
def production():
    path = Path(__file__).resolve().parents[1] / 'app/services/channel_production.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom)
        and node.module in {'app.config', 'app.services.studio_state'}
    )]
    namespace = {
        'settings': SimpleNamespace(redis_url='redis://not-used'),
        'JOB_PREFIX': 'youtube_studio:job:', 'JOB_INDEX': 'youtube_studio:jobs',
        'JOB_TTL_SECONDS': 90 * 24 * 3600,
    }
    exec(compile(tree, str(path), 'exec'), namespace)
    client = fakeredis.FakeRedis(decode_responses=True)
    namespace['_redis'] = lambda: client
    return SimpleNamespace(**namespace), client


@pytest.mark.parametrize(('topic', 'format_name', 'duration'), [
    ('Bir doların seri yılı neyi anlatır? https://www.uscurrency.gov/denominations/bank-note-identifiers', 'shorts', 0.5),
    ('Enflasyonun nedenlerini, tarihini ve sonuçlarını ayrıntılı anlat. https://example.org/facts', 'landscape', 3.0),
])
def test_scheduled_job_and_dispatch_freeze_the_same_audited_editorial_choice(production, topic, format_name, duration):
    module, client = production
    channel = 'UC_channel_editorial'
    connection = {'id': channel, 'connection_id': 'connection-editorial-one'}
    profile = {
        'channel_id': channel, 'profile_revision': 'profile-editorial-one',
        'channel_identity': 'Kaynaklı Türkçe anlatım.', 'route_label': 'facts-tr',
        'default_language': 'tr', 'production_enabled': True, 'auto_publish': True,
        'production_topics': [topic], 'production_interval_hours': 24,
    }
    before = deepcopy(profile)
    client.set(module.PROFILE_PREFIX + channel, json.dumps(profile))
    client.set(module.OAUTH_CHANNEL_PREFIX + channel, json.dumps(connection))
    client.set(module.OAUTH_CREDENTIAL_PREFIX + channel, 'opaque-test-credential')
    client.sadd(module.OAUTH_CHANNEL_INDEX, channel)
    reservation = module.reserve_due_production(profile, connection, now=1000)
    assert reservation['status'] == 'reserved'
    args = reservation['args']
    job_key = module.JOB_PREFIX + reservation['task_id']
    frozen_raw = client.get(job_key)
    job = json.loads(frozen_raw)
    expected_brief = topic + '\n\nChannel editorial direction: ' + profile['channel_identity']
    assert args[0] == expected_brief
    assert args[1] == duration
    assert args[4]['format'] == format_name
    assert job['spec'] == {
        'topic': args[0], 'duration_minutes': args[1], 'language': args[2],
        'channel_id': args[3], **args[4],
    }
    assert args[4]['production_editorial']['format'] == format_name
    assert args[4]['production_editorial']['duration_minutes'] == duration
    assert args[4]['production_editorial']['version'] == 1
    assert args[4]['quality_threshold'] == 86
    assert args[4]['visual_mix'] == 'real_first'
    assert args[4]['mode'] == 'production'
    assert args[4]['publish_after_render'] is True
    assert args[4]['production_connection_id'] == connection['connection_id']
    assert profile == before
    assert module.reserve_due_production(profile, connection, now=1001)['status'] != 'reserved'
    assert client.get(job_key) == frozen_raw


def test_selector_has_no_provider_storage_or_environment_dependencies():
    path = Path(__file__).resolve().parents[1] / 'app/services/production_editorial.py'
    imports = [node for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    modules = {node.module if isinstance(node, ast.ImportFrom) else alias.name
               for node in imports for alias in (node.names if isinstance(node, ast.Import) else [None])}
    assert modules == {'__future__', 're', 'unicodedata'}
