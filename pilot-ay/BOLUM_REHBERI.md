# Ya Olsaydı? bölüm rehberi

Her yeni bölüm bu rehbere göre yazılır. Amaç: 35-45 saniyelik, kaynaklı, iki karakterli, kodla çizilmiş bir Shorts.

## Kanal

- Ad: Ya Olsaydı? (@yaolsaydibilim). Dil: Türkçe. Kitle: 13 yaş üstü meraklı izleyici, çocuklara özel değil.
- Seriler: **Ya Olsaydı?** (hayali senaryo, gerçek bilim), **Ne Kadar Büyük?** (dev rakamlar, görsel karşılaştırma), **1 Gün Boyunca** (vücudun ve dünyanın sınırları).
- Stok görüntü yok. Her şey `anim.html` içinde Canvas 2D ile çizilir.

## Karakterler

- **Profesör Pi**: beyaz saçlı, gözlüklü, bıyıklı bilim insanı. Sakin, net, esprili. Ses: Emrullah Tuna.
- **Bit**: uçan küçük robot, ekran yüzlü, merak eden taraf. Soruyu o sorar, şaşırır, izleyicinin ağzından konuşur. Ses: Damla, hafif inceltilmiş.

## Senaryo kalıbı (`script.js`)

1. **Kanca (0-3 sn)**: Bit soruyu sorar. Soru başlıkla aynı olur.
2. **İlk sonuç**: Pi'nin herkesin tahmin edeceği ilk etkiyi söylemesi.
3. **"Sadece bu mu?"**: Bit'in itirazı, asıl şaşırtıcı bilgiye geçiş.
4. **2-3 şaşırtıcı bilgi**: her biri tek cümle, her birinde ekranda büyüyen bir rakam veya etiket.
5. **Ters köşe kapanış**: Pi'nin son bilgisi, Bit'in tepkisi.
6. **Çağrı**: "Sıradaki soruyu sen seç! Yorumlara yaz."

Kurallar:
- 8-12 replik, toplam 380-520 karakter (ElevenLabs kredisi ve süre için).
- Her cümle kısa ve konuşma dilinde. Rakamlar yazıyla değil, Türkçe okunuşuna uygun ("3,8 santim").
- Her sahne değişimi bir kelimeye bağlanır (`WT(satır, "kelime")`), efektler `CUES` ile.

## Bilgi doğruluğu

- Videodaki her iddia için güvenilir bir kaynak (NASA, ESA, hakemli dergi, üniversite). Kaynak `youtube.json` içindeki `facts` listesine linkiyle yazılır.
- Kesin olmayan şey kesinmiş gibi söylenmez ("olabilir", "yaklaşık").
- Ekrandaki etiketler de kaynakta geçen rakamla birebir aynı olur. Uydurma yüzde veya rakam yok.

## Yükleme bilgileri (`youtube.json`)

- `title`: Türkçe başlık soru biçiminde, 60 karakteri geçmez. 10 dilde çevirisi: en, es, pt, ar, hi, id, fr, de, ru, az.
- `summary`: iki cümlelik özet, aynı 10 dilde.
- `facts`: her bilgi 11 dilde, tek kaynak linkiyle.
- `tags`: 12-20 etiket, Türkçe ve İngilizce karışık, toplam 500 karakteri geçmez.
- `subtitles.en`: her replik için bir İngilizce satır, replik sayısıyla aynı.
- `playlist`: bölümün serisi. `privacy`: Ulaş onaylayana kadar `private`.

Açıklama `youtube.py` tarafından otomatik kurulur: özet, kaynaklı bilgiler, yayındaki ilgili bölümler, seri listesi, abone bağlantısı, etiketler.

## Yayın

- İlk 10 bölüm Ulaş'ın onayıyla herkese açılır (`privacy: "public"`).
- Kapak `brand.html` içindeki `kapak` çizimiyle hazırlanır: büyük başlık, iki karakter, sahnenin en çarpıcı anı.
- Seslendirme: YouTube'un otomatik dublajı (Türkçeden İngilizceye). Ayrı dil kanalı şimdilik yok.
