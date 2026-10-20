"""Cached search-interest observations, never factual evidence or reach promises.

Only fixed public Google Trends RSS endpoints are read. Daily snapshots retain
seven days of observed interest; an unavailable feed cannot erase prior data or
become a dependency of the production queue. No paid search or YouTube quota.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
import unicodedata
from xml.etree import ElementTree as ET

import httpx
import redis

from app.config import settings

REGIONS = ('TR', 'US', 'GB', 'BR', 'IN', 'MX')
PREFIX = 'youtube_studio:audience_trends:v1:'
MAX_BYTES = 1_000_000
REFRESH_SECONDS = 2 * 3600
FRESH_SECONDS = 6 * 3600
HT = '{https://trends.google.com/trending/rss}'
BUSINESS = ('economy', 'inflation', 'interest rate', 'federal reserve', 'ecb', 'tcmb',
    'ekonomi', 'enflasyon', 'faiz', 'banka', 'bank', 'currency', 'dollar', 'dolar',
    'tariff', 'retail', 'supply chain', 'chip', 'semiconductor', 'yapay zeka',
    'artificial intelligence', 'tesla', 'apple', 'iphone', 'nvidia', 'microsoft',
    'google', 'amazon', 'openai', 'samsung', 'toyota', 'ikea', 'costco', 'lego',
    'nintendo', 'coca cola', 'mcdonald', 'fedex', 'starbucks', 'netflix', 'spotify',
    'electric vehicle', 'battery', 'perakende', 'lojistik', 'business')
BUSINESS_CHANNELS = {'UC5v9AvNtD3PTLgo6m1jROOA', 'UCgvESYtYbn2w9R2ExBOF_cw'}
STOP = {'the', 'and', 'how', 'why', 'with', 'this', 'that', 'what', 'from', 'into',
    'bir', 'icin', 'ile', 'nasil', 'neden', 'kanal', 'video', 'shorts', 'serisi',
    'dunya', 'dunyasi', 'world', 'everyday', 'life', 'gibi', 'olan', 'about'}


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
        socket_connect_timeout=2, socket_timeout=3)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def terms(text):
    normalized = unicodedata.normalize('NFKD', str(text).casefold().replace('ı', 'i'))
    return re.findall(r'[^\W_]+', ''.join(c for c in normalized if not unicodedata.combining(c)))


def _plain(text, limit=180):
    return ' '.join(re.sub(r'<[^>]*>', '', str(text or '')).split())[:limit]


def parse_feed(raw, region, now):
    if region not in REGIONS or type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('trend_feed_invalid')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('trend_feed_invalid')
    root = ET.fromstring(raw)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('trend_feed_invalid')
    rows = []
    for item in root.findall('./channel/item')[:40]:
        try:
            title = _plain(item.findtext('title'))
            started = parsedate_to_datetime(item.findtext('pubDate') or '')
            if started.tzinfo is None:
                continue
            started = started.astimezone(timezone.utc)
            if not (2 <= len(title) <= 180 and now - timedelta(days=7) <= started <= now + timedelta(hours=1)):
                continue
            traffic = _plain(item.findtext(HT + 'approx_traffic'), 30)
            match = re.fullmatch(r'([0-9]+(?:[.,][0-9]+)?)\s*([KMB]?)\+?', traffic, re.I)
            volume = int(float(match[1].replace(',', '.')) * {'': 1, 'K': 1000, 'M': 1_000_000, 'B': 1_000_000_000}[match[2].upper()]) if match else None
            rows.append({'term': title, 'region': region, 'started_at': started.isoformat(),
                'traffic_label': traffic, 'traffic_lower_bound': volume,
                'observed_at': now.isoformat(), 'source': 'google_trends_rss'})
        except (TypeError, ValueError, OverflowError):
            continue
    return rows


def _fetch(region, now):
    url = 'https://trends.google.com/trending/rss?geo=' + region
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', url, headers={'Accept': 'application/rss+xml, application/xml',
                'User-Agent': 'YouTubeEditorialTrends/1.0'}) as response:
            if response.status_code != 200:
                raise ValueError('trend_feed_unavailable')
            chunks, count = [], 0
            for chunk in response.iter_bytes():
                count += len(chunk)
                if count > MAX_BYTES:
                    raise ValueError('trend_feed_oversized')
                chunks.append(chunk)
    raw = b''.join(chunks)
    return {'region': region, 'observed_at': now.isoformat(), 'body_sha256': hashlib.sha256(raw).hexdigest(),
        'rows': parse_feed(raw, region, now)}


def refresh(*, client=None, now=None):
    client, now = client or _client(), now or datetime.now(timezone.utc)
    # Throttle protects a free GET only; expiry never grants generation/replay.
    if not client.set(PREFIX + 'refresh', now.isoformat(), nx=True, ex=REFRESH_SECONDS):
        return {'status': 'not_due'}
    def read(region):
        try:
            return _fetch(region, now)
        except Exception:
            return {'region': region, 'error': 'source_unavailable'}
    with ThreadPoolExecutor(max_workers=3) as pool:
        observed = list(pool.map(read, REGIONS))
    day = now.date().isoformat()
    for row in observed:
        if 'rows' in row:
            client.set(PREFIX + 'region:' + row['region'], _json(row), ex=8 * 86400)
            # Daily union remembers an earlier surge even after it leaves RSS.
            key = PREFIX + 'day:' + day + ':' + row['region']
            prior = json.loads(client.get(key) or '{}')
            for item in row['rows']:
                prior[' '.join(terms(item['term']))] = item
            ordered = sorted(prior.items(), key=lambda p: p[1]['observed_at'], reverse=True)[:400]
            client.set(key, _json(dict(ordered)), ex=8 * 86400)
    status = {'status': 'fresh' if any('rows' in r for r in observed) else 'unavailable',
        'checked_at': now.isoformat(), 'regions_ok': [r['region'] for r in observed if 'rows' in r],
        'regions_unavailable': [r['region'] for r in observed if 'error' in r]}
    client.set(PREFIX + 'status', _json(status), ex=8 * 86400)
    return status


def snapshot(*, client=None, now=None):
    client, now = client or _client(), now or datetime.now(timezone.utc)
    try:
        status = json.loads(client.get(PREFIX + 'status') or '{}')
        if status.get('status') == 'fresh' and (not status.get('checked_at')
                or (now - datetime.fromisoformat(status['checked_at'])).total_seconds() > FRESH_SECONDS):
            status['status'] = 'stale'
        keys = [PREFIX + 'day:' + (now - timedelta(days=n)).date().isoformat() + ':' + region
                for n in range(7) for region in REGIONS]
        grouped = {}
        for raw in client.mget(keys):
            for key, row in json.loads(raw or '{}').items():
                seen = datetime.fromisoformat(row['observed_at'])
                if now - timedelta(days=7) > seen or seen > now + timedelta(minutes=5):
                    continue
                item = grouped.setdefault(key, {**row, 'regions': set(), 'observed_days': set()})
                item['regions'].add(row['region']); item['observed_days'].add(seen.date().isoformat())
                if row['observed_at'] > item['observed_at']:
                    item.update({k: v for k, v in row.items() if k not in {'regions', 'observed_days'}})
        rows = []
        for row in grouped.values():
            age = (now - datetime.fromisoformat(row['observed_at'])).total_seconds()
            row.update(regions=sorted(row['regions']), observed_days=sorted(row['observed_days']),
                fresh=age <= FRESH_SECONDS,
                score=round(math.log10(1 + (row['traffic_lower_bound'] or 0))
                    + .4 * len(row['regions']) + .15 * len(row['observed_days']) - min(7, age / 86400), 2))
            rows.append(row)
        return {**status, 'source': 'Google Trends · arama ilgisi',
            'signals': sorted(rows, key=lambda row: (row['fresh'], row['score']), reverse=True)[:400]}
    except Exception:
        return {'status': 'unavailable', 'signals': [], 'regions_ok': [], 'regions_unavailable': list(REGIONS)}


def relevant(profile, *, data=None):
    """Conservative lexical niche filter; the planner must still verify fit."""
    data = snapshot() if data is None else data
    identity = ' '.join(str(profile.get(k) or '') for k in ('channel_identity', 'series_name', 'channel_title'))
    keywords = {w for w in terms(identity) if len(w) >= 4 and w not in STOP}
    business = profile.get('channel_id') in BUSINESS_CHANNELS
    selected = []
    for row in data.get('signals', []):
        text = ' ' + ' '.join(terms(row['term'])) + ' '
        matches = sorted(keywords & set(terms(row['term'])))
        matches += [word for word in BUSINESS if business and ' ' + ' '.join(terms(word)) + ' ' in text]
        if matches:
            selected.append({**row, 'niche_matches': list(dict.fromkeys(matches))[:5]})
    return selected[:12]


def planning_context(profile, *, client=None):
    from app.services.audience_strategy import read_settings
    if not read_settings(profile['channel_id'], client=client)['trend_enabled']:
        return None
    rows = [r for r in relevant(profile, data=snapshot(client=client)) if r['fresh']]
    if not rows:
        return None
    return {'basis': 'observed_google_search_interest_not_youtube_views', 'signals': rows[:8],
        'instruction': 'Prefer a current niche-relevant business question supported by actually retrieved '
            'primary sources. Search interest is a demand hint, never factual evidence or proof of YouTube '
            'ranking. Do not force unrelated trends into this channel. Preserve queued series order. '
            'Avoid repeating prior topics, sensational claims, financial advice or promised outcomes. '
            'Use evergreen alternatives when a current angle lacks two supporting sources.'}
