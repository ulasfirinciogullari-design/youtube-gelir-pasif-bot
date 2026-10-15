# YA OLSAYDI?

Kodla çizilen animasyon kanalı. Zirvenin Kanunu'ndan ayrı tutulur: bu dal `main`e hiç birleşmez ve YouTube'a yükleme yapmaz.

- `pilot-ay/anim.html` — sahneler ve karakterler (Profesör Pi, Bit), `draw(t)` ile kare kare çizilir
- `pilot-ay/script.js` — replikler, ses ayarları, efekt zamanları
- `pilot-ay/tts.py` — ElevenLabs ses (kelime zamanlarıyla), efekt, müzik; `audio/` önbelleği kredi harcamadan tekrar kullanır
- `pilot-ay/render.js` — Playwright ile kareleri kaydedip ffmpeg ile videoya çevirir

Her push iş akışını çalıştırır; çıktı `pilot-ay/out/` klasörüne `[skip ci]` commit'iyle yazılır.
