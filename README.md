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

### Zamanlama ve sıra kontrolü

GitHub saat başı zamanlamaları geciktiriyor ya da hiç çalıştırmıyor (çalışmalar
3,6 saate kadar geç başladı; 2026-10-14'te 11:00 ve 14:00 UTC çalışmaları hiç
başlamadı). Bu yüzden iş akışı her slotun (08:00, 13:00, 17:00, 21:00 TR) 3
saatlik penceresinde :07 ve :37'de uyanır ve önce `scripts/slot_gate.py`
("🚦 Sıra Kontrolü" adımı) çalışır. Kurulumlardan önce çalıştığı için atlanan bir
tur birkaç saniye sürer. Short ancak şunların hepsi tutarsa üretilir:

- slot en fazla 3 saat önce başladı ve o slotta herkese açık / liste dışı yükleme yok;
- son herkese açık / liste dışı yükleme en az 2,5 saat önce (arka arkaya
  yüklemeler erişimi düşürüyor);
- 07:00 UTC kota sıfırlamasından beri 5'ten az yükleme var (gizliler dahil);
- bu slotta başka bir zamanlanmış çalışma yükleme adımını başarıyla bitirmedi
  ve 3'ten az çalışma hata verdi.

Gizli yüklemeler (`uploads.json` kaydındaki `privacy` alanı) slotu doldurmaz;
`PUBLISH_PRIVACY=private` iken aynı slotta ikinci yüklemeyi yalnızca Actions
geçmişi kontrolü engeller. Elle çalıştırmalar kontrolsüz devam eder. Kararın
gerekçeleri adımın logunda yazar; yerelde denemek için:
`python scripts/slot_gate.py --now 2026-10-15T10:07:00Z`

Anlatıcı sesi denemeleri: `task=sesornek` aynı kısa metni farklı anlatıcı
sesleriyle seslendirip `channel-data/history/voice_samples/` altına koyar.
`task=deneme` ile `ses` girdisine bir ElevenLabs voice id yazılırsa o deneme o
sesle seslendirilir (`<voice id>:eleven_v3` modeli de değiştirir). Kalıcı ayarlar
`ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL`, `ELEVENLABS_SPEED`, `ELEVENLABS_STYLE`
ve `ELEVENLABS_STABILITY` repo değişkenleridir (boş = varsayılan).

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
  sarı yazıyla belirir, yazıdan 30 ms önce bir pop sesi çalar (`pop.mp3`, `ses` görevi
  eksikse üretir). İlk sahnenin anahtar bilgisi kanca vuruşuyla birlikte ilk karede
  belirir. Anlatım 1,1 kat hızda okunur (`ELEVENLABS_SPEED` verilmişse sesin kendi
  hızıyla, ayrıca hızlandırılmadan; bkz. Anlatıcı sesi).
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
  ElevenLabs'in cümle içinde bıraktığı uzun duraklamalar da kısaltılır: ~0,27 sn'den
  uzun her duraklama ~0,27 sn'ye iner (baykuş Short'unda 10 duraklama 31 sn'ye ~3,6 sn
  ekliyordu; aynı anlatım 28,5 sn'ye indi, kelimeler kısalmadı).
- Video son kelimeyle biter: son cümleden sonra 0,2 sn kalır ve müzik 0,3 sn'de
  kısılır (eskiden 0,6 sn + 1,5 sn'lik sönüş yeniden başlamayı belli ediyordu). Short
  tekrar başladığında doğrudan açılış vuruşuna bağlanır; 31 Mart 2025'ten beri tekrar
  izlemeler de görüntülenme sayılıyor. Riser yine sona doğru yükselip tekrarın açılış
  vuruşuna oturur.
- Bütün serilerde: kanca sorusu video boyunca ekranın üstünde beyaz, kalın
  kenarlı yazı olarak durur; altyazı alt üçte birde. Yazı tipi Montserrat Black
  (iş akışı `fonts-montserrat` paketini kurar).
- Ses seviyeleri ölçülerek ayarlanır: anlatım önce standart seviyeye (-16 LUFS)
  getirilir (ElevenLabs sesi -38 LUFS civarında geliyordu, bu yüzden eski sabit %9
  müzik anlatımla neredeyse eşit, efektler ondan çok daha yüksek duyuluyordu),
  müzik anlatımın 12 LU altına ayarlanır ve konuşmanın en net duyulduğu bantta
  (2,5 kHz) biraz kısılır; efektler anlatımın altında kalır (kanca vuruşu ~2 LU,
  pop ~4 LU, riser ~6 LU, whoosh ~14 LU altında; pop ~8 LU altındayken eleştirmen
  hiç efekt duymamıştı). Sessiz bir girişle açılan parça
  (`merak_3` ilk 8 saniyede neredeyse duyulmuyor) girişi atlanarak başlatılır,
  ama Short bitmeden parça bitmeyecek kadar erken. Ölçülemezse eski sabit
  seviyeler kullanılır. `deneme` işi anlatımı ve son miksi
  `_voice.mp3` / `_mix.mp3` olarak kanal hafızasına da bırakır. Merak senaryosunu yazan model
  videonun havasını da seçer: `belgesel` (vahşi doğa, uzay, gizem) `merak_3`,
  `eğlenceli` (komik, sevimli) `merak_2`, `sakin` (vücut, psikoloji, gündelik
  bilim) `merak_1` parçasını çalar. Seçilen hava meta dosyasında `music_mood`.
- Zamanlanmış çalışmalar varsayılan olarak merak videosu üretir (Ulaş'ın seçtiği
  format). `--series karisik` son yüklemeye bakıp merak, karakter ve tarih
  videolarını sırayla üretir.

### Anlatıcı sesi (ElevenLabs)

Anlatım her cümle ayrı okunarak ElevenLabs ile seslendirilir (anahtar yoksa veya
hata verirse edge-tts). Ses, model ve ayarlar kod değişmeden repo Variables ile
değiştirilir (GitHub → Settings → Secrets and variables → Actions → Variables); boş
veya tanımsız değişken bugünkü ayarı korur ve istek bugünküyle birebir aynı kalır.

| Variable | Ne değiştirir | Boşken |
|---|---|---|
| `ELEVENLABS_VOICE_ID` | Anlatıcı sesi | Alp (`HV5rAif1q1ITHdvxqyMw`) |
| `ELEVENLABS_MODEL` | Model, ör. `eleven_v3`, `eleven_turbo_v2_5` | `eleven_multilingual_v2` |
| `ELEVENLABS_SPEED` | Sesin kendi konuşma hızı (0,7-1,2) | gönderilmez, ffmpeg ile 1,1x |
| `ELEVENLABS_STYLE` | Vurgu ve duygu (0-1) | 0,45 |
| `ELEVENLABS_STABILITY` | Düşük = daha canlı, yüksek = daha düz (0-1) | 0,32 |

- `ELEVENLABS_SPEED` verilince anlatım ayrıca ffmpeg ile hızlandırılmaz (hız iki kez
  uygulanmasın diye); ElevenLabs hiçbir cümleyi okuyamazsa edge-tts anlatımı yine
  1,1x hızla hazırlanır. Sayılar `1,15` veya `1.15` diye yazılabilir.
- `eleven_v3` komşu cümleleri (`previous_text`/`next_text`) kabul etmediği için ona
  gönderilmez; kararlılık bu modelin kabul ettiği 0 / 0,5 / 1 adımlarına yuvarlanır.
- Meta dosyasındaki `voice` alanı kullanılan ses kimliğini, modeli ve ayarlanmış
  değerleri yazar (`voice_engine`'in hemen ardından).
- Bir Short'un bazı cümleleri ElevenLabs, bazıları edge-tts ile okunursa (ör. kredi
  video ortasında biterse), anlatıcı video ortasında değişmesin diye bütün cümleler
  edge-tts ile yeniden okunur.
- `deneme` işinde `ses` girdisi sesi yalnız o çalışma için değiştirir: `ses_kimliği`
  veya `ses_kimliği:model` (ör. `QftNgZhlSOYKrYbk7WR7` ya da
  `HV5rAif1q1ITHdvxqyMw:eleven_v3`).
- `sesornek` işi (`python scripts/voice_samples.py --out channel-data/history/voice_samples`)
  aynı dört cümlelik baykuş senaryosunu en çok 6 ses/ayarla okutur (örnek başı ~190
  karakter): `1_alp_simdiki.mp3` (bugünkü ses), `2_alp_hizli_enerjik.mp3` (kendi hızıyla
  1,15, stil 0,6, kararlılık 0,35), `3_alp_v3_duygulu.mp3` (`eleven_v3`, `[excited]` ve
  `[surprised]` etiketleri), sonra bugünkü ayarlarla diğer Türkçe sesler: ses haritasındakiler
  (Taner Torun, Emrullah Tuna, Özgür), yer kalırsa ElevenLabs'in paylaşılan kütüphanesinden en
  çok kullanılan enerjik Türkçe anlatıcılar. Her örnek Short'taki gibi kırpılır ve aynı
  seviyeye (-16 LUFS) getirilir, böylece yalnız ses karşılaştırılır. `index.json` her örneğin
  süresini, saniyedeki kelime sayısını ve beğenilirse girilecek repo Variables değerlerini
  (`repo_variables`) yazar; çalışma özeti harcanan krediyi gösterir. Kredi biterse veya
  anahtar geçersizse kalan sesler denenmez; hiç örnek çıkmazsa önceki örnekler kalır.

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
