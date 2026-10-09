"""
Episodic Multi-Part Series Engine for Zirvenin Kanunu (@zirveninkanunu).
Transforms standalone clips into addictive 5 to 10-part serialized sagas:
1. Dynamic Series HUD badges in subtitles: [ ⚡ BÖLÜM X / 10 ]
2. Agonizing cliffhanger endings that force viewers to watch the next episode.
3. Cross-linking metadata generator (points to Part X-1, Part X+1, and Master Playlist).
4. Seamless binge-watch retention loops.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Dict, List, Optional, Tuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Master 10-Part Series Blueprints
SERIALIZED_SAGAS = {
    "shelby_silent_rules": {
        "title": "Thomas Shelby: Sessiz Gücün 10 Yasası",
        "niche": "shelby",
        "playlist_id": "PLWp2bBxJInPI",
        "total_parts": 10,
        "parts": [
            {"part": 1, "title": "BÖLÜM 1: 4 Saniye Kuralı", "hook": "Duygularını ilk gösteren masayı ilk kaybeder.", "cliffhanger": "Ama 2. kural, en yakın dostunu bile şüpheliye dönüştürür..."},
            {"part": 2, "title": "BÖLÜM 2: Asla Özür Dileme", "hook": "Özür dilemek zayıfların sığınağıdır.", "cliffhanger": "3. bölümde Alfie Solomons ile olan o meşhur pazarlığın perde arkası var..."},
            {"part": 3, "title": "BÖLÜM 3: Masada Boş Sandalye Etkisi", "hook": "Görünmeyen bir düşman daima daha korkutucudur.", "cliffhanger": "4. kuralı öğrendiğinde kimseye sırtını dönemeyeceksin..."},
            {"part": 4, "title": "BÖLÜM 4: Göz Kırpmadan Dinle", "hook": "Konuşurken gözlerini kaçıran, yalanını gizliyordur.", "cliffhanger": "5. kural düşmanını dostundan daha iyi tanımanın bedelidir..."},
            {"part": 5, "title": "BÖLÜM 5: Sakinliğin Şiddeti", "hook": "Bağıran adam acizdir; fısıldayan adam tehlikeli.", "cliffhanger": "6. kuralda Thomas'ın Luca Changretta'yı nasıl dize getirdiği var..."},
            {"part": 6, "title": "BÖLÜM 6: Bilgiyi Silah Gibi Sakla", "hook": "Düşmanın ne bildiğini bildiğinde, satranç biter.", "cliffhanger": "7. kural ihaneti önceden koklama sanatıdır..."},
            {"part": 7, "title": "BÖLÜM 7: En Kötü İhtimale Hazırlık", "hook": "Herkes kaçarken Shelby ailesi adımlarını sayar.", "cliffhanger": "8. kuralda kaybetmenin yasak olduğu o karanlık an var..."},
            {"part": 8, "title": "BÖLÜM 8: Gözyaşı Dökmeyen Gözler", "hook": "Acını sakla, zafere kadar kimseye belli etme.", "cliffhanger": "9. kural gücün zirvesindeki yalnızlığın bedelidir..."},
            {"part": 9, "title": "BÖLÜM 9: Sözünü Asla Geri Alma", "hook": "Bir kral bir kez konuşur, bedeli ne olursa olsun.", "cliffhanger": "Ve işte final... 10. kural her şeyi başa döndürecek..."},
            {"part": 10, "title": "BÖLÜM 10: Zirvenin Kanunu (FİNAL)", "hook": "Çünkü sonunda korku seni tahmin edilebilir yapar.", "cliffhanger": "Ve döngü yeniden başlar... Tüm seriyi baştan izle!"}
        ]
    },
    "48_laws_deadliest": {
        "title": "48 Güç Yasası: En Ölümcül 10 Yasa",
        "niche": "power_laws",
        "playlist_id": "PLfpXGpE89vUI",
        "total_parts": 10,
        "parts": [
            {"part": 1, "title": "BÖLÜM 1: Efendini Asla Gölgede Bırakma (Yasa 1)", "hook": "Patronunu aşmaya çalışırsan ilk kurban sen olursun.", "cliffhanger": "2. yasa dostların neden en tehlikeli düşmanlar olduğunu anlatır..."},
            {"part": 2, "title": "BÖLÜM 2: Dostlarına Fazla Güvenme (Yasa 2)", "hook": "Dostun kıskanır, düşmanın borcunu ödemek için çalışır.", "cliffhanger": "3. yasa niyetini gizlemenin ölümcül formülüdür..."},
            {"part": 3, "title": "BÖLÜM 3: Niyetini Asla Belli Etme (Yasa 3)", "hook": "Kurban tuzağı fark ettiğinde iş işten geçmiş olmalıdır.", "cliffhanger": "4. yasa her zaman gerekenden az konuşmanın gücüdür..."},
            {"part": 4, "title": "BÖLÜM 4: Gerekenden Az Konuş (Yasa 4)", "hook": "Ne kadar çok konuşursan, o kadar sıradan görünürsün.", "cliffhanger": "5. yasa itibarın can damarı olduğunu gösterir..."},
            {"part": 5, "title": "BÖLÜM 5: İtibarını Hayatın Pahasına Koru (Yasa 5)", "hook": "İtibar tek bir çatlakla yerle bir olur.", "cliffhanger": "6. yasa dikkat çekmenin karanlık psikolojisidir..."},
            {"part": 6, "title": "BÖLÜM 6: Her Ne Pahasına Olursa Olsun Dikkat Çek (Yasa 6)", "hook": "Görünmeyen adam asla kazanamaz.", "cliffhanger": "7. yasa başkalarının emeğini sahiplenme sanatıdır..."},
            {"part": 7, "title": "BÖLÜM 7: İşi Başkalarına Yaptır, Övgüyü Al (Yasa 7)", "hook": "Edison ve Tesla arasındaki savaşın tek gerçeği buydu.", "cliffhanger": "8. yasa insanları ayağına getirtmenin taktiğidir..."},
            {"part": 8, "title": "BÖLÜM 8: İnsanları Ayağına Getirt (Yasa 8)", "hook": "Yemi sen belirle, kontrol her zaman sende kalsın.", "cliffhanger": "15. yasa düşmanı tamamen ezmenin kuralıdır..."},
            {"part": 9, "title": "BÖLÜM 9: Düşmanını Tamamen Yok Et (Yasa 15)", "hook": "Közü sönmemiş ateş yeniden ormanı yakar. Yaralı bırakma.", "cliffhanger": "Ve zirvedeki 10. yasa: Zarafetle hükmetme sanatı..."},
            {"part": 10, "title": "BÖLÜM 10: İstediğin Gibi Düşün, Çoğunluk Gibi Davran (FİNAL)", "hook": "Farklı olduğunu haykıranlar giyotine gider; susanlar tahta oturur.", "cliffhanger": "10 bölümü tamamladın. Artık oyunu sen yönetiyorsun!"}
        ]
    }
}


def build_series_metadata(
    saga_key: str,
    part_number: int,
    video_url_prev: Optional[str] = None,
    video_url_next: Optional[str] = None
) -> Dict:
    """
    Generates high-converting interlinked metadata for a specific series episode.
    """
    saga = SERIALIZED_SAGAS.get(saga_key, SERIALIZED_SAGAS["shelby_silent_rules"])
    part_info = saga["parts"][min(part_number - 1, len(saga["parts"]) - 1)]

    total = saga["total_parts"]
    badge_label = f"BÖLÜM {part_number}/{total}"

    title = f"👑 {part_info['title']} #shorts"
    description_lines = [
        f"🔥 {saga['title']} — {part_info['title']}",
        f"💡 {part_info['hook']}\n",
        f"⏳ {part_info['cliffhanger']}\n",
        f"📌 OYNATMA LİSTESİ: Tüm bölümleri sırayla izle -> https://youtube.com/playlist?list={saga['playlist_id']}",
    ]

    if video_url_next:
        description_lines.append(f"▶️ SONRAKİ BÖLÜM (Part {part_number + 1}): {video_url_next}")
    if video_url_prev:
        description_lines.append(f"◀️ ÖNCEKİ BÖLÜM (Part {part_number - 1}): {video_url_prev}")

    description_lines.extend([
        "\n⚡ Her gün yeni 60 FPS sinematik güç analizleri: @zirveninkanunu",
        f"#shorts #{saga['niche']} #seri #part{part_number} #güçyasaları #motivasyon #felsefe"
    ])

    pinned_comment = (
        f"💬 Part {part_number}'i tamamladın! Bu kuralı hayatında uyguluyor musun? "
        f"Part {min(part_number + 1, total)} oynatma listesinde yayında! Fikrini yaz 👇"
    )

    return {
        "title": title[:100],
        "description": "\n".join(description_lines)[:5000],
        "badge_label": badge_label,
        "pinned_comment": pinned_comment,
        "part_number": part_number,
        "total_parts": total,
        "playlist_id": saga["playlist_id"]
    }


if __name__ == "__main__":
    meta = build_series_metadata("shelby_silent_rules", 1, video_url_next="https://youtube.com/shorts/part2_demo")
    print("🎬 Bölümlü Seri Meta Verisi Hazır:")
    print(f"Başlık: {meta['title']}")
    print(f"Rozet: {meta['badge_label']}")
    print(f"Pinli Yorum: {meta['pinned_comment']}")
    print("\nAçıklama:\n" + meta['description'])
