"""
Multi-Sector Viral Shorts Factory for 'Zirvenin Kanunu' (@zirveninkanunu).
Produces high-retention, single-comment Shorts across 7 distinct viral sectors:
1. CULT_CINEMA (Kurtlar Vadisi, Peaky Blinders, Fight Club, Breaking Bad, Godfather)
2. STREET_ESNAF (Sanayi Ustası, Taksici, Berber, Pazar Esnafı)
3. PET_PSYCHOLOGY (Kedilerin Sahibini Takmaması, Komik Hayvan Tepkileri)
4. CORPORATE_PLAZA (LinkedIn Cringe, İK Mülakatları, Mesai Absürtlüğü)
5. FOOD_GOURMET (Lüks Restoran vs Esnaf Lokantası, Kurye Notları)
6. SPORTS_PASSION (Futbol Hakemleri, Fatih Terim, Taraftar Raconu)
7. TECH_MINDSET (Elon Musk, Kod Yazarken Sabahlamak, Yapay Zeka Şaşkınlığı)

STRICT RULE FROM CHANNEL OWNER:
- EXACTLY 1 SINGLE VIRAL COMMENT CARD PER VIDEO. (Zero 2nd/3rd cards).
- Clip speaks first with normalized audio. Narrator speaks strictly after.
- Narrator reads the exact card text with natural human delivery & laughter.
- Seamless infinite loop outro.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SectorEpisodeBlueprint:
    sector_id: str
    sector_name: str
    title: str
    clip_path: str
    clip_start: float
    clip_duration: float
    clip_transcript: str
    header_badge: str
    
    # The STRICT SINGLE COMMENT
    comment_platform: str  # 'x', 'youtube', 'tiktok', 'linkedin'
    comment_author: str
    comment_handle: str
    comment_text: str
    comment_likes: str
    comment_timestamp: str
    avatar_file: str
    
    # Voice Narration (Zero Collision, Starts when clip finishes)
    voice_persona: str  # 'alp', 'baran', 'damla', 'yunus'
    narrator_speech: str  # Exactly matching the card + punchline
    
    # Music & Mood
    music_track: str
    music_volume: float = 0.05
    sfx_type: str = "ui_pop"
    
    # Infinite loop question
    outro_question: str = "Sen ne düşünüyorsun? Yoruma yaz! 👇"
    tags: List[str] = field(default_factory=list)


# Catalog of multi-sector blueprints
SECTOR_CATALOG: Dict[str, SectorEpisodeBlueprint] = {
    # 1. CULT CINEMA (Tyler Durden / Fight Club)
    "tyler_durden_system": SectorEpisodeBlueprint(
        sector_id="cult_cinema",
        sector_name="Kült Sinema & Racon",
        title="Tyler Durden Tüketim Çılgınlığını Tek Cümleyle Bitirdi 👑 #shorts #fightclub",
        clip_path="assets/cinematic/fight_club_master.mp4",
        clip_start=15.0,
        clip_duration=9.0,
        clip_transcript="Sahip oldukların sonunda sana sahip olur.",
        header_badge="🥊 FIGHT CLUB | TYLER DURDEN",
        comment_platform="x",
        comment_author="Milenyum Filozofu",
        comment_handle="@milenyum_filozofu",
        comment_text="Tyler Durden 1999 yılında 3 maaşla iPhone 16 Pro Max alanları anlatmış meğer 😂💀",
        comment_likes="64.8 B",
        comment_timestamp="3 saat önce",
        avatar_file="avatar_casual_guy.jpg",
        voice_persona="alp",
        narrator_speech="Haha, yoruma gel: 'Tyler Durden 1999 yılında 3 maaşla iPhone 16 Pro Max alanları anlatmış meğer!' Harbi öyle ya. Sen ne düşünüyorsun, Tyler haklı mı? Yoruma yaz!",
        music_track="assets/music/dark_tension_pulse.mp3",
        music_volume=0.06,
        sfx_type="ui_pop",
        outro_question="Sence Tyler Durden haklı mı? Yoruma yaz!",
        tags=["fight club", "tyler durden", "felsefe", "tüketim çılgınlığı", "shorts", "viral"]
    ),

    # 2. STREET ESNAF (Sanayi Ustası)
    "sanayi_ustasi_teshis": SectorEpisodeBlueprint(
        sector_id="street_esnaf",
        sector_name="Gerçek Sokak & Esnaf Kültürü",
        title="Sanayi Ustasının Kaputu Açtığı An Koyduğu Efsane Teşhis 😂🔧 #shorts #esnaf",
        clip_path="assets/cinematic/wolf_master.mp4",  # or real street clip
        clip_start=5.0,
        clip_duration=8.5,
        clip_transcript="Bunu daha önce hangi ustaya götürdün sen?",
        header_badge="🔧 SANAYİ RACONU | USTA TEŞHİSİ",
        comment_platform="x",
        comment_author="Düz Vites",
        comment_handle="@duzvites34",
        comment_text="Sanayi ustası kaputu açınca: 'Bunu kim yaptıysa eline sağlık ama komple motor inecek' dememe ihtimali yok amk 😂💀",
        comment_likes="52.1 B",
        comment_timestamp="5 saat önce",
        avatar_file="avatar_burak.jpg",
        voice_persona="alp",
        narrator_speech="Haha, yoruma bak: 'Sanayi ustası kaputu açınca, bunu kim yaptıysa komple motor inecek dememe ihtimali yok!' Birebir gerçek ya. Senin ustanın favori lafı neydi? Yoruma yaz!",
        music_track="assets/music/quirky_comedy_groove.mp3",
        music_volume=0.06,
        sfx_type="ui_pop",
        outro_question="Senin ustanın en meşhur lafı ne? Yoruma yaz!",
        tags=["sanayi", "sanayi ustası", "esnaf", "araba", "komedi", "shorts", "viral"]
    ),

    # 3. PET PSYCHOLOGY (Kedi Gerçekleri)
    "kedi_kargo_kutusu": SectorEpisodeBlueprint(
        sector_id="pet_psychology",
        sector_name="Evcil Hayvanlar & Hayvan Psikolojisi",
        title="Kedisine 2.000 TL'lik Yatak Alan Sahibin Hazin Sonu 😂🐱 #shorts #kediler",
        clip_path="output/kasap_kedi_raw.mp4",
        clip_start=2.0,
        clip_duration=8.0,
        clip_transcript="Kedi sadece kargo kutusuna bakar.",
        header_badge="🐱 KEDİ PSİKOLOJİSİ | ACI GERÇEKLER",
        comment_platform="tiktok",
        comment_author="Pati Dostu",
        comment_handle="@patidostu_official",
        comment_text="Kedisine 2 bin liralık ortopedik yatak alırsın, gider yatağın geldiği yırtık karton kutuda uyur 😂😭",
        comment_likes="89.4 B",
        comment_timestamp="1 gün önce",
        avatar_file="avatar_cat.jpg",
        voice_persona="alp",
        narrator_speech="Haha, yoruma gel: 'Kedisine iki bin liralık ortopedik yatak alırsın, gider yatağın geldiği yırtık karton kutuda uyur!' Harbiden dünyadaki tüm kediler aynı. Senin kedin nerde uyuyor? Yoruma yaz!",
        music_track="assets/music/carefree.mp3",
        music_volume=0.05,
        sfx_type="ui_pop",
        outro_question="Senin kedin nerede uyuyor? Yoruma yaz!",
        tags=["kedi", "kediler", "komik hayvanlar", "kedi psikolojisi", "shorts", "viral"]
    ),

    # 4. CORPORATE PLAZA (Beyaz Yaka Absürtlüğü)
    "plaza_toplanti_mail": SectorEpisodeBlueprint(
        sector_id="corporate_plaza",
        sector_name="Plaza & Beyaz Yaka Mizahı",
        title="1 Saatlik Toplantının Aslında 1 Cümlelik Mail Olması 😂💼 #shorts #beyazyaka",
        clip_path="assets/cinematic/peaky_master.mp4",
        clip_start=10.0,
        clip_duration=8.0,
        clip_transcript="Bu toplantıyı neden yaptık?",
        header_badge="💼 PLAZA GÜNLÜKLERİ | BEYAZ YAKA",
        comment_platform="x",
        comment_author="Kurumsal Köle",
        comment_handle="@kurumsal_kole",
        comment_text="1 saatlik toplantı bitti, çıkarılan tek aksiyon: 'Bunun için haftaya bir toplantı daha yapalım' amk 😂💀",
        comment_likes="71.3 B",
        comment_timestamp="4 saat önce",
        avatar_file="avatar_mertcan.jpg",
        voice_persona="alp",
        narrator_speech="Haha, yoruma bak: 'Bir saatlik toplantı bitti, çıkarılan tek aksiyon: Bunun için haftaya bir toplantı daha yapalım!' Bütün kurumsal hayatın özeti bu resmen. Sen de haftada kaç gereksiz toplantıya giriyorsun? Yoruma yaz!",
        music_track="assets/music/lofi_midnight_chill.mp3",
        music_volume=0.05,
        sfx_type="ui_pop",
        outro_question="Sen haftada kaç gereksiz toplantıya giriyorsun? Yoruma yaz!",
        tags=["beyaz yaka", "plaza", "kurumsal", "ofis mizahı", "toplantı", "shorts", "viral"]
    ),

    # 5. FOOD GOURMET (Lüks Tabak vs Esnaf)
    "luks_tabak_doyurmaz": SectorEpisodeBlueprint(
        sector_id="food_gourmet",
        sector_name="Yemek & Restoran Eleştirileri",
        title="3.500 TL Hesap Ödeyip Aç Kalkanların İbretlik Hikayesi 😂🍽️ #shorts #yemek",
        clip_path="assets/cinematic/polat_alemdar_sample2.mp4",
        clip_start=5.0,
        clip_duration=8.0,
        clip_transcript="Masa dolu ama tabakta yemek yok.",
        header_badge="🍽️ GURME RESTORAN | MİNYATÜR PORSİYON",
        comment_platform="x",
        comment_author="Aç Gezen Gurme",
        comment_handle="@ac_gurme",
        comment_text="Tabağın ortasına damla büyüklüğünde sos sıkıp etrafına ot koymuşlar, 3500 TL hesap ödeyip çıkışta dönerci arıyorsun 😂😭",
        comment_likes="44.9 B",
        comment_timestamp="2 saat önce",
        avatar_file="avatar_onur.jpg",
        voice_persona="alp",
        narrator_speech="Haha, yoruma bak: 'Tabağın ortasına damla kadar sos sıkıp ot koymuşlar, 3500 lira ödeyip çıkışta dönerci arıyorsun!' Harbi öyle ya. Sen hiç lüks restorandan aç çıktın mı? Yoruma yaz!",
        music_track="assets/music/monkeys_spinning_monkeys.mp3",
        music_volume=0.05,
        sfx_type="ui_pop",
        outro_question="Sen hiç lüks restorandan aç kalktın mı? Yoruma yaz!",
        tags=["yemek", "restoran", "hesap", "dönerci", "esnaf lokantası", "shorts", "viral"]
    )
}


def get_sector_blueprint(blueprint_key: str) -> SectorEpisodeBlueprint:
    """Retrieves blueprint by key, with fallback to Tyler Durden."""
    return SECTOR_CATALOG.get(blueprint_key, SECTOR_CATALOG["tyler_durden_system"])


def list_available_sectors() -> List[Dict[str, str]]:
    """Returns all available sector keys and metadata."""
    return [
        {
            "key": k,
            "sector_name": v.sector_name,
            "title": v.title,
            "author": v.comment_author,
            "comment": v.comment_text[:60] + "..."
        }
        for k, v in SECTOR_CATALOG.items()
    ]


if __name__ == "__main__":
    print("=== MULTI-SECTOR VIRAL CATALOG ===")
    for sec in list_available_sectors():
        print(f"[{sec['key']}] {sec['sector_name']} -> {sec['title']}")
