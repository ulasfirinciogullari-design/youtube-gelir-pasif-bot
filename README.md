# YouTube 7/24 Content Factory

Çok kanallı, kuyruk tabanlı YouTube içerik üretim ve yayın altyapısı.

## Hedef mimari

- FastAPI: kontrol API'si
- Celery + Redis: paralel görev kuyruğu
- PostgreSQL: kanal/görev/token metadata
- OpenAI API + web search: araştırma, senaryo, başlık, açıklama
- ElevenLabs: doğal çok dilli seslendirme
- Pexels: gerçek B-roll
- Fal üzerinden Veo / Seedance seçimi; mevcut Runway yolu: AI video sahneleri
- FFmpeg: kurgu, altyazı, ses miksajı
- YouTube Data API: yükleme ve yayın
- YouTube Analytics API: performans geri beslemesi

## Güvenlik

API anahtarları ve OAuth secret'ları repoya yazılmaz. Railway Variables / secret store kullanılır.

Fal bağlantısı için web ve `video-worker` hizmetlerine `FAL_KEY` eklenir.
Varsayılan `STUDIO_VIDEO_PROVIDER=auto`, anahtar varsa Fal'ı seçer; yoksa
mevcut sağlayıcı yolu sürer. `legacy` eski yola dönmek, `fal` ise anahtar
eksikliğinde durmak için kullanılabilir. `STUDIO_FAL_VIDEO_MODEL=auto`, 8 saniyeye kadar
sessiz Veo 3.1 Lite, daha uzun sahnelerde sessiz Seedance 1.5 Pro kullanır.
İsteğe bağlı sabit seçimler `veo_lite`, `seedance_pro`, `seedance_fast`.
Tüm profiller 720p'dir; süreyi karşılamayan seçim ücretli istek göndermeden
reddedilir. Model seçimi kalite onayı değildir; mevcut görüntü kontrolleri sürer.

Fal seçimi, daha pahalı bir sağlayıcıya otomatik geçiş yapmaz. Mevcut kurulum
üretiminde aynı kalıcı sahne kaydı ve bölüm başına üretim sınırı kullanılır.
Normal bütçeli üretimde Fal hesabının mevcut bütçe sistemine ayrıca tanımlanması
gerekir; yalnız API anahtarı eklemek para harcama yetkisi oluşturmaz.
Bağlantı, fiyat doğrulaması ve devreye alma ayrıntıları: [Fal video](docs/fal-video.md).

YouTube yayın merkezi en fazla 10 doğrulanmış kanalı ayrı OAuth bağlantıları
olarak saklar. Her yükleme başlatılırken hedef kanal ve bağlantı nesli
rezervasyona sabitlenir; çalışan başka bir hesaba geri düşmez. İlk yükleme
daima `private` olur ve aynı final ikinci bir kanala yeniden gönderilmez.

## Düzenli kanal üretimi

Ana web servisi ve `video-worker` yanında tek bir `production-scheduler`
servisi çalıştırılır. Aynı repo ve Redis kullanılır; zamanlayıcı için model
anahtarları gerekmez. Başlatma komutu:

```text
celery -A app.celery_app.celery beat --loglevel=info --schedule=/tmp/celerybeat-schedule
```

Studio → YouTube → Otomatik üretim bölümünde bağlı kanalın konu sırası,
dili, üretim aralığı ve yayın davranışı saklanır. Üretim ve otomatik yükleme
varsayılan olarak kapalıdır. İkisi etkinleştirilince ilk konu hemen sıraya
girer; zamanlayıcı her dakika kontrol eder. Varsayılan aralık 24 saattir.

Zamanlayıcı konu kapsamına göre biçim seçer: tek merak sorusu için 30 saniyelik
dikey Shorts, açıkça kapsamlı anlatım veya çok boyutlu karşılaştırma için
3 dakikalık yatay video. Karar ve gerekçesi görev kaydına sabitlenir;
bu seçim için ek model çağrısı yapılmaz. Belirsiz kapsam kısa kalır.

Aynı anda en fazla iki farklı kanalda otomatik iş aktiftir. Aynı kanalın
sonraki bölümü için render ve YouTube tesliminin doğrulanmış başarısı
beklenir; seri sırası korunur. `video-worker` varsayılan iki yürütme yuvasıyla
çalışır. Kalite/yayın hatası ilgili kanalı durdurur. Belirsiz teslim sonuçları
yeniden ücretli üretim veya tekrar yükleme başlatmaz.

Başlık, açıklama, ilgili etiketler ve seri numarası yayın planına kaydedilir.
Profil `public` seçilmişse kalite ve varlık kontrollerini geçen ilk gizli
yükleme otomatik herkese açılır. Profil değişikliği geçmişte tamamlanmış
gizli yüklemeleri geriye dönük yayımlamaz. Gerçekçi AI sahnesi veya eksik
üretim kanıtı varsa YouTube'un sentetik içerik bildirimi korunur; yalnızca
senaryo yazımında AI kullanılması gerçekçi görüntü üretimiyle eş tutulmaz.
Bu önlemler ban veya para kazanma garantisi değildir. Tekrarlanan düşük
katkılı içerik, yanıltıcı başlık/etiket, taklit ve telif ihlali üretilmemelidir.

Yayın merkezinde kalite onaylı, daha önce gizli yüklenmiş bir videonun
`Herkese aç` işlemi aynı YouTube video kimliğini kullanır; tekrar yükleme
yapmaz. Yeni açık yayın yetkisi eski gizli yükleme planından ayrı kaydedilir.
Belirsiz yayın yanıtı otomatik tekrarlanmaz. `Hemen sıradaki videoyu üret`
ise yalnız boş ve duraklatılmamış kanalın mevcut sonraki konusunu öne alır;
konu sırasını sıfırlamaz ve aynı profil sürümündeki tekrarlar yeni iş açmaz.

Türkçe kısa anlatımda ses denetimi cümle içi uzun duraklama bulursa, gerçek
ses dalgası ve aynı dosyaya bağlı kelime zamanlarıyla kanıtlanan boşluklar
bir defa kısaltılabilir. Kelimeler ve doğal nefes korunur; yeni ses satın
alınmaz. Düzeltilen sesin metin, süre ve doğallık kontrolleri baştan çalışır.
Onarım zincirinde aynı ses tekrar tekrar kısaltılmaz. Başarılı bir claimed
retry'nin public teslimi doğrulandıktan sonra `resume_after_public_retry`
yalnız hata duraklamasını kaldırır; konu sırası ve yayın kayıtları korunur.

Başarısız Türkçe kısa ses kaydı için Studio'daki `Sesi tek denemeyle yenile`
işlemi aynı senaryoyu farklı bir ElevenLabs modeliyle bir kez okutabilir.
Hak, başarısız son işe ve ses dosyasının parmak izine bağlıdır; servis
çağrısından önce kalıcı olarak tüketilir. Belirsiz yanıt ikinci bir ses
ücretine yol açmaz. Eski kayıtlar ve kanal ayarları değiştirilmez; yeni ses
onaylanmamış aday olarak saklanır ve bütün kontroller yeniden çalışır.

Türkçe ve İngilizce kısa videolarda metin ve süre kontrolüne ek olarak
doğal anlatım denetimi zorunludur. İngilizce birleşik kelimeler ve açık yıl
ifadelerinin eşdeğer yazımları karşılaştırılabilir; eksik kelime zamanları
uydurulmaz, değiştirilmiş tarihler veya metinler eşdeğer sayılmaz.

Konu etiketleri eksikse yalnız tamamlanmış videonun başlık ve açıklamasındaki
ifadelerden sınırlı etiket/hashtag desteği çıkarılır; kaynak bağlantıları,
üretim talimatları ve kanal dipnotları bu seçime katılmaz. Etiketler veya
yükleme sıklığı keşfet, izlenme ya da gelir garantisi değildir.

Konu listesi en fazla 60 girdidir ve tüketilince durur; otomatik trend
araştırması, konu listesini yenileme ve Analytics geri beslemesi bu
zamanlayıcının parçası değildir. OAuth izni iptal edilirse veya Google
yeniden giriş isterse hesap sahibi bağlantıyı yenilemelidir.
