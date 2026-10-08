"""Conservative, deterministic format planning before a scheduled job freezes."""

from __future__ import annotations

import re
import unicodedata


_URL = re.compile(r'https?://\S+', re.IGNORECASE)
_NEGATIVE_CLAUSE = re.compile(
    r"\b(?:degil\w*|olmasin|istemiyorum|yapma\w*|anlatma\w*|"
    r"not|never|without|don['’]t)\b"
)
_SHORT_DIRECTION = re.compile(
    r'\b(?:(?:yalnizca|sadece|only)\s+(?:dikey\s+)?shorts?\b|'
    r'shorts?\s+(?:only|kanali|channel|olarak)\b|'
    r'(?:yalnizca|sadece)\s+(?:kisa|dikey)\s+videolar?\b|'
    r'format\s*:\s*(?:shorts|dikey)\b|shorts?\s*:|'
    r'30\s*(?:saniyelik|saniye|seconds?)\s+(?:shorts?|video)\b|'
    r'kisa\s+(?:bir\s+)?video\s+olarak\b|'
    r'as\s+a\s+short(?:[- ]form)?\s+video\b)'
)
_LONG_DIRECTION = re.compile(
    r'\b(?:format\s*:\s*(?:yatay|normal|landscape|long[- ]form)\b|'
    r'(?:yatay|normal|uzun)\s+(?:bir\s+)?video\s+olarak\b|'
    r'long[- ]form\s+video\b)'
)
_COMPARISON = re.compile(r'\b(?:karsilastir\w*|kiyasla\w*|compare\w*|comparison|versus|vs)\b')
_EXTENDED = re.compile(r'\b(?:kapsamli|derinlemesine|ayrintili|detayli|in[- ]depth|comprehensive|detailed)\b')
_DIMENSIONS = {
    'cost': r'\b(?:maliyet\w*|fiyat\w*|butce\w*|costs?|prices?|budget)\b',
    'performance': r'\b(?:performans\w*|hiz\w*|verim\w*|performance|speed|efficiency)\b',
    'risk': r'\b(?:guvenlik\w*|risk\w*|safety|security)\b',
    'use': r'\b(?:kullanim\w*|uyumluluk\w*|uygulama\w*|usability|compatibility|applications?)\b',
    'history': r'\b(?:tarih\w*|gelisim\w*|history|evolution)\b',
    'mechanism': r'\b(?:mekanizma\w*|isleyis\w*|nedenler\w*|mechanisms?|causes?)\b',
    'impact': r'\b(?:etkiler\w*|sonuclar\w*|impact|effects?|consequences?)\b',
    'production': r'\b(?:uretim\w*|malzeme\w*|tasarim\w*|production|materials?|design)\b',
}


def _positive_scope(value: str) -> str:
    # Citation URLs and negative/forbidden directions are not positive scope.
    text = unicodedata.normalize('NFKD', _URL.sub(' ', value).casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char)).replace('ı', 'i')
    return ' '.join(
        clause for clause in re.split(r'[.!?;\n]+', text)
        if not _NEGATIVE_CLAUSE.search(clause)
    )


def choose_production_editorial(topic: str, channel_identity: str = '', *, long_duration_minutes: int = 3) -> dict:
    """Prefer 30s Shorts; select an explicitly bounded broader format.

    This does not research facts or promise coverage/quality. Unrecognized or
    ambiguous briefs stay short. Three minutes remains the legacy default;
    eight minutes requires the new delivery policy. No existing job is edited.
    """
    if type(long_duration_minutes) is not int or long_duration_minutes not in {3, 8}:
        raise ValueError('Invalid scheduled long-form duration')
    if (
        not isinstance(topic, str) or not topic.strip() or len(topic) > 240
        or not isinstance(channel_identity, str) or len(channel_identity) > 240
    ):
        raise ValueError('Invalid scheduled editorial brief')
    scope = _positive_scope(topic)
    identity = _positive_scope(channel_identity)
    dimensions = sorted(name for name, pattern in _DIMENSIONS.items() if re.search(pattern, scope))
    format_name, duration = 'shorts', 0.5
    reason_code = 'focused_or_unspecified_scope'
    reason = 'Tek soruluk veya kapsamı sınırlı konu: 30 saniyelik Shorts.'
    signals = []
    if _SHORT_DIRECTION.search(scope) or _SHORT_DIRECTION.search(identity):
        reason_code = 'explicit_short_direction'
        reason = 'Konu veya kanal yönlendirmesi kısa/dikey içerik istiyor.'
    elif _LONG_DIRECTION.search(scope):
        format_name, duration = 'landscape', 3.0
        reason_code = 'explicit_long_form_direction'
        reason = 'Konu normal/yatay video istiyor; başlangıç süresi 3 dakika.'
        signals = ['long_form_direction']
    elif len(dimensions) >= 3 and (_COMPARISON.search(scope) or _EXTENDED.search(scope)):
        format_name, duration = 'landscape', 3.0
        comparison = bool(_COMPARISON.search(scope))
        reason_code = 'multi_dimension_comparison' if comparison else 'multi_part_explanation'
        reason = (
            'En az üç başlıklı karşılaştırma için 3 dakikalık yatay video.'
            if comparison else 'En az üç başlıklı ayrıntılı açıklama için 3 dakikalık yatay video.'
        )
        signals = dimensions
    if format_name == 'landscape' and long_duration_minutes == 8:
        duration = 8.0
        reason = 'Kaynaklı uzun video ve aynı anlatımdan üç bağımsız Shorts: 8 dakikalık üretim paketi.'
    return {
        'version': 2 if duration == 8 else 1,
        'format': format_name,
        'duration_minutes': duration,
        'reason_code': reason_code,
        'reason': reason,
        'scope_signals': signals,
    }
