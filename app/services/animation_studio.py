"""A separate family-animation catalogue and durable editorial queue.

Browsing is read-only. Preparing a day creates editorial work, never a paid
generation, a QA pass, a YouTube upload or a social publication. Existing
documentary profiles, money journals and channel queues are not modified.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo
from zipfile import ZipFile, ZIP_DEFLATED

import redis

from app.config import settings

PREFIX = 'youtube_studio:{animation}:v1:'
CONFIG = PREFIX + 'config'
QUEUE = PREFIX + 'queue'
DAY = PREFIX + 'day:'
BRAND_ID = 'puppy'
ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'animation' / BRAND_ID
LANGUAGES = {'en': 'İngilizce', 'tr': 'Türkçe', 'es': 'İspanyolca', 'pt-BR': 'Portekizce (Brezilya)',
    'fr': 'Fransızca', 'de': 'Almanca', 'ar': 'Arapça', 'hi': 'Hintçe', 'id': 'Endonezce',
    'ko': 'Korece', 'ja': 'Japonca', 'it': 'İtalyanca', 'nl': 'Felemenkçe', 'sv': 'İsveççe',
    'da': 'Danca', 'nb': 'Norveççe', 'fi': 'Fince', 'sw': 'Svahili', 'zu': 'Zulu', 'bn': 'Bengalce'}
PHASES = {'outline': 'Hikâye taslağı', 'script_ready': 'Senaryo hazır', 'assets_pending': 'Görseller hazırlanacak',
    'producing': 'Animasyon üretiliyor', 'quality_review': 'Görüntü ve ses kontrolü',
    'ready': 'Yayına hazır', 'published': 'Yayımlandı', 'attention': 'Kontrol gerekli'}
NEXT_ADVENTURES = (
    ('the-cloud-that-stayed', 'Gitmek İstemeyen Bulut', 'Küçük bir bulut sürekli Papi’yi takip eder; Papi burnunun peşinden rüzgârı arar.',
     ['Peşimdeki Bulut', 'Rüzgâr Nerede?', 'Bir Tüyün Peşinde', 'Dönen Fırıldak', 'Gökyüzüne Merhaba']),
    ('the-missing-sound', 'Kaybolan Ses', 'Köyün rüzgâr çanı ses çıkarmayınca arkadaşlar eksik parçayı bulur; her deneme başka bir komik ses çıkarır.',
     ['Sessiz Sabah', 'Yanlış Nota', 'Çınlayan Yaprak', 'Küçük Taş', 'Birlikte Bir Melodi']),
    ('the-too-big-gift', 'Kocaman Bir Hediye', 'Papi’nin getirdiği kocaman hediye Mika’nın kapısından geçmez; arkadaşlar onu birlikte paylaşır.',
     ['Kapıya Sığmadı', 'Biraz Döndür', 'Paketin İçinde', 'Küçük Parçalar', 'Herkese Bir Parça']),
    ('the-upside-down-picnic', 'Ters Giden Piknik', 'Esinti, yaprak masa örtüsünü ve küçük tabakları taşır; arkadaşlar doğayla yarışmak yerine ona uyum sağlar.',
     ['Uçan Masa Örtüsü', 'Yuvarlanan Tabak', 'Gölgedeki Yer', 'Bir Dal Daha', 'En Güzel Piknik']),
    ('the-little-footprints', 'Minik Ayak İzleri', 'Yosundaki küçük izlerin peşinden giden arkadaşlar, yuvası için yumuşak yaprak arayan bir misafirle tanışır.',
     ['Bu İz Kimin?', 'Papi’nin Tahmini', 'İki Küçük Yaprak', 'Yeni Komşu', 'Hoş Geldin']),
    ('the-wobbly-wheel', 'Sallanan Tekerlek', 'Mika’nın küçük arabası düz gidemez; Papi sallanan tekerleği koklayınca araya sıkışan minicik taşı fark eder.',
     ['Sağa Sola', 'Kocaman Çözüm', 'Biraz Dinleyelim', 'Minicik Taş', 'Yol Arkadaşı'])
)


class AnimationError(ValueError):
    pass


def require(value, reason='animation_invalid'):
    if not value:
        raise AnimationError(reason)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
        socket_timeout=3, socket_connect_timeout=3)


def brand():
    return json.loads((ASSETS / 'brand.json').read_text(encoding='utf-8'))


def pilot():
    result = json.loads((ASSETS / 'pilot.json').read_text(encoding='utf-8'))
    validate_adventure(result)
    return result


def validate_adventure(adventure):
    require(type(adventure) is dict and adventure.get('brand_id') == BRAND_ID
        and type(adventure.get('episodes')) is list and 4 <= len(adventure['episodes']) <= 5)
    require(re.fullmatch(r'[a-z0-9-]{3,80}', str(adventure.get('id', ''))))
    characters = {row['id'] for row in brand()['characters']}
    for number, episode in enumerate(adventure['episodes'], 1):
        require(episode.get('number') == number and episode.get('hook') and episode.get('payoff'))
        require(type(episode.get('shots')) is list and 3 <= len(episode['shots']) <= 6)
        length = 0; cues = 0
        for index, shot in enumerate(episode['shots'], 1):
            require(shot.get('id') == f's{index:02d}' and type(shot.get('seconds')) is int and 4 <= shot['seconds'] <= 10)
            require(all(isinstance(shot.get(k), str) and 10 <= len(shot[k]) <= 1800
                        for k in ('action', 'start_state', 'end_state', 'camera')))
            length += shot['seconds']; previous = 0
            for cue in shot.get('dialogue', []):
                require(cue.get('speaker') in characters and type(cue.get('text')) is str
                    and 1 <= len(cue['text']) <= 100 and '<' not in cue['text'] and '-->' not in cue['text'])
                start, end = cue.get('start'), cue.get('end')
                require(type(start) in (int, float) and type(end) in (int, float)
                    and previous <= start < end <= shot['seconds'])
                previous = end; cues += 1
        require(20 <= length <= 45 and 1 <= cues <= 10)
    require(adventure['episodes'][-1]['next_question'] == '', 'animation_finale_unresolved')


def initial_config():
    bible = brand()
    return {'version': 1, 'revision': 'new', 'brand_id': bible['id'], 'planning_enabled': True,
        'episodes_per_day': 5, 'stock_target': 10, 'timezone': 'Europe/Istanbul',
        'subtitle_languages': bible['language_targets'], 'dub_languages': bible['first_dub_wave'],
        'youtube_channel_id': None, 'social_autopublish': True,
        'youtube_publish_path': 'existing_youtube_automation', 'made_for_kids': True}


def _validate_config(value):
    require(type(value) is dict and set(value) == set(initial_config()))
    require(value['version'] == 1 and value['brand_id'] == BRAND_ID
        and type(value['planning_enabled']) is bool and type(value['social_autopublish']) is bool
        and value['episodes_per_day'] in (4, 5) and type(value['episodes_per_day']) is int
        and type(value['stock_target']) is int and value['episodes_per_day'] <= value['stock_target'] <= 20
        and value['made_for_kids'] is True and value['youtube_publish_path'] == 'existing_youtube_automation')
    require(value['youtube_channel_id'] is None or re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value['youtube_channel_id']))
    for key in ('subtitle_languages', 'dub_languages'):
        require(type(value[key]) is list and 1 <= len(value[key]) <= len(LANGUAGES)
            and len(set(value[key])) == len(value[key]) and all(v in LANGUAGES for v in value[key]))
    require(set(value['dub_languages']) <= set(value['subtitle_languages']))
    require(value['timezone'] in {'Europe/Istanbul', 'UTC'})


def read_config(*, store=None):
    raw = (store or client()).get(CONFIG)
    value = json.loads(raw) if raw else initial_config()
    _validate_config(value)
    return value


def save_config(revision, changes, *, store=None):
    require(type(changes) is dict and set(changes) <= {'planning_enabled', 'episodes_per_day', 'stock_target',
        'timezone', 'subtitle_languages', 'dub_languages', 'social_autopublish'})
    store = store or client()
    with store.pipeline() as pipe:
        pipe.watch(CONFIG)
        config = read_config(store=pipe)
        require(config['revision'] == revision, 'animation_changed')
        config.update(deepcopy(changes), revision=str(uuid4()))
        _validate_config(config)
        pipe.multi(); pipe.set(CONFIG, encoded(config))
        require(pipe.execute() == [True], 'animation_save_uncertain')
    return config


def queue(*, store=None):
    raw = (store or client()).get(QUEUE)
    rows = json.loads(raw) if raw else []
    require(type(rows) is list and len(rows) <= 500, 'animation_queue_invalid')
    for row in rows:
        require(type(row) is dict and row.get('phase') in PHASES and row.get('brand_id') == BRAND_ID)
    return rows


def prepare_day(*, store=None, now=None):
    """Once per local day; queued drafts also count toward the stock ceiling."""
    store, now = store or client(), now or datetime.now(timezone.utc)
    require(now.tzinfo is not None)
    with store.pipeline() as pipe:
        pipe.watch(CONFIG, QUEUE)
        config = read_config(store=pipe)
        if not config['planning_enabled']:
            return {'status': 'paused', 'created': 0}
        today = now.astimezone(ZoneInfo(config['timezone'])).date().isoformat()
        key = DAY + today; pipe.watch(key)
        if pipe.get(key):
            return {'status': 'already_prepared', 'created': 0}
        rows = queue(store=pipe)
        available = config['stock_target'] - sum(row['phase'] != 'published' for row in rows)
        if available < config['episodes_per_day']:
            return {'status': 'stock_full', 'created': 0}
        known = {row['adventure_id'] for row in rows}
        if not known:
            adventure = pilot()
            # The pilot's five-episode story must not lose its resolution just
            # because a future cadence is changed to four. Keep it intact.
            if available < 5:
                return {'status': 'pilot_requires_five_stock_slots', 'created': 0}
            additions = [{'brand_id': BRAND_ID, 'id': 'the-little-light-' + str(ep['number']),
                'adventure_id': adventure['id'], 'adventure_title': adventure['title_tr'],
                'number': ep['number'], 'total': 5, 'title': ep['title_tr'],
                'phase': 'script_ready', 'prepared_day': today, 'story_sha256': digest(ep),
                'hook': ep['hook'], 'payoff': ep['payoff'], 'output': None,
                'duration_seconds': sum(s['seconds'] for s in ep['shots'])} for ep in adventure['episodes']]
        else:
            choice = next((r for r in NEXT_ADVENTURES if r[0] not in known), None)
            if choice is None:
                return {'status': 'new_story_required', 'created': 0}
            identity, title, idea, titles = choice
            count = config['episodes_per_day']
            selected = titles if count == 5 else [*titles[:3], titles[-1]]
            additions = [{'brand_id': BRAND_ID, 'id': identity + '-' + str(i), 'adventure_id': identity,
                'adventure_title': title, 'number': i, 'total': count, 'title': name,
                'phase': 'outline', 'prepared_day': today, 'story_sha256': None,
                'hook': idea, 'payoff': '', 'output': None, 'duration_seconds': 32}
                for i, name in enumerate(selected, 1)]
        require(len(rows) + len(additions) <= 500, 'animation_archive_required')
        receipt = {'version': 1, 'day': today, 'created_at': now.isoformat(), 'episodes': [r['id'] for r in additions],
            'provider_requests': 0, 'publication_requests': 0}
        pipe.multi(); pipe.set(QUEUE, encoded(rows + additions)); pipe.set(key, encoded(receipt), nx=True)
        require(pipe.execute() == [True, True], 'animation_prepare_uncertain')
    return {'status': 'prepared', 'created': len(additions), 'day': today}


def episode_script(episode_id):
    for episode in pilot()['episodes']:
        if episode_id == 'the-little-light-' + str(episode['number']):
            return episode
    raise AnimationError('animation_script_not_ready')


def dialogue_cues(episode):
    result, offset = [], 0.0
    for shot in episode['shots']:
        for index, cue in enumerate(shot.get('dialogue', []), 1):
            result.append({**cue, 'id': shot['id'] + f'-{index}',
                'start': round(offset + cue['start'], 3), 'end': round(offset + cue['end'], 3)})
        offset += shot['seconds']
    return result


def _time(seconds, sep=','):
    millis = round(seconds * 1000)
    return f'{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02}{sep}{millis % 1000:03}'


def subtitles(episode, language, translations, *, webvtt=False):
    require(language in LANGUAGES and type(translations) is dict, 'animation_translation_invalid')
    cues = dialogue_cues(episode)
    require(set(translations) == {r['id'] for r in cues}, 'animation_translation_missing_cues')
    parts = ['WEBVTT\n'] if webvtt else []
    for index, cue in enumerate(cues, 1):
        text = translations[cue['id']]
        require(type(text) is str and 1 <= len(text.strip()) <= 150 and '<' not in text
            and '-->' not in text and not any(ord(c) < 32 for c in text), 'animation_translation_invalid')
        # Candidate subtitles remain unapproved until language/timing review.
        sep = '.' if webvtt else ','
        parts.append(f'{index}\n{_time(cue["start"], sep)} --> {_time(cue["end"], sep)}\n{text.strip()}\n')
    return '\n'.join(parts)


def language_status(episode, config):
    available = set(translation_candidates(episode))
    return [{'code': code, 'name': LANGUAGES[code], 'subtitles': 'source_script' if code == 'en' else 'draft' if code in available else 'translation_pending',
        'dubbing': 'voice_review_pending' if code in config['dub_languages'] else 'not_requested',
        'youtube_subtitle_track': 'not_uploaded', 'youtube_audio_track': 'not_uploaded'}
        for code in config['subtitle_languages']]


def translation_candidates(episode):
    adventure = pilot()
    document = json.loads((ASSETS / 'translation-lines.json').read_text(encoding='utf-8'))
    require(document['source_sha256'] == digest(adventure), 'animation_translation_outdated')
    require(episode in adventure['episodes'], 'animation_translation_outdated')
    cues = dialogue_cues(episode)
    offset = sum(len(dialogue_cues(ep)) for ep in adventure['episodes'][:episode['number']-1])
    count = sum(len(dialogue_cues(ep)) for ep in adventure['episodes'])
    result = {'en': {row['id']: row['text'] for row in cues}}
    for code, lines in document['languages'].items():
        require(code in LANGUAGES and type(lines) is list and len(lines) == count)
        result[code] = {row['id']: line for row, line in zip(cues, lines[offset:offset+len(cues)], strict=True)}
        subtitles(episode, code, result[code])
    return result


def subtitle_package(episode_id):
    episode = episode_script(episode_id)
    translations = translation_candidates(episode)
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
        for code, cues in translations.items():
            for suffix in ('srt', 'vtt'):
                archive.writestr(f'{episode_id}.{code}.{suffix}', subtitles(episode, code, cues, webvtt=suffix == 'vtt'))
        archive.writestr('manifest.json', encoded({'episode': episode_id, 'story_sha256': digest(episode),
            'languages': list(translations), 'status': 'draft', 'timing': 'script_cues_not_recorded_voice',
            'native_language_review': 'pending', 'audio_tracks': [], 'youtube_uploaded': False}))
        archive.writestr('README.txt', 'Altyazı taslaklarıdır. Zamanlar senaryo planına bağlıdır; gerçek ses kaydıyla henüz eşleştirilmedi.\n'
            'Dublaj, ana dil kontrolü ve YouTube yüklemesi yapılmış değildir.\n'
            'Puppy karakterinin adı her dilde Papi olarak okunur.\n')
    return stream.getvalue()


def social_copy(episode, platform):
    require(platform in {'instagram', 'tiktok'}, 'animation_social_platform_invalid')
    number = episode['number']
    return {'text': f'{episode["title"]} · {number}/5\nA tiny adventure with Puppy (Papi) and friends. '
        + ('A gentle story to enjoy together.\n#PuppyAndFriends #Animation #FamilyStories' if platform == 'instagram'
           else 'Small paws, big adventures.\n#PuppyAndFriends #Animation'),
        'platform': platform, 'format': 'reel' if platform == 'instagram' else 'video',
        'comments_requested': False, 'watermark': False}


def presentation(*, store=None):
    store = store or client()
    config, rows = read_config(store=store), queue(store=store)
    return {'brand': brand(), 'config': config, 'episodes': rows, 'pilot': pilot(),
        'counts': {'planned': len(rows), 'ready': sum(r['phase'] == 'ready' for r in rows),
                   'published': sum(r['phase'] == 'published' for r in rows)},
        'production_status': 'character_motion_pilot_required',
        'youtube_status': 'channel_connection_required' if not config['youtube_channel_id'] else 'connected'}
