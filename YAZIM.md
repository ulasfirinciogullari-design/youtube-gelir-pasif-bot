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
    "scenes": [{"text": "...", "visual": "...", "stock_query": "...", "source": "stock", "callout": "..."}],
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

## Metin kuralları (fabrika bunları kontrol eder, uymayan dosya atlanır)

- Toplam 40-55 kelime, 4-6 sahne, her sahne 1-2 kısa cümle, sahne en fazla 230 karakter. 15-20 saniyelik Short.
- İlk cümle soru değil, en fazla 8 kelimelik bir iddia: ekrandakini göstersin ("Bu zürafa...") ve bir sayı ya da
  şaşırtıcı bir çelişki içersin. "Biliyor muydun", "Merhaba", "Bugün" ile başlama. İlk sahnede mutlaka `callout`.
- Akış: ekrandaki iddia, sorunun açılması, şaşırtıcı cevap, cevabı somutlaştıran örnek ya da sayı, son cümle.
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
- `title`: en fazla 60 karakter, merak uyandıran soru ya da şaşırtıcı iddia, sonda tek emoji. #shorts yok.
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

- Kanalda tutan konular: görüntüsü kolay bulunan hayvanlar (zürafa 5.591, flamingo 3.153, köpek 2.796, zebra,
  kedi, baykuş, fil izlenme). İnsan vücudu ve psikoloji ortalama 1.000 civarı.
- Daha önce yüklenen başlıklara (`uploads.json`) ve kuyruktaki dosyalara bak, aynı konuyu tekrar yazma.
- Kuyrukta her zaman en az 10 kullanılmamış metin olsun (günde 8 Short yükleniyor).

## Kontrol

Göndermeden önce main dalının bir kopyasıyla dene; her dosya için "OK" görmelisin:

```bash
python3 kontrol.py <main dalının kopyası>
```
