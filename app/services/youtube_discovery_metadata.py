"""Bounded, extractive topic metadata; no model, trends or keyword invention."""
from __future__ import annotations

from collections import Counter
import re
import unicodedata


_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
_LINK = re.compile(r'(?:[a-z][a-z0-9+.-]*://|www\.)\S+|\b\S+@\S+\b', re.IGNORECASE)
_INSTRUCTION = re.compile(
    r'\b(?:ignore|disregard|instructions?|prompts?|talimat\w*|yönerge\w*|'
    r'api\s*key|secret|password|şifre|token)\b', re.IGNORECASE,
)
_STOP = frozenset('''
    a an and are as at be by for from how in is it its of on or that the their
    this to was were what when where which who why with your
    put puts make makes made does did
    acaba ama artık asıl aynı bazı ben bile bir bu buna bunu bunun burada böyle
    çünkü da daha de değil diye dünya dünyayı en fakat gibi gün günü gününde
    hakkında hangi her hem hep hiç için ile ilk ise işte kadar kez ki kim nasıl
    ne neden nerede nereye o olan olarak oldu oluyor olay öyle peki sadece sana
    saniye sen sonra şu tam tek tüm ve veya ya yani yıl yılında yılda zaman
    şey şeyi şeyin ürün ürünü üründe hikaye hikayesi hikâye hikâyesi sır sırrı
    sırları gerçek gerçekte gerçekten şaşırtıcı inanılmaz videoda video videosu
    bölüm bölümü izle izleyin abone beğen begen subscribe like follow kaynak
    kaynaklar kaynakça source sources ai aı yapay zeka zekâ destekli viral fyp
    keşfet kesfet trending trend shorts short
    neyden nedir neydi neye nasıldı nasıldır olur olabilir yapılır yapılıyor
    yapılmıyor yapılmış yapıldı üretilir üretiliyor üretilmiyor üretilen satın
    alındı aldı alınan satıldı başladı başlayan değişti değiştirdi tarandı
    taratıldı sanıldığı
    kapandı açıldı ocak şubat mart nisan mayıs haziran temmuz ağustos eylül ekim kasım aralık
    yüzde sıfır iki üç dört beş altı yedi sekiz dokuz on yirmi otuz kırk elli
    altmış yetmiş seksen doksan yüz bin milyon biri ikisi üçü dördü beşi altısı
    yedisi sekizi dokuzu onu
'''.split())
# Small, explicit inflection families, not prefix stemming: for example
# "bankacı" is never silently re-labelled "banka". Surface phrases stay exact.
_FORMS = {
    'barkod': ('barkod', 'barkodu', 'barkodun', 'barkodunu', 'barkoda', 'barkodda',
               'barkoddan', 'barkodla', 'barkodlar', 'barkodları', 'barkodların',
               'barkodlara', 'barkodlarda', 'barkodlardan'),
    'sakız': ('sakız', 'sakızı', 'sakızın', 'sakızını', 'sakızda', 'sakızdan',
             'sakızla', 'sakızlar', 'sakızları', 'sakızların', 'sakızıydı'),
    'banka': ('banka', 'bankayı', 'bankaya', 'bankada', 'bankadan', 'bankayla',
              'bankanın', 'bankası', 'bankalar', 'bankaları', 'bankaların',
              'bankalara', 'bankalarda', 'bankalardan'),
    'dolar': ('dolar', 'doları', 'doların', 'dolarını', 'dolara', 'dolarda', 'dolardan'),
    'banknot': ('banknot', 'banknotu', 'banknotun', 'banknotunu', 'banknota',
               'banknotlar', 'banknotları', 'banknotların'),
    'pamuk': ('pamuk', 'pamuğu', 'pamuğun', 'pamuktan', 'pamukla'),
    'keten': ('keten', 'keteni', 'ketenin', 'ketenden', 'ketenle'),
}
_ANCHOR = {form: base for base, forms in _FORMS.items() for form in forms}


def _fold(text: str) -> str:
    return unicodedata.normalize('NFKC', text).translate(str.maketrans({'I': 'ı', 'İ': 'i'})).casefold()


def _sentences(value: str, maximum: int) -> list[str]:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(unicodedata.category(char) in {'Cc', 'Cf'} and char not in '\n\r\t' for char in value)):
        return []
    if '<' in value or '>' in value:
        return []
    value = _LINK.sub(' ', value)
    sentences = []
    for line in value.splitlines():
        if re.match(r'^\s*(?:kaynak(?:lar|ça)?|sources?)\s*:', line, re.IGNORECASE):
            break  # A citation section can span several following lines.
        for sentence in re.split(r'[.!?;\n]+', line):
            if sentence.strip() and not _INSTRUCTION.search(sentence):
                sentences.append(sentence.strip())
    return sentences


def _words(sentence: str) -> list[dict]:
    matches = list(_WORD.finditer(sentence))[:160]
    words = []
    for index, match in enumerate(matches):
        surface = match.group()
        key = _fold(surface)
        content = 3 <= len(key) <= 40 and key not in _STOP
        words.append({'text': surface, 'key': key, 'content': content,
                      'start': match.start(), 'end': match.end(),
                      'proper': index > 0 and surface[0].isupper() and not surface.isupper()})
    return words


def _hashtag(text: str) -> str:
    text = _ANCHOR.get(_fold(text), text)
    parts = _WORD.findall(text)
    result = ''
    for part in parts:
        word = _fold(part).replace("'", '').replace('’', '')
        first = {'i': 'İ', 'ı': 'I'}.get(word[0], word[0].upper()) if word else ''
        result += first + word[1:]
    return result if 3 <= len(result) <= 60 else ''


def topic_metadata_fallback(title: str, description: str) -> dict[str, list[str]]:
    """Extract at most six literal topic tags and three topic hashtags.

    Only the already-authored title/body belongs here, before series numbers,
    citations and channel footer are appended. Description-only common words
    must recur; named entities and explicit subject inflections can occur once.
    Returning fewer terms is preferable to padding with unrelated SEO slogans.
    """
    title_sentences = _sentences(title, 500)
    body_sentences = _sentences(description, 5000)
    rows = [(True, text, _words(text)) for text in title_sentences]
    rows += [(False, text, _words(text)) for text in body_sentences]
    frequency = Counter(word['key'] for _, _, words in rows for word in words if word['content'])
    candidates: dict[str, tuple[int, int, str, bool]] = {}
    order = 0

    def add(text: str, priority: int, single: bool) -> None:
        nonlocal order
        key = _ANCHOR.get(_fold(text), _fold(text)) if single else _fold(text)
        if not text or len(text) > 100 or key in _STOP:
            return
        prior = candidates.get(key)
        if prior is None:
            candidates[key] = (priority, order, text, single)
            order += 1
        elif priority > prior[0]:
            candidates[key] = (priority, prior[1], prior[2], single)

    for in_title, sentence, words in rows:
        for index, word in enumerate(words):
            if not word['content']:
                continue
            anchor = _ANCHOR.get(word['key'])
            eligible = in_title or anchor or word['proper'] or frequency[word['key']] >= 2
            if not eligible:
                continue
            # Keep the original word; inflections affect ranking, not wording.
            priority = (100 if anchor else 75) if in_title else (65 if anchor else 55 if word['proper'] else 40)
            nearby_subject = (word['proper'] and index + 1 < len(words)
                              and words[index + 1]['key'] in _ANCHOR)
            if nearby_subject:
                priority = max(priority, 85)
            add(word['text'], priority, True)
            if index + 1 < len(words):
                right = words[index + 1]
                if (right['content'] and sentence[word['end']:right['start']].isspace()
                        and (in_title or nearby_subject or word['proper'] and right['proper']
                             or frequency[word['key']] >= 2 and frequency[right['key']] >= 2)):
                    add(sentence[word['start']:right['end']], priority + 5, False)
    ranked = sorted(candidates.values(), key=lambda item: (-item[0], item[1]))
    tags = [item[2] for item in ranked[:6]]
    hashtags = []
    seen = set()
    # Prefer independent subject words over concatenated near-duplicate phrases.
    singles = [item for item in ranked if item[3]]
    singles.sort(key=lambda item: (0 if _fold(item[2]) in _ANCHOR else 1, -item[0], item[1]))
    for _, _, text, _ in singles + [item for item in ranked if not item[3]]:
        value = _hashtag(text)
        key = _fold(value)
        if value and key not in seen and key not in _STOP:
            seen.add(key)
            hashtags.append(value)
        if len(hashtags) == 3:
            break
    return {'tags': tags, 'hashtags': hashtags}
