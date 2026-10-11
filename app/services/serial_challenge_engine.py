"""
Serial Challenge Engine for 'Zirvenin Kanunu' (@zirveninkanunu)
30-Day Peak Challenge: '30 GÜNLÜK ZİRVE MEYDAN OKUMASI'
Architects daily stoic, dark psychology, and executive fortitude tasks.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CURRICULUM_FILE = Path(__file__).resolve().parent / "challenge_30_days_curriculum.json"

CHALLENGE_CURRICULUM: List[Dict[str, Any]] = [
    {
        "day": 1,
        "title": "Sıfır Mazeret Kuralı",
        "subtitle": "Zero Excuse Rule",
        "category": "Stoic Discipline",
        "quote_author": "Marcus Aurelius",
        "quote": "Mazeret üretmeyi bıraktığın an, kendi krallığını inşa etmeye başlarsın.",
        "concept": "Günün 24 saati boyunca tek bir şikayet veya mazeret cümlesi kurmayacaksın. Karşılaştığın her engelde sorumluluğu tamamen üstlen.",
        "mission_steps": [
            "Bugün karşılaştığın hiçbir zorluk için dış etkenleri suçlama.",
            "Ağzından mazeret çıkacağını hissettiğin an 3 saniye dur ve sus.",
            "Günün sonunda başardığın en zor görevi yoruma 'GÜN 1 TAMAMLANDI' yazarak mühürle."
        ],
        "script_lines": [
            "30 Günlük Zirve Meydan Okuması başlıyor. Gün 1: Sıfır Mazeret Kuralı.",
            "Bugünden itibaren zayıfların sığınağı olan mazeretleri hayatından siliyorsun.",
            "Marcus Aurelius der ki: Mazeret üretmeyi bıraktığın an, kendi krallığını kurmaya başlarsın.",
            "Günün görevi: Bugün karşılaştığın hiçbir şey için başkalarını suçlamayacaksın. Trafik, yorgunluk ya da insanlar.",
            "Kontrol sende. Mazeret yok, sadece sonuç var.",
            "Bu meydan okumayı kabul ediyorsan yoruma GÜN 1 TAMAM yaz.",
            "Yarın Gün 2 geliyor: 3 Saniyelik Sessizlik Kuralı. Takipte kal ve bildirimleri aç!"
        ],
        "cliffhanger": "Yarın Gün 2 geliyor: 3 Saniyelik Sessizlik Kuralı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 1/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#30GünlükZirveMeydanOkuması", "#Gelişim", "#Stoacılık", "#Disiplin", "#Motivasyon"]
    },
    {
        "day": 2,
        "title": "3 Saniyelik Sessizlik Kuralı",
        "subtitle": "The 3-Second Silence Rule",
        "category": "Executive Presence & Dark Psychology",
        "quote_author": "Thomas Shelby",
        "quote": "Bir odadaki en güçlü adam, konuşmadan önce en uzun süre sessiz kalabilendir.",
        "concept": "Sana yöneltilen her soruya ve kışkırtmaya cevap vermeden önce tam 3 saniye bekle. Aceleci tepki zayıflıktır, kontrollü sessizlik güçtür.",
        "mission_steps": [
            "Sana bir soru sorulduğunda göz teması kur ve 3 saniye sessiz kal.",
            "Söz kesme, laf yetiştirme telaşını tamamen terk et.",
            "Sessizliğin karşı tarafta yarattığı psikolojik ağırlığı hisset."
        ],
        "script_lines": [
            "30 Günlük Zirve Meydan Okuması. Gün 2: 3 Saniyelik Sessizlik Kuralı.",
            "Zayıf insanlar her şeye hemen tepki verir. Güçlü olanlar ise sessizlikle odayı yönetir.",
            "Thomas Shelby gibi düşün: Bir soru aldığında hemen cevap verme. Tam üç saniye bekle.",
            "Gözlerini hedeften ayırma ve derin bir nefes al. Acele eden zayıftır, bekleyen kraldır.",
            "Günün görevi: Bugün konuştuğun herkese karşı bu üç saniyelik kuralı uygula.",
            "Yoruma GÜN 2 TAMAM yazarak iradeni mühürle.",
            "Yarın Gün 3 geliyor: Bilgi Detoksu ve Zihinsel Netlik. Takipte kal!"
        ],
        "cliffhanger": "Yarın Gün 3 geliyor: Bilgi Detoksu ve Zihinsel Netlik. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 2/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "godfather_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#ThomasShelby", "#Psikoloji", "#İletişim", "#BedenDili"]
    },
    {
        "day": 3,
        "title": "Bilgi Detoksu ve Zihinsel Netlik",
        "subtitle": "Information Detox & Mental Clarity",
        "category": "Mental Fortitude",
        "quote_author": "Seneca",
        "quote": "Her yerde olan insan, hiçbir yerde değildir. Zihnini korumayan köleleşir.",
        "concept": "Gereksiz haberler, dedikodular ve boş kaydırmalar zihnini zehirler. Bugün sadece hedefine hizmet eden bilgilere izin ver.",
        "mission_steps": [
            "Günün ilk ve son 1 saatinde ekrana bakma.",
            "Sana fayda sağlamayan tüm bildirimleri kapat.",
            "20 dakika mutlak sessizlikte düşüncelerini dinle."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 3: Bilgi Detoksu ve Zihinsel Netlik.",
            "Zihnini her gün çöplüğe çeviren binlerce gereksiz bildirimi durdurmanın vakti geldi.",
            "Seneca der ki: Her yerde olan insan, hiçbir yerde değildir.",
            "Günün görevi: Bugün tüm bildirimleri sustur ve anlamsız sosyal medya kaydırmalarını sıfırla.",
            "Zihnin berraklaştığında düşmanlarının göremediği fırsatları göreceksin.",
            "Yoruma GÜN 3 TAMAM yaz.",
            "Yarın Gün 4 geliyor: Göz Teması Hakimiyeti. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 4 geliyor: Göz Teması Hakimiyeti. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 3/30",
        "primary_cinematic": "fight_club_master.mp4",
        "secondary_cinematic": "matrix_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Odaklanma", "#Seneca", "#ZihinDetoksu"]
    },
    {
        "day": 4,
        "title": "Göz Teması Hakimiyeti",
        "subtitle": "Dominant Eye Contact",
        "category": "Executive Dominance",
        "quote_author": "Robert Greene",
        "quote": "Bakışlarını ilk kaçıran taraf, psikolojik üstünlüğü masada bırakır.",
        "concept": "Konuştuğun herkesle sarsılmaz ve delici bir göz teması kur. Bakışlarını ilk kaçıran asla sen olma.",
        "mission_steps": [
            "Biriyle konuşurken doğrudan göz bebeklerine odaklan.",
            "Karşı taraf bakışını kaçırana kadar gözlerini ayırma.",
            "Göz kırpma hızını yavaşlat ve mimiklerini sabitle."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 4: Göz Teması Hakimiyeti.",
            "İnsanlar arasındaki hiyerarşi kelimelerle değil, gözlerle belirlenir.",
            "Günün görevi: Bugün konuştuğun herkesle sarsılmaz bir göz teması kur.",
            "Bakışlarını ilk kaçıran taraf asla sen olmayacaksın. Sakin, derin ve keskin bak.",
            "Korkusuz bir bakış, yüzlerce kelimeden daha yıkıcıdır.",
            "Yoruma GÜN 4 TAMAM yaz.",
            "Yarın Gün 5 geliyor: Soğuk Şok ve Zihinsel Sıfırlama. Hazırlan!"
        ],
        "cliffhanger": "Yarın Gün 5 geliyor: Soğuk Şok ve Zihinsel Sıfırlama. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 4/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "batman_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#BedenDili", "#Özgüven", "#Güç"]
    },
    {
        "day": 5,
        "title": "Soğuk Duş ve Zihinsel Şok",
        "subtitle": "Dopamine Reset & Thermal Shock",
        "category": "Physical Willpower",
        "quote_author": "Epiktetos",
        "quote": "Bedenini ve dürtülerini fethedemeyen insan, asla gerçek anlamda özgür olamaz.",
        "concept": "Sabah duşunun son 2 dakikasını buz gibi suyun altında geçir. Beynindeki konfor tuzağını parçala.",
        "mission_steps": [
            "Duşun son 120 saniyesinde suyu tamamen soğuğa çevir.",
            "Nefesini kontrol et, panikleme ve dimdik dur.",
            "Zihninin direnç duvarını aşmanın zaferini kutla."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 5: Soğuk Şok ve Zihinsel Sıfırlama.",
            "Konfor seni yavaş yavaş zehirleyen en tatlı tuzaktır.",
            "Günün görevi: Bugün duşun son iki dakikasını buz gibi suyun altında geçireceksin.",
            "Nefesini yavaşlat, titremeyi durdur ve zihnini zorla.",
            "Bedenine hükmedemeyen bir adam dünyaya asla hükmedemez.",
            "Yoruma GÜN 5 TAMAM yaz.",
            "Yarın Gün 6 geliyor: Dijital Hayalet Modu. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 6 geliyor: Dijital Hayalet Modu. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 5/30",
        "primary_cinematic": "fight_club_master.mp4",
        "secondary_cinematic": "gladiator_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Disiplin", "#SoğukDuş", "#İrade"]
    },
    {
        "day": 6,
        "title": "Dijital Görünmezlik (Hayalet Modu)",
        "subtitle": "Ghost Mode & Strategic Disappearance",
        "category": "Dark Psychology & Focus",
        "quote_author": "Sun Tzu",
        "quote": "Görünmez ol, sessiz ol. Gerçek gücün fısıltısı gölgelerin arasından doğar.",
        "concept": "Bugün hiçbir sosyal medyada durum paylaşma, hikaye atma, varlığını hissettirme. Sessizlikte büyü.",
        "mission_steps": [
            "Tüm sosyal medya paylaşımlarını durdur.",
            "Nerede ve ne yaptığını kimseye raporlama.",
            "Enerjini dış dünyaya gösterişe değil, içsel üretim gücüne aktar."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 6: Hayalet Modu.",
            "Herkesin kendini sergilediği bir dünyada en büyük güç gizemdir.",
            "Sun Tzu der ki: Görünmez ol, sessiz ol; zafer gölgelerde olgunlaşır.",
            "Günün görevi: Bugün tek bir paylaşım bile yapma. Kimseye nerede olduğunu anlatma.",
            "Sessizce çalış, fırtınayı sadece sonuçlar kopsun.",
            "Yoruma GÜN 6 TAMAM yaz.",
            "Yarın Gün 7 geliyor: Tepkisizlik Kalkanı. Bildirimleri aç!"
        ],
        "cliffhanger": "Yarın Gün 7 geliyor: Tepkisizlik Kalkanı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 6/30",
        "primary_cinematic": "batman_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Gizem", "#HayaletModu", "#Güç"]
    },
    {
        "day": 7,
        "title": "Tepkisizlik Kalkanı",
        "subtitle": "The Non-Reaction Shield",
        "category": "Emotional Stoicism",
        "quote_author": "Marcus Aurelius",
        "quote": "Seni yalnızca senin izin verdiğin kelimeler incitebilir. İzni iptal et, güç sende kalsın.",
        "concept": "Bugün seni provoke etmeye çalışan hiçbir söze öfkeyle veya savunmayla karşılık verme. Sadece gözlemle.",
        "mission_steps": [
            "Sana yapılan eleştirilere ve iğnelemelere nötr bir tebessümle bak.",
            "Haklı çıkma ihtiyacını tamamen çöpe at.",
            "Provokasyonların üstünden bir kaya gibi sessizce geç."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 7: Tepkisizlik Kalkanı.",
            "Biri seni kışkırttığında öfkeleniyorsan, onun kuklası olmuşsun demektir.",
            "Marcus Aurelius hatırla: Seni sadece senin onay verdiğin düşünceler incitebilir.",
            "Günün görevi: Bugün karşılaştığın hiçbir kışkırtmaya tepki verme. Sadece hafifçe gülümse ve sus.",
            "Tepkisizlik, bir düşmanı çıldırtacak en asil silahtır.",
            "Yoruma GÜN 7 TAMAM yaz.",
            "Yarın Gün 8 geliyor: Hayır Deme Sanatı. Takipte kal!"
        ],
        "cliffhanger": "Yarın Gün 8 geliyor: Hayır Deme Sanatı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 7/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "godfather_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Stoacılık", "#Sakinlik", "#Özdenetim"]
    },
    {
        "day": 8,
        "title": "Hayır Deme Sanatı",
        "subtitle": "The Sovereign 'No'",
        "category": "Boundaries & Sovereignty",
        "quote_author": "Seneca",
        "quote": "Başkalarına evet derken, kendine gizlice hayır demediğinden emin ol.",
        "concept": "Hedeflerine ve değerlerine uymayan en az üç gereksiz talebe mazeret bildirmeksizin net bir 'Hayır' de.",
        "mission_steps": [
            "İstemediğin davetlere ve ricalara net şekilde 'Hayır' de.",
            "Mazeret uydurma, özür dileme; sadece 'Müsait değilim' de.",
            "Kendi sınırlarını bir kale gibi savun."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 8: Hayır Deme Sanatı.",
            "İnsanları memnun etmeye çalışan herkes günün sonunda kendi hayatını feda eder.",
            "Seneca der ki: Başkalarına evet derken, kendine hayır demediğinden emin ol.",
            "Günün görevi: Bugün seni hedeflerinden alıkoyan en az iki şeye net bir şekilde HAYIR de. Açıklama yapma, özür dileme.",
            "Sınırlarını çizen adam saygı görür.",
            "Yoruma GÜN 8 TAMAM yaz.",
            "Yarın Gün 9 geliyor: Postür ve Alan Hakimiyeti. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 9 geliyor: Postür ve Alan Hakimiyeti. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 8/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "godfather_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Özgüven", "#Sınırlar", "#KişiselGelişim"]
    },
    {
        "day": 9,
        "title": "Postür ve Alan Hakimiyeti",
        "subtitle": "Alpha Posture & Spatial Dominance",
        "category": "Body Language & Status",
        "quote_author": "Thomas Shelby",
        "quote": "Odaya girdiğinde omurgan bir kılıç gibi dik olmalı; yürüyüşün nereye gittiğini bildiğini haykırmalı.",
        "concept": "Omuzlarını geriye al, çeneni paralel tut ve her girdiğin ortamda alanı sahiplen.",
        "mission_steps": [
            "Yürürken adımlarını yüzde 20 yavaşlat ve yere sağlam bas.",
            "Otururken büzülme, kollarını ve bacaklarını rahatça açarak alanı doldur.",
            "Telefonuna bakarken kafanı aşağı eğme, göz hizasına kaldır."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 9: Postür ve Alan Hakimiyeti.",
            "Vücudun beynine nasıl hissetmesi gerektiğini söyler.",
            "Omuzlarını indirip büzüldüğünde dünya seni bir av olarak görür.",
            "Günün görevi: Omuzlarını geriye al, göğsünü aç ve adımlarını yavaşlat.",
            "Bir odaya girdiğinde sanki mekanın sahibi senmişsin gibi yürü.",
            "Yoruma GÜN 9 TAMAM yaz.",
            "Yarın Gün 10 geliyor: 24 Saat Sıfır Şikayet. Hazır ol!"
        ],
        "cliffhanger": "Yarın Gün 10 geliyor: 24 Saat Sıfır Şikayet Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 9/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "scarface_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#BedenDili", "#Karizma", "#Duruş"]
    },
    {
        "day": 10,
        "title": "24 Saat Sıfır Şikayet",
        "subtitle": "Zero Complaint Protocol",
        "category": "Stoic Mastery",
        "quote_author": "Marcus Aurelius",
        "quote": "Kaderinden ve şartlarından şikayet eden adam, kendi boynuna tasma takar.",
        "concept": "24 saat boyunca hava durumu, para, yorgunluk veya insanlar hakkında tek bir negatif kelime bile söyleme.",
        "mission_steps": [
            "Bileğine bir lastik veya toka tak; her şikayette diğer kola geçir.",
            "Negatif bir durum olduğunda 'Bunu nasıl çözerim?' sorusuna odaklan.",
            "Günü sıfır şikayetle tamamla."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 10: 24 Saat Sıfır Şikayet.",
            "Şikayet etmek, çaresizliğini itiraf eden zavallıların sporudur.",
            "Marcus Aurelius der ki: Şartlarından şikayet eden, kendi boynuna zincir vurur.",
            "Günün görevi: Bugün yirmi dört saat boyunca tek bir negatif kelime bile etmeyeceksin.",
            "Hava sıcak mı? Yoruldun mu? Sus ve işini yap. Çözüm üret, sızlanma.",
            "Yoruma GÜN 10 TAMAM yaz.",
            "Yarın Gün 11 geliyor: Keşiş Modu (Derin Odak). Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 11 geliyor: Keşiş Modu ve Derin Odak. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 10/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "interstellar_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Disiplin", "#Stoacılık", "#ŞikayetYok"]
    },
    {
        "day": 11,
        "title": "Keşiş Modu (Monk Mode Odak)",
        "subtitle": "Monk Mode Deep Work",
        "category": "Ruthless Productivity",
        "quote_author": "Seneca",
        "quote": "Zamanını çalanlar, hayatını çalan en tehlikeli hırsızlardır.",
        "concept": "Günde kesintisiz 90 dakika boyunca telefonu başka odaya koyarak en önemli projen üzerinde çalış.",
        "mission_steps": [
            "90 dakikalık zamanlayıcı başlat.",
            "Tüm sekmeleri ve cihazları kapat.",
            "Sadece tek bir kritik görev üzerinde derinleş."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 11: Keşiş Modu.",
            "Ortalama insanlar dikkatlerini bedavaya satar, krallar ise odaklarını korur.",
            "Günün görevi: Bugün doksan dakika boyunca dünyadan tamamen kopacaksın.",
            "Telefon başka odada, sıfır bildirim, tek bir kritik hedef.",
            "Bu doksan dakikada üreteceğin şey, başkalarının bir haftada ürettiğinden fazla olacak.",
            "Yoruma GÜN 11 TAMAM yaz.",
            "Yarın Gün 12 geliyor: Mikro Alışkanlık Çeliği. Devam et!"
        ],
        "cliffhanger": "Yarın Gün 12 geliyor: Mikro Alışkanlık Çeliği. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 11/30",
        "primary_cinematic": "oppenheimer_master.mp4",
        "secondary_cinematic": "matrix_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#MonkMode", "#Verimlilik", "#Odaklanma"]
    },
    {
        "day": 12,
        "title": "Mikro Alışkanlık Çeliği",
        "subtitle": "Atomic Willpower",
        "category": "Habit Architecture",
        "quote_author": "Aristoteles",
        "quote": "Biz tekrar tekrar yaptığımız şeyleriz. Mükemmellik bir eylem değil, bir alışkanlıktır.",
        "concept": "Her gün yapmaktan kaçındığın küçük bir eylemi seç ve bunu mükemmel bir disiplinle yerine getir.",
        "mission_steps": [
            "Yatağını askeri intizamla topla.",
            "Masandaki tüm dağınıklığı sıfırla.",
            "Küçük şeyleri kusursuz yapan, büyük şeyleri de kusursuz yapar."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 12: Mikro Alışkanlık Çeliği.",
            "Büyük savaşlar küçük detaylarda kazanılır ya da kaybedilir.",
            "Aristoteles der ki: Mükemmellik bir eylem değil, bir alışkanlıktır.",
            "Günün görevi: Yatağını askeri intizamla düzelt, masanı sıfırla ve ertelenen o küçük görevi hemen bitir.",
            "Küçük şeylerde disiplini olmayan birinin büyük hedefleri sadece bir hayaldir.",
            "Yoruma GÜN 12 TAMAM yaz.",
            "Yarın Gün 13 geliyor: Stratejik Yalnızlık. Bildirimleri aç!"
        ],
        "cliffhanger": "Yarın Gün 13 geliyor: Stratejik Yalnızlık Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 12/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Alışkanlık", "#Disiplin", "#Düzen"]
    },
    {
        "day": 13,
        "title": "Stratejik Yalnızlık",
        "subtitle": "Strategic Solitude",
        "category": "Mental Independence",
        "quote_author": "Arthur Schopenhauer",
        "quote": "Büyük zihinler yalnızlıkta devleşir, kalabalıkta sıradanlaşır.",
        "concept": "Tek başına bir kafeye git veya tek başına 45 dakika yürü; müziksiz, podcastsiz, sadece kendi düşüncelerinle baş başa kal.",
        "mission_steps": [
            "Kulaklıklarını çıkar.",
            "Kendi içine dön ve son bir yılını tarafsızca yargıla.",
            "Kendi kendine yetebilmenin kudretini tat."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 13: Stratejik Yalnızlık.",
            "Kendi başına beş dakika bile kalamayan bir insan, herkesin kölesi olmaya mahkumdur.",
            "Schopenhauer der ki: Büyük zihinler yalnızlıkta devleşir.",
            "Günün görevi: Bugün kırk beş dakika boyunca tek başına yürü. Kulaklık yok, telefon yok, müzik yok.",
            "Sadece sen ve zihnin. Kendi sesini duymayan, başkalarının gürültüsünde boğulur.",
            "Yoruma GÜN 13 TAMAM yaz.",
            "Yarın Gün 14 geliyor: Duygusal Zırh. Takipte kal!"
        ],
        "cliffhanger": "Yarın Gün 14 geliyor: Duygusal Zırh Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 13/30",
        "primary_cinematic": "batman_master.mp4",
        "secondary_cinematic": "oppenheimer_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Yalnızlık", "#Felsefe", "#Zihin"]
    },
    {
        "day": 14,
        "title": "Duygusal Zırh",
        "subtitle": "Emotional Armor & Stoic Unflappability",
        "category": "Emotional Stoicism",
        "quote_author": "Epiktetos",
        "quote": "Duygularının esiri olan bir adam, herkesin kolayca oynatabileceği bir kukladır.",
        "concept": "Beklenmedik bir kriz anında nabzını düşür. Ses tonunu alçalt, sakinliğinle çevrene dehşet verici bir otorite yay.",
        "mission_steps": [
            "Ters giden bir durumda sesini asla yükseltme.",
            "Kalp atışını burnundan derin nefesler alarak yavaşlat.",
            "Çözümü soğukkanlı bir cerrah gibi uygula."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 14: Duygusal Zırh.",
            "Kaos patlak verdiğinde bağıranlar zayıflardır. Liderler sesini alçaltır.",
            "Epiktetos der ki: Duygularını kontrol edemeyen, herkesin oyuncağı olur.",
            "Günün görevi: Bugün ne ters giderse gitsin, ses tonunu asla yükseltme. Sakin, buz gibi ve net ol.",
            "Sakinlik kriz anında en ölümcül silahtır.",
            "Yoruma GÜN 14 TAMAM yaz.",
            "Yarın Gün 15 geliyor: Finansal Sessizlik Kuralı. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 15 geliyor: Finansal Sessizlik Kuralı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 14/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Soğukkanlılık", "#Liderlik", "#Stoacılık"]
    },
    {
        "day": 15,
        "title": "Finansal Sessizlik Kuralı",
        "subtitle": "Stealth Wealth & Financial Silence",
        "category": "Executive Power",
        "quote_author": "Thomas Shelby",
        "quote": "Ne kadar kazandığını asla kimseye söyleme. Gerçek zenginlik fısıldar, ucuzluk bağırır.",
        "concept": "Maddi durumun, yatırımların ve kazancın hakkında kimseye tek kelime etme. Hayalet gibi varlıklı ol.",
        "mission_steps": [
            "Para hakkında övünmeyi veya yakınmayı tamamen kes.",
            "Maddi harcamalarını gösteriş için değil değer için yap.",
            "Sessiz sermaye biriktir."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 15: Finansal Sessizlik Kuralı.",
            "Parası hakkında bağıran insanlar aslında içten içe yoksuldur.",
            "Thomas Shelby gibi ol: Ne kadar kazandığını, ne kadar biriktirdiğini kimse bilmesin.",
            "Günün görevi: Bugün paran veya harcamaların hakkında kimseye tek bir laf bile etme.",
            "Bırak insanlar seni çözemesin. Zenginlik sessizlikte büyür.",
            "Yoruma GÜN 15 TAMAM yaz.",
            "Yarın Gün 16 geliyor: 5 Saniyelik İcraat Protokolü. Devam!"
        ],
        "cliffhanger": "Yarın Gün 16 geliyor: 5 Saniyelik İcraat Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 15/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "wolf_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Finans", "#SessizGüç", "#Para"]
    },
    {
        "day": 16,
        "title": "5 Saniyelik İcraat Protokolü",
        "subtitle": "Executive Decisiveness",
        "category": "High Agency",
        "quote_author": "Robert Greene",
        "quote": "Tereddüt yenilginin ilk adımıdır; cesaret ise kapıları ardına kadar açar.",
        "concept": "Bir karar vermen gerektiğinde beynine bahane üretmesi için zaman tanıma. 5'ten geriye say ve harekete geç.",
        "mission_steps": [
            "Ertelediğin görevi belirle.",
            "5-4-3-2-1 de ve ayağa kalk.",
            "Düşünmeyi bırak, sadece uygula."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 16: 5 Saniyelik İcraat Protokolü.",
            "Tereddüt eden insan her zaman kaybeder. Hızlı karar alanlar ise dünyayı yönetir.",
            "Günün görevi: Bugün yapman gereken ama ertelediğin bir şeyi seç. Beşten geriye say ve derhal başla.",
            "Beyninin mazeret üretmesine izin verme. Harekete geçen irade dağları devirir.",
            "Yoruma GÜN 16 TAMAM yaz.",
            "Yarın Gün 17 geliyor: Konuşma Orucu. Hazır ol!"
        ],
        "cliffhanger": "Yarın Gün 17 geliyor: Konuşma Orucu ve Az Kelime Gücü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 16/30",
        "primary_cinematic": "fight_club_master.mp4",
        "secondary_cinematic": "matrix_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#HareketeGeç", "#İrade", "#Eylem"]
    },
    {
        "day": 17,
        "title": "Konuşma Orucu (Stoik Özlü Söz)",
        "subtitle": "Speech Fasting & Stoic Brevity",
        "category": "Dark Psychology",
        "quote_author": "Zeno",
        "quote": "İki kulağımız ve bir ağzımız var; daha çok dinleyip daha az konuşalım diye.",
        "concept": "Gereksiz laf kalabalığını sıfırla. Bugün sadece zorunlu olduğunda ve net cümlelerle konuş.",
        "mission_steps": [
            "Boş sohbetlere ve dedikodulara katılma.",
            "Cümlelerini yarı yarıya kısalt.",
            "Kelimelerinin değerini artır."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 17: Konuşma Orucu.",
            "Çok konuşan insan sırlarını ve zayıflıklarını açık eder.",
            "Zenon der ki: İki kulağımız ve bir ağzımız var; daha çok dinleyip az konuşalım diye.",
            "Günün görevi: Bugün kelime sayını yarı yarıya düşür. Sadece gerektiğinde, kısa ve net konuş.",
            "Kelimelerin azaldıkça ağırlığın artacak.",
            "Yoruma GÜN 17 TAMAM yaz.",
            "Yarın Gün 18 geliyor: Kontrollü Rahatsızlık. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 18 geliyor: Kontrollü Rahatsızlık Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 17/30",
        "primary_cinematic": "godfather_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Sessizlik", "#Ağırlık", "#Karizma"]
    },
    {
        "day": 18,
        "title": "Kontrollü Rahatsızlık",
        "subtitle": "Controlled Discomfort",
        "category": "Anti-Fragility",
        "quote_author": "Seneca",
        "quote": "Zorluklar zihni çelikleştirir, tıpkı ateşin demiri dövdüğü gibi.",
        "concept": "Bugün bilerek ve isteyerek fiziksel veya zihinsel bir konforsuzluk yarat (örneğin yerde oturmak, bir öğün aç kalmak).",
        "mission_steps": [
            "Bir konfor alışkanlığını bugünlük terk et.",
            "Rahatsızlığın içinde dingin kalmayı öğren.",
            "Zorlukları bir antrenman gibi karşıla."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 18: Kontrollü Rahatsızlık.",
            "Sürekli konfor arayan bir zihin ilk fırtınada paramparça olur.",
            "Seneca zengin bir adamken bile günlerce kuru ekmek yer ve yerde yatardı.",
            "Günün görevi: Bugün bilerek konforunu boz. İster bir öğün atla, ister yerde otur.",
            "Zorluklara alışkın bir adamı hiçbir düşman korkutamaz.",
            "Yoruma GÜN 18 TAMAM yaz.",
            "Yarın Gün 19 geliyor: Karanlık Empati ve İnsan Okuma. Devam!"
        ],
        "cliffhanger": "Yarın Gün 19 geliyor: Karanlık Empati ve İnsan Okuma. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 18/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "fight_club_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Seneca", "#Antifrajil", "#Zorluk"]
    },
    {
        "day": 19,
        "title": "Karanlık Empati ve İnsan Okuma",
        "subtitle": "Dark Psychology & Cold Reading",
        "category": "Dark Psychology",
        "quote_author": "Niccolò Machiavelli",
        "quote": "İnsanlar söyledikleriyle değil, gizlemeye çalıştıkları zayıflıklarıyla kendilerini ele verirler.",
        "concept": "Etrafındaki insanların beden dilini, mikro mimiklerini ve sakladıkları güvensizlikleri dikkatle analiz et.",
        "mission_steps": [
            "İnsanların söylediklerine değil, bedenlerinin yönüne ve gözlerine bak.",
            "Kimlerin onay aradığını, kimlerin korktuğunu tespit et.",
            "Bilgiyi bir avantaj olarak cebinde tut."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 19: Karanlık Empati ve İnsan Okuma.",
            "İnsanlar kelimeleriyle yalan söyler ama beden dilleri asla yalan söylemez.",
            "Machiavelli der ki: İnsanlar gizlemeye çalıştıklarıyla kendilerini ele verir.",
            "Günün görevi: Bugün etrafındaki insanların söylediklerine değil, ayak uçlarına, ellerine ve gözlerine odaklan.",
            "Kimin onay aradığını, kimin korktuğunu hemen anlayacaksın.",
            "Yoruma GÜN 19 TAMAM yaz.",
            "Yarın Gün 20 geliyor: İçsel Monolog Hakimiyeti. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 20 geliyor: İçsel Monolog Hakimiyeti. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 19/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "godfather_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#KaranlıkPsikoloji", "#İnsanOkuma", "#Strateji"]
    },
    {
        "day": 20,
        "title": "İçsel Monolog Hakimiyeti",
        "subtitle": "Mastering the Inner Voice",
        "category": "Mental Mastery",
        "quote_author": "Marcus Aurelius",
        "quote": "Hayatının kalitesi, içindeki düşüncelerin asaletine bağlıdır.",
        "concept": "Kendi kendine konuştuğun ses tonunu bir imparatorun ciddiyetine yükselt. Şüpheci iç sesi derhal sustur.",
        "mission_steps": [
            "Kendi kendine 'Yapamam' dediğin anı yakala ve yeniden kodla.",
            "Zihnine bir komutan gibi emir ver.",
            "İçindeki kurban psikolojisini yok et."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 20: İçsel Monolog Hakimiyeti.",
            "Kendi kendine fısıldadığın kelimeler kaderini çizer.",
            "Marcus Aurelius der ki: Hayatın, düşüncelerinin rengine boyanır.",
            "Günün görevi: Bugün kafanın içindeki o şüpheci ve korkak sesi yakaladığın an susturacaksın.",
            "Kendine bir kurban gibi değil, bir imparator gibi konuş.",
            "Yoruma GÜN 20 TAMAM yaz.",
            "Yarın Gün 21 geliyor: Şafak Zaferi (05:00 Disiplini). Hazır ol!"
        ],
        "cliffhanger": "Yarın Gün 21 geliyor: Şafak Zaferi ve Erken Kalkış Kuralı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 21/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "interstellar_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Zihniyet", "#Özgüven", "#Stoacılık"]
    },
    {
        "day": 21,
        "title": "Şafak Zaferi (Erken Kalkış)",
        "subtitle": "Dawn Victory & 5 AM Conquest",
        "category": "Iron Discipline",
        "quote_author": "Marcus Aurelius",
        "quote": "Sabah yataktan kalkarken hatırla: Sen bir insanın yapması gereken işi yapmak için doğdun.",
        "concept": "Güneş doğmadan önce uyan ve günün ilk savaşını erteleme butonuna basmayarak kazan.",
        "mission_steps": [
            "Alarmla birlikte tek hamlede ayağa kalk.",
            "Erteleme (snooze) düğmesine asla dokunma.",
            "Dünya uyurken ilk zaferini ilan et."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 21: Şafak Zaferi.",
            "Günün ilk zaferi alarm çaldığında kazanılır ya da kaybedilir.",
            "Marcus Aurelius yatağında sıcak kalmak istediğinde kendine şunu söylerdi: Bir insanın görevini yapmak için doğdum.",
            "Günün görevi: Yarın sabah alarm çaldığı an sıfır saniyede ayağa fırla. Erteleme yok.",
            "Güneş doğmadan önce kazanan, günün geri kalanını fetheder.",
            "Yoruma GÜN 21 TAMAM yaz.",
            "Yarın Gün 22 geliyor: Öfkeyi Güce Dönüştürme. Takipte kal!"
        ],
        "cliffhanger": "Yarın Gün 22 geliyor: Öfkeyi Güce Dönüştürme Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 22/30",
        "primary_cinematic": "oppenheimer_master.mp4",
        "secondary_cinematic": "gladiator_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#ErkenKalkmak", "#Disiplin", "#SabahRutini"]
    },
    {
        "day": 22,
        "title": "Öfkeyi Güce Dönüştürme",
        "subtitle": "Transmuting Anger into Cold Power",
        "category": "Emotional Alchemy",
        "quote_author": "Seneca",
        "quote": "Öfke, hedefinden çok onu taşıyan kabı yakıp kül eder.",
        "concept": "Öfkelendiğin bir anı al ve onu bağırıp çağırmak yerine yoğun bir çalışma veya antrenman enerjisine dönüştür.",
        "mission_steps": [
            "Öfkeni fark ettiğin an fiziksel hareketle (şınav, ağırlık, koşu) dışarı at.",
            "Zihnini intikam planlarına değil, başarı inşa etmeye kilitle.",
            "En büyük intikamın ezici bir başarı olduğunu hatırla."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 22: Öfkeyi Güce Dönüştürme.",
            "Öfkelenip bağıran insan sadece zayıflığını ilan eder.",
            "Seneca der ki: Öfke hedefinden çok onu taşıyan kabı yakar.",
            "Günün görevi: Bugün hissettiğin her öfkeyi soğuk bir yakıta dönüştür. Bağırıp çağırma, o enerjiyi antrenmanına ve hedeflerine göm.",
            "En asil intikam, düşmanının hayal bile edemeyeceği bir zirveye çıkmaktır.",
            "Yoruma GÜN 22 TAMAM yaz.",
            "Yarın Gün 23 geliyor: Yüksek Statü Beden Dili. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 23 geliyor: Yüksek Statü Beden Dili. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 23/30",
        "primary_cinematic": "batman_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#ÖfkeKontrolü", "#Güç", "#Başarı"]
    },
    {
        "day": 23,
        "title": "Yüksek Statü Beden Dili",
        "subtitle": "High-Status Body Language",
        "category": "Executive Dominance",
        "quote_author": "Robert Greene",
        "quote": "Alanı kapla, gereksiz kıpırdanmaları kes, sakinliğinle çevrene ağırlık ver.",
        "concept": "Gereksiz el-kol hareketlerini, bacak sallamaları ve kıpırdanmaları tamamen kes. Taş gibi sabit ol.",
        "mission_steps": [
            "Otururken bacak sallamayı derhal durdur.",
            "Konuşurken ellerini sabit ve kontrollü tut.",
            "Hareketsizliğin yaydığı derin özgüveni deneyimle."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 23: Yüksek Statü Beden Dili.",
            "Sürekli kımıldayan, bacağını sallayan ve parmaklarıyla oynayan insanlar stres saçar.",
            "Günün görevi: Bugün tüm gereksiz mikro hareketlerini sıfırla.",
            "Bir heykel gibi sabit, sakin ve oturaklı dur. Konuşurken acele etme.",
            "Hareketsizlik en yüksek statü göstergesidir.",
            "Yoruma GÜN 23 TAMAM yaz.",
            "Yarın Gün 24 geliyor: Memento Mori (Ölüm Meditasyonu). Bildirimleri aç!"
        ],
        "cliffhanger": "Yarın Gün 24 geliyor: Memento Mori Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 24/30",
        "primary_cinematic": "godfather_master.mp4",
        "secondary_cinematic": "peaky_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#BedenDili", "#Statü", "#Karizma"]
    },
    {
        "day": 24,
        "title": "Memento Mori Protokolü",
        "subtitle": "Remember You Must Die",
        "category": "Stoic Transcendence",
        "quote_author": "Marcus Aurelius",
        "quote": "Şu anda bu hayattan ayrılabileceğini bilerek yaşa; o zaman önemsiz dertler buharlaşır.",
        "concept": "Günde 5 dakika gözlerini kapat ve bir gün öleceğini, tüm korkularının ve utançlarının önemsizliğini idrak et.",
        "mission_steps": [
            "5 dakika boyunca hayatının son gününü hayal et.",
            "Korktuğun o adımı at; çünkü kaybedecek hiçbir şeyin yok.",
            "Ölüm bilinciyle cesaretini zirveye çıkar."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 24: Memento Mori.",
            "İnsanların ne düşündüğünden korkuyorsun çünkü sonsuza dek yaşayacağını sanıyorsun.",
            "Marcus Aurelius der ki: Şu an ölebileceğini bilerek yaşa. Korkuların önemsizliğini anla.",
            "Günün görevi: Bugün korktuğun o kararı al. Bir gün yok olacaksın; korkarak yaşayacak kadar vaktin yok.",
            "Cesur ol ve hamleni yap.",
            "Yoruma GÜN 24 TAMAM yaz.",
            "Yarın Gün 25 geliyor: Manipülasyon Kalkanı. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 25 geliyor: Manipülasyon Kalkanı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 25/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "interstellar_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#MementoMori", "#Stoacılık", "#Cesaret"]
    },
    {
        "day": 25,
        "title": "Manipülasyon Kalkanı",
        "subtitle": "Dark Psychology Deflector",
        "category": "Dark Psychology Defense",
        "quote_author": "Sun Tzu",
        "quote": "Düşmanını ve kendini tanı; yüz savaşta da asla mağlup olmazsın.",
        "concept": "Suçluluk duygusu veya sahte iltifatlarla seni yönlendirmeye çalışan taktikleri fark et ve nötralize et.",
        "mission_steps": [
            "Sana kendini borçlu hissettirmeye çalışanlara karşı uyanık ol.",
            "Haksız bir suçlamada savunmaya geçme; 'Bunu neden şimdi söylüyorsun?' diye sor.",
            "Manipülasyonu kaynağında dondur."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 25: Manipülasyon Kalkanı.",
            "Zayıf insanlar seni suçluluk duygusuyla yönetmeye çalışır.",
            "Günün görevi: Biri seni suçlu hissettirmeye veya kışkırtmaya çalıştığında savunmaya geçme.",
            "Gözlerinin içine bak ve sor: 'Bunu söyleyerek tam olarak ne elde etmeye çalışıyorsun?'",
            "Oyunlarını gördüğünü anladıkları an kaçacak delik arayacaklar.",
            "Yoruma GÜN 25 TAMAM yaz.",
            "Yarın Gün 26 geliyor: Sosyal Çember Temizliği. Takipte kal!"
        ],
        "cliffhanger": "Yarın Gün 26 geliyor: Sosyal Çember Temizliği Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 25/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "godfather_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Manipülasyon", "#KaranlıkPsikoloji", "#Strateji"]
    },
    {
        "day": 26,
        "title": "Sosyal Çember Temizliği",
        "subtitle": "Pruning Toxic Circles",
        "category": "Social Strategy",
        "quote_author": "Seneca",
        "quote": "Seni geliştirmeyen ve ileri taşımayan her insan, seni geriye çeken bir prangadır.",
        "concept": "Enerjini sömüren, sürekli şikayet eden ve vizyonsuz insanlarla arana aşılmaz bir mesafe koy.",
        "mission_steps": [
            "Seni yoran 1 kişiye mesaj atmayı veya görüşmeyi kes.",
            "Gereksiz WhatsApp gruplarından sessizce ayrıl.",
            "Çemberini daralt, kaliteni artır."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 26: Sosyal Çember Temizliği.",
            "Etrafındaki beş kişinin ortalamasısın; ve o beş kişi şikayet ediyorsa sen de batarsın.",
            "Seneca der ki: Seni ileri taşımayan herkes sana yüktür.",
            "Günün görevi: Bugün enerjini çalan en az bir kişiyle iletişimini kes veya minimuma indir.",
            "Zirveye herkesle çıkamazsın; bazı ağırlıkları aşağıda bırakmalısın.",
            "Yoruma GÜN 26 TAMAM yaz.",
            "Yarın Gün 27 geliyor: Kusursuz İcraat. Devam!"
        ],
        "cliffhanger": "Yarın Gün 27 geliyor: Kusursuz İcraat Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 26/30",
        "primary_cinematic": "godfather_master.mp4",
        "secondary_cinematic": "scarface_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Çevre", "#İlişkiler", "#Gelişim"]
    },
    {
        "day": 27,
        "title": "Kusursuz İcraat",
        "subtitle": "Ruthless Execution",
        "category": "High Agency",
        "quote_author": "Thomas Shelby",
        "quote": "Bir plan ancak kan ve terle hayata geçirildiğinde gerçektir; gerisi sadece kuru laftır.",
        "concept": "Bugün plan yapmayı, not almayı, düşünmeyi bırak. Doğrudan sonuca odaklanan en zor işi bitir.",
        "mission_steps": [
            "Aylardır düşündüğün o projeye ilk adımı at.",
            "Mazeret dinleme, erteleme yapma.",
            "Günü somut bir zaferle kapat."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 27: Kusursuz İcraat.",
            "Dünya plan yapan hayalperestlerle dolu ama zirveyi sadece uygulayanlar fetheder.",
            "Thomas Shelby gibi ol: Konuşma, planlarını anlatma; sadece bitir.",
            "Günün görevi: Bugün günlerdir ertelediğin en zor görevi masaya oturup tek seferde bitireceksin.",
            "Eyleme geçmeyen bilgi çöp hükmündedir.",
            "Yoruma GÜN 27 TAMAM yaz.",
            "Yarın Gün 28 geliyor: Sessiz Zafer Kuralı. Kaçırma!"
        ],
        "cliffhanger": "Yarın Gün 28 geliyor: Sessiz Zafer Kuralı. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 27/30",
        "primary_cinematic": "peaky_master.mp4",
        "secondary_cinematic": "fight_club_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Eylem", "#Başarı", "#Disiplin"]
    },
    {
        "day": 28,
        "title": "Sessiz Zafer Protokolü",
        "subtitle": "Silent Victory & Humble Dominance",
        "category": "Executive Mastery",
        "quote_author": "Robert Greene",
        "quote": "Büyük zaferler kazandığında kutlama yapma; başarın bir gök gürültüsü gibi konuşsun.",
        "concept": "Kazandığın bir başarıyı kimseye anlatma, sosyal medyada paylaşma. Başarını sadece kendi içinde kutla.",
        "mission_steps": [
            "Başardığın bir şeyi kimseye anlatmadan içine sindir.",
            "Övgü ve takdir arayışından kurtul.",
            "Kendi kendine verdiğin onayın gücünü tat."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 28: Sessiz Zafer Protokolü.",
            "Küçük insanlar her küçük başarısını davul zurnayla ilan eder.",
            "Gerçek krallar ise kazandıklarında sadece bir sonraki fethini düşünür.",
            "Günün görevi: Bugün başardığın hiçbir şeyi kimseye anlatma. Sosyal medyada paylaşma.",
            "Başarın kendi kendine gürlesin. Sessizlik en büyük asalettir.",
            "Yoruma GÜN 28 TAMAM yaz.",
            "Yarın Gün 29 geliyor: Antifrajil Zırh. Son viraj!"
        ],
        "cliffhanger": "Yarın Gün 29 geliyor: Antifrajil Zırh Protokolü. Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 28/30",
        "primary_cinematic": "godfather_master.mp4",
        "secondary_cinematic": "gladiator_master.mp4",
        "bottom_stock": "beamng_crash.mp4",
        "hashtags": ["#ZirveninKanunu", "#Asalet", "#Zafer", "#SessizGüç"]
    },
    {
        "day": 29,
        "title": "Antifrajil Zırh (Kırılmaz Ruh)",
        "subtitle": "Antifragile Spirit",
        "category": "Stoic Transcendence",
        "quote_author": "Marcus Aurelius",
        "quote": "Rüzgar zayıf mumu söndürür ama büyük yangını daha da körükler. Rüzgar ol, ateş ol.",
        "concept": "Başına gelen her felaketi ve engeli seni daha da güçlendiren bir yakıt olarak kabul et.",
        "mission_steps": [
            "Yaşadığın bir hayal kırıklığını 'Bu beni nasıl daha güçlü yapar?' filtresinden geçir.",
            "Yıkılmayı değil, darbeden güç alarak büyümeyi seç.",
            "Yaralarını birer zırh plakası gibi taşı."
        ],
        "script_lines": [
            "Zirve Meydan Okuması. Gün 29: Antifrajil Zırh.",
            "Sıradan insanlar darbelerle kırılır. Zirvedekiler ise darbelerle bilenir.",
            "Marcus Aurelius der ki: Rüzgar mumu söndürür ama dev yangını büyütür. Rüzgar ol!",
            "Günün görevi: Bugün yaşadığın her aksilikte gülümse ve fısılda: 'Bu beni daha da güçlendirecek.'",
            "Seni hiçbir şeyin yıkamayacağını kanıtla.",
            "Yoruma GÜN 29 TAMAM yaz.",
            "Yarın BÜYÜK FİNAL Gün 30: Zirvenin Kanunu ve Taç Giyme Töreni!"
        ],
        "cliffhanger": "Yarın BÜYÜK FİNAL Gün 30: Zirvenin Kanunu ve Yeni Kral! Takipte kal ve bildirimleri aç! 👑",
        "badge_text": "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN 29/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "fight_club_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#Antifrajil", "#Yenilmez", "#Stoacılık"]
    },
    {
        "day": 30,
        "title": "Zirvenin Kanunu: Yeni Hükümdar",
        "subtitle": "The Sovereign Initiation - Final Day",
        "category": "Sovereignty & Initiation",
        "quote_author": "Marcus Aurelius",
        "quote": "Artık iyi bir insanın nasıl olması gerektiğini tartışmayı bırak. O insan ol!",
        "concept": "30 günlük sınavı tamamladın. Eski sen öldü. Artık kendi hayatının mutlak hükümdarısın.",
        "mission_steps": [
            "30 günlük yolculuğunu gözünün önünden geçir.",
            "Kendine verdiğin sözü asla bozmama yemini et.",
            "Zirvenin Kanunu'nu hayatının değişmez pusulası yap."
        ],
        "script_lines": [
            "30 Günlük Zirve Meydan Okuması. Gün 30: Büyük Final.",
            "Otuz gündür iradeni çelikleştirdin, mazeretleri yok ettin ve sessizlikte büyüdün.",
            "Eski zayıf halin artık geçmişte kaldı.",
            "Marcus Aurelius'un son emri: Artık iyi bir insanın nasıl olması gerektiğini tartışmayı bırak. O insan ol!",
            "Sen artık kendi hayatının hükümdarısın.",
            "Bu 30 günü tamamladıysan yoruma 'ZİRVENİN KANUNU BENİM' yazarak krallığını ilan et!",
            "Kanalı takip et, bildirimleri açık tut. Yolculuk yeni başlıyor! 👑"
        ],
        "cliffhanger": "Tebrikler Hükümdar! Bu seriyi tamamladın. Zirvede kalmak için takipte kal! 👑",
        "badge_text": "👑 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | FİNAL GÜN 30/30",
        "primary_cinematic": "gladiator_master.mp4",
        "secondary_cinematic": "godfather_master.mp4",
        "bottom_stock": "minecraft_parkour.mp4",
        "hashtags": ["#ZirveninKanunu", "#BüyükFinal", "#30GünlükZirveMeydanOkuması", "#Kral", "#Zafer"]
    }
]


def save_curriculum_json(target_path: Optional[Path] = None) -> Path:
    """Saves the 30-day curriculum as structured JSON."""
    out_file = Path(target_path) if target_path else CURRICULUM_FILE
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(CHALLENGE_CURRICULUM, f, ensure_ascii=False, indent=2)
    return out_file


def get_day_curriculum(day_number: int) -> Dict[str, Any]:
    """Retrieves full metadata and script for a specific challenge day (1-30)."""
    if not (1 <= day_number <= 30):
        raise ValueError(f"Day number must be between 1 and 30, got: {day_number}")
    return CHALLENGE_CURRICULUM[day_number - 1]


def get_all_days() -> List[Dict[str, Any]]:
    """Returns the full 30-day curriculum list."""
    return CHALLENGE_CURRICULUM


if __name__ == "__main__":
    saved_path = save_curriculum_json()
    print(f"✅ 30 Günlük Zirve Meydan Okuması Müfredatı Kaydedildi: {saved_path}")
    print(f"📊 Toplam Gün Sayısı: {len(CHALLENGE_CURRICULUM)}")
    day1 = get_day_curriculum(1)
    print(f"🎯 Gün 1: {day1['title']} ({day1['quote_author']})")
