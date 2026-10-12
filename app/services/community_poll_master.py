"""
Community Tab Viral Poll Master for Zirvenin Kanunu (@zirveninkanunu).
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
        "id": "poll_enemy_forgive_or_wait",
        "question": "Düşmanını affeder misin, yoksa doğru anı mı beklersin? ♟️",
        "options": [
            "Doğru anı ve zayıf anını beklerim (Stratejik Sabır)",
            "Affederim ama asla unutmam (Soğuk Mesafe)",
            "Affetmek zayıflıktır, tamamen yok ederim (48 Güç Yasası)",
            "Umursamam, kendi zirveme odaklanırım (Stoacı Güç)"
        ],
        "category": "power_strategy",
        "virality_score": 98
    },
    {
        "id": "poll_shelby_vs_corleone",
        "question": "Thomas Shelby mi, Michael Corleone mi? Aynı masada otursalar kim kazanır? 👑",
        "options": [
            "Thomas Shelby (Soğukkanlı Zeka & Gözükaralık)",
            "Michael Corleone (Stratejik Ağırlık & Acımasızlık)",
            "Masadan ikisi de ortak olarak kalkar",
            "Birbirlerini tamamen yok ederler"
        ],
        "category": "cinema_power",
        "virality_score": 99
    },
    {
        "id": "poll_first_eye_contact_break",
        "question": "Bir odaya girdiğinde bakışlarını ilk kaçıran sen mi olursun, karşı taraf mı? 👁️",
        "options": [
            "Karşı taraf (Göz temasında asla geri adım atmam)",
            "Bazen ben (Gereksiz güç savaşına girmem)",
            "Göz teması yerine beden dilini okurum",
            "Umursamam, sessizce odayı tartar ve yönetirim"
        ],
        "category": "body_language",
        "virality_score": 97
    },
    {
        "id": "poll_silence_power_or_weakness",
        "question": "Sessizlik bir güç gösterisi midir, yoksa zayıflık mı? 🤫",
        "options": [
            "En büyük güç gösterisidir (Zihni okunamayan kazanır)",
            "Duruma göre zayıflık gibi algılanabilir",
            "Sadece arkasında stratejik eylem varsa güçtür",
            "Zayıfların kaçış noktasıdır"
        ],
        "category": "psychology",
        "virality_score": 96
    },
    {
        "id": "poll_48_laws_law_1",
        "question": "48 Güç Yasası #1: Asla Efendini Gölgede Bırakma. Bu kurala katılıyor musun? ♟️",
        "options": [
            "Kesinlikle evet (Kıskançlık en ölümcül tuzaktır)",
            "Hayır (Yetenekli olan her zaman parlamalı)",
            "Kural doğrudur ama efendini geçme anını iyi seçmelisin",
            "Kendi efendim benim, kimsenin gölgesinde kalmam"
        ],
        "category": "power_laws",
        "virality_score": 98
    }
]


def get_all_polls():
    """Returns the complete viral poll suite."""
    return VIRAL_COMMUNITY_POLLS


def get_next_community_poll():
    """Returns the most engaging community poll."""
    return VIRAL_COMMUNITY_POLLS[0]


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
