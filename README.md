# YA OLSAYDI?

Kodla çizilen animasyon kanalı. Zirvenin Kanunu'ndan ayrı tutulur: bu dal `main`e hiç birleşmez ve sadece Ya Olsaydı? kanalına (`YA_YT_*` anahtarları) bağlanır. Videolar gizli yüklenir; herkese açmak ayrı, onaylı bir adımdır.

- `pilot-ay/anim.html` — sahneler ve karakterler (Profesör Pi, Bit), `draw(t)` ile kare kare çizilir
- `pilot-ay/script.js` — replikler, ses ayarları, efekt zamanları
- `pilot-ay/tts.py` — ElevenLabs ses (kelime zamanlarıyla), efekt, müzik; `audio/` önbelleği kredi harcamadan tekrar kullanır
- `pilot-ay/render.js` — Playwright ile kareleri kaydedip ffmpeg ile videoya çevirir
- `pilot-ay/brand.html`, `brand.js` — profil, banner, filigran ve kapak görselleri (`brand/`)
- `pilot-ay/youtube.json` — kanal açıklaması, listeler, bölüm başlıkları ve kaynaklı açıklamalar
- `pilot-ay/youtube.py` — kanalı doğrular, boş ayarları doldurur, listeleri açar, bölümleri gizli yükler; yapılanlar `youtube_state.json`'da, rapor `out/youtube_report.json`'da

Her push iş akışını çalıştırır; çıktı `pilot-ay/out/` klasörüne `[skip ci]` commit'iyle yazılır.
