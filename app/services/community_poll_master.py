"""
Community Tab Viral Poll Master for Zirvenin Kanunu.
Automates high-conversion community feed polls designed to appear on the
YouTube Home Feeds of non-subscribers to drive 10,000+ votes and organic subscribers.
"""

import json
from pathlib import Path
import random
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VIRAL_COMMUNITY_POLLS = [
    {
        "id": "poll_shelby_vs_walter",
        "question": "🔥 Hangi karakterin zihni ve stratejisi daha tehlikeli?",
        "options": [
            "Thomas Shelby (Soğukkanlı & Sessiz Güç)",
            "Walter White / Heisenberg (Zeka & Öfke)",
            "Gustavo Fring (Kusursuz Disiplin)",
            "Tyler Durden (Kaos & Özgürlük)"
        ],
        "category": "cinema_power"
    },
    {
        "id": "poll_48_laws_deadliest",
        "question": "♟️ 48 Güç Yasası arasından çiğnenmesi EN TEHLİKELİ olan yasa hangisi?",
        "options": [
            "1. Yasa: Efendini asla gölgede bırakma",
            "2. Yasa: Dostlarına fazla güvenme, düşmanlarını kullan",
            "15. Yasa: Düşmanını tamamen yok et",
            "38. Yasa: İstediğin gibi düşün, çoğunluk gibi davran"
        ],
        "category": "power_laws"
    },
    {
        "id": "poll_stoic_mindset",
        "question": "🏛️ Bir erkeği hayatta yenilmez yapan en önemli güç nedir?",
        "options": [
            "Duygusal kontrol (Hiçbir şeye öfkelenmemek)",
            "Finansal bağımsızlık (Kimseye muhtaç olmamak)",
            "Stratejik sessizlik (Planlarını kimseye açmamak)",
            "Sınırsız cesaret ve acımasızlık"
        ],
        "category": "stoicism"
    },
    {
        "id": "poll_oppenheimer_destiny",
        "question": "🌌 İnsanlığın geleceğini en çok hangi tehdit belirleyecek?",
        "options": [
            "Yapay zeka ve nükleer teknoloji",
            "Zaman ve uzayın bilinmeyen sınırları",
            "İnsanın doyumsuz güç arzusu",
            "Fermi Paradoksu (Yalnız değiliz)"
        ],
        "category": "cosmic"
    }
]


def get_next_community_poll():
    """Returns the most engaging community poll."""
    return random.choice(VIRAL_COMMUNITY_POLLS)


def format_community_post_payload(poll: dict) -> dict:
    """Formats payload ready for YouTube API or webhook integration."""
    return {
        "snippet": {
            "type": "poll",
            "text": poll["question"],
            "pollOptions": [{"text": opt} for opt in poll["options"]],
        }
    }


if __name__ == "__main__":
    poll = get_next_community_poll()
    print("📊 Topluluk Sekmesi Viral Anket Örneği:")
    print(f"Soru: {poll['question']}")
    for idx, opt in enumerate(poll['options'], 1):
        print(f"  {idx}. {opt}")
