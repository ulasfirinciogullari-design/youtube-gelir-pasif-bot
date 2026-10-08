"""
Viral Controversy & Comment Ignition Engine for Zirvenin Kanunu.
Engineered to maximize YouTube Shorts comment velocity and viewer debate.
Generates tribal clash prompts, high-stakes moral dilemmas, and smart persona auto-replies.
"""

import random
import sys
from typing import Dict, List

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VIRAL_COMMENT_PROMPTS = {
    "shelby": [
        "📌 TARTIŞMA: Thomas Shelby mi yoksa Walter White mı? Aynı masada otursalar ilk kim geri adım atar? Fikrini yaz 👇",
        "📌 \"Korku seni tahmin edilebilir yapar.\" Hayatında bu hataya düşüp bedel ödediğin bir an oldu mu? 👇",
        "📌 Alfie Solomons vs Thomas Shelby: Kimin zihni daha tehlikeli ve acımasız? Tarafını seç 👇",
        "📌 Erkeklerin %99'u duygularıyla hareket eder, %1'i Thomas Shelby gibi satranç oynar. Katılıyor musun? 👇"
    ],
    "walter_white": [
        "📌 TARTIŞMA: Walter White ailesi için mi yaptı, yoksa saf egosunun kölesi mi oldu? Dürüst olanlar yazsın 👇",
        "📌 \"Tehlikenin ta kendisi benim.\" Heisenberg'e dönüştüğü o an haklı mıydı, yoksa bir canavar mıydı? 👇",
        "📌 Walter White vs Gustavo Fring: Hangisi daha büyük bir strateji dehasıydı? Yorumlarda tartışalım 👇",
        "📌 Skyler White haklı mıydı yoksa ihanet mi etti? Yorumlarda büyük savaş başlasın 👇"
    ],
    "power_laws": [
        "📌 YASA 1: 'Efendini asla gölgede bırakma.' Bu kuralı çiğneyip patronundan veya çevrenden darbe yiyen var mı? 👇",
        "📌 YASA 15: 'Düşmanını tamamen yok et, yaralı bırakma.' Gerçek hayatta merhamet zayıflık mıdır? 👇",
        "📌 48 Güç Yasasından en çok korktuğun yasa hangisi? 1 mi, 15 mi, 33 mü? Yazın analiz edelim 👇",
        "📌 Dostlarına mı daha çok güvenirsin yoksa düşmanlarına mı? Machiavelli cevabı vermişti 👇"
    ],
    "fight_club": [
        "📌 Tyler Durden: 'Sahip olduğun şeyler en sonunda sana sahip olur.' Gerçekten telefonlarımızın ve eşyalarımızın kölesi miyiz? 👇",
        "📌 2026 dünyasında Tyler Durden yaşasaydı ilk neyi yok ederdi? Düşüncelerini dök 👇",
        "📌 Rahat bir kölelik mi, yoksa acı dolu bir özgürlük mü? Hangisini seçersin? 👇"
    ],
    "stoic": [
        "📌 Marcus Aurelius: 'Başına gelenler değil, onlara verdiğin tepki seni belirler.' Bugün seni en çok sinirlendiren şeyi yaz, stoacı gözle çözelim 👇",
        "📌 Çelik bir irade mi, yoksa sınırsız para mı? Hangisi bir erkeği yenilmez yapar? 👇",
        "📌 Stoacılık hissizlik midir, yoksa duygularının efendisi olmak mı? Tartışma başladı 👇"
    ],
    "cosmic": [
        "📌 Oppenheimer: 'Ben ölüm oldum, dünyaların yok edicisi.' İnsanlık kendi icat ettiği teknolojiyle yok olacak mı? 👇",
        "📌 Interstellar: Miller gezegenindeki o 1 saat Dünya'da 7 yıldı. Zamanın acımasızlığı seni de korkutuyor mu? 👇",
        "📌 Evrende yalnız mıyız, yoksa bizi izleyenler cevap vermemizi mi bekliyor? Fermi Paradoksu başladı 👇"
    ]
}

PERSONA_REPLIES = [
    "Zirveye giden yol tam da bu bakış açısından geçer. Harika bir analiz 👑",
    "Çoğu insan bu detayı kaçırır. Güç yasalarını iyi kavramışsın.",
    "İşte tam da bu yüzden kitleler sıradan kalırken liderler tarih yazar.",
    "Duygusal tepki verenler elenir, stratejik bakanlar kazanır. Katılıyorum.",
    "Bu tespit derin. Bir sonraki bölümde bu konunun perde arkasını açacağız."
]


def get_viral_pinned_comment(niche_key: str = "shelby") -> str:
    """Returns a high-conversion provocative pinned comment."""
    for key, prompts in VIRAL_COMMENT_PROMPTS.items():
        if key in niche_key.lower():
            return random.choice(prompts)
    return random.choice(VIRAL_COMMENT_PROMPTS["power_laws"])


def get_persona_reply(user_comment: str = "") -> str:
    """Returns a charismatic, high-status channel persona reply."""
    return random.choice(PERSONA_REPLIES)


if __name__ == "__main__":
    for k in VIRAL_COMMENT_PROMPTS.keys():
        print(f"[{k.upper()}] Pinned Comment Hook:\n  {get_viral_pinned_comment(k)}\n")
