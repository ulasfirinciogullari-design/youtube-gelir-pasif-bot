# YA OLSAYDI?

Kodla çizilen animasyon kanalı. Zirvenin Kanunu'ndan ayrı tutulur: bu dal `main`e hiç birleşmez ve sadece Ya Olsaydı? kanalına (`YA_YT_*` anahtarları) bağlanır. Pilot elle yazıldı; sonraki bölümleri `fabrika/` saatte bir kendisi yazar, çizer, seslendirir ve herkese açık yükler (Ulaş 2026-10-16'da istedi).

- `pilot-ay/BOLUM_REHBERI.md` — yeni bölümlerin yazım, kaynak ve yükleme kuralları
- `pilot-ay/anim.html` — sahneler ve karakterler (Profesör Pi, Bit), `draw(t)` ile kare kare çizilir
- `pilot-ay/script.js` — replikler, ses ayarları, efekt zamanları
- `pilot-ay/tts.py` — ElevenLabs ses (kelime zamanlarıyla), efekt, müzik; `audio/` önbelleği kredi harcamadan tekrar kullanır
- `pilot-ay/render.js` — Playwright ile kareleri kaydedip ffmpeg ile videoya çevirir
- `pilot-ay/brand.html`, `brand.js` — profil, banner, filigran ve kapak görselleri (`brand/`)
- `pilot-ay/youtube.json` — kanal, listeler ve bölümler için Türkçe + 10 dilde başlık ve açıklama, kaynaklar, etiketler, İngilizce altyazı satırları
- `pilot-ay/youtube.py` — kanalı doğrular, ayarları ve çevirileri günceller (Studio'da elle değiştirileni ellemez), listeleri açar, bölümleri gizli yükler, açıklamaya ilgili videoları ekler, Türkçe ve İngilizce altyazı yükler; yapılanlar `youtube_state.json`'da, rapor `out/youtube_report.json`'da

Her push iş akışını çalıştırır; çıktı `pilot-ay/out/` klasörüne `[skip ci]` commit'iyle yazılır.

## Fabrika (saatte bir bölüm)

- `fabrika/uret.py` — bir tur: kapı (bu saat boş mu), konu, Gemini ile senaryo ve görseller (web aramalı), ikinci aramalı bilgi kontrolü, 10 dile çeviri, ses, çizim, kapak, yükleme. Durum `fabrika/durum.json`, son turun raporu `fabrika/rapor.json`.
- `fabrika/yazar.py` — Gemini istemleri, kurallar, görsel türleri, doğrulama, kaynak linki kontrolü
- `fabrika/ses.py` — ElevenLabs (varsayılan `eleven_flash_v2_5`, `YO_TTS_MODEL` ile değişir), efektler `fabrika/ses/` içinde bir kez üretilir, müzik pilotunki
- `fabrika/sayi.py` — rakamları Türkçe okunuşa çevirir (3,8 → üç virgül sekiz); altyazıda rakam kalır
- `fabrika/motor/` — `motor.html` (10 sahne türü, karakterler, altyazı), `arkaplan.js` (arka planlar, gezegenler), Noto renkli emoji fontu, `render.js`
- `fabrika/yukle.py` — açıklama (özet, bilgiler, kaynaklar, ilgili bölümler, liste, abone linki, etiketler) ve 10 dilde başlık/açıklama; bölüm başına ~100 kota birimi
- `fabrika/konular.json` — 120 başlangıç konusu, seriler sırayla (Ya Olsaydı?, Ne Kadar Büyük?, Ya Olsaydı?, 1 Gün Boyunca, Ya Olsaydı?); bitince Gemini yenilerini önerir
- Zamanlayıcı `main` dalındaki `.github/workflows/ya_olsaydi_saatlik.yml` (her saat :17 ve :47, saat başına bir bölüm). Elle tur: `fabrika/tetik.txt` dosyasına `deneme` (yüklemez, videoyu `fabrika/deneme/`e koyar) ya da `zorla` yazıp göndermek.
- Durdurmak için GitHub → Settings → Secrets and variables → Actions → Variables'a `YO_DURDUR` = `1` eklemek yeter. Günlük sayı `YO_GUNLUK` (varsayılan 24).
- Yorumlar: her turda `fabrika/yorum.py` kanalın yeni yorumlarını okur, Profesör Pi ya da Bit ağzından yorumun dilinde kısa cevap yazar (spam, link, hakaret ve hassas konular atlanır). Yorumdaki konu önerileri sıranın başına eklenir. Günde en çok `YO_YORUM_GUNLUK` (varsayılan 30) cevap; kapatmak için `YO_YORUM_KAPALI` = `1`. Sonuç `fabrika/yorum_rapor.json`. Elle tur: `fabrika/tetik.txt`'ye `yorum` ya da `yorumdeneme` (cevapları yazar, göndermez).
