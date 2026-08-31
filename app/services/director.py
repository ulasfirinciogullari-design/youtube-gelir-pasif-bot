import hashlib
import json
import re
import unicodedata
from openai import OpenAI
from app.config import settings
from app.services.gemini_critic import (
    CONTINUITY_DEICTIC_RULE,
    GEMINI_DEFAULT_MODEL,
    _contract_schema,
    run_optional_gemini_critic,
    setting_is_enabled,
)
from app.services.gemini_generation import (
    GeminiGenerationError,
    generate_gemini_json,
)
from app.services.visual_routing import preview_authored_ai_limit
from app.services.source_evidence import normalize_evidence_sources

STYLE_NOTES = {
    'documentary': 'authoritative premium documentary, restrained and evidence-led',
    'technology': 'modern technology documentary, precise, visual and human',
    'story': 'story-led narrative with escalation and payoff',
    'cinematic': 'cinematic essay with controlled reveals and recurring visual motifs',
    'explainer': 'clear causal explainer with demonstrations and comparisons',
}

_PRODUCTION_SCENES_PER_MINUTE = 7.0
_MAX_PRODUCTION_SCENES = 70
_SHORT_PREVIEW_AI_SCENE_MAX_WORDS = 13


class ImmutableNarrationSceneBudgetError(RuntimeError):
    """An exact narration cannot fit the requested single-pass scene plan."""

_EXPLICIT_SCENE_COUNT_WORDS = {
    'bir': 1,
    'iki': 2,
    'üç': 3,
    'uc': 3,
    'dört': 4,
    'dort': 4,
    'beş': 5,
    'bes': 5,
    'altı': 6,
    'alti': 6,
    'yedi': 7,
    'sekiz': 8,
    'dokuz': 9,
    'on': 10,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
}
_EXPLICIT_SCENE_COUNT_PATTERN = re.compile(
    r'\b(?:tam(?:\s+olarak)?|exactly)\s+'
    r'(?P<count>\d{1,3}|bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|'
    r'yedi|sekiz|dokuz|on|one|two|three|four|five|six|seven|eight|nine|ten)'
    r'\s+(?:sahne(?:li|lik)?|scenes?)\b',
    flags=re.IGNORECASE,
)
_EXPLICIT_SCENE_COUNT_NEGATED_BEFORE = re.compile(
    r"(?:\bnot\s+|\b(?:never|do\s+not|don't|dont|must\s+not|"
    r'should\s+not|need\s+not)\s+'
    r'(?:(?:return|use|write|create|make|produce|require|want|need)\s+)?)$',
    flags=re.IGNORECASE,
)
_EXPLICIT_SCENE_COUNT_NEGATED_AFTER = re.compile(
    r"^\s*(?:değil\b|degil\b|olmasın\b|olmasin\b|istemiyorum\b|"
    r'istenmiyor\b|kullanma(?:yın(?:ız)?|yin(?:iz)?)?\b|'
    r'yazma(?:yın(?:ız)?|yin(?:iz)?)?\b|'
    r'yapma(?:yın(?:ız)?|yin(?:iz)?)?\b|yerine\b|'
    r'(?:zorunlu|sart|gerekli|mecbur)\s+(?:değil|degil)\b|'
    r'olmas(?:ına|ina)\s+gerek\s+yok\b|'
    r'olmas(?:ını|ini)\s+istemiyorum\b|'
    r'olmas(?:ı|i)\s+gerekmiyor\b|'
    r'olmamal(?:ı|i)\b|'
    r'(?:kullan|yaz|yap)(?:ılmasın|ilmasin|ılmamalı|ilmamali)\b|'
    r"(?:is|are|was|were)\s+not\b|(?:isn't|aren't|wasn't|weren't)\b|"
    r'(?:should|must)\s+not\b)',
    flags=re.IGNORECASE,
)
_MAX_STORY_BRIEF_CHARS = 8000
_EXACT_NARRATION_QUOTE_PATTERN = re.compile(
    r'“(?P<curly>[^”]*)”|"(?P<straight>[^"]*)"|«(?P<guillemet>[^»]*)»',
    flags=re.DOTALL,
)
_EXACT_NARRATION_PREFIX_PATTERN = re.compile(
    r'(?:'
    r'(?:(?:konuşma|seslendirme|anlatım|anlatıcı)\s+metni)\s+'
    r'(?:tam\s+olarak|aynen)\s+(?:(?:şu|şöyle|aşağıdaki)\s+)?'
    r'[^:“"«]{0,120}\b(?:olsun|kullanılsın|okunsun)'
    r'|'
    r'(?:(?:spoken\s+narration|spoken\s+text|voiceover(?:\s+text)?|'
    r'narration)\s+(?:must|should|shall)\s+be\s+'
    r'(?:exactly|verbatim)(?:\s+(?:this|the\s+following))?'
    r'|use\s+(?:exactly\s+|verbatim\s+)?(?:this|the\s+following)\s+'
    r'(?:spoken\s+narration|spoken\s+text|voiceover(?:\s+text)?|narration)'
    r'(?:\s+(?:exactly|verbatim))?)'
    r')(?:\s*:?\s*|\s*;\s*[^:“"«]{1,240}:\s*)$',
    flags=re.IGNORECASE | re.UNICODE,
)
_EXACT_NARRATION_SENTENCE_PATTERN = re.compile(
    r'[^.!?…]+(?:[.!?…]+)(?=\s+|$)',
    flags=re.UNICODE,
)
_EXACT_NARRATION_LIST_MARKER_PATTERN = re.compile(
    r'(?:^|\s)(?:\d{1,2}[.)]|[A-Za-zÇĞİÖŞÜçğıöşü][.)]|[-•])\s+',
    flags=re.UNICODE,
)
_EXACT_NARRATION_ABBREVIATION_BOUNDARY_PATTERN = re.compile(
    r'\b(?:dr|prof|doç|av|uzm|op|yrd|arş|ast|gen|alb|sn|say|'
    r'mr|mrs|ms|jr|sr|örn|vb|vs|bkz|no|st|cad|sok|mah|apt|tel)\.\s+'
    r'(?=[A-ZÇĞİÖŞÜ])|'
    r'\b[A-ZÇĞİÖŞÜ]\.\s+(?=[A-ZÇĞİÖŞÜ])',
    flags=re.IGNORECASE | re.UNICODE,
)
_EXACT_NARRATION_MID_CLAUSE_ELLIPSIS_PATTERN = re.compile(
    r'(?:\.{3}|…)\s+(?=[a-zçğıöşü])',
    flags=re.UNICODE,
)


def _explicit_scene_count_from_brief(brief: str) -> int | None:
    """Read only an unmistakable exact scene-count instruction from a brief."""
    folded = unicodedata.normalize(
        'NFKD',
        str(brief or '').casefold(),
    )
    folded = ''.join(
        character
        for character in folded
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r'\s+', ' ', folded).strip()
    requested_counts: list[int] = []
    negated_counts: list[int] = []
    for match in _EXPLICIT_SCENE_COUNT_PATTERN.finditer(normalized):
        token = match.group('count')
        count = (
            int(token)
            if token.isdigit()
            else _EXPLICIT_SCENE_COUNT_WORDS[token]
        )
        before = normalized[max(0, match.start() - 80):match.start()]
        after = normalized[match.end():match.end() + 80]
        if (
            _EXPLICIT_SCENE_COUNT_NEGATED_BEFORE.search(before)
            or _EXPLICIT_SCENE_COUNT_NEGATED_AFTER.match(after)
        ):
            negated_counts.append(count)
            continue
        requested_counts.append(count)
    if not requested_counts:
        return None
    unique_counts = set(requested_counts)
    if len(unique_counts) != 1 or unique_counts.intersection(negated_counts):
        raise RuntimeError(
            'User brief contains conflicting explicit scene counts'
        )
    requested = requested_counts[0]
    if not 3 <= requested <= _MAX_PRODUCTION_SCENES:
        raise RuntimeError(
            'User brief explicit scene count must be between 3 and '
            f'{_MAX_PRODUCTION_SCENES}'
        )
    return requested


def _has_explicit_technical_insert_return_contract(
    brief: str,
    scenes: list[dict],
) -> bool:
    """Recognize only a numbered AI macro-to-same-setting ending contract."""
    scene_count = len(scenes)
    if _explicit_scene_count_from_brief(brief) != scene_count:
        return False

    numbered_markers = list(re.finditer(
        r'(?m)^\s*(?P<number>\d{1,2})[.)]\s*',
        str(brief or ''),
    ))
    sections: dict[int, str] = {}
    for index, marker in enumerate(numbered_markers):
        number = int(marker.group('number'))
        end = (
            numbered_markers[index + 1].start()
            if index + 1 < len(numbered_markers)
            else len(str(brief or ''))
        )
        if number in sections:
            return False
        sections[number] = str(brief or '')[marker.end():end]

    if set(sections) != set(range(1, scene_count + 1)):
        return False

    def fold(value: str) -> str:
        decomposed = unicodedata.normalize('NFKD', value.casefold())
        return ''.join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        )

    penultimate = fold(sections[scene_count - 1])
    final = fold(sections[scene_count])
    if not all(isinstance(scene, dict) for scene in scenes[-2:]):
        return False
    penultimate_prompt = fold(str(scenes[-2].get('ai_prompt') or ''))
    final_prompt = fold(str(scenes[-1].get('ai_prompt') or ''))
    ai_route = re.compile(r'^\s*ai\s*:', flags=re.IGNORECASE)
    technical_insert = re.compile(
        r'\b(?:macro|makro|cutaway|cross[ -]?section|kesit|mechanism|'
        r'mekanizma|retractor|makara|internal|inside|icindeki|icinde)\b',
        flags=re.IGNORECASE,
    )
    explicit_return = re.compile(
        r'\b(?:same|ayni|return\w*|back\s+to|geri\s+don\w*)\b',
        flags=re.IGNORECASE,
    )
    return bool(
        ai_route.search(penultimate)
        and ai_route.search(final)
        and technical_insert.search(penultimate)
        and technical_insert.search(penultimate_prompt)
        and explicit_return.search(final)
        and explicit_return.search(final_prompt)
    )


def _story_brief_for_qc(brief: str) -> str:
    """Keep the complete user brief available to every pre-media quality gate."""
    value = str(brief or '').strip()
    if len(value) > _MAX_STORY_BRIEF_CHARS:
        raise RuntimeError(
            'User brief is too long for complete pre-media constraint review'
        )
    return value


def _normalize_exact_narration(value: str) -> str:
    return re.sub(
        r'\s+',
        ' ',
        unicodedata.normalize('NFC', str(value or '')),
    ).strip()


def _exact_narration_lock_from_brief(brief: str) -> str | None:
    """Read only an unmistakable exact quoted spoken-narration contract."""
    value = _story_brief_for_qc(brief)
    locked_blocks: list[str] = []
    for match in _EXACT_NARRATION_QUOTE_PATTERN.finditer(value):
        prefix = value[max(0, match.start() - 280):match.start()]
        if not _EXACT_NARRATION_PREFIX_PATTERN.search(prefix):
            continue
        block = next(
            (
                group
                for group in match.groups()
                if isinstance(group, str)
            ),
            '',
        )
        normalized = _normalize_exact_narration(block)
        if not normalized:
            raise RuntimeError(
                'User brief exact spoken-narration lock is empty'
            )
        locked_blocks.append(normalized)
    if not locked_blocks:
        return None
    if len(set(locked_blocks)) != 1:
        raise RuntimeError(
            'User brief contains conflicting exact spoken-narration locks'
        )
    return locked_blocks[0]


def _split_exact_narration_lock(
    locked_narration: str,
    expected_scene_count: int,
) -> list[str]:
    normalized = _normalize_exact_narration(locked_narration)
    if (
        _EXACT_NARRATION_LIST_MARKER_PATTERN.search(normalized)
        or _EXACT_NARRATION_ABBREVIATION_BOUNDARY_PATTERN.search(normalized)
        or _EXACT_NARRATION_MID_CLAUSE_ELLIPSIS_PATTERN.search(normalized)
    ):
        raise RuntimeError(
            'Exact spoken-narration lock cannot be segmented unambiguously '
            'into the returned scene count'
        )
    scenes = [
        match.group(0).strip()
        for match in _EXACT_NARRATION_SENTENCE_PATTERN.finditer(normalized)
    ]
    reconstructed = _normalize_exact_narration(' '.join(scenes))
    if (
        expected_scene_count < 1
        or len(scenes) != expected_scene_count
        or reconstructed != normalized
        or any(_word_count(scene) < 5 for scene in scenes)
    ):
        raise RuntimeError(
            'Exact spoken-narration lock cannot be segmented unambiguously '
            'into the returned scene count'
        )
    return scenes


def _infer_exact_narration_scene_count(locked_narration: str) -> int:
    """Count only sentence boundaries that pass the strict lock splitter."""
    normalized = _normalize_exact_narration(locked_narration)
    proposed_count = len(
        list(_EXACT_NARRATION_SENTENCE_PATTERN.finditer(normalized))
    )
    if not 3 <= proposed_count <= _MAX_PRODUCTION_SCENES:
        raise RuntimeError(
            'Exact spoken-narration lock must contain between 3 and '
            f'{_MAX_PRODUCTION_SCENES} unambiguous scene sentences'
        )
    return len(
        _split_exact_narration_lock(locked_narration, proposed_count)
    )


def _apply_exact_narration_lock(
    package: dict,
    brief: str,
    *,
    expected_scene_count: int | None = None,
) -> dict:
    """Restore an explicit spoken contract without changing visual planning."""
    locked_narration = _exact_narration_lock_from_brief(brief)
    if locked_narration is None:
        return package
    scenes = package.get('scenes') if isinstance(package, dict) else None
    if not isinstance(scenes, list) or not all(
        isinstance(scene, dict) for scene in scenes
    ):
        raise RuntimeError(
            'Exact spoken-narration lock requires a valid returned scene plan'
        )
    immutable_scene_count = (
        expected_scene_count
        if type(expected_scene_count) is int
        else len(scenes)
    )
    locked_scenes = _split_exact_narration_lock(
        locked_narration,
        immutable_scene_count,
    )
    if len(scenes) != immutable_scene_count:
        raise RuntimeError(
            'Exact spoken-narration lock cannot be segmented unambiguously '
            'into the returned scene count'
        )
    out = dict(package)
    out_scenes = [dict(scene) for scene in scenes]
    for position, narration in enumerate(locked_scenes):
        out_scenes[position]['narration'] = narration
        out_scenes[position]['tts_text'] = narration
    out['scenes'] = out_scenes
    out['narration'] = ' '.join(locked_scenes)
    out['tts_narration'] = out['narration']
    return out


def _studio_plan_provider() -> str:
    provider = str(
        getattr(settings, 'studio_plan_provider', 'openai') or ''
    ).strip().casefold()
    if provider not in {'openai', 'gemini'}:
        raise RuntimeError(
            'STUDIO_PLAN_PROVIDER must be openai or gemini'
        )
    return provider


def _director_json_schema(
    target_scenes: int,
    *,
    exact_scene_count: bool = False,
) -> dict:
    if exact_scene_count:
        minimum_scenes = maximum_scenes = int(target_scenes)
    else:
        minimum_scenes = max(3, int(target_scenes) - 1)
        maximum_scenes = max(minimum_scenes, int(target_scenes) + 1)
    scene_schema = {
        'type': 'object',
        'properties': {
            'narration': {'type': 'string'},
            'visual_queries': {
                'type': 'array',
                'items': {'type': 'string'},
                'minItems': 2,
                'maxItems': 3,
            },
            'ai_prompt': {'type': ['string', 'null']},
            'pace': {
                'type': 'string',
                'enum': ['fast', 'normal', 'slow'],
            },
            'transition': {
                'type': 'string',
                'enum': ['cut', 'match', 'dip'],
            },
        },
        'required': [
            'narration',
            'visual_queries',
            'ai_prompt',
            'pace',
            'transition',
        ],
        'additionalProperties': False,
    }
    return {
        'type': 'object',
        'properties': {
            'title': {'type': 'string'},
            'thumbnail_text': {'type': 'string'},
            'description': {'type': 'string'},
            'scenes': {
                'type': 'array',
                'items': scene_schema,
                'minItems': minimum_scenes,
                'maxItems': maximum_scenes,
            },
            'qc_summary': {
                'type': 'array',
                'items': {'type': 'string'},
            },
        },
        'required': [
            'title',
            'thumbnail_text',
            'description',
            'scenes',
            'qc_summary',
        ],
        'additionalProperties': False,
    }


def _stock_writer_json_schema(request_positions: list[int]) -> dict:
    row_schema = {
        'type': 'object',
        'properties': {
            'position': {
                'type': 'integer',
                'enum': list(request_positions),
            },
            'narration': {'type': 'string'},
            'visual_queries': {
                'type': 'array',
                'items': {'type': 'string'},
                'minItems': 2,
                'maxItems': 3,
            },
            'ai_prompt': {'type': 'null'},
        },
        'required': [
            'position',
            'narration',
            'visual_queries',
            'ai_prompt',
        ],
        'additionalProperties': False,
    }
    expected_count = len(request_positions)
    return {
        'type': 'object',
        'properties': {
            'scenes': {
                'type': 'array',
                'items': row_schema,
                'minItems': expected_count,
                'maxItems': expected_count,
            },
        },
        'required': ['scenes'],
        'additionalProperties': False,
    }


class _WholeStoryRepairRequired(RuntimeError):
    """One bounded whole-story repair is required before media work starts."""

    def __init__(self, failed_checks: list[str], evidence: str):
        self.failed_checks = [
            str(check or '').strip()
            for check in (failed_checks or [])
            if str(check or '').strip()
        ]
        self.evidence = str(evidence or '').strip()
        super().__init__(
            'Independent critic requested a bounded whole-story repair'
        )


class _NaturalSpokenLanguageRepairRequired(_WholeStoryRepairRequired):
    """A bounded whole-story copy edit is required before media work starts."""

    def __init__(self, evidence: str):
        super().__init__(['natural_spoken_language'], evidence)


def _json(text: str) -> dict:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError('Director response is not a JSON object')
    return data


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÇĞİÖŞÜçğıöşü'-]+\b", text or '', flags=re.UNICODE))


_TURKISH_SHORT_TTS_UNSAFE_PATTERN = re.compile(
    r"\b(?:(?:oled|gps|qr)(?:['’]?[A-Za-zÇĞİÖŞÜçğıöşü]+)|"
    r"wi(?:[-‑ ]?fi)\w*|reed(?:[-‑ ]?solomon)\w*)\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_TURKISH_SHORT_NORMALIZED_INITIALISMS = {'oled', 'gps', 'qr'}
_TURKISH_SHORT_GENERIC_INITIALISM_PATTERN = re.compile(
    r"(?<![\w])(?:[A-ZÇĞİÖŞÜ]{2,6})(?:['’]?[A-Za-zÇĞİÖŞÜçğıöşü]{0,8})?(?![\w])",
    flags=re.UNICODE,
)
_TURKISH_SHORT_TRANSLATIONESE_PATTERNS = (
    (re.compile(r'\bsiyah\s+yerde\b', flags=re.IGNORECASE), 'unnatural “siyah yerde” phrasing'),
    (re.compile(r'\bhücresel\s+zamanlama\b', flags=re.IGNORECASE), 'unnatural “hücresel zamanlama” noun stack'),
    (re.compile(r'\btamamlar\s+konumu\b', flags=re.IGNORECASE), 'inverted “tamamlar konumu” phrasing'),
    (re.compile(r'\bokunur\s+yine\s+kolayca\b', flags=re.IGNORECASE), 'translated “okunur yine kolayca” phrasing'),
    (
        re.compile(
            r'\b(?:sıkışan|hapsolan)\s+ısı\b',
            flags=re.IGNORECASE | re.UNICODE,
        ),
        (
            'translated energy wording such as “sıkışan ısı”; '
            'say that hot air stays inside or becomes trapped'
        ),
    ),
    (
        re.compile(
            r'\b(?:(?:açılan|oluşan)\s+(?:hava\s+)?boşluk|'
            r'ısı|sıcaklık)\s+(?:fanı|motoru|cihazı)\s+'
            r'(?:hızlandır|yavaşlat)\w*\b',
            flags=re.IGNORECASE | re.UNICODE,
        ),
        (
            'translated inanimate-cause wording; express the temperature or '
            'airflow change as a natural condition instead'
        ),
    ),
)
_TURKISH_SHORT_WARDROBE_METADATA_PATTERN = re.compile(
    r'\b(?:(?:açık|koyu)\s+)?'
    r'(?:beyaz|siyah|gri|füme|lacivert|mavi|kırmızı|yeşil|'
    r'sarı|kahverengi|bej|turuncu|mor|pembe)\s+'
    r'(?:tişörtlü|gömlekli|ceketli|kazaklı|montlu|kapüşonlu)\b',
    flags=re.IGNORECASE | re.UNICODE,
)
_TURKISH_SHORT_CAMERA_METADATA_PATTERN = re.compile(
    r'\b(?:kadraj\w*|yakın\s+plan\w*|geniş\s+plan\w*|'
    r'(?:alçak|yüksek)\s+açı\w*|üstten\s+çekim\w*|'
    r'(?:önden|arkadan|yandan)\s+'
    r'(?:izliyor|seyrediyor|görünüyor|gösteriliyor|çekiliyor))\b',
    flags=re.IGNORECASE | re.UNICODE,
)


def _short_spoken_quality_issues(
    package: dict,
    language_name: str,
) -> list[str]:
    if not str(language_name or '').casefold().startswith('turk'):
        return []
    issues: list[str] = []
    for scene_idx, scene in enumerate(package.get('scenes') or []):
        narration = str(scene.get('narration') or '').strip()
        unsafe_terms = sorted({
            *{
                match.group(0)
                for match in _TURKISH_SHORT_TTS_UNSAFE_PATTERN.finditer(narration)
            },
            *{
                match.group(0)
                for match in _TURKISH_SHORT_GENERIC_INITIALISM_PATTERN.finditer(narration)
                if match.group(0).casefold()
                not in _TURKISH_SHORT_NORMALIZED_INITIALISMS
            },
        })
        if unsafe_terms:
            issues.append(
                f'scene {scene_idx} uses TTS-unsafe raw term(s): '
                + ', '.join(unsafe_terms)
            )
        for pattern, reason in _TURKISH_SHORT_TRANSLATIONESE_PATTERNS:
            if pattern.search(narration):
                issues.append(f'scene {scene_idx} has {reason}')
        camera_metadata = _TURKISH_SHORT_CAMERA_METADATA_PATTERN.search(
            narration
        )
        if camera_metadata:
            issues.append(
                f'scene {scene_idx} has production-only camera direction or '
                'framing in narration; keep camera and framing instructions '
                'in visual_queries or ai_prompt'
            )
            if _TURKISH_SHORT_WARDROBE_METADATA_PATTERN.search(narration):
                issues.append(
                    f'scene {scene_idx} has wardrobe wording paired with '
                    'camera/framing metadata in narration; keep wardrobe '
                    'continuity in visual_queries or ai_prompt'
                )
    return issues


_TURKISH_SHORT_STORY_FAMILIES = (
    (
        'display-pixel mechanism',
        re.compile(
            r'\b(?:oled\w*|organik\s+ekran\w*|alt\s+piksel\w*|altpiksel\w*)\b',
            flags=re.IGNORECASE,
        ),
    ),
    (
        'positioning-network mechanism',
        re.compile(
            r'\b(?:gps\w*|wi(?:[-‑ ]?fi)\w*|hücresel\w*|'
            r'baz\s+istasyon\w*|uydu\s+sinyal\w*|konum\s+hesab\w*)\b',
            flags=re.IGNORECASE,
        ),
    ),
    (
        'code-recovery mechanism',
        re.compile(
            r'\b(?:qr\w*|kare\s+kod\w*|reed(?:[-‑ ]?solomon)\w*|'
            r'hata\s+düzelt\w*)\b',
            flags=re.IGNORECASE,
        ),
    ),
)

_TURKISH_SHORT_SEATBELT_PATTERN = re.compile(
    r'\b(?:emniyet\s+)?kemer\w*\b',
    flags=re.IGNORECASE | re.UNICODE,
)
_TURKISH_SHORT_SEATBELT_HARDWARE_PATTERN = re.compile(
    r'\b(?:(?:metal\s+)?dil\w*|toka\w*|yuva\w*)\b',
    flags=re.IGNORECASE | re.UNICODE,
)
_TURKISH_SHORT_PRECISION_CONNECTION_ACTION_PATTERN = re.compile(
    r'\b(?:tak|sok|geçir|kilitle|birleştir|bağla)\w*\b',
    flags=re.IGNORECASE | re.UNICODE,
)
_ENGLISH_SHORT_SEATBELT_PATTERN = re.compile(
    r'\b(?:seat\s*[- ]?belt|seatbelt|three\s*[- ]?point\s+belt)\b',
    flags=re.IGNORECASE,
)
_ENGLISH_SHORT_SEATBELT_HARDWARE_PATTERN = re.compile(
    r'\b(?:latch\s+(?:plate|tongue)|metal\s+(?:plate|tongue)|'
    r'buckle|buckle\s+receiver|receiver\s+slot)\b',
    flags=re.IGNORECASE,
)
_ENGLISH_SHORT_PRECISION_CONNECTION_ACTION_PATTERN = re.compile(
    r'\b(?:insert|fasten|buckle|latch|connect|push|slide|enter)\w*\b',
    flags=re.IGNORECASE,
)
_ENGLISH_SHORT_SAFE_SEATBELT_PAYOFF_PATTERN = re.compile(
    r'\b(?:'
    r'already\s*[- ]?(?:fastened|buckled)\b.{0,32}\b'
    r'(?:seat\s*[- ]?belt|seatbelt|three\s*[- ]?point\s+belt)|'
    r'(?:seat\s*[- ]?belt|seatbelt|three\s*[- ]?point\s+belt)\b'
    r'.{0,32}\balready\s*[- ]?(?:fastened|buckled)|'
    r'(?:visibly\s+)?wear(?:s|ing)\b.{0,48}\b'
    r'(?:seat\s*[- ]?belt|seatbelt|three\s*[- ]?point\s+belt)|'
    r'(?:seat\s*[- ]?belt|seatbelt|three\s*[- ]?point\s+belt)\b'
    r'.{0,48}\bacross\s+(?:the\s+)?chest)\b',
    flags=re.IGNORECASE,
)


def _short_story_quality_issues(
    package: dict,
    language_name: str,
) -> list[str]:
    issues = _short_spoken_quality_issues(package, language_name)
    if not str(language_name or '').casefold().startswith('turk'):
        return issues

    narration = ' '.join(
        str(scene.get('narration') or '')
        for scene in (package.get('scenes') or [])
    )
    mechanism_families = [
        label
        for label, pattern in _TURKISH_SHORT_STORY_FAMILIES
        if pattern.search(narration)
    ]
    if len(mechanism_families) > 1:
        issues.append(
            'short preview mixes unrelated mechanism families instead of '
            'answering one human question: ' + ', '.join(mechanism_families)
        )
    for scene_idx, scene in enumerate(package.get('scenes') or []):
        if not isinstance(scene, dict) or not str(
            scene.get('ai_prompt') or ''
        ).strip():
            continue
        scene_narration = str(scene.get('narration') or '')
        ai_prompt = str(scene.get('ai_prompt') or '')
        complete_contract = f'{scene_narration} {ai_prompt}'
        seatbelt_contract = bool(
            _TURKISH_SHORT_SEATBELT_PATTERN.search(complete_contract)
            or _ENGLISH_SHORT_SEATBELT_PATTERN.search(complete_contract)
        )
        hardware_contract = bool(
            _TURKISH_SHORT_SEATBELT_HARDWARE_PATTERN.search(
                complete_contract
            )
            or _ENGLISH_SHORT_SEATBELT_HARDWARE_PATTERN.search(
                complete_contract
            )
        )
        connection_action = bool(
            _TURKISH_SHORT_PRECISION_CONNECTION_ACTION_PATTERN.search(
                complete_contract
            )
            or _ENGLISH_SHORT_PRECISION_CONNECTION_ACTION_PATTERN.search(
                complete_contract
            )
        )
        safe_ai_payoff = bool(
            _ENGLISH_SHORT_SAFE_SEATBELT_PAYOFF_PATTERN.search(ai_prompt)
        )
        narration_precision_action = bool(
            (
                _TURKISH_SHORT_SEATBELT_PATTERN.search(scene_narration)
                or _ENGLISH_SHORT_SEATBELT_PATTERN.search(scene_narration)
            )
            and (
                _TURKISH_SHORT_SEATBELT_HARDWARE_PATTERN.search(
                    scene_narration
                )
                or _ENGLISH_SHORT_SEATBELT_HARDWARE_PATTERN.search(
                    scene_narration
                )
            )
            and (
                _TURKISH_SHORT_PRECISION_CONNECTION_ACTION_PATTERN.search(
                    scene_narration
                )
                or _ENGLISH_SHORT_PRECISION_CONNECTION_ACTION_PATTERN.search(
                    scene_narration
                )
            )
        )
        if (
            narration_precision_action
            or (
                seatbelt_contract
                and hardware_contract
                and connection_action
                and not safe_ai_payoff
            )
        ):
            issues.append(
                f'scene {scene_idx} assigns paid AI video a precision '
                'seat-belt latch insertion; rewrite the payoff as the same '
                'driver visibly wearing an already-fastened three-point belt '
                'after the preceding mechanism scene, without narrating the '
                'small metal tongue entering the buckle'
            )
            continue
        if (
            scene_idx == len(package.get('scenes') or []) - 1
            and seatbelt_contract
            and not safe_ai_payoff
        ):
            issues.append(
                f'scene {scene_idx} has an AI-routed seat-belt payoff without '
                'a stable visible result; require the prompt to show the same '
                'driver visibly wearing an already-fastened three-point belt '
                'across the chest before preparing to drive'
            )
    return issues


_SHORT_STORY_QC_VERSION = 4
_STOCK_SCENE_QC_VERSION = 6
_STORY_STOCK_CONTRACT = 'openai-story-stock-v2'


def _normalize_short_story_topic(topic: str) -> str:
    return re.sub(r'\s+', ' ', str(topic or '')).strip().casefold()


def _short_story_fingerprint(package: dict) -> str:
    material = {
        'title': package.get('title'),
        'thumbnail_text': package.get('thumbnail_text'),
        'description': package.get('description'),
        'narration': package.get('narration'),
        'tts_narration': package.get('tts_narration'),
        'requested_topic': _normalize_short_story_topic(
            (package.get('short_story_qc') or {}).get('requested_topic')
        ),
        'sources': package.get('sources') or [],
        'scenes': [
            {
                'index': scene.get('index'),
                'narration': scene.get('narration'),
                'tts_text': scene.get('tts_text'),
                'visual_queries': scene.get('visual_queries') or [],
                'ai_prompt': scene.get('ai_prompt'),
                'pace': scene.get('pace'),
                'transition': scene.get('transition'),
            }
            for scene in (package.get('scenes') or [])
            if isinstance(scene, dict)
        ],
        'stock_scene_qc': package.get('stock_scene_qc'),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def short_story_package_is_approved(
    package: dict,
    topic: str | None = None,
) -> bool:
    if not isinstance(package, dict):
        return False
    scenes = package.get('scenes')
    sources = package.get('sources')
    qc = package.get('short_story_qc')
    stock_qc = package.get('stock_scene_qc')
    try:
        normalize_evidence_sources(
            sources,
            min_count=2,
            max_count=5,
        )
    except ValueError:
        return False
    if (
        not isinstance(scenes, list)
        or not scenes
        or not all(isinstance(scene, dict) for scene in scenes)
        or not isinstance(qc, dict)
        or not isinstance(stock_qc, dict)
    ):
        return False
    approval_brief = (
        topic
        if topic is not None
        else qc.get('requested_topic')
    )
    try:
        _story_brief_for_qc(approval_brief)
        explicit_scene_count = _explicit_scene_count_from_brief(
            approval_brief
        )
        exact_narration = _exact_narration_lock_from_brief(
            approval_brief
        )
        exact_scene_narrations = (
            _split_exact_narration_lock(exact_narration, len(scenes))
            if exact_narration is not None
            else None
        )
    except RuntimeError:
        return False
    if (
        explicit_scene_count is not None
        and len(scenes) != explicit_scene_count
    ):
        return False
    if exact_scene_narrations is not None:
        actual_scene_narrations = [
            _normalize_exact_narration(scene.get('narration'))
            for scene in scenes
        ]
        actual_scene_tts = [
            _normalize_exact_narration(scene.get('tts_text'))
            for scene in scenes
        ]
        if (
            actual_scene_narrations != exact_scene_narrations
            or actual_scene_tts != exact_scene_narrations
            or _normalize_exact_narration(package.get('narration'))
            != exact_narration
            or _normalize_exact_narration(package.get('tts_narration'))
            != exact_narration
        ):
            return False
    if setting_is_enabled(
        getattr(settings, 'gemini_critic_enabled', False)
    ):
        if not str(getattr(settings, 'gemini_api_key', '') or '').strip():
            return False
        gemini_qc = stock_qc.get('gemini_critic')
        target_positions = stock_qc.get('target_positions')
        configured_model = str(
            getattr(settings, 'gemini_model', GEMINI_DEFAULT_MODEL)
            or GEMINI_DEFAULT_MODEL
        ).strip()
        if (
            not isinstance(gemini_qc, dict)
            or set(gemini_qc.keys()) != {
                'accepted',
                'model',
                'contract',
                'reviewed_scene_count',
            }
            or gemini_qc.get('accepted') is not True
            or gemini_qc.get('model') != configured_model
            or gemini_qc.get('contract') != _STORY_STOCK_CONTRACT
            or type(gemini_qc.get('reviewed_scene_count')) is not int
            or not isinstance(target_positions, list)
            or gemini_qc.get('reviewed_scene_count') != len(target_positions)
        ):
            return False
    if (
        type(qc.get('version')) is not int
        or qc.get('version') != _SHORT_STORY_QC_VERSION
    ):
        return False
    if (
        type(stock_qc.get('version')) is not int
        or stock_qc.get('version') < _STOCK_SCENE_QC_VERSION
    ):
        return False
    story_review = stock_qc.get('story_review')
    ending_review = stock_qc.get('ending_pair_review')
    if (
        not isinstance(story_review, dict)
        or story_review.get('accepted') is not True
        or not isinstance(ending_review, dict)
        or ending_review.get('accepted') is not True
        or qc.get('story_review_accepted') is not True
        or qc.get('ending_pair_accepted') is not True
    ):
        return False
    attested_topic = _normalize_short_story_topic(
        qc.get('requested_topic')
    )
    if not attested_topic:
        return False
    if (
        topic is not None
        and attested_topic != _normalize_short_story_topic(topic)
    ):
        return False
    fingerprint = str(qc.get('fingerprint') or '')
    return (
        len(fingerprint) == 64
        and fingerprint == _short_story_fingerprint(package)
    )


def _target_scene_count(duration_minutes: float, pace: str) -> int:
    if duration_minutes > 1.1:
        # Production clips are single-pass. Keep the spoken beat short enough
        # for one 5-10 second shot even when the requested pace is calm.
        return min(
            _MAX_PRODUCTION_SCENES,
            max(
                8,
                int(round(duration_minutes * _PRODUCTION_SCENES_PER_MINUTE)),
            ),
        )
    if duration_minutes <= 0.6:
        base = max(5, int(round(duration_minutes * 12)))
    else:
        base = 6
    if pace == 'calm':
        return max(3, int(round(base * 0.82)))
    if pace == 'dynamic':
        return min(32, max(3, int(round(base * 1.12))))
    return base


def _scene_count_matches(
    actual: int,
    target: int,
    *,
    exact_scene_count: bool,
) -> bool:
    return (
        actual == target
        if exact_scene_count
        else abs(actual - target) <= 1
    )


def _target_word_budget(duration_minutes: float) -> tuple[int, int, int]:
    if duration_minutes <= 0.6:
        target = max(44, int(round(duration_minutes * 96)))
    elif duration_minutes <= 1.1:
        target = 82
    elif duration_minutes <= 3.1:
        target = int(round(duration_minutes * 92))
    else:
        target = int(round(duration_minutes * 100))
    # Keep the 30-second narrator natural: concise 40-word scripts are safer
    # than accepting the post-synthesis tempo distortion rejected downstream.
    minimum = max(
        30,
        int(round(target * (0.83 if duration_minutes <= 0.6 else 0.86))),
    )
    maximum = max(minimum + 4, int(round(target * 1.06)))
    return target, minimum, maximum


def _short_preview_scene_word_ranges(
    target_words: int,
    target_scenes: int,
) -> list[list[int]]:
    """Return the same balanced scene contract shown to the director."""
    if target_scenes < 1:
        return []
    base, extra = divmod(max(0, int(target_words)), int(target_scenes))
    quotas = [
        base + (1 if position < extra else 0)
        for position in range(target_scenes)
    ]
    return [
        [max(5, quota - 2), quota + 2]
        for quota in quotas
    ]


def _short_preview_scene_budget_issues(
    package: dict,
    target_words: int,
    target_scenes: int,
) -> list[str]:
    """Reject oversized spoken beats before any voice or visual provider call.

    The real synthesized duration remains the final authority in the worker.
    This deterministic editorial ceiling prevents a clearly unbalanced AI
    scene from predictably reaching that paid-media preflight.
    """
    scenes = package.get('scenes') if isinstance(package, dict) else None
    if not isinstance(scenes, list) or len(scenes) != target_scenes:
        return []
    ranges = _short_preview_scene_word_ranges(target_words, target_scenes)
    issues: list[str] = []
    for position, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        if not str(scene.get('ai_prompt') or '').strip():
            continue
        words = _word_count(scene.get('narration') or '')
        maximum = min(
            ranges[position][1],
            _SHORT_PREVIEW_AI_SCENE_MAX_WORDS,
        )
        if words > maximum:
            issues.append(
                f'scene {position} narration has {words} words; hard '
                f'AI single-pass maximum is {maximum}'
            )
    return issues


def _clean_scene(scene: dict, idx: int) -> dict:
    narration = str(scene.get('narration') or '').strip()
    queries = scene.get('visual_queries') or []
    if isinstance(queries, str):
        queries = [queries]
    queries = [str(q).strip() for q in queries if str(q).strip()][:3]
    pace = str(scene.get('pace') or 'normal').strip().lower()
    if pace not in {'fast', 'normal', 'slow'}:
        pace = 'normal'
    transition = str(scene.get('transition') or 'cut').strip().lower()
    if transition not in {'cut', 'match', 'dip'}:
        transition = 'cut'
    return {
        'index': idx,
        'narration': narration,
        'tts_text': narration,
        'visual_queries': queries,
        'ai_prompt': str(scene.get('ai_prompt') or '').strip() or None,
        'overlay_text': None,
        'pace': pace,
        'transition': transition,
    }


def _clean_package(revised: dict, original: dict) -> dict:
    scenes = revised.get('scenes') or []
    cleaned = [_clean_scene(s, i) for i, s in enumerate(scenes) if isinstance(s, dict)]
    cleaned = [s for s in cleaned if s['narration'] and s['visual_queries']]
    if len(cleaned) < 3:
        raise RuntimeError('Director produced too few usable scenes')
    out = dict(original)
    out['title'] = revised.get('title') or original.get('title')
    out['thumbnail_text'] = revised.get('thumbnail_text') or original.get('thumbnail_text')
    out['description'] = revised.get('description') or original.get('description')
    out['scenes'] = cleaned
    out['narration'] = ' '.join(s['narration'] for s in cleaned)
    out['tts_narration'] = out['narration']
    out['visual_queries'] = [q for s in cleaned for q in s['visual_queries']]
    out['ai_scenes'] = [s['ai_prompt'] for s in cleaned if s.get('ai_prompt')]
    out['overlay_phrases'] = []
    out['director_qc'] = revised.get('qc_summary') or []
    return out


def _run_director(
    client: OpenAI,
    compact: dict,
    topic: str,
    language_name: str,
    duration_minutes: float,
    target_words: int,
    min_words: int,
    max_words: int,
    target_scenes: int,
    options: dict,
    correction: bool = False,
    exact_scene_count: bool = False,
) -> dict:
    style = str(options.get('content_style') or 'documentary')
    pace_profile = str(options.get('pace') or 'balanced')
    visual_mix = str(options.get('visual_mix') or 'balanced')
    reference_url = str(options.get('reference_url') or '').strip()
    current_words = int(compact.get('current_word_count') or 0)
    short_quota_note = ''
    short_visual_note = ''
    short_language_note = ''
    if duration_minutes <= 0.6 and target_scenes > 0:
        authored_ai_limit = preview_authored_ai_limit(
            options,
            target_scenes,
            duration_minutes,
        )
        if authored_ai_limit is None:
            authored_ai_limit = target_scenes
        ai_first_routing_note = (
            f'The selected AI-first mix may carry at most {authored_ai_limit} non-null ai_prompt values. '
            'Preserve deliberate AI routes already present in the draft, including a user-requested AI ending pair, '
            'unless doing so would exceed that limit. Do not downgrade an authored AI route merely because stock might exist. '
            if visual_mix == 'ai_first'
            else ''
        )
        base, extra = divmod(target_words, target_scenes)
        quotas = [base + (1 if i < extra else 0) for i in range(target_scenes)]
        scene_ranges = _short_preview_scene_word_ranges(
            target_words,
            target_scenes,
        )
        short_quota_note = (
            f'SHORT PREVIEW — HIGHEST PRIORITY: return exactly {target_scenes} scenes. '
            f'Aim for scene narration word counts near {quotas}, using these allowed ranges {scene_ranges}. '
            f'Every scene with a non-null ai_prompt has a HARD maximum of '
            f'{_SHORT_PREVIEW_AI_SCENE_MAX_WORDS} spoken words so its action '
            'fits one Runway shot without looping; never compensate for a '
            'short scene by making an AI-routed scene longer. '
            f'The HARD total is {min_words}-{max_words} words; aim for {target_words}. '
            'Never pad a sentence with adverbs, time words or empty qualifiers merely to hit a count. '
            'Count hyphenated or apostrophe compounds as one word. '
        )
        short_visual_note = (
            'SHORT PREVIEW VISUAL ROUTING — HIGHEST PRIORITY: ai_prompt values are free fallback candidates, not promised generations. '
            f'Up to {authored_ai_limit} scenes may carry a non-null fallback, while the worker will submit at most three paid Runway generations after measuring the exact current stock clips. '
            f'{ai_first_routing_note}'
            'EXPLICIT USER-BRIEF OVERRIDE: explicit numbered scene beats, route assignments and continuity constraints in the user topic override generic story-shaping heuristics below. '
            'Follow them exactly and never merge or move a required beat merely to prefer one mechanism scene. '
            'Before writing, silently choose ONE precise everyday curiosity a real person would willingly spend thirty seconds to resolve. '
            'The supplied topic is broad context, never permission to make a technology-trivia sampler. '
            'Use one recurring person or object, one immediate goal or problem, one causal reveal, and one visible everyday payoff. '
            'Every scene must advance that same question; never mix unrelated mechanisms, products or clever facts merely because they fit the topic. '
            'Structure the story so no more than three scenes truly depend on AI, and reserve those dependencies only for the single chosen mechanism that stock cannot literally show. '
            'Unless the explicit user topic assigns a multi-scene causal demonstration, compress that mechanism and its complete causal explanation into one scene. '
            'Never merge, split, repeat or move explicit numbered beats from the user topic. '
            'Every other scene must remain publishable with a plainly filmable real-world action whose exact subject and action appear in its stock queries, '
            'even when it also carries a fallback ai_prompt for uncertain stock coverage. '
            'The penultimate action and visible payoff must happen seconds apart to the same person or object in the SAME named ordinary micro-location, '
            'such as the same café counter, desk or doorway. Repeat that location phrase in both scenes; never jump between home, store, street, a new room or a later time. '
            'Every ai_prompt-null scene must be fully provable by one ordinary stock clip; if all named nouns and actions are unlikely to coexist in that clip, '
            'rewrite the narration and its queries before returning. '
            'Every spoken clause in an ai_prompt-null scene must be literally visible in that same clip; never append abstract phrases such as magic happening, '
            'more working than the viewer can see, hidden systems or silent partners. '
            'Each non-null ai_prompt must be a concrete English prompt for one continuous cinematic 16:9 scene-length shot, normally 5-10 seconds, '
            'with the named subject and action visible and no captions, logos, watermarks or fake interface text. '
            'Treat each non-null ai_prompt as a standalone paid-generation contract: repeat every visible identity, size, color, '
            'wardrobe, setting, continuity and forbidden-element constraint from the user topic that applies to that numbered scene. '
            'Never rely on an earlier scene prompt to carry a shared constraint forward. '
        )
    elif options.get('mode') == 'preview':
        short_visual_note = (
            'PREVIEW VISUAL ROUTING — HIGHEST PRIORITY: every ai_prompt MUST be null for this duration. '
            'Make every scene literally stock-filmable and preserve this assignment during all corrections. '
        )
    if duration_minutes <= 0.6 and str(language_name).casefold().startswith('turk'):
        short_language_note = (
            'TURKISH SPOKEN-SURFACE — HIGHEST PRIORITY: write native, breath-friendly Turkish. '
            'Standalone OLED, GPS and QR are allowed only when paired with a natural Turkish noun because the voice layer normalizes them. '
            'Never attach Turkish suffixes directly to abbreviations, and never speak raw Wi-Fi or Reed-Solomon. '
            'Prefer meaning-first phrases such as OLED ekran, GPS sinyali, QR kodu, kablosuz ağ or hata düzeltme yöntemi. '
            'Avoid translated noun stacks, inverted word order and phrases like “siyah yerde”, '
            '“hücresel zamanlama tamamlar konumu” or “okunur yine kolayca”. '
            'Express cause and effect through natural Turkish conditions: say “sıcak hava içeride kalınca” or “sıcak hava sıkışınca”, '
            'never translated energy-agent phrases such as “sıkışan ısı fanı hızlandırıyor” or “açılan boşluk fanı yavaşlatıyor”. '
            'Keep purely visual production metadata out of speech: wardrobe and color continuity, camera direction, shot size, '
            'face visibility and framing belong in visual_queries or ai_prompt unless the story itself causally depends on them. '
            'Never narrate phrases such as “koyu lacivert tişörtlü Mert” or “arkadan izliyor” merely to control the picture. '
            'Precise technical English is allowed in visual_queries and ai_prompt because those fields are not spoken. '
        )

    correction_issues = compact.get('narration_quality_issues') or []
    if not isinstance(correction_issues, list):
        correction_issues = [str(correction_issues)]
    correction_note = ''
    if correction:
        correction_note = (
            f'CRITICAL CORRECTION: the server counted {current_words} words. '
            f'Rewrite within the hard allowed range {min_words}-{max_words} total words; '
            f'aim for {target_words} without padding individual scenes. '
            'Preserve the supported topic and useful facts, but DROP unrelated mechanisms, examples and draft wording '
            'whenever needed to create one focused human story. Do not add unsupported facts. '
        )
        if correction_issues:
            correction_note += (
                'The previous draft failed these editorial gates; repair every item: '
                + json.dumps(correction_issues, ensure_ascii=False)
                + '. '
            )
    reference_note = (
        f'Reference URL: {reference_url}. Use only high-level information architecture and pacing inspiration; never copy wording, signature creative devices or branding.'
        if reference_url else 'No external reference structure was supplied.'
    )
    scene_budget_note = (
        f'USER-BRIEF HARD CONSTRAINT: return exactly {target_scenes} scenes; '
        'one fewer or one extra scene is invalid.'
        if exact_scene_count
        else (
            f'Target scene budget: approximately {target_scenes} scenes, '
            'never more than one scene away.'
        )
    )
    production_scene_note = (
        'PRODUCTION SINGLE-PASS SCENE CONTRACT: distribute spoken narration '
        'evenly across scenes; preferably keep each scene narration at 5-14 '
        'words. Every scene must be fully visualizable in one continuous '
        '5-10-second shot without looping or combining multiple shots. This '
        "preference never overrides a user brief's explicit exact scene count."
        if duration_minutes > 1.1
        else ''
    )
    prompt = f'''You are the FINAL EDITORIAL DIRECTOR for a premium faceless YouTube video.
Topic: {topic}
Language: {language_name}
Requested duration: {duration_minutes} minutes.
Studio style: {STYLE_NOTES.get(style, STYLE_NOTES['documentary'])}
Studio pace profile: {pace_profile}
Studio visual mix: {visual_mix}
{reference_note}
HARD spoken-word budget: {min_words}-{max_words}; aim for {target_words}.
{short_quota_note}
{short_visual_note}
{short_language_note}
{scene_budget_note}
{production_scene_note}
{correction_note}

DRAFT JSON:
{json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON with exactly these keys:
title, thumbnail_text, description, scenes, qc_summary.

Each scene must contain exactly:
narration, visual_queries, ai_prompt, pace, transition.

EDITORIAL QC RULES:
- Produce one coherent story. Repair every abrupt subject jump.
- Treat the complete Topic as a literal production contract. Before returning, silently audit every numbered scene against every explicit positive, negative, routing and continuity constraint in it.
- Every non-null ai_prompt is a standalone paid-generation instruction. Restate all applicable visible object identity, dimensions, brand state, color, wardrobe, room, lighting, continuity and forbidden elements inside that scene's own prompt, even when this repeats earlier prompts. Never assume a later generation can see an earlier prompt.
- For a short preview, commit to one narrow human situation, one curiosity hook, one recurring person or object, one causal mini-story and one visible everyday payoff.
- A broad topic is not a story. Never create a sampler of unrelated mechanisms or facts; at most one technical mechanism family may drive a short preview.
- Every scene must continue, explain, contrast, escalate or pay off the previous scene.
- Remove filler, robotic listicle wording and repetitive transition phrases.
- Spoken {language_name} must sound natural, confident and punctuated for real breaths.
- In Turkish narration, reject translated energy or geometry as a grammatical agent. Prefer a natural condition such as hot air remaining trapped and the fan then speeding up or slowing down.
- Do not verbalize production-only wardrobe, color-continuity, camera-direction, shot-size, face-visibility or framing notes. Preserve those requirements in visual_queries or ai_prompt instead; narration should contain them only when they change the story's human meaning.
- Each scene contains one complete thought that can remain under one excellent hero visual.
- Never make an ai_prompt-null scene recap several earlier mechanisms or invisible abstractions; it must narrate one visible subject performing one visible action in one ordinary location.
- Every clause of every ai_prompt-null narration must be directly visible in that one clip; remove magic-like hooks, hidden-system claims and spoken conclusions.
- Before returning, audit every ai_prompt-null scene against its queries: all named subjects, actions and context must realistically coexist in a single stock clip.
- Match the selected Studio style without imitating a named creator.
- Apply the global pace profile, but still vary individual scene pace intentionally.
- The master video is text-free. Do not create subtitles, lower thirds or overlay copy.
- visual_queries must literally match the exact spoken meaning and name the visible subject, action and context in the same phrase, while also carrying applicable silent visual-production constraints from the Topic without forcing those constraints into narration.
- Never search for an abstract property alone: keep the named subject attached (for example, a damaged QR code being scanned, not a generic software error; OLED pixel microscopy, not digital glitch footage).
- CONDITIONAL VALIDATION EXAMPLE, not a story suggestion: only if the user's topic and the chosen single story already require OLED or true black, narration, stock queries and ai_prompt must show black-region subpixel emitters visibly unlit beside illuminated colored subpixels; a whole-screen fade or hand turning a screen off is not evidence.
- In that same conditional OLED case, never claim lower power use in a short scene unless a real physical power meter visibly falls in that same continuous shot.
- For a short seat-belt story, never ask paid AI video to animate the small metal latch plate entering the buckle. Explain the internal locking mechanism in the preceding technical scene, then make the payoff show the same driver visibly wearing an already-fastened three-point belt across the chest and preparing to drive. Do not narrate the precision insertion in that AI scene.
- Reject generic typing, code errors, random phones, office workers, skylines, fireworks, finance charts, digital noise or abstract tech footage unless literally required by the narration.
- Give every scene 2-3 search options with different shot grammar.
- ai_prompt is null unless stock footage cannot honestly show the concept.
- pace is fast, normal or slow. transition is mostly cut; use match only for a real visual relationship and dip sparingly.
- Final scene must resolve the central curiosity and provide a memorable payoff.
- Total narration word count must be between {min_words} and {max_words}.
- qc_summary is a short list of the main editorial repairs.
'''
    reasoning_effort = 'medium' if correction else 'low'
    if _studio_plan_provider() == 'gemini':
        return generate_gemini_json(
            prompt,
            api_key=str(getattr(settings, 'gemini_api_key', '') or ''),
            model=str(
                getattr(settings, 'gemini_model', GEMINI_DEFAULT_MODEL)
                or GEMINI_DEFAULT_MODEL
            ),
            json_schema=_director_json_schema(
                target_scenes,
                exact_scene_count=exact_scene_count,
            ),
            google_search=False,
            thinking_level=reasoning_effort,
        )
    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': reasoning_effort},
        input=prompt,
    )
    return _json(response.output_text)


def _repair_short_stock_scenes(
    client: OpenAI,
    package: dict,
    language_name: str,
    duration_minutes: float,
    topic: str = '',
    *,
    allow_natural_language_repair: bool = True,
    allow_explicit_brief_repair: bool = True,
) -> dict:
    if duration_minutes > 0.6:
        return package

    plan_provider = _studio_plan_provider()
    requested_brief = _story_brief_for_qc(topic)

    scenes = package.get('scenes') or []
    if len(scenes) < 3:
        return package
    explicit_technical_insert_return_contract = (
        _has_explicit_technical_insert_return_contract(
            requested_brief,
            scenes,
        )
    )

    exact_narration_lock = _exact_narration_lock_from_brief(requested_brief)
    locked_narration_by_position: dict[int, str] = {}
    if exact_narration_lock is not None:
        complete_scene_narration = _normalize_exact_narration(
            ' '.join(
                str(scene.get('narration') or '').strip()
                for scene in scenes
                if isinstance(scene, dict)
            )
        )
        if complete_scene_narration != exact_narration_lock:
            raise RuntimeError(
                'Exact spoken-narration lock does not match the complete '
                'candidate story before stock repair'
            )
        locked_narration_by_position = {
            position: str(scene.get('narration') or '').strip()
            for position, scene in enumerate(scenes)
        }

    target_total_words, minimum_total_words, maximum_total_words = (
        _target_word_budget(duration_minutes)
    )
    minimum_scene_words = 5
    maximum_scene_words = 11

    stock_positions = [
        position
        for position, scene in enumerate(scenes)
        if not str(scene.get('ai_prompt') or '').strip()
    ]
    role_by_position = {
        position: (
            'hook' if position == 0
            else 'payoff' if position == len(scenes) - 1
            else 'penultimate' if position == len(scenes) - 2
            else 'bridge'
        )
        for position in stock_positions
    }
    targets_by_position = {
        position: {
            'position': position,
            'role': role_by_position[position],
            'current_word_count': _word_count(
                scenes[position].get('narration') or ''
            ),
            'allowed_word_count': [
                minimum_scene_words,
                maximum_scene_words,
            ],
            'current_narration': scenes[position].get('narration'),
            'current_visual_queries': scenes[position].get('visual_queries') or [],
        }
        for position in stock_positions
    }
    for position in stock_positions:
        if position in locked_narration_by_position:
            targets_by_position[position]['locked_narration'] = (
                locked_narration_by_position[position]
            )
    original_story = [
        {
            'position': position,
            'route': 'ai' if str(scene.get('ai_prompt') or '').strip() else 'stock',
            'narration': scene.get('narration'),
        }
        for position, scene in enumerate(scenes)
    ]
    mechanism_pattern = re.compile(
        r'\b(?:oled|pixels?|piksel\w*|alt\s*piksel\w*|altpiksel\w*|gps|'
        r'wi[-‑]?fi|cellular|hücresel\w*|qr|error\s+correction|hata\s+düzelt\w*|'
        r'algebra|cebir\w*|timing|zamanlama\w*|'
        r'location\s+(?:systems?|services?|data|tracking|determination)|'
        r'konum\s+(?:sistem\w*|servis\w*|veri\w*|belirle\w*|takip\w*)|'
        r'(?:uydu|wi[-‑]?fi|hücresel)\s+(?:konumla\w*|sinyal\w*))\b',
        flags=re.IGNORECASE,
    )
    abstract_pattern = re.compile(
        r'\b(?:magic|magical|miracle|invisible|hidden\s+systems?|silent\s+partners?|'
        r'sihir\w*|mucize\w*|görünmeyen|gizli\s+sistem\w*|sessiz\s+ortak\w*)\b',
        flags=re.IGNORECASE,
    )
    original_ai_count = sum(
        1 for scene in scenes if str(scene.get('ai_prompt') or '').strip()
    )

    def validate_generated_row(position: int, row: dict) -> tuple[dict | None, str]:
        target = targets_by_position[position]
        narration = str(row.get('narration') or '').strip()
        locked_narration = target.get('locked_narration')
        if (
            isinstance(locked_narration, str)
            and narration != locked_narration
        ):
            return None, (
                f'position {position} changed exact locked narration; only '
                'visual_queries may be repaired'
            )
        got_words = _word_count(narration)
        allowed_words = target['allowed_word_count']
        if not allowed_words[0] <= got_words <= allowed_words[1]:
            return None, (
                f'position {position} has {got_words} narration words; '
                f'expected {allowed_words[0]}-{allowed_words[1]}'
            )
        if (
            ';' in narration
            or ':' in narration
            or not re.fullmatch(r'[^.!?…\r\n]+(?:[.!?…]+)?', narration)
        ):
            return None, f'position {position} must contain one simple sentence'
        if mechanism_pattern.search(narration):
            return None, f'position {position} contains technical recap'
        if abstract_pattern.search(narration):
            return None, f'position {position} contains an unfilmable abstraction'
        spoken_issues = _short_spoken_quality_issues(
            {'scenes': [{'narration': narration}]},
            language_name,
        )
        if spoken_issues:
            return None, (
                f'position {position} has unsafe spoken wording: '
                + '; '.join(spoken_issues)
            )
        if row.get('ai_prompt') is not None:
            return None, f'position {position} must explicitly keep ai_prompt null'

        queries = row.get('visual_queries')
        if not isinstance(queries, list) or not 2 <= len(queries) <= 3:
            return None, (
                f'position {position} must contain exactly two or three stock queries'
            )
        if any(not isinstance(query, str) or not query.strip() for query in queries):
            return None, f'position {position} contains an invalid stock query'
        queries = [query.strip() for query in queries]
        if len({query.casefold() for query in queries}) != len(queries):
            return None, f'position {position} contains duplicate stock queries'
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '\-]*", query)
            for query in queries
        ):
            return None, (
                f'position {position} stock queries must use plain English search text'
            )
        query_lengths = [
            len(re.findall(r"[A-Za-z0-9'-]+", query))
            for query in queries
        ]
        if any(length < 3 or length > 9 for length in query_lengths):
            return None, f'position {position} stock query is not concise'
        return {
            'narration': narration,
            'visual_queries': queries,
            'ai_prompt': None,
        }, ''

    accepted_rows: dict[int, dict] = {}
    failed_candidates: dict[int, dict] = {}
    pending_positions = list(stock_positions)
    feedback_by_position: dict[int, str] = {}
    final_critic_reviews: dict[int, dict] = {}
    generator_calls = 0
    critic_calls = 0
    last_failures: dict[int, str] = {
        position: 'not reviewed yet'
        for position in stock_positions
    }

    for attempt in range(2):
        request_positions = list(pending_positions)
        current_story = [
            {
                'position': position,
                'route': 'stock' if position in stock_positions else 'ai',
                'role': role_by_position.get(position),
                'narration': (
                    accepted_rows[position]['narration']
                    if position in accepted_rows
                    else scene.get('narration')
                ),
            }
            for position, scene in enumerate(scenes)
        ]
        current_narrations = {
            row['position']: row.get('narration')
            for row in current_story
        }
        request_targets = [
            {
                **targets_by_position[position],
                'previous_narration': (
                    current_narrations.get(position - 1)
                    if position > 0 else None
                ),
                'next_narration': (
                    current_narrations.get(position + 1)
                    if position + 1 < len(scenes) else None
                ),
                'previous_failed_candidate': failed_candidates.get(position),
                'validation_feedback': feedback_by_position.get(position),
            }
            for position in request_positions
        ]
        response_shape = {
            'scenes': [
                {
                    'position': position,
                    'narration': '...',
                    'visual_queries': ['...', '...'],
                    'ai_prompt': None,
                }
                for position in request_positions
            ],
        }
        generation_context = {
            'requested_brief': requested_brief,
            'title': package.get('title'),
            'whole_story_word_budget': {
                'minimum': minimum_total_words,
                'target': target_total_words,
                'maximum': maximum_total_words,
            },
            'complete_original_story_in_order': original_story,
            'complete_current_story_in_order': current_story,
            'accepted_stock_scenes_locked': [
                {
                    'position': position,
                    'role': role_by_position[position],
                    **accepted_rows[position],
                }
                for position in sorted(accepted_rows)
            ],
            'stock_positions_to_rewrite': request_targets,
        }
        generator_input = f'''You are repairing the requested stock-routed scenes of a 30-second premium YouTube story before footage search.
Language of spoken narration: {language_name}
Story context:
{json.dumps(generation_context, ensure_ascii=False)}

Return ONLY JSON in exactly this shape:
{json.dumps(response_shape, ensure_ascii=False)}

NON-NEGOTIABLE RULES:
- Return exactly the requested positions and no others. Never rewrite an accepted locked stock scene or an AI-routed mechanism scene.
- When a target contains locked_narration, copy that narration exactly, character for character. Repair only visual_queries; never paraphrase, punctuate, pad or otherwise edit the locked spoken text.
- Preserve every explicit positive, negative, routing and continuity constraint in requested_brief. Never introduce an actor, object, action, setting, screen state or payoff that the brief forbids.
- Respect each requested scene's allowed_word_count range. Keep the complete story within whole_story_word_budget; exact per-scene equality is neither required nor desirable.
- Never add empty padding such as "bugün", "şimdi", "sakinlikle" or "dikkatlice" unless that word changes the visible action and sounds necessary in normal speech.
- Each narration describes ONE visible human or physical action in ONE ordinary location.
- Every spoken clause must be literally visible in the same common five-second stock clip. Do not append an abstract hook, comparison, mystery, lesson or recap.
- {CONTINUITY_DEICTIC_RULE}
- Use one simple sentence. Do not combine distinct actions, even with a conjunction, gerund, sequence or subordinate clause.
- Do not use a semicolon or colon to join actions.
- Do not mention or recap OLED, pixels, GPS, Wi-Fi, cellular signals, location systems, QR, error correction, timing, algebra or invisible mechanisms.
- ai_prompt must be JSON null.
- Give exactly 2-3 simple ENGLISH stock search phrases per scene.
- First choose one canonical actor/object, one action verb phrase and one ordinary setting. Repeat that same semantic contract in every query; vary only framing or camera distance.
- Every query must contain 3-9 English words and depict the narration's exact same single action.
- Keep each scene faithful to its supplied role and add no new fact, product or unrelated activity.
- A hook must be one concrete everyday action that opens naturally into the next technical scene.
- A bridge or penultimate scene must connect its immediate neighbors without repeating their mechanism.
- The penultimate and payoff scenes are one continuous two-beat action by the same person or object, seconds apart in the SAME named micro-location.
- Name the same concrete micro-location in both ending query sets and establish it explicitly in the penultimate narration. The payoff narration may use a minimal deictic under the adjacent-continuity rule above, but it must not invent or widen the referent. A venue-level match is insufficient if one shot is at a counter and the other is outside.
- Never use an exit, journey, new room, later time of day or home/store/street jump as the payoff.
- A payoff must visibly complete the preceding action and show the everyday benefit, not merely state a conclusion.
- Keep the spoken narration natural and easy to pronounce in {language_name}; for Turkish, use meaning-first native wording and never raw technical abbreviations.
- In Turkish, express causality as a natural condition. Use wording such as “sıcak hava içeride kalınca” or “sıcak hava sıkışınca”; never write translated energy-agent phrases such as “sıkışan ısı fanı hızlandırıyor” or “açılan boşluk fanı yavaşlatıyor”.
- Keep production-only wardrobe, color-continuity, camera-direction, shot-size, face-visibility and framing notes in visual_queries, not spoken narration. Phrases such as “koyu lacivert tişörtlü Mert” or “arkadan izliyor” are not human narration when they exist only to control the picture.
- When validation_feedback names natural_spoken_language, rewrite formal, translated or textbook-like wording as something a Turkish speaker would naturally say aloud while preserving the exact visible meaning.
'''

        if not request_positions:
            data = {'scenes': []}
        else:
            generator_calls += 1
            if plan_provider == 'gemini':
                data = generate_gemini_json(
                    generator_input,
                    api_key=str(
                        getattr(settings, 'gemini_api_key', '') or ''
                    ),
                    model=str(
                        getattr(settings, 'gemini_model', GEMINI_DEFAULT_MODEL)
                        or GEMINI_DEFAULT_MODEL
                    ),
                    json_schema=_stock_writer_json_schema(request_positions),
                    google_search=False,
                    thinking_level='medium' if attempt else 'low',
                )
            else:
                response = client.responses.create(
                    model=settings.openai_model,
                    reasoning={'effort': 'medium' if attempt else 'low'},
                    input=generator_input,
                )
                try:
                    data = _json(response.output_text)
                except Exception:
                    data = {}

        global_generation_error = ''
        if not data:
            global_generation_error = 'response was not valid JSON'
        if data and set(data.keys()) != {'scenes'}:
            global_generation_error = 'response must contain exactly the scenes key'
        rows = data.get('scenes') if isinstance(data, dict) else None
        if not global_generation_error and (
            not isinstance(rows, list)
            or len(rows) != len(request_positions)
        ):
            global_generation_error = (
                f'expected exactly {len(request_positions)} requested scene objects'
            )

        expected_row_keys = {'position', 'narration', 'visual_queries', 'ai_prompt'}
        rows_by_position: dict[int, dict] = {}
        if not global_generation_error:
            for row in rows:
                if not isinstance(row, dict) or set(row.keys()) != expected_row_keys:
                    global_generation_error = (
                        'each scene must contain exactly position, narration, '
                        'visual_queries and ai_prompt'
                    )
                    break
                position = row.get('position')
                if type(position) is not int:
                    global_generation_error = 'scene position must be an integer'
                    break
                if position not in request_positions:
                    global_generation_error = (
                        f'unexpected requested scene position {position}'
                    )
                    break
                if position in rows_by_position:
                    global_generation_error = f'duplicate scene position {position}'
                    break
                rows_by_position[position] = row
            if (
                not global_generation_error
                and set(rows_by_position) != set(request_positions)
            ):
                global_generation_error = (
                    'requested scene positions were not returned exactly once'
                )

        deterministic_errors: dict[int, str] = {}
        if global_generation_error:
            deterministic_errors = {
                position: global_generation_error
                for position in request_positions
            }
        else:
            for position in request_positions:
                candidate, error = validate_generated_row(
                    position,
                    rows_by_position[position],
                )
                if error:
                    deterministic_errors[position] = error
                    failed_candidates[position] = {
                        'narration': rows_by_position[position].get('narration'),
                        'visual_queries': rows_by_position[position].get('visual_queries'),
                    }
                elif candidate:
                    accepted_rows[position] = candidate
                    failed_candidates.pop(position, None)

        if deterministic_errors:
            pending_positions = sorted(deterministic_errors)
            feedback_by_position = deterministic_errors
            last_failures = dict(deterministic_errors)
            if attempt == 0:
                continue
            break

        if set(accepted_rows) != set(stock_positions):
            missing = sorted(set(stock_positions) - set(accepted_rows))
            last_failures = {
                position: 'candidate was not available for full-story review'
                for position in missing
            }
            pending_positions = missing
            feedback_by_position = dict(last_failures)
            if attempt == 0:
                continue
            break

        candidate_total_words = sum(
            _word_count(
                accepted_rows[position]['narration']
                if position in accepted_rows
                else str(scene.get('narration') or '')
            )
            for position, scene in enumerate(scenes)
        )
        if not minimum_total_words <= candidate_total_words <= maximum_total_words:
            total_error = (
                f'complete story has {candidate_total_words} narration words; '
                f'expected {minimum_total_words}-{maximum_total_words}'
            )
            last_failures = {
                position: total_error
                for position in request_positions
            }
            if attempt == 0:
                pending_positions = list(request_positions)
                feedback_by_position = dict(last_failures)
                for position in request_positions:
                    failed_candidates[position] = dict(accepted_rows[position])
                    accepted_rows.pop(position, None)
                continue
            break

        candidate_story = [
            {
                'position': position,
                'route': 'stock' if position in stock_positions else 'ai',
                'role': role_by_position.get(position),
                'narration': (
                    accepted_rows[position]['narration']
                    if position in accepted_rows
                    else scene.get('narration')
                ),
                'visual_queries': (
                    accepted_rows[position]['visual_queries']
                    if position in accepted_rows
                    else scene.get('visual_queries') or []
                ),
                'ai_prompt': (
                    accepted_rows[position]['ai_prompt']
                    if position in accepted_rows
                    else scene.get('ai_prompt')
                ),
            }
            for position, scene in enumerate(scenes)
        ]
        ending_positions = [len(scenes) - 2, len(scenes) - 1]
        story_boolean_keys = {
            'all_explicit_brief_constraints_preserved',
            'single_human_situation',
            'single_central_question',
            'not_fact_montage',
            'causal_scene_chain',
            'same_actor_or_object_thread',
            'human_payoff_visible',
            'natural_spoken_language',
            'directly_answers_requested_topic',
            'one_specific_useful_reveal',
            'causal_claim_supported',
            'hook_payoff_same_promise',
        }
        ending_boolean_keys = {
            'same_immediate_location',
            'continuous_visible_action_chain',
            'same_actor_or_object_thread',
            'everyday_benefit_visible',
            'explicit_technical_insert_return_contract_satisfied',
        }
        critic_boolean_keys = {
            'single_sentence',
            'single_visible_action',
            'single_ordinary_location',
            'all_spoken_meaning_visible',
            'no_invisible_or_abstract_claim',
            'all_named_subjects_coexist',
            'queries_are_english',
            'queries_match_same_action',
            'common_stock_clip_feasible',
            'continues_from_previous',
            'leads_to_next',
            'preserves_story_role',
            'adds_no_new_fact',
        }
        critic_shape = {
            'story_review': {
                **{key: True for key in sorted(story_boolean_keys)},
                'central_question': 'one precise human question',
                'causal_answer': 'one supported causal reveal',
                'visible_payoff': 'one visible everyday benefit',
                'natural_spoken_language_evidence': (
                    'PASS, or scene N plus an exact quote and the spoken-language issue'
                ),
                'reason': 'brief evidence-based whole-story verdict',
            },
            'ending_pair': {
                'penultimate_position': ending_positions[0],
                'final_position': ending_positions[1],
                **{key: True for key in sorted(ending_boolean_keys)},
                'location_anchor': 'same exact counter, table, doorway or room',
                'reason': 'brief evidence-based ending-pair verdict',
            },
            'scenes': [
                {
                    'position': position,
                    'single_sentence': True,
                    'single_visible_action': True,
                    'single_ordinary_location': True,
                    'all_spoken_meaning_visible': True,
                    'no_invisible_or_abstract_claim': True,
                    'all_named_subjects_coexist': True,
                    'queries_are_english': True,
                    'queries_match_same_action': True,
                    'common_stock_clip_feasible': True,
                    'continues_from_previous': True,
                    'leads_to_next': True,
                    'preserves_story_role': True,
                    'adds_no_new_fact': True,
                    'reason': 'brief evidence-based verdict',
                }
                for position in stock_positions
            ],
        }
        critic_context = {
            'requested_topic': requested_brief,
            'title': package.get('title'),
            'description': package.get('description'),
            'sources': [
                {
                    'url': str(source.get('url') or '')[:500],
                    'evidence': str(source.get('evidence') or '')[:500],
                }
                for source in (package.get('sources') or [])[:6]
                if isinstance(source, dict)
            ],
            'candidate_story_in_order': candidate_story,
            'candidate_stock_scenes': [
                {
                    'position': position,
                    'role': role_by_position[position],
                    **accepted_rows[position],
                }
                for position in stock_positions
            ],
        }
        critic_input = f'''Act as an independent, fail-closed stock-shot feasibility critic. Do not rewrite anything.
Evaluate every stock-routed candidate against its exact narration, queries, role, adjacent scenes and complete short story.
{json.dumps(critic_context, ensure_ascii=False)}

Return ONLY JSON in exactly this shape:
{json.dumps(critic_shape, ensure_ascii=False)}

Review the WHOLE story before reviewing individual stock shots. Set each story_review boolean independently and false whenever evidence is ambiguous.
- all_explicit_brief_constraints_preserved: every explicit structural, routing, continuity, required-element and forbidden-element constraint in requested_topic is obeyed by the complete candidate story, including narration, visual queries and ai_prompt routes. False if any explicit constraint is omitted, contradicted or replaced by a generic payoff. A wardrobe, camera or framing constraint is preserved when it is explicit in the applicable visual_queries or ai_prompt; never require production-only metadata to be spoken merely to prove compliance.
- single_human_situation: the short follows one concrete everyday situation a person can care about.
- single_central_question: one curiosity or problem is opened and resolved.
- not_fact_montage: the story is not a sampler, listicle or collage of unrelated mechanisms, products or clever facts.
- causal_scene_chain: every scene advances the same cause-and-effect answer rather than merely sharing a broad topic.
- same_actor_or_object_thread: one recognisable person or object gives the story continuity.
- human_payoff_visible: the last beat visibly delivers an everyday benefit that earns the hook.
- natural_spoken_language: all narration is idiomatic, breath-friendly {language_name}, without translationese, unsafe suffix-attached abbreviations or unsupported foreign terms. For Turkish, this is false when heat, energy or an opened gap becomes an awkward translated grammatical agent, as in “sıkışan ısı fanı hızlandırıyor” or “açılan boşluk fanı yavaşlatıyor”; natural causality says that hot air stays trapped and the fan then changes speed. It is also false when narration verbalizes wardrobe/color continuity, camera direction, shot size, face visibility or framing solely to control production, as in “koyu lacivert tişörtlü Mert ... arkadan izliyor”. Keep that metadata in visual fields unless it changes the story's human meaning.
- directly_answers_requested_topic: the actual hook, reveal and payoff directly answer the supplied topic rather than drifting to a merely coherent side story.
- one_specific_useful_reveal: the viewer learns one non-obvious, useful or genuinely surprising thing worth thirty seconds.
- causal_claim_supported: independently verify the central cause-and-effect explanation against the supplied source URLs and evidence. Use bounded web search when the evidence is insufficient; false if the claim cannot be verified or overstates a source.
- hook_payoff_same_promise: the ending visibly fulfills the exact curiosity opened by the hook.
central_question, causal_answer and visible_payoff must each be one short, concrete, non-empty summary grounded in the candidate story.
natural_spoken_language_evidence must begin with PASS when natural_spoken_language is true. When it is false, it must name the scene position, quote the exact offending words and explain the concrete spoken-language problem. Never use the general reason to hide or contradict this language evidence.
If any story_review boolean is false, the general reason must name the failed key and discuss only concrete failure evidence, not summarize checks that passed.
A whole-story failure is fatal: do not approve a polished shot plan for a bad idea.

Review ending_pair jointly. The positions must match the supplied final two indexes exactly.
- same_immediate_location: both beats occur in the same named micro-location, such as the same café counter, desk, doorway or room. Same venue but counter-to-street is false.
- continuous_visible_action_chain: the payoff is the immediately following visible action, seconds later, with no exit, travel, new room, new day or time-of-day jump.
- same_actor_or_object_thread: the same person or object carries both ending beats.
- everyday_benefit_visible: the final action visibly completes the preceding action and shows the benefit.
- explicit_technical_insert_return_contract_satisfied: true when requested_topic has no explicit numbered technical-insert return contract. When requested_topic does explicitly number and AI-route the penultimate beat as a technical macro, cutaway, cross-section or inside-the-mechanism insert and the final beat straight back to the same enclosing ordinary setting, set this true only if the candidate obeys that exact route, the insert reveals the mechanism of the same recurring object, and there is no travel, new room, new day or unrelated venue. Otherwise false. A satisfied narrow insert may have same_immediate_location=false because the camera temporarily enters the object; ordinary location changes, implicit routes and generic thematic continuity never qualify for the exception.
location_anchor must name the exact shared micro-location; reason must cite concrete evidence.

For EACH requested position, set every boolean independently. If evidence is ambiguous, set it false.
- single_sentence: narration contains only one sentence.
- single_visible_action: narration requires exactly one visible action, not two actions joined by a conjunction, gerund, sequence or implied cut.
- single_ordinary_location: narration and every query can share one ordinary physical setting.
- all_spoken_meaning_visible: every spoken clause is directly visible in that single clip.
- Apply this exact narrow semantic rule when judging all_spoken_meaning_visible: {CONTINUITY_DEICTIC_RULE}
- no_invisible_or_abstract_claim: there is no magic-like hook, comparison, mystery, technical implication or spoken conclusion.
- all_named_subjects_coexist: one normal five-second stock clip can visibly contain every named subject and object.
- queries_are_english: every query is idiomatic English stock-search text.
- queries_match_same_action: every query depicts the narration's exact same actor/object, action and setting.
- common_stock_clip_feasible: the exact shot is realistically common in stock libraries, not merely imaginable.
- continues_from_previous: it follows the previous scene; for position 0 this boundary check is true.
- leads_to_next: it leads naturally to the next scene; for the final position this boundary check is true.
- preserves_story_role: hook, bridge, penultimate or payoff behavior matches the supplied role.
- adds_no_new_fact: it introduces no unsupported claim, product or unrelated activity.
The reason must name concrete evidence for the verdict. Individual shot approval requires all thirteen booleans to be true.
'''
        critic_request = {
            'model': settings.openai_model,
            'reasoning': {'effort': 'medium'},
            'tools': [{'type': 'web_search', 'search_context_size': 'low'}],
            'tool_choice': 'auto',
            'max_tool_calls': 1,
            'input': critic_input,
        }
        critic_schema = _contract_schema(critic_shape)
        expected_story_keys = {
            'central_question',
            'causal_answer',
            'visible_payoff',
            'natural_spoken_language_evidence',
            'reason',
            *story_boolean_keys,
        }
        expected_ending_keys = {
            'penultimate_position',
            'final_position',
            'location_anchor',
            'reason',
            *ending_boolean_keys,
        }
        expected_critic_keys = {'position', 'reason', *critic_boolean_keys}
        critic = {}
        story_review = None
        ending_pair = None
        story_failure = ''
        failed_story_checks: list[str] = []
        natural_language_evidence = ''
        critic_rows = None
        critic_by_position: dict[int, dict] = {}
        critic_global_error = ''

        for critic_attempt in range(2):
            critic_calls += 1
            critic = {}
            story_review = None
            ending_pair = None
            story_failure = ''
            failed_story_checks = []
            natural_language_evidence = ''
            critic_rows = None
            critic_by_position = {}
            critic_global_error = ''
            if plan_provider == 'gemini':
                try:
                    critic = generate_gemini_json(
                        critic_input,
                        api_key=str(
                            getattr(settings, 'gemini_api_key', '') or ''
                        ),
                        model=str(
                            getattr(
                                settings,
                                'gemini_model',
                                GEMINI_DEFAULT_MODEL,
                            ) or GEMINI_DEFAULT_MODEL
                        ),
                        json_schema=critic_schema,
                        google_search=False,
                        thinking_level='medium',
                    )
                except GeminiGenerationError:
                    critic_global_error = (
                        'independent stock-shot critic returned invalid JSON'
                    )
            else:
                critic_response = client.responses.create(**critic_request)
                try:
                    critic = _json(critic_response.output_text)
                except Exception:
                    critic_global_error = (
                        'independent stock-shot critic returned invalid JSON'
                    )
            if (
                critic
                and set(critic.keys()) != {'story_review', 'ending_pair', 'scenes'}
            ):
                critic_global_error = (
                    'independent stock-shot critic returned an invalid object'
                )
            story_review = (
                critic.get('story_review')
                if isinstance(critic, dict)
                else None
            )
            ending_pair = (
                critic.get('ending_pair')
                if isinstance(critic, dict)
                else None
            )
            if not critic_global_error:
                if (
                    not isinstance(story_review, dict)
                    or set(story_review.keys()) != expected_story_keys
                ):
                    critic_global_error = (
                        'whole-story critic returned the wrong fields'
                    )
                else:
                    failed_story_checks = sorted(
                        key
                        for key in story_boolean_keys
                        if story_review.get(key) is not True
                    )
                    story_reason = str(
                        story_review.get('reason') or ''
                    ).strip()
                    natural_language_evidence = str(
                        story_review.get('natural_spoken_language_evidence') or ''
                    ).strip()
                    story_summaries = {
                        key: str(story_review.get(key) or '').strip()
                        for key in (
                            'central_question',
                            'causal_answer',
                            'visible_payoff',
                        )
                    }
                    if not story_reason:
                        failed_story_checks.append('missing_evidence')
                        story_reason = 'critic omitted whole-story evidence'
                    if not natural_language_evidence:
                        failed_story_checks.append(
                            'missing_natural_spoken_language_evidence'
                        )
                    elif story_review.get('natural_spoken_language') is True:
                        if not re.match(
                            r'^pass\b',
                            natural_language_evidence,
                            flags=re.IGNORECASE,
                        ):
                            failed_story_checks.append(
                                'inconsistent_natural_spoken_language_evidence'
                            )
                    elif (
                        re.match(
                            r'^pass\b',
                            natural_language_evidence,
                            flags=re.IGNORECASE,
                        )
                        or not re.search(
                            r'\bscene\s+\d+\b',
                            natural_language_evidence,
                            flags=re.IGNORECASE,
                        )
                        or not any(
                            quote in natural_language_evidence
                            for quote in ('"', '“', '”')
                        )
                    ):
                        failed_story_checks.append(
                            'inconsistent_natural_spoken_language_evidence'
                        )
                    for key, value in story_summaries.items():
                        if not value:
                            failed_story_checks.append(f'missing_{key}')
                    if failed_story_checks:
                        failure_reason = (
                            natural_language_evidence
                            if failed_story_checks == ['natural_spoken_language']
                            else story_reason
                        )
                        story_failure = (
                            f'{", ".join(failed_story_checks)}; '
                            f'{failure_reason[:180]}'
                        )
                if not critic_global_error and (
                    not isinstance(ending_pair, dict)
                    or set(ending_pair.keys()) != expected_ending_keys
                    or type(ending_pair.get('penultimate_position')) is not int
                    or type(ending_pair.get('final_position')) is not int
                    or ending_pair.get('penultimate_position') != ending_positions[0]
                    or ending_pair.get('final_position') != ending_positions[1]
                ):
                    critic_global_error = (
                        'ending-pair critic returned an invalid contract'
                    )
            critic_rows = (
                critic.get('scenes')
                if isinstance(critic, dict)
                else None
            )
            if not critic_global_error and (
                not isinstance(critic_rows, list)
                or len(critic_rows) != len(stock_positions)
            ):
                critic_global_error = (
                    'independent stock-shot critic did not review every stock scene'
                )
            if not critic_global_error:
                for row in critic_rows:
                    if (
                        not isinstance(row, dict)
                        or set(row.keys()) != expected_critic_keys
                    ):
                        critic_global_error = (
                            'independent critic returned the wrong fields'
                        )
                        break
                    position = row.get('position')
                    if (
                        type(position) is not int
                        or position not in stock_positions
                    ):
                        critic_global_error = (
                            'independent critic returned an invalid stock position'
                        )
                        break
                    if position in critic_by_position:
                        critic_global_error = (
                            f'independent critic repeated position {position}'
                        )
                        break
                    critic_by_position[position] = row
                if (
                    not critic_global_error
                    and set(critic_by_position) != set(stock_positions)
                ):
                    critic_global_error = (
                        'independent critic missed a requested stock position'
                    )
            if not critic_global_error:
                break

        if not critic_global_error and story_failure:
            if (
                allow_natural_language_repair
                and failed_story_checks == ['natural_spoken_language']
            ):
                raise _NaturalSpokenLanguageRepairRequired(
                    natural_language_evidence
                )
            if (
                allow_explicit_brief_repair
                and failed_story_checks
                == ['all_explicit_brief_constraints_preserved']
            ):
                raise _WholeStoryRepairRequired(
                    sorted(set(failed_story_checks)),
                    story_failure,
                )
            failure_details = {
                'generator_calls': generator_calls,
                'critic_calls': critic_calls,
                'reason': story_failure[:220],
            }
            raise RuntimeError(
                'Director rejected an incoherent short-preview story before paid media: '
                + json.dumps(
                    failure_details,
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
            )

        critic_failures: dict[int, str] = {}
        parsed_reviews: dict[int, dict] = {}
        if critic_global_error:
            critic_failures = {
                position: critic_global_error
                for position in stock_positions
            }
            last_failures = dict(critic_failures)
            break
        else:
            for position in stock_positions:
                row = critic_by_position[position]
                failed_checks = sorted(
                    key
                    for key in critic_boolean_keys
                    if row.get(key) is not True
                )
                reason = str(row.get('reason') or '').strip()
                if not reason:
                    failed_checks.append('missing_evidence')
                    reason = 'critic omitted evidence'
                parsed_reviews[position] = {
                    'position': position,
                    'accepted': not failed_checks,
                    'failed_checks': failed_checks,
                    'reason': reason[:160],
                }
                if failed_checks:
                    critic_failures[position] = (
                        f'{", ".join(failed_checks)}; {reason[:160]}'
                    )

        ending_failed_checks: list[str] = []
        ending_reason = ''
        ending_location_anchor = ''
        technical_insert_return_exception_applied = False
        if not critic_global_error:
            ending_failed_checks = sorted(
                key
                for key in ending_boolean_keys
                if ending_pair.get(key) is not True
            )
            ending_reason = str(ending_pair.get('reason') or '').strip()
            ending_location_anchor = str(
                ending_pair.get('location_anchor') or ''
            ).strip()
            if not ending_reason:
                ending_failed_checks.append('missing_evidence')
                ending_reason = 'critic omitted ending-pair evidence'
            if not ending_location_anchor:
                ending_failed_checks.append('missing_location_anchor')
            if (
                ending_failed_checks == ['same_immediate_location']
                and explicit_technical_insert_return_contract
                and ending_pair.get(
                    'explicit_technical_insert_return_contract_satisfied'
                ) is True
            ):
                ending_failed_checks = []
                technical_insert_return_exception_applied = True
            if ending_failed_checks:
                pair_failure = (
                    f'ending pair: {", ".join(ending_failed_checks)}; '
                    f'{ending_reason[:160]}'
                )
                ai_routed_ending_positions = [
                    position
                    for position in ending_positions
                    if position not in stock_positions
                ]
                if ai_routed_ending_positions:
                    raise RuntimeError(
                        'Director rejected an AI-routed short-preview ending '
                        'before paid media: '
                        + json.dumps(
                            {
                                'positions': ai_routed_ending_positions,
                                'failed_checks': ending_failed_checks,
                                'reason': ending_reason[:180],
                            },
                            ensure_ascii=False,
                            separators=(',', ':'),
                        )
                    )
                for position in ending_positions:
                    critic_failures[position] = pair_failure
                    review = parsed_reviews.get(position)
                    if review is not None:
                        review['accepted'] = False
                        review['failed_checks'] = sorted({
                            *review.get('failed_checks', []),
                            *[f'ending_pair.{key}' for key in ending_failed_checks],
                        })
                        review['reason'] = pair_failure[:160]

        gemini_attestation = None
        if not critic_failures:
            if plan_provider == 'gemini':
                selected_model = str(
                    getattr(
                        settings,
                        'gemini_model',
                        GEMINI_DEFAULT_MODEL,
                    ) or GEMINI_DEFAULT_MODEL
                ).strip()
                gemini_attestation = {
                    'accepted': True,
                    'model': selected_model,
                    'contract': _STORY_STOCK_CONTRACT,
                    'reviewed_scene_count': len(
                        critic_shape.get('scenes') or []
                    ),
                }
            else:
                gemini_attestation = run_optional_gemini_critic(
                    critic_context,
                    critic_shape,
                    enabled=getattr(
                        settings,
                        'gemini_critic_enabled',
                        False,
                    ),
                    api_key=getattr(settings, 'gemini_api_key', ''),
                    model=getattr(
                        settings,
                        'gemini_model',
                        GEMINI_DEFAULT_MODEL,
                    ),
                    allowed_false_paths=(
                        frozenset({
                            '$.ending_pair.same_immediate_location',
                        })
                        if technical_insert_return_exception_applied
                        else frozenset()
                    ),
                )
                if gemini_attestation is not None:
                    gemini_attestation = dict(gemini_attestation)
                    gemini_attestation['contract'] = _STORY_STOCK_CONTRACT
            final_critic_reviews = parsed_reviews
            repaired = dict(package)
            repaired_scenes = [dict(scene) for scene in scenes]
            for position in stock_positions:
                repaired_scenes[position]['narration'] = accepted_rows[position]['narration']
                repaired_scenes[position]['tts_text'] = accepted_rows[position]['narration']
                repaired_scenes[position]['visual_queries'] = accepted_rows[position]['visual_queries']
                repaired_scenes[position]['ai_prompt'] = None

            non_target_positions = [
                position
                for position in range(len(scenes))
                if position not in stock_positions
            ]
            invariants_ok = (
                len(repaired_scenes) == len(scenes)
                and all(
                    repaired_scenes[position] == scenes[position]
                    for position in non_target_positions
                )
                and [
                    scene.get('index')
                    for scene in repaired_scenes
                ] == [
                    scene.get('index')
                    for scene in scenes
                ]
            )
            if not invariants_ok:
                last_failures = {
                    position: 'repair changed a non-target scene or scene index'
                    for position in stock_positions
                }
                break

            repaired['scenes'] = repaired_scenes
            repaired['narration'] = ' '.join(
                scene['narration']
                for scene in repaired_scenes
            )
            repaired['tts_narration'] = repaired['narration']
            if (
                exact_narration_lock is not None
                and _normalize_exact_narration(repaired['narration'])
                != exact_narration_lock
            ):
                last_failures = {
                    position: 'repair changed exact locked narration'
                    for position in stock_positions
                }
                break
            repaired_total_words = _word_count(repaired['narration'])
            if not (
                minimum_total_words
                <= repaired_total_words
                <= maximum_total_words
            ):
                last_failures = {
                    position: (
                        f'repair produced {repaired_total_words} total words; '
                        f'expected {minimum_total_words}-{maximum_total_words}'
                    )
                    for position in stock_positions
                }
                break
            repaired['visual_queries'] = [
                query
                for scene in repaired_scenes
                for query in (scene.get('visual_queries') or [])
            ]
            repaired['ai_scenes'] = [
                scene['ai_prompt']
                for scene in repaired_scenes
                if scene.get('ai_prompt')
            ]
            if len(repaired['ai_scenes']) > original_ai_count:
                last_failures = {
                    position: 'repair increased the authored AI-scene count'
                    for position in stock_positions
                }
                break

            qc_summary = repaired.get('director_qc') or []
            if not isinstance(qc_summary, list):
                qc_summary = [str(qc_summary)]
            repaired['director_qc'] = [
                *qc_summary,
                (
                    'Locked every short-preview stock scene to independently '
                    'verified single-action coverage.'
                ),
            ]
            stock_scene_qc = {
                'version': _STOCK_SCENE_QC_VERSION,
                'target_positions': stock_positions,
                'roles': [
                    {
                        'position': position,
                        'role': role_by_position[position],
                    }
                    for position in stock_positions
                ],
                'generator_calls': generator_calls,
                'critic_calls': critic_calls,
                'attempts_used': generator_calls,
                'story_review': {
                    'accepted': True,
                    'reason': str(story_review.get('reason') or '')[:180],
                },
                'ending_pair_review': {
                    'accepted': True,
                    'positions': ending_positions,
                    'technical_insert_return_exception': (
                        technical_insert_return_exception_applied
                    ),
                    'location_anchor': ending_location_anchor[:120],
                    'reason': ending_reason[:180],
                },
                'reviews': [
                    final_critic_reviews[position]
                    for position in stock_positions
                ],
            }
            if gemini_attestation is not None:
                stock_scene_qc['gemini_critic'] = gemini_attestation
            repaired['stock_scene_qc'] = stock_scene_qc
            return repaired

        last_failures = dict(critic_failures)
        if attempt == 0:
            pending_positions = sorted(critic_failures)
            feedback_by_position = dict(critic_failures)
            for position in pending_positions:
                failed_candidates[position] = dict(accepted_rows[position])
                accepted_rows.pop(position, None)
            continue
        break

    failure_details = {
        'positions': stock_positions,
        'generator_calls': generator_calls,
        'critic_calls': critic_calls,
        'failures': [
            {
                'position': position,
                'reason': str(reason)[:140],
            }
            for position, reason in sorted(last_failures.items())
        ],
    }
    raise RuntimeError(
        'Director could not produce fully stock-safe short-preview scenes: '
        + json.dumps(failure_details, ensure_ascii=False, separators=(',', ':'))
    )


_SAFE_WHOLE_STORY_CHECK_NAMES = frozenset({
    'all_explicit_brief_constraints_preserved',
    'single_human_situation',
    'single_central_question',
    'not_fact_montage',
    'causal_scene_chain',
    'same_actor_or_object_thread',
    'human_payoff_visible',
    'natural_spoken_language',
    'directly_answers_requested_topic',
    'one_specific_useful_reveal',
    'causal_claim_supported',
    'hook_payoff_same_promise',
})


def _safe_short_editorial_issue_categories(issues: list[str]) -> list[str]:
    categories: set[str] = set()
    for raw_issue in issues or []:
        issue = str(raw_issue or '').casefold()
        if 'tts-unsafe raw term' in issue:
            categories.add('tts_unsafe_raw_terms')
        elif 'wardrobe wording paired with' in issue:
            categories.add('wardrobe_camera_metadata_in_narration')
        elif 'production-only camera direction' in issue:
            categories.add('camera_metadata_in_narration')
        elif 'mixes unrelated mechanism families' in issue:
            categories.add('mixed_mechanism_families')
        elif 'translated' in issue or 'translation' in issue:
            categories.add('translationese')
        else:
            categories.add('other_short_editorial_issue')
    return sorted(categories)[:8]


def _whole_story_repair_diagnostics(
    *,
    failed_checks: list[str],
    words: int,
    min_words: int,
    max_words: int,
    scene_count: int,
    target_scenes: int,
    exact_scene_count: bool,
    ai_scene_count: int,
    preview_ai_limit: int | None,
    short_editorial_issues: list[str],
) -> dict:
    supplied_check_names = {str(check) for check in (failed_checks or [])}
    safe_failed_checks = sorted(
        supplied_check_names.intersection(_SAFE_WHOLE_STORY_CHECK_NAMES)
    )
    if len(safe_failed_checks) != len(supplied_check_names):
        safe_failed_checks.append('unknown_story_check')

    failed_deterministic_gates: list[str] = []
    if not min_words <= words <= max_words:
        failed_deterministic_gates.append('narration_word_count')
    if not _scene_count_matches(
        scene_count,
        target_scenes,
        exact_scene_count=exact_scene_count,
    ):
        failed_deterministic_gates.append('scene_count')
    if preview_ai_limit is not None and ai_scene_count > preview_ai_limit:
        failed_deterministic_gates.append('ai_scene_count')
    if short_editorial_issues:
        failed_deterministic_gates.append('short_editorial_issues')

    return {
        'critic_failed_checks': safe_failed_checks[:16],
        'failed_deterministic_gates': failed_deterministic_gates,
        'post_repair_shape': {
            'narration_word_count': int(words),
            'required_narration_word_range': [int(min_words), int(max_words)],
            'scene_count': int(scene_count),
            'target_scene_count': int(target_scenes),
            'exact_scene_count': bool(exact_scene_count),
            'ai_scene_count': int(ai_scene_count),
            'max_ai_scene_count': (
                int(preview_ai_limit)
                if preview_ai_limit is not None
                else None
            ),
            'short_editorial_issue_count': len(short_editorial_issues or []),
            'short_editorial_issue_categories': (
                _safe_short_editorial_issue_categories(
                    short_editorial_issues
                )
            ),
        },
    }


def direct_and_qc(package: dict, topic: str, duration_minutes: float, language: str, options: dict | None = None) -> dict:
    if duration_minutes <= 0.6:
        _story_brief_for_qc(topic)
    provider = _studio_plan_provider()
    explicit_scene_count = _explicit_scene_count_from_brief(topic)
    exact_narration = _exact_narration_lock_from_brief(topic)
    locked_scene_count = (
        _infer_exact_narration_scene_count(exact_narration)
        if exact_narration is not None
        else None
    )
    if (
        explicit_scene_count is not None
        and locked_scene_count is not None
        and explicit_scene_count != locked_scene_count
    ):
        raise RuntimeError(
            'User brief exact scene count conflicts with the unambiguous '
            'exact spoken-narration sentence count'
        )
    immutable_scene_count = (
        explicit_scene_count
        if explicit_scene_count is not None
        else locked_scene_count
    )
    options = dict(options or package.get('studio_options') or {})
    pace_profile = str(options.get('pace') or 'balanced')
    target_words, min_words, max_words = _target_word_budget(duration_minutes)
    exact_scene_count = immutable_scene_count is not None
    target_scenes = (
        immutable_scene_count
        if immutable_scene_count is not None
        else _target_scene_count(duration_minutes, pace_profile)
    )
    scenes = package.get('scenes') or []
    if provider == 'openai' and not settings.openai_api_key:
        if (
            immutable_scene_count is not None
            and len(scenes) != immutable_scene_count
        ):
            raise RuntimeError(
                'User-brief scene-count gate rejected package without a '
                f'director: {len(scenes)} scenes; required exactly '
                f'{immutable_scene_count}'
            )
        locked_package = _apply_exact_narration_lock(
            package,
            topic,
            expected_scene_count=immutable_scene_count,
        )
        locked_budget_issues = (
            _short_preview_scene_budget_issues(
                locked_package,
                target_words,
                target_scenes,
            )
            if exact_narration is not None and duration_minutes <= 0.6
            else []
        )
        if locked_budget_issues:
            raise ImmutableNarrationSceneBudgetError(
                'Exact spoken-narration lock has an oversized short-preview '
                'AI scene and cannot be rewritten or safely rendered as one '
                'single-pass shot: '
                + '; '.join(locked_budget_issues)
            )
        return locked_package
    if not scenes:
        if immutable_scene_count is not None:
            raise RuntimeError(
                'User-brief scene-count gate rejected an empty package; '
                f'required exactly {immutable_scene_count}'
            )
        return package

    client = (
        OpenAI(
            api_key=settings.openai_api_key,
            timeout=90.0,
            max_retries=1,
        )
        if provider == 'openai'
        else None
    )
    language_name = 'Turkish' if language.lower().startswith('tr') else language

    def short_preview_issues(candidate: dict) -> list[str]:
        if duration_minutes > 0.6:
            return []
        budget_issues = _short_preview_scene_budget_issues(
            candidate,
            target_words,
            target_scenes,
        )
        if exact_narration is not None and budget_issues:
            raise ImmutableNarrationSceneBudgetError(
                'Exact spoken-narration lock has an oversized short-preview '
                'AI scene and cannot be rewritten or safely rendered as one '
                'single-pass shot: '
                + '; '.join(budget_issues)
            )
        return [
            *_short_story_quality_issues(candidate, language_name),
            *budget_issues,
        ]

    compact = {
        'title': package.get('title'),
        'thumbnail_text': package.get('thumbnail_text'),
        'description': package.get('description'),
        'scenes': scenes,
        'sources': package.get('sources', []),
    }

    revised = _run_director(
        client, compact, topic, language_name, duration_minutes,
        target_words, min_words, max_words, target_scenes, options,
        exact_scene_count=exact_scene_count,
    )
    out = _apply_exact_narration_lock(
        _clean_package(revised, package),
        topic,
        expected_scene_count=(target_scenes if exact_scene_count else None),
    )
    words = _word_count(out['narration'])
    scene_count = len(out['scenes'])
    ai_scene_count = sum(1 for scene in out['scenes'] if scene.get('ai_prompt'))
    preview_ai_limit = None
    if options.get('mode') == 'preview':
        preview_ai_limit = preview_authored_ai_limit(
            options,
            target_scenes,
            duration_minutes,
        )
        if preview_ai_limit is None:
            preview_ai_limit = 0

    short_editorial_issues = short_preview_issues(out)

    for correction_attempt in range(3):
        ai_count_ok = preview_ai_limit is None or ai_scene_count <= preview_ai_limit
        if (
            min_words <= words <= max_words
            and _scene_count_matches(
                scene_count,
                target_scenes,
                exact_scene_count=exact_scene_count,
            )
            and ai_count_ok
            and not short_editorial_issues
        ):
            break
        correction_input = {
            'title': out.get('title'),
            'thumbnail_text': out.get('thumbnail_text'),
            'description': out.get('description'),
            'scenes': out.get('scenes'),
            'sources': package.get('sources', []),
            'current_word_count': words,
            'current_scene_count': scene_count,
            'current_ai_scene_count': ai_scene_count,
            'max_ai_scene_count': preview_ai_limit,
            'correction_attempt': correction_attempt + 1,
            'narration_quality_issues': short_editorial_issues,
        }
        revised = _run_director(
            client, correction_input, topic, language_name, duration_minutes,
            target_words, min_words, max_words, target_scenes, options, correction=True,
            exact_scene_count=exact_scene_count,
        )
        out = _apply_exact_narration_lock(
            _clean_package(revised, package),
            topic,
            expected_scene_count=(target_scenes if exact_scene_count else None),
        )
        words = _word_count(out['narration'])
        scene_count = len(out['scenes'])
        ai_scene_count = sum(1 for scene in out['scenes'] if scene.get('ai_prompt'))
        short_editorial_issues = short_preview_issues(out)

    if short_editorial_issues:
        failure_details = {
            'issues': [
                str(issue)[:220]
                for issue in short_editorial_issues[:12]
            ],
        }
        raise RuntimeError(
            'Short-preview editorial gate rejected narration before paid media: '
            + json.dumps(
                failure_details,
                ensure_ascii=False,
                separators=(',', ':'),
            )
        )

    if exact_scene_count and scene_count != target_scenes:
        raise RuntimeError(
            'User-brief scene-count gate rejected final director edit before '
            f'paid media: {scene_count} scenes; required exactly {target_scenes}'
        )

    if options.get('mode') == 'preview' and duration_minutes <= 0.6:
        try:
            out = _repair_short_stock_scenes(
                client,
                out,
                language_name,
                duration_minutes,
                topic,
            )
        except _WholeStoryRepairRequired as exc:
            if isinstance(exc, _NaturalSpokenLanguageRepairRequired):
                whole_story_feedback = (
                    'The independent critic set natural_spoken_language=false. '
                    'Rewrite the complete narration as idiomatic, conversational '
                    f'{language_name} without changing the supported causal claim, '
                    'actor/object thread, visual actions, route count or ending '
                    'location. Do not add filler to satisfy a scene quota. '
                    f'Critic evidence: {exc.evidence[:320]}'
                )
            else:
                failed_checks = ', '.join(exc.failed_checks)
                whole_story_feedback = (
                    'The independent fail-closed critic rejected the complete '
                    f'story on these checks: {failed_checks}. Repair every cited '
                    'omission or contradiction while preserving all already valid '
                    'facts, scene positions, route assignments and word limits. '
                    'For all_explicit_brief_constraints_preserved, rebuild a '
                    'scene-by-scene checklist from the complete user Topic and '
                    'make each non-null ai_prompt independently repeat every '
                    'applicable visible identity, dimension, brand state, color, '
                    'wardrobe, setting, continuity and forbidden-element '
                    'constraint. Do not weaken or paraphrase away a requirement. '
                    f'Critic evidence: {exc.evidence[:480]}'
                )
            correction_input = {
                'title': out.get('title'),
                'thumbnail_text': out.get('thumbnail_text'),
                'description': out.get('description'),
                'scenes': out.get('scenes'),
                'sources': package.get('sources', []),
                'current_word_count': words,
                'current_scene_count': scene_count,
                'current_ai_scene_count': ai_scene_count,
                'max_ai_scene_count': preview_ai_limit,
                'correction_attempt': 'whole_story_critic',
                'narration_quality_issues': [whole_story_feedback],
            }
            revised = _run_director(
                client,
                correction_input,
                topic,
                language_name,
                duration_minutes,
                target_words,
                min_words,
                max_words,
                target_scenes,
                options,
                correction=True,
                exact_scene_count=exact_scene_count,
            )
            out = _apply_exact_narration_lock(
                _clean_package(revised, package),
                topic,
                expected_scene_count=(
                    target_scenes if exact_scene_count else None
                ),
            )
            words = _word_count(out['narration'])
            scene_count = len(out['scenes'])
            ai_scene_count = sum(
                1 for scene in out['scenes'] if scene.get('ai_prompt')
            )
            short_editorial_issues = short_preview_issues(out)
            corrected_shape_is_safe = (
                min_words <= words <= max_words
                and _scene_count_matches(
                    scene_count,
                    target_scenes,
                    exact_scene_count=exact_scene_count,
                )
                and (
                    preview_ai_limit is None
                    or ai_scene_count <= preview_ai_limit
                )
                and not short_editorial_issues
            )
            if not corrected_shape_is_safe:
                diagnostics = _whole_story_repair_diagnostics(
                    failed_checks=exc.failed_checks,
                    words=words,
                    min_words=min_words,
                    max_words=max_words,
                    scene_count=scene_count,
                    target_scenes=target_scenes,
                    exact_scene_count=exact_scene_count,
                    ai_scene_count=ai_scene_count,
                    preview_ai_limit=preview_ai_limit,
                    short_editorial_issues=short_editorial_issues,
                )
                raise RuntimeError(
                    'Whole-story critic repair violated a deterministic '
                    'short-preview gate before paid media: '
                    + json.dumps(
                        diagnostics,
                        ensure_ascii=False,
                        separators=(',', ':'),
                    )
                )
            out = _repair_short_stock_scenes(
                client,
                out,
                language_name,
                duration_minutes,
                topic,
                allow_natural_language_repair=False,
                allow_explicit_brief_repair=False,
            )
        words = _word_count(out['narration'])
        scene_count = len(out['scenes'])
        ai_scene_count = sum(1 for scene in out['scenes'] if scene.get('ai_prompt'))
        short_editorial_issues = short_preview_issues(out)
        if short_editorial_issues:
            raise RuntimeError(
                'Short-preview stock repair reintroduced unsafe narration: '
                + json.dumps(
                    {
                        'issues': [
                            str(issue)[:220]
                            for issue in short_editorial_issues[:12]
                        ],
                    },
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
            )

    if words < min_words or words > max_words:
        raise RuntimeError(f'Duration gate rejected script: {words} words for requested {duration_minutes} min (target {min_words}-{max_words})')
    if not _scene_count_matches(
        scene_count,
        target_scenes,
        exact_scene_count=exact_scene_count,
    ):
        requirement = (
            f'required exactly {target_scenes}'
            if exact_scene_count
            else f'target {target_scenes}'
        )
        raise RuntimeError(
            f'Scene-count gate rejected final edit: {scene_count} scenes; '
            + requirement
        )
    if preview_ai_limit is not None and ai_scene_count > preview_ai_limit:
        raise RuntimeError(
            f'Preview AI-scene gate rejected {ai_scene_count} scenes; maximum {preview_ai_limit}'
        )

    out['narration_word_count'] = words
    out['target_word_range'] = [min_words, max_words]
    out['target_scene_count'] = target_scenes
    out['ai_scene_count'] = ai_scene_count
    out['max_ai_scene_count'] = preview_ai_limit
    out['studio_options'] = options
    if options.get('mode') == 'preview' and duration_minutes <= 0.6:
        stock_qc = out.get('stock_scene_qc') or {}
        story_review = stock_qc.get('story_review') or {}
        ending_review = stock_qc.get('ending_pair_review') or {}
        if (
            int(stock_qc.get('version') or 0) < _STOCK_SCENE_QC_VERSION
            or story_review.get('accepted') is not True
            or ending_review.get('accepted') is not True
        ):
            raise RuntimeError(
                'Short-preview QC attestation is missing before paid media'
            )
        out['short_story_qc'] = {
            'version': _SHORT_STORY_QC_VERSION,
            'requested_topic': _normalize_short_story_topic(topic),
            'story_review_accepted': True,
            'ending_pair_accepted': True,
        }
        out['short_story_qc']['fingerprint'] = _short_story_fingerprint(out)
    return out
