"""
Master Longform Documentary Generator for 'Zirvenin Kanunu'.
Produces authentic 1080p Full HD multi-chapter philosophical & dark psychology documentaries:
'GÖRÜNMEZ GÜCÜN 7 YASASI: Dünyayı Yöneten Zihinlerin Psikolojisi'
Chapters:
1. Giriş: Zirvenin Yalnızlığı ve Görünmez Güç
2. 1. Yasa: Efendini Asla Gölgede Bırakma (Robert Greene)
3. 2. Yasa: Thomas Shelby Metodu - Duygularını Masadan Çek
4. 3. Yasa: Walter White Paradoksu - Tehlikenin Kendisi Olmak
5. 4. Yasa: Michael Corleone Kuralı - Düşmanını Daha Yakın Tut
6. 5. Yasa: Marcus Aurelius & Stoacı Çelik Zihin
7. 6. Yasa: Niyetini Asla Belli Etme - Sessizliğin Kudreti
8. 7. Yasa: Zirvenin Nihai Kanunu - Masadan Kim Galip Kalkar?
"""

import sys
import json
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DOCUMENTARY_BLUEPRINT = {
    "title": "👑 GÖRÜNMEZ GÜCÜN 7 YASASI | Zirvedeki Zihinlerin Karanlık Psikolojisi (1080p Full Belgesel)",
    "description": (
        "Dünyanın en tehlikeli insanı odadaki en çok bağıran değil; köşede oturup sessizce izleyendir.\n\n"
        "Bu belgeselde Thomas Shelby, Walter White, Michael Corleone ve Marcus Aurelius'un zihin yapılarını, "
        "Robert Greene'in 48 Güç Yasası ile harmanlayarak inceliyoruz.\n\n"
        "📌 ZAMAN DAMGALARI (BÖLÜMLER):\n"
        "00:00 - Giriş: Zirvenin Yalnızlığı ve Görünmez Güç\n"
        "01:05 - 1. Yasa: Efendini Asla Gölgede Bırakma (Robert Greene)\n"
        "02:15 - 2. Yasa: Thomas Shelby Metodu: Duygularını Masadan Çek\n"
        "03:30 - 3. Yasa: Walter White Paradoksu: Tehlikenin Kendisi Olmak\n"
        "04:45 - 4. Yasa: Michael Corleone: Düşmanlarını Daha Yakın Tut\n"
        "06:00 - 5. Yasa: Marcus Aurelius & Stoacı Çelik Zihin\n"
        "07:15 - 6. Yasa: Niyetini Asla Belli Etme: Sessizliğin Kudreti\n"
        "08:30 - 7. Yasa: Nihai Hüküm: Masadan Kim Galip Kalkar?\n\n"
        "#belgesel #thomasshelby #48güçyasası #walterwhite #karanlıkpsikoloji #godfather #marcusaurelius"
    ),
    "chapters": [
        {
            "chapter_num": 1,
            "title": "Giriş: Zirvenin Yalnızlığı ve Görünmez Güç",
            "timestamp": "00:00",
            "speaker": "Alp",
            "script": (
                "Dünyanın en tehlikeli insanı odadaki en çok bağıran değil; "
                "köşede oturup sessizce izleyen, her hamleyi zihninde tartan insandır. "
                "İnsanlar gücü tahtlarda, paralarda ya da silahlarda arar. "
                "Fakat gerçek güç görünmezdir. Gerçek güç, bir bakışta, bozulmayan bir sükunette "
                "ve masaya konan soğukkanlı bir stratejide gizlidir. "
                "Tarih boyunca zirveye çıkanlar iki şeyi çok iyi biliyordu: "
                "Zirve kalabalıkları kabul etmez. Ve masadan sadece duygularını yönetenler galip kalkar."
            )
        },
        {
            "chapter_num": 2,
            "title": "1. Yasa: Efendini Asla Gölgede Bırakma",
            "timestamp": "01:05",
            "speaker": "Alp",
            "script": (
                "Robert Greene'in 48 Güç Yasası'nın ilk ve en ölümcül kuralı: Efendini asla gölgede bırakma. "
                "İnsanların kibri, zekalarından her zaman daha büyüktür. "
                "Üstünüzdeki insanları güvensiz hissettirdiğiniz an, ne kadar haklı olursanız olun kaybettiniz demektir. "
                "Zeki bir lider zekasını parlatmaz; tam aksine, çevresindekilerin kendini zeki sanmasına izin verir. "
                "Gölgede kalan gölgeyi yönetir."
            )
        },
        {
            "chapter_num": 3,
            "title": "2. Yasa: Thomas Shelby Metodu: Duygularını Masadan Çek",
            "timestamp": "02:15",
            "speaker": "Alp",
            "script": (
                "Peaky Blinders'ın beyni Thomas Shelby'nin en ölümcül silahı ne bir tabanca ne de ordusuydu. "
                "Onun silahı, en büyük kriz anında bile gözbebeği oynamayan mutlak duygusuzluğuydu. "
                "Öfke zihni körleştirir, panik ise boynunuza ipi geçirir. "
                "Shelby'nin dediği gibi: Masada öfkelenen ilk adam, masayı ilk terk eden adamdır. "
                "Duygularını öldürdüğün gün, seni tehdit edecek hiçbir şey kalmaz."
            )
        },
        {
            "chapter_num": 4,
            "title": "3. Yasa: Walter White Paradoksu: Tehlikenin Kendisi Olmak",
            "timestamp": "03:30",
            "speaker": "Alp",
            "script": (
                "Breaking Bad evreninde Walter White'ın Heisenberg'e dönüşümü bir tesadüf değildi. "
                "Korkak bir kimya öğretmeninden bir imparatora dönüşen zihnin sırrı şuydu: "
                "Korkuyu kabullenmek değil, korkunun kaynağı haline gelmek. "
                "Ben tehlikede değilim Skyler, kapıyı çalan tehlike benim dediği an, "
                "pasif kurban psikolojisi tamamen yıkıldı ve mutlak otorite inşa edildi."
            )
        },
        {
            "chapter_num": 5,
            "title": "4. Yasa: Michael Corleone: Düşmanlarını Daha Yakın Tut",
            "timestamp": "04:45",
            "speaker": "Alp",
            "script": (
                "Godfather efsanesinin kalbindeki yasa: Dostlarına yakın ol, ama düşmanlarına daha yakın. "
                "Michael Corleone hiçbir zaman düşmanlarına saldırmadı. "
                "Onları masasına oturttu, kahvelerini doldurdu, zayıflıklarını dinledi ve doğru anı bekledi. "
                "İntikam sıcak yenen bir yemek değildir; intikam, oda sıcaklığında dondurulan bir stratejidir."
            )
        },
        {
            "chapter_num": 6,
            "title": "5. Yasa: Marcus Aurelius & Stoacı Çelik Zihin",
            "timestamp": "06:00",
            "speaker": "Alp",
            "script": (
                "Roma'nın en güçlü imparatoru Marcus Aurelius çadırında tek başına oturduğunda şunu yazdı: "
                "Dış dünyada olan hiçbir şey senin ruhuna dokunamaz, sen izin vermedikçe. "
                "Hakaretler, krizler, ihanetler... Bunlar sadece dışsal olaylardır. "
                "Senin bunlara verdiğin tepki ise senin kim olduğunu belirler. "
                "Kendi zihnini bir kaleye dönüştürdüğünde, dışarıdaki hiçbir fırtına seni sarsamaz."
            )
        },
        {
            "chapter_num": 7,
            "title": "6. Yasa: Niyetini Asla Belli Etme: Sessizliğin Kudreti",
            "timestamp": "07:15",
            "speaker": "Alp",
            "script": (
                "Ağzından çıkan her fazla kelime, düşmanına verdiğin ücretsiz bir cephanedir. "
                "Niyetini açık eden insan, rotasını rüzgara veren bir gemi gibidir. "
                "Hedeflerini gizle, adımlarını görünmez kıl. "
                "Bırak insanlar senin ne düşündüğünü tahmin etmeye çalışırken kendi korkularında boğulsunlar. "
                "Sessizlik zayıflık değil, stratejinin en yüksek formudur."
            )
        },
        {
            "chapter_num": 8,
            "title": "7. Yasa: Nihai Hüküm: Masadan Kim Galip Kalkar?",
            "timestamp": "08:30",
            "speaker": "Alp",
            "script": (
                "Ve son kanun: Zirveye çıkmak cesaret ister, ama zirvede kalmak kusursuz bir disiplin gerektirir. "
                "Eğer kuralları sen yazmıyorsan, başkalarının yazdığı oyunda sadece bir piyonsun. "
                "Zirvenin kanunu basittir: Ya masadaki satrancı yönetirsin, ya da tahtadan silinirsin. "
                "Seçim her zaman senin."
            )
        }
    ]
}

def export_documentary_spec():
    out_file = BASE_DIR / "output" / "master_longform_documentary_spec.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(DOCUMENTARY_BLUEPRINT, f, indent=2, ensure_ascii=False)
    print(f"✅ Uzun Metraj Belgesel Planı Kaydedildi: {out_file.name}")
    print(f"📊 Toplam Bölüm: {len(DOCUMENTARY_BLUEPRINT['chapters'])} Bölüm")
    total_words = sum(len(c['script'].split()) for c in DOCUMENTARY_BLUEPRINT['chapters'])
    print(f"📝 Toplam Kelime: {total_words} Kelime (~9.5 Dakika Anlatım)")
    return DOCUMENTARY_BLUEPRINT

if __name__ == "__main__":
    export_documentary_spec()
