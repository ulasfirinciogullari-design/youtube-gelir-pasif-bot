"""
Trends Radar & Rapid-Fire Autopilot Engine for 'Zirvenin Kanunu' (@zirveninkanunu).

Mandate:
- Scrapes real-time trending topics from Google Trends RSS (Turkey & US / Global) and simulates/monitors social velocity signals.
- Extracts search volumes, related news headlines, and velocity metrics.
- Peak Psychology Angle Generator: Transforms any breaking topic into timeless rules of power,
  dark psychology, strategic warfare (Sun Tzu, Machiavelli, Robert Greene, Stoicism).
- Generates high-RPM 30-second viral Short packages with scroll-stopping hooks, psychological breakdowns,
  comment magnets, infinite loops, and full 10-language localized metadata.
- Outputs active radar reports to output/trending_radar_active_brief.json.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import feedparser
except ImportError:
    feedparser = None

# Base path resolution
BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ACTIVE_BRIEF_PATH = OUTPUT_DIR / "trending_radar_active_brief.json"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 1. 10 Canonical Global Languages Configuration
# -----------------------------------------------------------------------------
GLOBAL_LANGUAGES = {
    "tr": {"name": "Turkish", "local": "Türkçe", "flag": "🇹🇷"},
    "en": {"name": "English", "local": "English", "flag": "🇺🇸"},
    "es": {"name": "Spanish", "local": "Español", "flag": "🇪🇸"},
    "pt": {"name": "Portuguese", "local": "Português", "flag": "🇧🇷"},
    "de": {"name": "German", "local": "Deutsch", "flag": "🇩🇪"},
    "fr": {"name": "French", "local": "Français", "flag": "🇫🇷"},
    "it": {"name": "Italian", "local": "Italiano", "flag": "🇮🇹"},
    "ru": {"name": "Russian", "local": "Русский", "flag": "🇷🇺"},
    "ar": {"name": "Arabic", "local": "العربية", "flag": "🇸🇦"},
    "hi": {"name": "Hindi", "local": "हिन्दी", "flag": "🇮🇳"},
}


# -----------------------------------------------------------------------------
# 2. Strategic Psychological Archetypes & Power Laws
# -----------------------------------------------------------------------------
PSYCHOLOGICAL_FRAMEWORKS = [
    {
        "id": "power_hidden_intent",
        "category": "geopolitics_power",
        "match_keywords": ["saldırı", "kınama", "riyad", "türkiye", "pakistan", "war", "attack", "election", "biden", "trump", "putin", "nato", "hükümet", "bakanlık", "crisis", "diplomasi"],
        "law_title": "48 Güç Yasası - Kural 3: Niyetini Asla Açığa Vurma",
        "author": "Robert Greene & Sun Tzu",
        "core_thesis": "Zayıf liderler gürültü yapar, gerçek güç sahipleri sessizce masayı yeniden dizer.",
        "crowd_perception": "Kalabalıklar diplomasiyi ahlak ve adalet meselesi sanır.",
        "apex_reality": "Zirvedekiler için her kriz, masadaki zayıf piyonları temizlemek için kusursuz bir bahanedir.",
        "hook_templates": [
            "Dünya bu haberi konuşurken, perde arkasındaki acımasız güç hamlesini kimse görmedi.",
            "Herkes sadece manşetleri okur; ama gerçek stratejistler masanın altındaki eli izler.",
            "Bu olay bir tesadüf değil. Zirvedekilerin asla açık etmediği 1 numaralı güç kuralı devrede."
        ],
        "tactical_rule": "Düşmanın sana öfkeyle saldırmasını bekle; çünkü öfkelenen zihin satranç oynayamaz.",
        "comment_question": "Sizce bu krizde asıl kazanan kim oldu? Görünürdeki liderler mi, perde arkasındaki aktörler mi?",
        "loop_phrase": "Ve işte güç sahiplerinin sıradan insanlardan gizlediği en büyük gerçek..."
    },
    {
        "id": "billionaire_status_game",
        "category": "billionaire_business",
        "match_keywords": ["bezos", "musk", "zuckerberg", "meyer", "wedding", "evlilik", "boşanma", "milyarder", "billionaire", "zengin", "apple", "tesla", "amazon", "para", "servet", "hisse"],
        "law_title": "48 Güç Yasası - Kural 1: Asla Efendini Gölgede Bırakma & Statü İllüzyonu",
        "author": "Niccolò Machiavelli",
        "core_thesis": "Milyarlarca dolar paranız olsa bile, insan doğasının statü ve ego savaşı asla değişmez.",
        "crowd_perception": "Halk sadece lüksü, düğünleri ve magazinel gösterişi görür.",
        "apex_reality": "Süper zenginler için evlilikler ve ittifaklar aşk değil, imparatorluk sigortasıdır.",
        "hook_templates": [
            "Milyarderlerin dünyasında hiçbir evlilik ya da boşanma tesadüf değildir.",
            "Halk lüks düğünleri alkışlarken, masadaki milyar dolarlık güç transferini kaçırdı.",
            "Zengin olmak kolaydır, fakat zirvedeki gücü korumak insanı bir canavara dönüştürür."
        ],
        "tactical_rule": "Duygularınla değil, çıkarlarınla hareket et; duygusal kararlar imparatorlukları batırır.",
        "comment_question": "Güç ve para her şeyi satın alabilir mi, yoksa sadakat asla satılık değil midir? Fikrinizi yazın.",
        "loop_phrase": "Ve zenginlerin asla halka anlatmadığı en acımasız psikolojik kural..."
    },
    {
        "id": "sports_mental_dominance",
        "category": "sports_rivalry",
        "match_keywords": ["nfl", "football", "derbi", "galatasaray", "fenerbahçe", "beşiktaş", "lig", "match", "maç", "transfer", "champion", "şampiyon", "koç", "ejection", "fight", "ufc"],
        "law_title": "Sun Tzu - Savaş Sanatı: Zihinsel Hakimiyet & Soğukkanlılık",
        "author": "Sun Tzu & Marcus Aurelius",
        "core_thesis": "Fiziksel güç sizi sahaya çıkarır; fakat rakibin beynini kontrol etmek sizi efsane yapar.",
        "crowd_perception": "Taraftarlar skora ve faullere odaklanır.",
        "apex_reality": "Büyük şampiyonlar rakibin psikolojisini ilk 5 dakikada çözer ve onu kendi öfkesinde boğar.",
        "hook_templates": [
            "Sahadaki kavgayı izlediniz, ama arka plandaki psikolojik yıkımı fark ettiniz mi?",
            "Kaybedenler hakeme kızar, kazananlar ise rakibin zihnini ele geçirir.",
            "Zirveye oynayan bir sporcunun en ölümcül silahı kasları değil, sıfır tepki verme yeteneğidir."
        ],
        "tactical_rule": "Bir rakibi yenmenin en hızlı yolu, onun gururuna dokunup mantığını devre dışı bırakmaktır.",
        "comment_question": "Sizce bir mücadeleyi kazandıran fiziksel güç müdür, yoksa çelik gibi bir psikoloji mi?",
        "loop_phrase": "Ve işte şampiyonları sıradan oyunculardan ayıran o görünmez çizgi..."
    },
    {
        "id": "tech_ai_dominance",
        "category": "tech_ai",
        "match_keywords": ["ai", "yapay zeka", "google", "openai", "nvidia", "robot", "apple", "çip", "teknoloji", "algorithm", "yazılım"],
        "law_title": "48 Güç Yasası - Kural 6: Her Ne Pahasına Olursa Olsun Dikkat Çek ve Tekelleş",
        "author": "Peter Thiel & Robert Greene",
        "core_thesis": "Geleceğin dünyasında petrol değil, insan dikkati ve veri gücü mutlak hükümdardır.",
        "crowd_perception": "İnsanlar teknolojiyi sadece bir kolaylık zanneder.",
        "apex_reality": "Teknoloji devleri insan iradesini manipüle eden görünmez dijital imparatorluklar inşa ediyor.",
        "hook_templates": [
            "Yapay zeka çağında en büyük tehdit makineler değil, onları kontrol eden küçük bir zümre.",
            "Dünya yeni bir teknolojiye hayran kalırken, kendi özgürlüğünü kendi elleriyle devretti.",
            "Geleceğin köleleri zincirlerle değil, bildirimlerle yönetilecek."
        ],
        "tactical_rule": "Bilgiyi kontrol eden, kitlelerin algısını ve kaderini kontrol eder.",
        "comment_question": "Yapay zeka insanlığı özgürleştirecek mi, yoksa yeni bir dijital esaret mi yaratacak?",
        "loop_phrase": "Ve geleceğin efendilerinin sessizce inşa ettiği yeni kanun..."
    },
    {
        "id": "universal_power_clash",
        "category": "general_psychology",
        "match_keywords": [],
        "law_title": "48 Güç Yasası - Kural 28: Eyleme Her Zaman Cesaretle Gir",
        "author": "Robert Greene & Marcus Aurelius",
        "core_thesis": "Zirveye çıkmak cesaret, orada kalmak ise mutlak duygusal kontrol gerektirir.",
        "crowd_perception": "Kitleler olayları iyi ve kötü diye ayırır.",
        "apex_reality": "Zirvedekiler dünyayı ahlaki değil, zayıflık ve stratejik üstünlük üzerinden okur.",
        "hook_templates": [
            "Bugün herkes bu olayı tartışıyor; fakat arkasındaki psikolojik tuzak gözden kaçtı.",
            "Güçlü bir insanın en büyük zayıflığı, karşısındakini kendisi gibi dürüst sanmasıdır.",
            "Eğer zirvede tek başına kalmak istemiyorsan, bu kuralı hayatına kazımak zorundasın."
        ],
        "tactical_rule": "Asla tereddüt etme; şüphe ve kararsızlık zayıflığın kokusunu etrafa yayar.",
        "comment_question": "Siz olsaydınız bu durumda mantığınızla mı hareket ederdiniz, duygularınızla mı?",
        "loop_phrase": "Çünkü güç basamaklarını tırmananların asla unutmadığı tek kural..."
    }
]


# -----------------------------------------------------------------------------
# 3. Google Trends Scraper & Normalizer
# -----------------------------------------------------------------------------
class GoogleTrendsScraper:
    """Scrapes Google Trends Daily & Realtime RSS for specified geo regions."""

    RSS_ENDPOINTS = {
        "TR": "https://trends.google.com/trending/rss?geo=TR",
        "US": "https://trends.google.com/trending/rss?geo=US",
    }
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    @classmethod
    def parse_traffic_score(cls, traffic_str: Optional[str]) -> int:
        """Converts strings like '500K+', '50K+', '200+' into sortable integers."""
        if not traffic_str:
            return 100
        clean = traffic_str.replace("+", "").replace(",", "").strip().upper()
        multiplier = 1
        if clean.endswith("M"):
            multiplier = 1_000_000
            clean = clean[:-1]
        elif clean.endswith("K"):
            multiplier = 1_000
            clean = clean[:-1]
        try:
            return int(float(clean) * multiplier)
        except Exception:
            return 100

    @classmethod
    def clean_text(cls, text: Optional[str]) -> str:
        """Cleans html entities and unreadable characters."""
        if not text:
            return ""
        text = html.unescape(text)
        # remove residual HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def slugify_hashtag(cls, text: str) -> str:
        """Transliterates Turkish characters and strips non-alphanumerics."""
        charmap = {
            'ç': 'c', 'ğ': 'g', 'ı': 'i', 'i': 'i', 'ö': 'o', 'ş': 's', 'ü': 'u',
            'Ç': 'c', 'Ğ': 'g', 'İ': 'i', 'I': 'i', 'Ö': 'o', 'Ş': 's', 'Ü': 'u',
        }
        for tr_char, lat_char in charmap.items():
            text = text.replace(tr_char, lat_char)
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text.lower())
        return cleaned or "trend"

    @classmethod
    def fetch_geo_trends(cls, geo: str = "TR") -> List[Dict[str, Any]]:
        """Fetches and parses Google Trends for a given geo ('TR' or 'US')."""
        url = cls.RSS_ENDPOINTS.get(geo.upper(), f"https://trends.google.com/trending/rss?geo={geo}")
        items: List[Dict[str, Any]] = []

        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=12)
            if resp.status_code != 200:
                print(f"[TrendsRadar] Warning: {geo} returned status {resp.status_code}")
                return items
            raw_xml = resp.content

            root = ET.fromstring(raw_xml)
            ns = {"ht": "https://trends.google.com/trending/rss"}

            for item_elem in root.findall("./channel/item"):
                title_elem = item_elem.find("title")
                traffic_elem = item_elem.find("ht:approx_traffic", ns)
                pub_elem = item_elem.find("pubDate")

                title = cls.clean_text(title_elem.text if title_elem is not None else "")
                traffic_str = traffic_elem.text if traffic_elem is not None else "100+"
                traffic_score = cls.parse_traffic_score(traffic_str)
                pub_date = pub_elem.text if pub_elem is not None else datetime.now(timezone.utc).isoformat()

                news_items = []
                for n_elem in item_elem.findall("ht:news_item", ns):
                    nt_elem = n_elem.find("ht:news_item_title", ns)
                    ns_elem = n_elem.find("ht:news_item_source", ns)
                    nu_elem = n_elem.find("ht:news_item_url", ns)
                    nsnip_elem = n_elem.find("ht:news_item_snippet", ns)

                    if nt_elem is not None and nt_elem.text:
                        news_items.append({
                            "title": cls.clean_text(nt_elem.text),
                            "source": cls.clean_text(ns_elem.text) if ns_elem is not None else "News",
                            "url": nu_elem.text if nu_elem is not None else "",
                            "snippet": cls.clean_text(nsnip_elem.text) if nsnip_elem is not None else ""
                        })

                if title:
                    items.append({
                        "geo": geo.upper(),
                        "topic": title,
                        "approx_traffic": traffic_str,
                        "traffic_score": traffic_score,
                        "published_at": pub_date,
                        "news_headlines": news_items,
                    })

        except Exception as exc:
            print(f"[TrendsRadar] Error fetching {geo} trends: {exc}")

        return items


# -----------------------------------------------------------------------------
# 4. Peak Psychology Angle Generator
# -----------------------------------------------------------------------------
class PeakPsychologyAngleGenerator:
    """
    Connects any trending topic (tech, sports rivalry, geopolitical tension, celebrity)
    to timeless rules of strategy, dark psychology, and mental power under 'Zirvenin Kanunu'.
    """

    @classmethod
    def match_framework(cls, topic: str, headlines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Matches the best psychological archetype based on semantic keywords."""
        search_corpus = (topic + " " + " ".join(h.get("title", "") for h in headlines)).lower()

        # Check each framework's keywords
        best_match = None
        highest_score = 0

        for fw in PSYCHOLOGICAL_FRAMEWORKS[:-1]:  # Exclude fallback for evaluation
            score = 0
            for kw in fw["match_keywords"]:
                if kw in search_corpus:
                    score += 2
            if score > highest_score:
                highest_score = score
                best_match = fw

        if not best_match or highest_score == 0:
            best_match = PSYCHOLOGICAL_FRAMEWORKS[-1]  # universal fallback

        return best_match

    @classmethod
    def generate_angle(cls, topic: str, headlines: List[Dict[str, Any]], geo: str) -> Dict[str, Any]:
        """Constructs an in-depth psychological perspective for the topic."""
        fw = cls.match_framework(topic, headlines)

        # Pick primary news headline for context
        main_headline = headlines[0]["title"] if headlines else f"{topic} gündemde yükseliyor"
        headline_source = headlines[0]["source"] if headlines else "Global Medya"

        # Construct specific hook
        hook_base = fw["hook_templates"][0]
        custom_hook = f"{topic.title()} gündemi kasıp kavururken, arkasındaki acımasız güç hamlesini kimse görmedi."

        # Strategic thesis tailored to the event
        narrative_breakdown = (
            f"Bugün herkes {topic.title()} olayının yüzeydeki ayrıntılarına takılmış durumda. "
            f"Fakat zirvedekiler bilir: {fw['core_thesis']} "
            f"{fw['law_title']} şunu açıkça emreder: {fw['tactical_rule']}"
        )

        return {
            "archetype_id": fw["id"],
            "category": fw["category"],
            "power_law": fw["law_title"],
            "master_strategist": fw["author"],
            "hook_first_2_5s": custom_hook,
            "crowd_perception": fw["crowd_perception"],
            "apex_reality": fw["apex_reality"],
            "tactical_rule": fw["tactical_rule"],
            "core_thesis": fw["core_thesis"],
            "narrative_breakdown": narrative_breakdown,
            "comment_magnet_question": fw.get("comment_magnet_question") or fw.get("comment_question", ""),
            "infinite_loop_bridge": fw.get("infinite_loop_bridge") or fw.get("loop_phrase", ""),
            "context_news": {
                "headline": main_headline,
                "source": headline_source
            }
        }


# -----------------------------------------------------------------------------
# 5. Viral 30-Second Short Package Generator
# -----------------------------------------------------------------------------
class ViralScriptGenerator:
    """
    Generates high-RPM, 30-second Short scripts (around 65-75 words) with:
    - 2.5s Scroll-stopping hook
    - Segment blurring (seamless escalation)
    - Comment-magnet engagement question
    - Grammatical infinite loop back to the hook
    """

    @classmethod
    def generate_short_package(
        cls,
        topic: str,
        angle: Dict[str, Any],
        traffic_volume: str,
        geo: str
    ) -> Dict[str, Any]:
        """Builds a complete production-ready viral package."""

        topic_clean = topic.title()
        power_law = angle["power_law"]
        hook = angle["hook_first_2_5s"]
        rule = angle["tactical_rule"]
        question = angle["comment_magnet_question"]
        loop = angle["infinite_loop_bridge"]

        # Word count tuned for dramatic 130 wpm delivery (approx 26-30 seconds)
        spoken_script_segments = [
            f"[0.0s - 2.5s | HOOK] {hook}",
            f"[2.5s - 9.0s | KONTEKST] Herkes {topic_clean} olayındaki manşetleri tartışıyor. Ancak kalabalıklar sadece bir illüzyonu izliyor.",
            f"[9.0s - 18.0s | STRATEJİ] Zirvedekiler için krizler felaket değil, güç pekiştirme aracıdır. {power_law}.",
            f"[18.0s - 24.0s | KURAL] Zirvenin Kanunu nettir: {rule}",
            f"[24.0s - 28.5s | YORUM MANYETİĞİ] {question}",
            f"[28.5s - 30.0s | INFINITE LOOP] {loop}"
        ]

        full_spoken_text = (
            f"{hook} "
            f"Herkes {topic_clean} olayındaki manşetleri tartışıyor. Ancak kalabalıklar sadece bir illüzyonu izliyor. "
            f"Zirvedekiler için krizler felaket değil, güç pekiştirme aracıdır. {power_law}. "
            f"Zirvenin Kanunu nettir: {rule} "
            f"{question} "
            f"{loop}"
        )

        viral_titles = [
            f"{topic_clean} ve Zirvenin Acımasız Güç Yasası ♟️",
            f"Medyadan Gizlenen Acı Gerçek: {topic_clean}",
            f"{topic_clean} Olayının Arkasındaki Karanlık Psikoloji",
            f"Asla Görmediğiniz Hamle: {topic_clean} Kuralı"
        ]

        seo_hashtags = [
            "#zirveninkanunu",
            "#güç",
            "#karanlıkpsikoloji",
            "#strateji",
            "#liderlik",
            "#motivasyon",
            "#shorts",
            f"#{GoogleTrendsScraper.slugify_hashtag(topic)}"
        ]

        # Storyboard visual prompts (Dark cinematic high-retention visuals)
        storyboard = [
            {
                "timecode": "00:00 - 00:03",
                "scene_name": "Shock Hook",
                "visual_prompt": f"Dramatic cinematic close-up of a shadowed leader in a tailored suit observing a dark luxury boardroom, high contrast noir lighting, golden amber reflections, 8K, cinematic photorealism, tense atmosphere representing {topic_clean}.",
                "text_overlay": f"GERÇEK NE? ♟️"
            },
            {
                "timecode": "00:03 - 00:09",
                "scene_name": "Breaking Illusion",
                "visual_prompt": f"Split screen visual: Fast moving paparazzi camera flashes on one side, cold calculated chess pieces moving across a marble board on the other side, moody lighting, dark luxury aesthetic.",
                "text_overlay": "İLLÜZYON VS GÜÇ"
            },
            {
                "timecode": "00:09 - 00:18",
                "scene_name": "The Strategic Truth",
                "visual_prompt": "Cinematic slow motion shot of an apex strategist sitting calmly while chaos unfolds outside floor-to-ceiling glass windows, rain streaks, cold blue and warm amber tones.",
                "text_overlay": f"{power_law[:25]}..."
            },
            {
                "timecode": "00:18 - 00:24",
                "scene_name": "Uncompromising Rule",
                "visual_prompt": "Extreme macro close-up of a calm, unwavering eye reflecting city skyline lights, showing zero emotional reactivity, ultra sharp focus, cinematic depth of field.",
                "text_overlay": "TEPKİSİZLİK = GÜÇ"
            },
            {
                "timecode": "00:24 - 00:30",
                "scene_name": "Comment Vortex & Loop",
                "visual_prompt": "Shadowy silhouette walking towards an illuminated skyscraper summit, infinite loop visual cadence, intense cinematic grading, dramatic crescendo.",
                "text_overlay": "SENİN KARARIN NE?"
            }
        ]

        return {
            "topic": topic,
            "geo": geo,
            "traffic_volume": traffic_volume,
            "duration_seconds": 30,
            "word_count": len(full_spoken_text.split()),
            "primary_viral_title": viral_titles[0],
            "alternative_viral_titles": viral_titles[1:],
            "seo_hashtags": seo_hashtags,
            "script_segments": spoken_script_segments,
            "full_spoken_voiceover": full_spoken_text,
            "recommended_voice": "tr-TR-AhmetNeural (Deep Baritone, measured, authoritarian pace)",
            "audio_soundtrack": "Deep cinematic drone, slow heartbeat riser, ominous cellos, climax bass drop",
            "visual_storyboard": storyboard,
        }


# -----------------------------------------------------------------------------
# 6. Global 10-Language Metadata Suite
# -----------------------------------------------------------------------------
class Global10LanguageMetadataGenerator:
    """
    Produces fully localized YouTube metadata (titles, descriptions, tags)
    across all 10 canonical global languages for maximal algorithmic reach.
    """

    TRANSLATION_MATRIX = {
        "tr": {
            "title_template": "{topic} ve Zirvenin Acımasız Güç Yasası ♟️",
            "desc_header": "Zirvenin Kanunu | Güç, Liderlik ve Zihinsel Üstünlük",
            "desc_body": "Gündemdeki {topic} olayının ardındaki çıplak güç savaşını ve manipülasyon dinamiklerini inceliyoruz.",
            "cta": "Daha fazlası için kanala abone olun ve bildirimleri açın.",
            "tags": ["zirvenin kanunu", "güç", "psikoloji", "liderlik", "strateji", "kişisel gelişim", "shorts"]
        },
        "en": {
            "title_template": "{topic}: The Brutal Law of Power Nobody Saw ♟️",
            "desc_header": "Law of the Peak | Power, Leadership & Dark Psychology",
            "desc_body": "Analyzing the real psychological power dynamics and strategic chess moves behind {topic}.",
            "cta": "Subscribe to master the rules of power.",
            "tags": ["law of power", "psychology", "48 laws of power", "leadership", "machiavelli", "mindset", "shorts"]
        },
        "es": {
            "title_template": "{topic}: La Despiadada Ley del Poder Revelada ♟️",
            "desc_header": "Ley de la Cumbre | Poder, Liderazgo y Psicología Oscura",
            "desc_body": "Analizando la verdadera lucha de poder y las leyes estratégicas detrás de {topic}.",
            "cta": "Suscríbete para dominar las reglas del juego.",
            "tags": ["ley del poder", "psicologia oscura", "estrategia", "liderazgo", "shorts"]
        },
        "pt": {
            "title_template": "{topic}: A Brutal Lei do Poder Que Ninguém Viu ♟️",
            "desc_header": "Lei do Topo | Poder, Liderança e Psicologia Estratégica",
            "desc_body": "Analisando a fria dinâmica de poder e estratégia por trás de {topic}.",
            "cta": "Inscreva-se para dominar a mente dos vencedores.",
            "tags": ["leis do poder", "psicologia", "liderança", "estratégia", "shorts"]
        },
        "de": {
            "title_template": "{topic}: Das brutale Gesetz der Macht enthüllt ♟️",
            "desc_header": "Gesetz des Gipfels | Macht, Führung & Strategische Psychologie",
            "desc_body": "Eine Analyse der verborgenen Machtdynamik und strategischen Züge hinter {topic}.",
            "cta": "Abonnieren Sie den Kanal für strategische Überlegenheit.",
            "tags": ["gesetze der macht", "psychologie", "strategie", "führung", "shorts"]
        },
        "fr": {
            "title_template": "{topic} : La Loi Brutale du Pouvoir Dévoilée ♟️",
            "desc_header": "Loi du Sommet | Pouvoir, Leadership & Psychologie Sombre",
            "desc_body": "Analyse des véritables dynamiques de pouvoir et d'échecs stratégiques derrière {topic}.",
            "cta": "Abonnez-vous pour comprendre les règles du pouvoir.",
            "tags": ["pouvoir", "psychologie", "leadership", "stratégie", "shorts"]
        },
        "it": {
            "title_template": "{topic}: La Spietata Legge del Potere Svelata ♟️",
            "desc_header": "La Legge della Vetta | Potere, Leadership e Psicologia",
            "desc_body": "Analisi approfondita delle mosse strategiche e delle dinamiche di potere dietro {topic}.",
            "cta": "Iscriviti per padroneggiare la psicologia del vertice.",
            "tags": ["potere", "psicologia", "strategia", "leadership", "shorts"]
        },
        "ru": {
            "title_template": "{topic}: Безжалостный закон власти, о котором молчат ♟️",
            "desc_header": "Закон Вершины | Власть, Лидерство и Стратегия",
            "desc_body": "Анализ скрытых механизмов власти и психологических ходов за событием {topic}.",
            "cta": "Подписывайтесь, чтобы понимать истинные правила игры.",
            "tags": ["законы власти", "психология", "лидерство", "стратегия", "shorts"]
        },
        "ar": {
            "title_template": "{topic}: قانون القوة القاسي الذي لم يلاحظه أحد ♟️",
            "desc_header": "قانون القمة | القوة، القيادة وعلم النفس المظلم",
            "desc_body": "تحليل أبعاد الصراع الخفي وموازين القوى الاستراتيجية وراء {topic}.",
            "cta": "اشترك لتتعلم قوانين القوة والسيطرة.",
            "tags": ["قوانين القوة", "علم النفس", "القيادة", "استراتيجية", "shorts"]
        },
        "hi": {
            "title_template": "{topic}: शक्ति का क्रूर नियम जिसे किसी ने नहीं देखा ♟️",
            "desc_header": "शिखर का नियम | शक्ति, नेतृत्व और रणनीति",
            "desc_body": "{topic} के पीछे चल रहे गुप्त सत्ता संघर्ष और मनोवैज्ञानिक रणनीतियों का गहरा विश्लेषण।",
            "cta": "शक्ति के नियमों को समझने के लिए चैनल को सब्सक्राइब करें।",
            "tags": ["शक्ति के नियम", "मनोविज्ञान", "रणनीति", "नेतृत्व", "shorts"]
        }
    }

    @classmethod
    def generate_suite(cls, topic: str) -> Dict[str, Dict[str, Any]]:
        """Generates full localized metadata dictionary for all 10 languages."""
        topic_title = topic.title()
        suite: Dict[str, Dict[str, Any]] = {}

        for lang_code, meta in cls.TRANSLATION_MATRIX.items():
            lang_info = GLOBAL_LANGUAGES.get(lang_code, {"name": lang_code, "flag": ""})
            title = meta["title_template"].format(topic=topic_title)
            desc = (
                f"{meta['desc_header']}\n\n"
                f"{meta['desc_body'].format(topic=topic_title)}\n\n"
                f"📌 {meta['cta']}\n\n"
                f"#zirveninkanunu #{GoogleTrendsScraper.slugify_hashtag(topic)} #shorts"
            )
            suite[lang_code] = {
                "language_name": lang_info["name"],
                "flag": lang_info["flag"],
                "title": title,
                "description": desc,
                "tags": meta["tags"] + [topic.lower()],
            }

        return suite


# -----------------------------------------------------------------------------
# 7. Trends Radar Autopilot Orchestrator
# -----------------------------------------------------------------------------
class TrendsRadarAutopilot:
    """Main Orchestrator running radar sweeps, angle extraction, and packaging."""

    def __init__(self, output_file: Path = ACTIVE_BRIEF_PATH):
        self.output_file = output_file
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

    def run_radar_sweep(self) -> Dict[str, Any]:
        """
        Executes a complete real-time radar sweep across TR and US Google Trends,
        identifies the #1 highest-velocity breakout topic, builds the psychology angle,
        generates the 30-second Short script, and produces the 10-language suite.
        """
        print("=================================================================")
        print("📡 [TRENDS RADAR AUTOPILOT] Initializing Live Radar Sweep...")
        print("=================================================================")

        # 1. Fetch trends from Turkey and US
        print("🔍 Scraping Google Trends TR (Turkey)...")
        tr_trends = GoogleTrendsScraper.fetch_geo_trends("TR")
        print(f"   -> Retrieved {len(tr_trends)} topics from TR.")

        print("🔍 Scraping Google Trends US (Global / US)...")
        us_trends = GoogleTrendsScraper.fetch_geo_trends("US")
        print(f"   -> Retrieved {len(us_trends)} topics from US.")

        all_topics = tr_trends + us_trends

        if not all_topics:
            print("⚠️ Warning: No topics retrieved from RSS. Injecting backup trending radar topics...")
            all_topics = [
                {
                    "geo": "TR",
                    "topic": "Türkiye Riyad Zirvesi",
                    "approx_traffic": "50K+",
                    "traffic_score": 50000,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "news_headlines": [
                        {
                            "title": "Türkiye ve Bölge Liderlerinden Kritik Riyad Güvenlik Zirvesi",
                            "source": "TRT Haber",
                            "url": "https://trthaber.com",
                            "snippet": "Bölgesel kriz masaya yatırıldı."
                        }
                    ]
                },
                {
                    "geo": "US",
                    "topic": "AI Superintelligence Race",
                    "approx_traffic": "100K+",
                    "traffic_score": 100000,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "news_headlines": [
                        {
                            "title": "Tech Giants Escalate Multi-Billion Dollar AI Infrastructure Race",
                            "source": "Bloomberg",
                            "url": "https://bloomberg.com",
                            "snippet": "New frontier model deployments accelerate."
                        }
                    ]
                }
            ]

        # 2. Enhance each topic with psychology categorization and viral opportunity scoring
        for t in all_topics:
            fw = PeakPsychologyAngleGenerator.match_framework(t["topic"], t["news_headlines"])
            t["psychology_archetype"] = fw["id"]
            t["psychology_category"] = fw["category"]
            t["power_law_preview"] = fw["law_title"]

            # Calculate viral opportunity score for Zirvenin Kanunu
            base_traffic = float(t.get("traffic_score", 100))
            category_multipliers = {
                "geopolitics_power": 2.5,
                "billionaire_business": 2.2,
                "tech_ai": 2.0,
                "sports_rivalry": 1.6,
                "general_psychology": 1.0,
            }
            mult = category_multipliers.get(fw["category"], 1.0)
            topic_lower = t["topic"].lower()
            mundane_filters = ["yağış", "hava durumu", "meteoroloji", "loto", "şans topu", "sayısal", "namaz"]
            if any(m in topic_lower for m in mundane_filters):
                mult = 0.05
            t["viral_opportunity_score"] = int(base_traffic * mult)

        # Sort topics by viral opportunity score (power, psychology, high-RPM multiplier)
        all_topics.sort(key=lambda x: x.get("viral_opportunity_score", 0), reverse=True)

        # 3. Select the #1 breakout trending topic
        champion_topic_data = all_topics[0]
        champion_topic = champion_topic_data["topic"]
        print(f"\n🏆 [TOP BREAKOUT TOPIC DETECTED]: '{champion_topic}' ({champion_topic_data['geo']} - Traffic: {champion_topic_data['approx_traffic']} | Viral Score: {champion_topic_data.get('viral_opportunity_score')})")

        # 4. Generate Peak Psychology Angle
        print(f"🧠 Synthesizing Peak Psychology Angle for '{champion_topic}'...")
        angle = PeakPsychologyAngleGenerator.generate_angle(
            champion_topic,
            champion_topic_data["news_headlines"],
            champion_topic_data["geo"]
        )

        # 5. Generate 30-Second Viral Short Package
        print(f"⚡ Drafting 30-second high-RPM Short package...")
        short_package = ViralScriptGenerator.generate_short_package(
            champion_topic,
            angle,
            champion_topic_data["approx_traffic"],
            champion_topic_data["geo"]
        )

        # 6. Generate Global 10-Language Metadata Suite
        print(f"🌍 Generating Global 10-Language metadata suite...")
        lang_suite = Global10LanguageMetadataGenerator.generate_suite(champion_topic)

        # 7. Construct Full Active Radar Brief
        timestamp_now = datetime.now(timezone.utc).isoformat()
        active_brief = {
            "radar_engine": "Zirvenin Kanunu Trends Radar Autopilot v2.0",
            "timestamp_utc": timestamp_now,
            "channel": "@zirveninkanunu",
            "summary": {
                "total_tracked_topics": len(all_topics),
                "tr_topics_count": len(tr_trends),
                "us_topics_count": len(us_trends),
                "champion_topic": champion_topic,
                "champion_traffic": champion_topic_data["approx_traffic"],
                "champion_geo": champion_topic_data["geo"]
            },
            "top_trending_rankings": all_topics[:10],
            "champion_production_package": {
                "topic": champion_topic,
                "angle": angle,
                "viral_short_script": short_package,
                "global_10_language_metadata": lang_suite
            }
        }

        # 8. Save output
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(active_brief, f, indent=2, ensure_ascii=False)

        print(f"\n✅ Active Radar Brief successfully written to: {self.output_file}")
        return active_brief


# -----------------------------------------------------------------------------
# 8. CLI Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    autopilot = TrendsRadarAutopilot()
    brief = autopilot.run_radar_sweep()

    # Pretty print summary for terminal verification
    champ_pkg = brief["champion_production_package"]
    script_data = champ_pkg["viral_short_script"]
    angle_data = champ_pkg["angle"]

    print("\n" + "=" * 75)
    print("🎯 [ZIRVENIN KANUNU - REAL-TIME TRENDS RADAR BRIEF REPORT]")
    print("=" * 75)
    print(f"🔥 TOPIC: {champ_pkg['topic']} ({script_data['geo']}) | TRAFFIC: {script_data['traffic_volume']}")
    print(f"📜 POWER LAW: {angle_data['power_law']} ({angle_data['master_strategist']})")
    print(f"🎯 VIRAL TITLE: {script_data['primary_viral_title']}")
    print("-" * 75)
    print("⏱️ 30-SECOND SPOKEN VIRAL SCRIPT:")
    for segment in script_data["script_segments"]:
        print(f"  {segment}")
    print("-" * 75)
    print("💬 COMMENT MAGNET QUESTION:")
    print(f"  👉 {angle_data['comment_magnet_question']}")
    print("🔄 SEAMLESS INFINITE LOOP:")
    print(f"  🔁 {angle_data['infinite_loop_bridge']}")
    print("-" * 75)
    print("🌍 10-LANGUAGE METADATA SUITE PREVIEW:")
    for code, m in champ_pkg["global_10_language_metadata"].items():
        print(f"  {m['flag']} [{code.upper()}] {m['title']}")
    print("=" * 75)
