# Zirvenin Kanunu metin kuyruğu

Bu dal, Zirvenin Kanunu'nun merak Shorts metinlerini tutar. Metinleri Claude yazar (Ulaş, 2026-10-19:
"Claude yazsın"); Gemini sadece stok klip seçer. Shorts iş akışı (`viral_shorts_autopilot.yml`) bu dalı
`metin-kuyrugu/` olarak açar ve fabrika `kuyruk/` içinden henüz yüklenmemiş en eski dosyayı alır
(`app/services/script_queue.py`, main dalında). Kuyruk boşsa fabrika eskisi gibi Gemini ile yazar.

## Dosya

Her Short bir dosya: `kuyruk/mk-cNNNN.json`. Dosya adı ve `id` aynı olsun, numara hep artsın (sıralama dosya
adına göre). Kullanılmış id'ler `channel-data` dalındaki `uploads.json` içindeki `topic_id` alanından okunur.
Kullanılan dosyalar silinmez; bir kez yüklenen id bir daha seçilmez.

```json
{
  "id": "mk-c0001",
  "topic": "Zürafanın boynunda kaç kemik var?",
  "kategori": "hayvan",
  "subject": ["giraffe"],
  "stock_hint": "giraffe neck savanna",
  "script": {
    "title": "...",
    "hook_text": "...",
    "scenes": [{"text": "...", "visual": "...", "stock_query": "...", "source": "stock", "callout": "..."},
               {"text": "...", "visual": "...", "stock_query": "human skeleton", "source": "stock", "subject": ["skeleton"]}],
    "description": "2-3 cümlelik Türkçe açıklama (yedek)",
    "tags": ["8-12 Türkçe etiket"],
    "pinned_comment": "...",
    "en_title": "...",
    "en_description": "...",
    "mood": "belgesel"
  },
  "seo": {
    "description": "...",
    "hashtags": ["#..."],
    "tags": ["..."],
    "localizations": {"en": {"title": "...", "description": "..."}}
  }
}
```

`kategori`: hayvan, vücut, psikoloji, doğa veya bilim. `subject`: Pixabay'de aranacak 1-2 İngilizce özne kelimesi.

## Görüntü öznesi (fabrika her klibi buna göre süzer)

- Fabrika yalnızca etiketinde `subject` geçen klipleri alır. Çok kelimeli terimde her kelime gerekir, bu yüzden
  benzer türü dışarıda bırakan terimi yaz: "panda" değil "giant panda" (panda Short'unda kırmızı panda çıktı),
  "polar bear", "sea turtle".
- Sahnenin görüntüsü konunun öznesi değilse (bebek videosunda iskelet, burun videosunda saat, koala videosunda
  vombat, biber videosunda süt) o sahneye kendi öznesini ver: `"subject": ["skeleton"]`, en fazla 3 sade İngilizce
  terim, ve `stock_query` o kelimeyi içersin. Vermezsen o sahne klip bulamaz ve önceki çekimler tekrar eder.
- İnsan konularında yatak, iç çamaşırı, bel ölçme gibi ten gösteren stok çağıran sorgu yazma ("woman stretching
  bed" gibi). Bu tür görüntülü "Sabahları akşamdan daha uzunsun" 97 izlenmede kaldı, diğerleri 1.000 civarı
  (sebep tahmin: YouTube dağıtımı kısmış olabilir). El, yüz profili, yürüyen insanlar, tıbbi model gibi nötr
  görüntüler seç.

## Metin kuralları (fabrika bunları kontrol eder, uymayan dosya atlanır)

- Toplam 40-55 kelime, 4-6 sahne, her sahne 1-2 kısa cümle, sahne en fazla 230 karakter. 15-20 saniyelik Short.
- İlk cümle soru değil, en fazla 8 kelimelik bir iddia: ekrandakini göstersin ("Bu zürafa...") ve bir sayı ya da
  şaşırtıcı bir çelişki içersin. "Biliyor muydun", "Merhaba", "Bugün" ile başlama. İlk sahnede mutlaka `callout`.
- Akış: ekrandaki iddia, sorunun açılması, ortada küçük bir ikinci şaşırtıcı detay, asıl cevap ("neden" ya da
  "nasıl") son üçte birde, son cümle. İlk cümle iddiayı söyler ama nedenini söylemez; nedeni videonun ilk yarısında
  verme. 1 Kasım izleyici eğrileri: en çok izlenen 4 Short'ta videonun yarısında izleyicinin %98'i hâlâ izliyordu,
  1.000-1.400'de kalan 15 Short'ta %72'si. Kopuş baştan değil ortadan oluyor.
- Son cümle şaşırtıcı sonucu ya da izleyicinin deneyebileceği somut bir şeyi versin; mümkünse ilk cümleye bağlansın
  (video başa sardığında tek cümle gibi okunsun). Son cümleye "Yani", "Böylece", "Kısacası", "Bu sayede", "Sonuç
  olarak", "Özetle" ile başlama; "doğa muhteşem" gibi genel bir lafla bitirme.
- Sadece doğrulanmış bilgi. Her sayıyı ve iddiayı web'de en az bir güvenilir kaynakla kontrol et; emin değilsen
  sayı verme. Tartışmalıysa "bir görüşe göre" de. Kaynakları dosyaya değil, commit mesajına yaz.
- Anlatımda emoji yok; sayıları yazıyla yaz ("yirmi beş santimetre"), ekrandaki `callout` rakamla olabilir.
- Film, dizi, karakter adı yok (telif).
- `stock_query`: Pixabay'de kesin bulunacak 2-3 basit İngilizce kelime, ana özne kelimesini içersin ("giraffe neck").
  Soyut kavram yazma. `source` hep "stock". `visual`: stok bulunamazsa üretilecek fotogerçekçi İngilizce tarif.
- `callout`: 1-3 kelime ya da birimiyle bir sayı, en fazla 24 karakter, büyük harf ("7 OMUR"). En çarpıcı 3-4 sahne.
- `title`: en fazla 60 karakter, şaşırtıcı bir iddia (soru değil), sonda tek emoji. #shorts yok. 1 Kasım verisi:
  iddia başlıklı Claude Short'ları ortanca 2.328 izlenme, soru başlıklılar 1.377 (23'e 10 video).
- `hook_text`: konunun sorusunu soran 3-7 kelimelik büyük yazı, emoji yok, ilk cümleyi tekrarlamasın.
- `mood`: "belgesel" (vahşi doğa, okyanus, uzay), "eğlenceli" (sevimli/komik hayvan, gündelik tuhaflık) ya da
  "sakin" (insan vücudu, psikoloji, gündelik bilim).
- `pinned_comment`: iki seçenekli tek soru ("Sence hangisi daha tuhaf: üç kalp mi, mavi kan mı?").

## SEO paketi kuralları

- `description`: Türkçe, 3 kısa paragraf, aralarında boş satır: (1) insanların arama kutusuna yazacağı haliyle
  videonun sorusu, (2) cevap 2-3 cümle, anlatımdaki sayı ve adlarla, (3) `pinned_comment` sorusu. En az 120
  karakter. Abone ol, beğen, kaynak, yapay zekâ yazma; hashtag yazma.
- `hashtags`: 3-5 Türkçe hashtag, önce canlı ya da nesne (#zürafa), sonra konu (#hayvanlar). #shorts yazma.
- `tags`: 20-25 etiket, en az %80'i Türkçe, en fazla 4 İngilizce; her biri en fazla 30 karakter, # yok. Önce en
  özeller (canlının adı, olgu, soru kalıpları), sonra genel olanlar (hayvanlar, bilim, ilginç bilgiler).
- `localizations`: şu 18 dilin hepsi: en es pt de fr it ru ar hi id ja ko pl nl uk vi az ro. Her biri
  `{"title", "description"}`; başlık o dilde doğal, en fazla 90 karakter, sonda Türkçe başlıktaki emoji; açıklama
  o dilde 2-3 cümle (en az 20 karakter). Çeviri kokmasın.

## Konu seçimi

- En çok tutan kalıp (1 Kasım verisi, 20-23 Ekim'in 33 Short'u): herkesin tanıdığı bir canlı hakkında yanlış bilinen
  ya da "senin hakkında" şaşırtıcı bir gerçek. Köpekler kırmızıyı göremez 15.723, bebeklerin kemiği senden fazla
  9.437, kargalar yüzü unutmaz 8.139, geyik her yıl yeni boynuz 5.727, pandanın altıncı parmağı 4.024. Uzak ya da
  az tanınan canlılar (deniz atı, kurbağa, kelebek) 1.100-1.400'de kalıyor.
- İzlenme oranı belirleyici: ortalama izleme %90'ın üstünde olan Short'lar ortanca 3.212, %75'in altındakiler
  1.364 izlendi. Bu yüzden 4-5 sahne, 15-17 saniye ve başa saran son cümle tercih et.
- Günde 8 Short'un 2'si insan psikolojisi (`kategori: psikoloji`, 2 Kasım'dan itibaren bir hafta deneme), 1'i insan
  vücudu, 5'i tanıdık hayvanlar. Kuyrukta bu sırayı koru: her 4 dosyadan biri psikoloji olsun.
- Psikoloji konuları izleyicinin kendisi ya da tanıdığı biri hakkında olsun ("kendi sesini kayıtta neden sevmezsin",
  "bir ricaya 'çünkü' eklemek"): beynin seni nasıl kandırdığı, insanların birbirini nasıl etkilediği, gündelik
  davranışın arkasındaki deney. Kanalın adına ve güç psikolojisi animasyonlarına köprü bunlar.
- Psikolojide sadece iyi tekrarlanmış bulgular. Tekrarlanamamış ya da tartışmalı deneyleri (güç pozu, ego tükenmesi,
  Stanford hapishane deneyi, kapı eşiği unutkanlığı, Zeigarnik etkisi, "beynin %10'u") kesin bilgi gibi verme.
  Tek bir deneye dayanıyorsa yılını ve "bir deneyde" diye söyle.
- Psikoloji görüntüleri: yüz ifadesi, konuşan iki kişi, kalabalık, telefon, ayna, eller. Ünlü kişi, film sahnesi ya
  da ten gösteren görüntü yok. `mood: "sakin"`.
- Daha önce yüklenen başlıklara (`uploads.json`) ve kuyruktaki dosyalara bak, aynı konuyu tekrar yazma.
- Kuyrukta her zaman en az 10 kullanılmamış metin olsun (günde 8 Short yükleniyor).

## Kontrol

Göndermeden önce main dalının bir kopyasıyla dene; her dosya için "OK" görmelisin:

```bash
python3 kontrol.py <main dalının kopyası>
```
