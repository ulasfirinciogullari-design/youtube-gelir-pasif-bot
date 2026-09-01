# YouTube 7/24 Content Factory

Çok kanallı, kuyruk tabanlı YouTube içerik üretim ve yayın altyapısı.

## Hedef mimari

- FastAPI: kontrol API'si
- Celery + Redis: paralel görev kuyruğu
- PostgreSQL: kanal/görev/token metadata
- OpenAI API + web search: araştırma, senaryo, başlık, açıklama
- ElevenLabs: doğal çok dilli seslendirme
- Pexels: gerçek B-roll
- Runway + isteğe bağlı Fal Seedance: premium AI video sahneleri
- FFmpeg: kurgu, altyazı, ses miksajı
- YouTube Data API: yükleme ve yayın
- YouTube Analytics API: performans geri beslemesi

## Güvenlik

API anahtarları ve OAuth secret'ları repoya yazılmaz. Railway Variables / secret store kullanılır. Fal video geri dönüşü yalnız sunucu tarafındaki `FAL_KEY` ile etkinleşir.

YouTube yayın merkezi en fazla 10 doğrulanmış kanalı ayrı OAuth bağlantıları
olarak saklar. Her yükleme başlatılırken hedef kanal ve bağlantı nesli
rezervasyona sabitlenir; çalışan başka bir hesaba geri düşmez. İlk yükleme
daima `private` olur ve aynı final ikinci bir kanala yeniden gönderilmez.

## Durum

İlk scaffold kuruluyor. Sonraki adımlar: servis hesapları, OAuth, render pipeline, çok-kanallı scheduler ve analytics feedback loop.
