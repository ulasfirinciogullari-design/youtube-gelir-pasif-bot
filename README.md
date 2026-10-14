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

## Düşük maliyetli özgün Shorts (GitHub Actions)

`original_factory.py`, Zirvenin Kanunu için film görüntüsü kullanmadan özgün
Shorts üretir: konu listesinden sıradaki konu → LLM senaryosu → edge-tts
anlatım → Pexels/Pixabay stok video veya Fal Flux görselleri → altyazı, ses tasarımı ve
1080x1920 60 FPS render → YouTube yüklemesi. AI video üretilmez; ücretli kısım
birkaç Flux görseli (görsel başı ~0,003 $) ve tek bir kısa LLM çağrısıdır.

- Konular: `app/services/original_topics_tr.json`. Her yükleme açıklamasına
  `Seri: zk-001` gibi bir kod yazılır; sonraki çalışma kanalda görmediği ilk
  konuyu seçer. Konu bitince iş hata verip durur.
- Senaryo sağlayıcı sırası: `GEMINI_API_KEY` (ücretsiz katman) → Anthropic
  uyumlu uç → OpenAI uyumlu uç. Geçerli senaryo çıkmazsa video yüklenmez.
- Görseller: stok video önce `PEXELS_API_KEY`, sonra `PIXABAY_API_KEY` ile
  aranır (hangisi tanımlıysa); stok çıkmazsa `FAL_KEY` ile Flux görseli. AI
  görsel kullanılan videolarda YouTube'un sentetik içerik bildirimi açılır.
- Zamanlanmış yüklemelerin görünürlüğü `PUBLISH_PRIVACY` repo değişkeniyle
  (varsayılan `public`), elle çalıştırmalar workflow girdisiyle seçilir.

Yerelde yüklemeden denemek için: `python original_factory.py --no-upload --topic-id zk-001`

### Film karakteri serisi

`--series karakter`, ünlü film ve dizi karakterlerinin (Thomas Shelby, Walter
White, Polat Alemdar...) bir kararını veya kuralını kendi anlatımımızla
yorumlar ve gerçek bir psikoloji ilkesine bağlar. Film sahnesi, replik kaydı,
film müziği veya oyuncu görüntüsü kullanılmaz.

- Konular: `app/services/character_topics_tr.json`, kodlar `fk-001` gibi.
  Liste bitince Gemini yeni karakter konusu üretir.
- Anlatımda karakter adı geçebilir; görsel tariflerinden karakter ve oyuncu
  adları silinir ve yüzler silüet/arkadan çekim olarak istenir, böylece Flux
  gerçek bir oyuncuyu çizmez.
- Başlık örnekleri kanalın eski film kliplerinin en çok izlenen başlıklarından alınır.

### Merak serisi (gerçek görüntü)

`--series merak`, Ulaş'ın örnek gösterdiği Shorts videolarının tarzında
(Keşif Vakti, Full Asfalt) bilim, hayvanlar, insan vücudu ve psikoloji
sorularını enerjik bir anlatımla ve konuyla birebir eşleşen ücretsiz stok
videolarla anlatır. Yapay görsel yalnızca bir sahne için hiç stok video
bulunamazsa kullanılır.

- Konular: `app/services/merak_topics_tr.json`, kodlar `mk-001` gibi; her
  konunun `stock_hint` alanı, sahne araması boş dönerse kullanılan genel aramadır.
  Liste bitince Gemini yeni konu üretir.
- Senaryo görüntüye göre yazılır (`app/services/footage_script.py`): Pexels'ten
  dikey Full HD klipler (önce bunlar) ve Pixabay'den gerçek çekimler (animasyon
  değil) aranır; etiketinde ya da Pexels sayfa adında konunun öznesi
  (`subject`, ör. `cat`, `kitten`) geçen 40'a kadar klip toplanır, her klibin üç
  karesi Gemini'ye gösterilir ve Gemini anlatımı bu kliplerde gerçekten görünen
  şeylere göre yazar; her cümleye 2-3 farklı klip ve her klibin hangi anının
  gösterileceğini de seçer (tek klip verirse bir kez geri gönderilir, eksik kalan
  sahneler aynı aramadan veya etiketi en yakın kliplerle tamamlanır). Görüntünün
  konuyu ne kadar karşıladığını 10 üzerinden puanlar; 6'nın altındaki konular
  atlanır ve `channel-data/skipped_topics.json`'a yazılır (konu elle `topic_id`
  ile istenirse yine de yapılır). Biten senaryoyu Gemini bir kez de bilim editörü
  gibi okur; yanlış veya abartılı bir sayı ya da karşılaştırma varsa o cümleyi
  ve ekran yazısını düzeltir.
- En çarpıcı 3-4 cümlenin anahtar bilgisi ("270 DERECE") ekranın ortasında büyük
  sarı yazıyla belirir, altında hafif bir pop sesi çalar (`pop.mp3`, `ses` görevi
  eksikse üretir). Anlatım 1,1 kat hızda okunur.
- Video bittiğinde Gemini onu Ulaş'ın referans Short'larından biriyle
  (`k951xfBlETg`) karşılaştırır (`app/services/video_critic.py`): 10 üzerinden puan
  ve etkisine göre sıralı sorunlar loga ve meta dosyasına (`critique`) yazılır;
  yükleme bunu beklemez.
- Bu yol çalışmazsa eski yola dönülür: önce senaryo, sonra Gemini adayların küçük
  resimlerinden her cümleye uygun klibi seçer (`app/services/clip_matcher.py`).
- Ses efektleri vurgu içindir: hafif bir whoosh sadece kancadan sonra ve son
  sahneye geçerken çalar.
- Kurgu: en uzun çekim 1,6 sn (diğer serilerde 2,5 sn, çünkü her çekim ücretli bir görsel).
- Bütün serilerde çekimler sert kesme yerine 0,3 sn'lik yumuşak geçişle birbirine
  karışır; her cümlenin seslendirmesindeki baştaki ve sondaki sessizlik kesilir,
  cümleler arasında 0,15 sn nefes payı kalır, böylece anlatım tek parça gibi akar.
- Bütün serilerde: kanca sorusu video boyunca ekranın üstünde beyaz, kalın
  kenarlı yazı olarak durur; altyazı alt üçte birde. Yazı tipi Montserrat Black
  (iş akışı `fonts-montserrat` paketini kurar).
- Ses seviyeleri ölçülerek ayarlanır: anlatım önce standart seviyeye (-16 LUFS)
  getirilir (ElevenLabs sesi -38 LUFS civarında geliyordu, bu yüzden eski sabit %9
  müzik anlatımla neredeyse eşit duyuluyordu), müzik anlatımın 15 LU altına
  ayarlanır ve konuşmanın en net duyulduğu bantta (2,5 kHz) biraz kısılır;
  ölçülemezse eski sabit seviyeler kullanılır. `deneme` işi anlatımı ve son miksi
  `_voice.mp3` / `_mix.mp3` olarak kanal hafızasına da bırakır. Merak senaryosunu yazan model
  videonun havasını da seçer: `belgesel` (vahşi doğa, uzay, gizem) `merak_3`,
  `eğlenceli` (komik, sevimli) `merak_2`, `sakin` (vücut, psikoloji, gündelik
  bilim) `merak_1` parçasını çalar. Seçilen hava meta dosyasında `music_mood`.
- Zamanlanmış çalışmalar varsayılan olarak merak videosu üretir (Ulaş'ın seçtiği
  format). `--series karisik` son yüklemeye bakıp merak, karakter ve tarih
  videolarını sırayla üretir.

### Komik dublaj formatı

`comedy_factory.py`, Pixabay'deki ücretsiz hayvan kliplerinden (ticari kullanım
serbest, kaynak gösterme zorunlu değil ama açıklamaya yazılır) birini seçer,
Gemini'ye klibi izletip zamanlı ve komik bir Türkçe iç ses yazdırır, her
karaktere ayrı bir ElevenLabs sesi verir (yoksa edge-tts), klibi bulanık arka
planlı 9:16 kadraja, altyazı ve kanca yazısıyla birlikte yerleştirir.

- Her klip bir kez kullanılır: açıklamaya `Seri: kd-<pixabay id>` yazılır.
- Gerekenler: `PIXABAY_API_KEY`, `GEMINI_API_KEY` (veya `GEMINI_API`),
  isteğe bağlı `ELEVENLABS_API_KEY`.
- Klip ya da senaryo çıkmazsa o çalışma özgün hikâye formatına döner.
- Format, workflow girdisi `format` ile (elle çalıştırma) veya
  `CONTENT_FORMAT` repo değişkeniyle (`merak` / `karisik` / `story` / `karakter` / `comedy`)
  seçilir; varsayılan `merak`.

Yerelde yüklemeden denemek için: `python comedy_factory.py --no-upload`

### Keşfet paketi, kanal raporu ve hafıza

- Her yüklemede Gemini tek çağrıda uzun Türkçe açıklama, 500 karakter sınırına
  sığan 20-25 etiket, hashtag'ler ve başlık/açıklamanın 18 dile çevirisini
  (YouTube localizations) hazırlar (`app/services/seo_pack.py`).
- Workflow'u `task=rapor` ile elle çalıştırmak hiçbir şeyi değiştirmeden kanal
  temizlik adaylarını (eski film klibi yüklemeleri, tekrar başlıklar, izlenmeyen
  videolar) ve rakip kanal analizini (Gemini ile "DNA", boşluklar, bizim
  kurallarımız, yeni konu fikirleri) çıkarır. `task=temizlik` sadece
  `video_ids` ile verilen videoları gizler (`private`) veya siler (`delete`).
- Metrikler, rakip analizi, üretilen konular ve her videonun meta kaydı
  `channel-data` dalında saklanır; senaryo yazarı rakip kurallarını oradan okur
  ve konu listesi bitince önce rakip boşluklarından, sonra Gemini'den yeni konu alır.
