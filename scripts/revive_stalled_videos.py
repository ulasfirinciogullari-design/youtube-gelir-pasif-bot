import sys, json, time
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from publish_to_youtube import get_authenticated_service

yt = get_authenticated_service()

VIRAL_REVIVALS = {
    "YG5F_i1wMlk": {
        "title": "👑 Interstellar | Miller Gezegenindeki 1 Saatin Korkunç Bedeli #shorts",
        "tags": ["interstellar", "miller gezegeni", "karadelik", "zaman paradoksu", "bilim kurgu", "shorts"]
    },
    "JsYP-J8i7ng": {
        "title": "👑 48 Laws of Power | Law 18: Why Isolation Destroys You #shorts",
        "tags": ["48 laws of power", "law 18", "robert greene", "power laws", "psychology", "shorts"]
    },
    "6Wj-x_l9qQE": {
        "title": "👑 Thomas Shelby'nin En Ölümcül Kuralı: Asla Öfkelenme #shorts",
        "tags": ["thomas shelby", "peaky blinders", "soğukkanlılık", "güç yasası", "sigma", "shorts"]
    },
    "-vqPZgMEXJE": {
        "title": "👑 48 Güç Yasası | Bölüm 13: Asla Merhamet Dilencisi Olma #shorts",
        "tags": ["48 güç yasası", "robert greene", "güç", "manipülasyon", "psikoloji", "shorts"]
    },
    "4DotMDpYJ3M": {
        "title": "👑 48 Güç Yasası | Bölüm 31: İnsanları Kendi Tuzağına Düşür #shorts",
        "tags": ["48 güç yasası", "tuzak", "strateji", "akıl oyunları", "karanlık zihin", "shorts"]
    },
    "mcjAB-IzW1I": {
        "title": "👑 Thomas Shelby: Zayıflar Özür Diler, Güçlüler Sonucu Değiştirir #shorts",
        "tags": ["thomas shelby", "özür dileme", "liderlik", "peaky blinders", "güç", "shorts"]
    },
    "cDzCHF9JjD8": {
        "title": "👑 Thomas Shelby: Odanın En Sessiz Adamından Daima Kork #shorts",
        "tags": ["thomas shelby", "sessizlik", "sessiz güç", "karanlık psikoloji", "sigma", "shorts"]
    },
    "vJuCABvC8sg": {
        "title": "👑 Machiavelli vs Marcus Aurelius: Lider Dediğin Korkutmalı mı? #shorts",
        "tags": ["machiavelli", "marcus aurelius", "stoacılık", "liderlik", "felsefe", "shorts"]
    },
    "Xzx63RlEbgc": {
        "title": "👑 Karanlık Psikoloji: Karşındakinin Yalanını 3 Saniyede Anla #shorts",
        "tags": ["karanlık psikoloji", "yalan yakalama", "mikro ifadeler", "beden dili", "manipülasyon", "shorts"]
    },
    "-B3cdS07Z9c": {
        "title": "👑 Karanlık Psikoloji: İnsanları Konuşturmanın En Acımasız Yolu #shorts",
        "tags": ["karanlık psikoloji", "sessizliğin gücü", "manipülasyon", "iletişim sırları", "shorts"]
    },
    "B41V5yaL21s": {
        "title": "👑 Tyler Durden: Gerçek Özgürlük Her Şeyi Kaybettiğinde Başlar #shorts",
        "tags": ["tyler durden", "fight club", "dövüş kulübü", "özgürlük", "felsefe", "shorts"]
    },
    "Eqj1OWsZ6rs": {
        "title": "👑 The Godfather: Don Corleone'nin Asla Affetmediği Tek Hata #shorts",
        "tags": ["the godfather", "baba filmi", "don corleone", "sadakat", "mafya yasaları", "shorts"]
    },
    "Go1brUNX40k": {
        "title": "👑 Marcus Aurelius: Kimsenin Yıkamayacağı Zihinsel Kale #shorts",
        "tags": ["marcus aurelius", "stoacılık", "zihinsel güç", "irade", "felsefe", "shorts"]
    },
    "iQAVINhYlJY": {
        "title": "👑 Gladyatör: Maximus'un Arenadaki İntikam Yemini #shorts",
        "tags": ["gladyatör", "maximus", "intikam", "onur", "irade", "shorts"]
    },
    "xyfllXPikRU": {
        "title": "👑 Çelik İrade: Zihnin Yorulduğu Yerde Gerçek Savaş Başlar #shorts",
        "tags": ["çelik irade", "tükenmişlik", "disiplin", "motivasyon", "gladyatör", "shorts"]
    }
}

print(f"🚀 {len(VIRAL_REVIVALS)} Adet Düşük İzlenmeli Videonun Başlık & Etiket Revizyonu Başlıyor...")
success_count = 0

for vid, opt in VIRAL_REVIVALS.items():
    try:
        resp = yt.videos().list(part="snippet", id=vid).execute()
        if not resp.get("items"):
            print(f"❌ Video bulunamadı: {vid}")
            continue
        item = resp["items"][0]
        snippet = item["snippet"]
        old_title = snippet["title"]
        
        snippet["title"] = opt["title"][:100]
        snippet["tags"] = opt["tags"]
        
        # Ensure funnel link is present in description
        desc = snippet.get("description", "")
        if "PY47MUbB71c" not in desc:
            funnel_header = (
                "🎬 İLGİLİ VİDEO (1080p Full Belgesel): https://youtu.be/PY47MUbB71c\n"
                "👑 Hollywood Trilogy | Thomas Shelby - Walter White - Tyler Durden\n\n"
            )
            snippet["description"] = funnel_header + desc
            
        update_resp = yt.videos().update(
            part="snippet",
            body={
                "id": vid,
                "snippet": snippet
            }
        ).execute()
        
        print(f"🔥 YENİLENDİ [{vid}]:")
        print(f"   Eski: {old_title}")
        print(f"   Yeni: {update_resp['snippet']['title']}")
        success_count += 1
        time.sleep(0.5)
    except Exception as e:
        print(f"⚠️ Hata [{vid}]: {e}")

print("="*60)
print(f"🎉 Toplam {success_count}/{len(VIRAL_REVIVALS)} video viral başlık ve etiketlerle güncellendi!")
