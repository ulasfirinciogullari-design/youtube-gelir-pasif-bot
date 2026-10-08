"""
MEGA CONTENT VAULT (500+ EPISODES & TOPICS).
A massive procedural content library for 'Zirvenin Kanunu' across 10 high-RPM niches:
1. 👑 Gücün 48 Yasası (The 48 Laws of Power - All 48 Laws Complete)
2. 🧠 Karanlık Psikoloji & Zihin Manipülasyonu (Dark Psychology & Manipulation)
3. 🏛️ Stoacılık & Çelik İrade (Marcus Aurelius & Stoic Mindset)
4. 🕶️ Yeraltı Dünyası & Mafya Yasaları (Godfather, Peaky Blinders, Scarface)
5. 🌌 Kozmik Dehşet & Evrenin Gizemleri (Interstellar, Oppenheimer, Black Holes)
6. 💰 %1'in Para Yasaları & Wall Street (Jordan Belfort, Wealth Accumulation)
7. 🕶️ Sistemin İllüzyonu & Matrix (Escaping the Rat Race, Red Pill Truth)
8. ⚔️ Savaş Sanatı & Strateji (Sun Tzu & 33 Strategies of War)
9. 🛡️ Gladyatör Disiplini & Acıyı Yenme (Relentless Warrior Mindset)
10. 🧲 Manyetik Karizma & Sessiz Otorite (Alpha Presence, Magnetic Aura)

Every episode has dual-language support:
- Turkish (`scenes_tr` / `tr-TR-AhmetNeural`)
- English (`scenes_en` / `en-US-ChristopherNeural`)
- Localized multi-lingual SEO metadata (TR, EN, ES, DE, FR)
- High-CTR pinned engagement comments
"""

from pathlib import Path
import random

# Niche Constants for algorithmic tracking and high-velocity routing
NICHE_COSMIC = "Kozmik Dehşet & Evrenin Gizemleri"
NICHE_POWER_LAWS = "Gücün 48 Yasası Serisi"
NICHE_STOIC = "Stoacılık & Çelik İrade"
NICHE_UNDERWORLD = "Yeraltı Dünyası & Mafya Yasaları"
NICHE_SHELBY = "Sessiz Güç & Thomas Shelby"


# =========================================================================
# ALL 48 LAWS OF POWER (ROBERT GREENE) - COMPLETE FULL ARCHIVE
# =========================================================================
THE_48_LAWS_OF_POWER = [
    {
        "num": 1,
        "title_tr": "Efendini Asla Gölgede Bırakma",
        "title_en": "Never Outshine the Master",
        "clip": "godfather_master.mp4",
        "hook_tr": "Güç oyunundaki en ölümcül hata, patronundan daha zeki görünmeye çalışmaktır.",
        "hook_en": "The most fatal mistake in power is trying to look smarter than your master.",
        "body_tr": "Üstündeki insanları her zaman kendilerini daha üstün ve güvende hissettir. Yeteneklerini sergilerken aşırıya kaçarsan, onların korku ve kıskançlığını uyandırırsın. Başarılarını daima onların dehasına mal etmeyi öğren.",
        "body_en": "Always make those above you feel comfortably superior and secure. Flaunt too much brilliance, and you awaken their deepest insecurities and fear. Learn to credit your triumphs to their wisdom.",
        "punch_tr": "Kendi ışığını gizlemeyi bilmeyen bir adam, zirveyi asla göremez. İşte bu yüzden gücün birinci ve en ölümcül yasası...",
        "punch_en": "A man who cannot conceal his own radiance will never reach the peak. And that is why the very first law of power...",
    },
    {
        "num": 2,
        "title_tr": "Dostlarına Asla Çok Güvenme, Düşmanlarını Kullanmayı Öğren",
        "title_en": "Never Put Too Much Trust in Friends, Learn to Use Enemies",
        "clip": "scarface_master.mp4",
        "hook_tr": "Dostların sana en çabuk ihanet edecek kişilerdir, çünkü kıskançlık sessizce büyür.",
        "hook_en": "Your friends will betray you faster than your enemies, because envy grows in absolute silence.",
        "body_tr": "Eski bir düşmanını yanına alırsan, sana kendini kanıtlamak için bir dosttan daha sadık olur. Bir dostuna iş verirsen sana borçlu olduğunu değil, senin ona borçlu olduğunu düşünür. Zirvede sevgiyi değil, mesafeyi yöneteceksin.",
        "body_en": "Hire a former adversary, and he will be far more loyal than a friend because he has everything to prove. Give a friend power, and he assumes you owe him. At the top, manage distance, never naive affection.",
        "punch_tr": "Çünkü sırtına saplanan hançerin sahibi asla yabancı değildir. İşte gücün ikinci yasası...",
        "punch_en": "Because the knife between your ribs is never held by a stranger. And that is why the second law of power...",
    },
    {
        "num": 3,
        "title_tr": "Niyetini Her Zaman Gizle",
        "title_en": "Conceal Your Intentions",
        "clip": "peaky_master.mp4",
        "hook_tr": "İnsanları ne yapacağın konusunda sürekli karanlıkta ve tahminde bırak.",
        "hook_en": "Keep people off-balance and in the dark by never revealing the purpose behind your actions.",
        "body_tr": "Niyetini açık eden bir adam, düşmanına kendi kalbini nişan tahtası olarak sunar. Sahte hedefler göster, duman perdesi yarat. Onlar senin doğuya gittiğini sanırken, sen batıdaki kaleyi çoktan fethetmiş ol.",
        "body_en": "A man who exposes his intentions offers his chest as a target to the enemy. Display false goals, lay down smokescreens. While they assume you march east, you have already captured the western citadel.",
        "punch_tr": "Sessizlik ve gizem, en keskin kılıçtan daha ölümcüldür. İşte niyetini saklamanın acımasız kuralı...",
        "punch_en": "Silence and enigma are sharper than any blade. And that is why the third unwritten law...",
    },
    {
        "num": 4,
        "title_tr": "Her Zaman Gerektiğinden Daha Az Konuş",
        "title_en": "Always Say Less Than Necessary",
        "clip": "peaky_master.mp4",
        "hook_tr": "Bir odadaki en güçlü adam, en çok konuşan değil, tek bir kelimeyle odayı susturandır.",
        "hook_en": "The most powerful man in the room is never the loudest, but the one whose single word commands dead silence.",
        "body_tr": "Kelimelerle insanları etkilemeye çalıştıkça, o kadar sıradan görünürsün ve kontrolü kaybedersin. Ne kadar az konuşursan, sözlerin o kadar derin ve tehditkar algılanır. Ağzından çıkan her fazla cümle, senin aleyhine bir silaha dönüşür.",
        "body_en": "The more you try to impress with words, the more common you appear and the more control you lose. The less you speak, the more profound and dangerous you become. Every redundant word becomes ammunition against you.",
        "punch_tr": "Güçlü insanlar sessiz kalır çünkü gizem güç doğurur. İşte Thomas Shelby'nin bile uyguladığı o kural...",
        "punch_en": "Powerful minds stay quiet because mystery breeds dominance. And that is why the fourth rule of power...",
    },
    {
        "num": 5,
        "title_tr": "İtibarını Canın Pahasına Koru",
        "title_en": "So Much Depends on Reputation, Guard It with Your Life",
        "clip": "godfather_master.mp4",
        "hook_tr": "İtibar, tek bir kurşun bile sıkmadan savaşları kazanmanı sağlayan zırhtır.",
        "hook_en": "Reputation is the impenetrable armor that wins wars before the first shot is even fired.",
        "body_tr": "İtibarın sarsıldığı an savunmasız kalır ve her yönden saldırıya açık hale gelirsin. Düşmanlarının itibarında gedikler açarken, kendi adını lekesiz ve dokunulmaz tut. Bir erkeğin en büyük sermayesi bankadaki parası değil, adının yarattığı ağırlıktır.",
        "body_en": "The moment your reputation cracks, you become completely vulnerable to attack from every angle. Keep your own name untouchable while opening breaches in your enemies'. A man's true capital is not money, but the sheer weight of his name.",
        "punch_tr": "Bir kez itibarını kaybeden bir adam, asla eski tahtına oturamaz. İşte beşinci kanun...",
        "punch_en": "A man who loses his reputation never returns to the throne. And that is the fifth law of power...",
    },
    {
        "num": 6,
        "title_tr": "Ne Pahasına Olursa Olsun İlgi Çek",
        "title_en": "Court Attention at All Costs",
        "clip": "wolf_master.mp4",
        "hook_tr": "Görünmeyen hiçbir şeyin değeri yoktur; kalabalığın içinde kaybolmak ölümdür.",
        "hook_en": "Everything is judged by its appearance; to be invisible in the crowd is absolute death.",
        "body_tr": "Kalabalığın seni unutmasına asla izin verme. Kendini daha büyük, daha gizemli ve daha tehlikeli göster. Sıradan ve silik olmaktansa, tartışılan ve dikkat çeken biri olmak her zaman daha kârlıdır.",
        "body_en": "Never let the masses forget your presence. Make yourself appear larger, more enigmatic, and far more lethal. Better to be controversial and fiercely watched than completely overlooked.",
        "punch_tr": "Zirvedeki adamlar asla kenarda beklemez, sahneyi yönetir. İşte altıncı yasa...",
        "punch_en": "Men at the peak never stand in corners; they command the center stage. And that is the sixth rule...",
    },
    {
        "num": 7,
        "title_tr": "İşi Başkalarına Yaptır, Ama Övgüyü Her Zaman Kendine Al",
        "title_en": "Get Others to Do the Work, but Always Take the Credit",
        "clip": "oppenheimer_master.mp4",
        "hook_tr": "Başkalarının bilgeliğini ve emeğini kendi davanı ilerletmek için kullan.",
        "hook_en": "Use the skills, intellect, and sweat of others to advance your own supreme cause.",
        "body_tr": "Bu strateji sana sadece paha biçilmez bir zaman ve enerji tasarrufu sağlamaz, aynı zamanda sana ilahi bir hız ve güç aurası kazandırır. Yardımcıların bir süre sonra unutulacak, fakat senin başarın tarihe kazınacaktır.",
        "body_en": "This ruthless tactic saves you precious time and energy while clothing you in an aura of divine mastery. Your assistants will eventually be forgotten, but your name will be carved into history.",
        "punch_tr": "Asla kendin yapabileceğin işi yapma, yöneten zihin ol. İşte yedinci yasa...",
        "punch_en": "Never do yourself what you can orchestrate others to accomplish. And that is the seventh law...",
    },
    {
        "num": 8,
        "title_tr": "İnsanları Kendi Alanına Çek, Gerekirse Yem Kullan",
        "title_en": "Make Other People Come to You, Use Bait if Necessary",
        "clip": "godfather_master.mp4",
        "hook_tr": "Karşındakini harekete geçmeye zorladığında, kontrolü elinde tutan taraf daima sen olursun.",
        "hook_en": "When you force the other person to act, you remain in absolute command of the chessboard.",
        "body_tr": "Düşmanının kendi planlarını terk etmesine ve senin sahanda oynamasına sebep ol. Onu karşı konulamaz vaatlerle veya gizli yemlerle kendine çek. O sana doğru adım attığı anda, bütün gücünü tüketmiş olur.",
        "body_en": "Compel your adversary to abandon his plans and enter your territory. Lure him with irresistible promises or invisible bait. The moment he steps toward you, his power dissolves into your hands.",
        "punch_tr": "Saldıran değil, tuzağı kuran daima kazanır. İşte sekizinci kural...",
        "punch_en": "The aggressor rarely wins; the architect of the trap always does. And that is the eighth law...",
    },
    {
        "num": 9,
        "title_tr": "Tartışarak Değil, Eylemlerinle Kazan",
        "title_en": "Win Through Your Actions, Never Through Argument",
        "clip": "breaking_bad_master.mp4",
        "hook_tr": "Kelimelerle kazandığın anlık zafer, karşı tarafta derin ve zehirli bir kin bırakır.",
        "hook_en": "Any brief triumph won through verbal argument only leaves behind toxic, lingering resentment.",
        "body_tr": "İnsanları fikirlerine inandırmaya çalışma, onlara sonucun kaçınılmazlığını göster. Tartışmak zayıflığın ve güvensizliğin kanıtıdır. Güçlü bir lider açıklama yapmaz; sadece hamlesini yapar ve sonucun konuşmasını izler.",
        "body_en": "Never waste energy convincing others through words; demonstrate reality through undeniable execution. Arguing reveals weakness and self-doubt. A dominant mind never justifies; he moves and lets the outcome speak.",
        "punch_tr": "Gerçek güç sessizce sonuç üretmektir. İşte Walter White'ın bile anladığı dokuzuncu yasa...",
        "punch_en": "True power manufactures reality in absolute silence. And that is the ninth law of power...",
    },
    {
        "num": 10,
        "title_tr": "Mutsuz ve Talihsiz İnsanlardan Uzak Dur",
        "title_en": "Infection: Avoid the Unhappy and Unlucky",
        "clip": "batman_master.mp4",
        "hook_tr": "Duygusal veba, fiziksel bir hastalıktan çok daha hızlı ve yıkıcı şekilde bulaşır.",
        "hook_en": "Emotional poison spreads ten times faster and more destructively than any physical contagion.",
        "body_tr": "Bazı insanlar kendi yarattıkları cehennemin kurbanıdır ve seni de o bataklığa çekmeden duramazlar. Onlara acımak sadece seni de batırır. Çevreni her zaman kazanan, disiplinli ve yükselen zihinlerle doldur.",
        "body_en": "Certain people are chronic victims of their own self-inflicted chaos, and they will drag you down with them. Pitying them only drowns your own ship. Surround yourself exclusively with disciplined, elevated minds.",
        "punch_tr": "Batan bir gemiye binerek kimseyi kurtaramazsın. İşte onuncu yasa...",
        "punch_en": "You cannot save anyone by boarding a sinking ship. And that is the tenth law of power...",
    },
    {
        "num": 11,
        "title_tr": "İnsanların Sana Muhtaç Olmasını Sağla",
        "title_en": "Learn to Keep People Dependent on You",
        "clip": "godfather_master.mp4",
        "hook_tr": "Bağımsızlığını korumak için, her zaman sana ihtiyaç duyulan adam olmalısın.",
        "hook_en": "To maintain absolute sovereignty, you must become the indispensable link everyone depends on.",
        "body_tr": "İnsanlar mutluluklarını ve refahlarını senin varlığına borçlu olduklarında, senden asla vazgeçemezler. Onlara asla her şeyi kendileri yapabilecek kadar bilgi verme. Vazgeçilmez ol, zirvede kal.",
        "body_en": "When people owe their safety and prosperity to your presence, they can never afford to discard you. Never teach them enough to stand entirely on their own feet. Stay indispensable to stay untouchable.",
        "punch_tr": "Sana muhtaç olan biri sana asla ihanet edemez. İşte Don Corleone'nin on birinci kanunu...",
        "punch_en": "A man who needs you can never afford to betray you. And that is the eleventh law of power...",
    },
    {
        "num": 12,
        "title_tr": "Düşmanını Silahsızlandırmak İçin Seçici Dürüstlük Kullan",
        "title_en": "Use Selective Honesty and Generosity to Disarm Your Victim",
        "clip": "peaky_master.mp4",
        "hook_tr": "Tek bir samimi hareket, onlarca aldatıcı hamlenin üzerini örten en mükemmel maskedir.",
        "hook_en": "One single sincere act of generosity covers up dozens of tactical strikes.",
        "body_tr": "Karşındakinin gardını düşürmek için ona küçük, beklenmedik bir dürüstlük veya hediye sun. İnsan doğası iyiliğe karşı körleşir ve şüpheyi unutur. O sana güvendiği anda, asıl hamleni yapma özgürlüğüne kavuşursun.",
        "body_en": "Lower the enemy's guard with an unexpected gesture of kindness or candid truth. Human nature blinds itself to danger when flattered by benevolence. The second trust settles in, your strike is fatal.",
        "punch_tr": "En ölümcül zehir, en tatlı şarabın içinde sunulur. İşte on ikinci yasa...",
        "punch_en": "The deadliest poison is served in the finest vintage. And that is the twelfth law of power...",
    },
    {
        "num": 13,
        "title_tr": "Yardım İsterken İnsanların Çıkarlarına Hitap Et, Merhametlerine Değil",
        "title_en": "When Asking for Help, Appeal to Self-Interest, Never to Mercy",
        "clip": "wolf_master.mp4",
        "hook_tr": "Güçlü bir müttefikten yardım istediğinde geçmişteki iyiliklerini değil, gelecekte kazanacağı parayı anlat.",
        "hook_en": "When asking power for an alliance, speak of future wealth, never past favors or emotional charity.",
        "body_tr": "İnsanlar merhamet ve minnettarlıktan çabuk sıkılırlar, ama kendi kârlarını asla geri çevirmezler. Karşındaki adama senin başarının ona nasıl bir servet kazandıracağını göster. Açgözlülük, vicdandan bin kat daha güçlü bir motivasyondur.",
        "body_en": "Men tire quickly of gratitude and pity, but self-interest is an unyielding master. Show your ally exactly how your victory multiplies his own fortune. Greed is a thousand times more reliable than conscience.",
        "punch_tr": "İş dünyasında ve sokakta kural aynıdır: kazandır ki kazanasın. İşte on üçüncü yasa...",
        "punch_en": "In the boardroom and on the streets the law is the same: align greed with purpose. And that is the thirteenth law...",
    },
    {
        "num": 14,
        "title_tr": "Dost Gibi Görün, Casus Gibi Çalış",
        "title_en": "Pose as a Friend, Work as a Spy",
        "clip": "peaky_master.mp4",
        "hook_tr": "Rakibin hakkında bilgi sahibi olmak, onun kaderini elinde tutmaktır.",
        "hook_en": "Possessing critical knowledge about your adversary means holding his destiny in your hands.",
        "body_tr": "Sosyal ortamlarda sıcak ve zararsız görünerek insanların sırlarını dinle. İnsanlar rahatladıklarında zayıflıklarını ve planlarını dökerler. Sen dinleyen taraf ol, onlar konuşurken kendi mezarlarını kazsınlar.",
        "body_en": "In casual settings, project warmth and harmlessness while observing everything. When people relax, they volunteer their darkest secrets and insecurities. Be the silent listener who watches them dig their own traps.",
        "punch_tr": "Bilgi en büyük cephanedir. İşte Thomas Shelby'nin on dördüncü kuralı...",
        "punch_en": "Intelligence is the supreme artillery. And that is the fourteenth rule of power...",
    },
    {
        "num": 15,
        "title_tr": "Düşmanını Tamamen Ez",
        "title_en": "Crush Your Enemy Totally",
        "clip": "gladiator_master.mp4",
        "hook_tr": "Yaralı bir yılan iyileştiğinde, eskisinden daha zehirli ve intikam dolu şekilde geri döner.",
        "hook_en": "A wounded viper always recovers with greater venom and an obsession for vengeance.",
        "body_tr": "Tarihteki tüm büyük fatihler bilirdi: düşmanını yarım bırakırsan, sana bedelini ödetir. Ruhunu, kaynaklarını ve savaşma iradesini tamamen yok et. Acımak zayıflara göredir, zirvede ikinci şans yoktur.",
        "body_en": "Every great conqueror understood: leave an adversary wounded, and he will exact retribution. Crush not just his body, but his resources and will to resist. Pity belongs to the weak; the peak offers no second chances.",
        "punch_tr": "Ya hiç vurma, ya da bir daha ayağa kalkamasın. İşte on beşinci kanun...",
        "punch_en": "Either do not strike at all, or strike so decisively he never rises again. And that is the fifteenth law...",
    },
    {
        "num": 16,
        "title_tr": "Yokluğunla Değerini Artır",
        "title_en": "Use Absence to Increase Respect and Honor",
        "clip": "interstellar_master.mp4",
        "hook_tr": "Sürekli ortalıkta görünen bir insan, hızla ucuzlar ve sıradanlaşır.",
        "hook_en": "Too much availability depreciates your value and makes you utterly commonplace.",
        "body_tr": "Ne kadar çok görünürsen, o kadar az saygı görürsün. Toplulukta bir varlık oluşturduktan sonra aniden geri çekil. Yokluğun merak ve özlem doğurur. İnsanlar ancak kaybettikleri şeylerin gerçek değerini anlarlar.",
        "body_en": "The more accessible you make yourself, the less respect you command. Establish your presence, then withdraw suddenly. Absence inflames curiosity and longing. Humanity only values what it struggles to keep.",
        "punch_tr": "Kıtlık değeri yaratır. İşte saygının on altıncı yasası...",
        "punch_en": "Scarcity manufactures prestige. And that is the sixteenth law of power...",
    },
    {
        "num": 17,
        "title_tr": "İnsanları Dehşet İçinde Bırak: Tahmin Edilemez Ol",
        "title_en": "Keep Others in Suspended Terror: Cultivate an Air of Unpredictability",
        "clip": "joker_master.mp4",
        "hook_tr": "İnsanlar tahmin edilebilir olanı kontrol eder, tahmin edilemeyenden ise ölümüne korkarlar.",
        "hook_en": "People master what is predictable, but they are terrified of what they cannot anticipate.",
        "body_tr": "Hareketlerini bir rutine bağlama. Bazen sebepsiz görünen hamleler yap, beklentilerin tam tersine hareket et. Bu tahmin edilemezlik düşmanlarının kafasını karıştırır ve onları sürekli savunmada bırakır.",
        "body_en": "Never lock your actions into predictable routines. Execute moves that defy logic and shatter expectations. This aura of volatility paralyzes rivals and forces them into perpetual defense.",
        "punch_tr": "Kuralı olmayan bir oyuncuyu kimse yenemez. İşte on yedinci yasa...",
        "punch_en": "No one can defeat a player who rewrites the rules mid-game. And that is the seventeenth law...",
    },
    {
        "num": 18,
        "title_tr": "Kendini Savunmak İçin Kaleler İnşa Etme: İzolasyon Tehlikelidir",
        "title_en": "Do Not Build Fortresses to Protect Yourself: Isolation is Dangerous",
        "clip": "matrix_master.mp4",
        "hook_tr": "Kendini dünyadan izole ettiğin an, kendi inşa ettiğin hapishanede körleşirsin.",
        "hook_en": "The moment you isolate yourself from the world, you blind yourself inside your own fortress.",
        "body_tr": "Kale seni korumaz, sadece düşmanların için sabit ve kolay bir hedef haline getirir. Bilgi akışını keser, seni dedikodulardan ve tehlikelerden habersiz bırakır. Gerçek güç kalabalıkların arasında, görünmez ama her yere hakim olmaktır.",
        "body_en": "A fortress offers no true safety; it only makes you an easy, immobile target. It cuts off your intelligence network and blinds you to approaching storm clouds. True dominance thrives within the crowd, unseen yet pervasive.",
        "punch_tr": "Güçlü adam saklanmaz, dalgaları yönetir. İşte on sekizinci yasa...",
        "punch_en": "A sovereign mind never hides; he commands the flow. And that is the eighteenth law...",
    },
    {
        "num": 19,
        "title_tr": "Kiminle Dans Ettiğini Bil: Asla Yanlış Kişiyi Gücendirme",
        "title_en": "Know Who You're Dealing With: Do Not Offend the Wrong Person",
        "clip": "breaking_bad_master.mp4",
        "hook_tr": "Her kurt koyun postunda gezmez, bazı ejderhalar sessizce köşede bekler.",
        "hook_en": "Not every wolf wears sheep's clothing; some dragons wait in terrifying, quiet patience.",
        "body_tr": "Dünyada asla küçümsememen gereken adamlar vardır. Onları bir kez incitirsen, hayatlarının sonuna kadar intikam peşinde koşarlar. Birine meydan okumadan önce onun ruhunun derinliğini ve tehlikesini tartmayı öğren.",
        "body_en": "There are individuals you must never dare to cross. Offend them once, and they will spend a lifetime hunting you down. Before issuing a challenge, measure the lethal depth of your opponent's soul.",
        "punch_tr": "Yanlış adama basarsan, bedelini hayatınla ödersin. İşte on dokuzuncu yasa...",
        "punch_en": "Cross the wrong man, and you pay with your empire. And that is the nineteenth law...",
    },
    {
        "num": 20,
        "title_tr": "Kimseye Bağlanma: Bağımsızlığını Koru",
        "title_en": "Do Not Commit to Anyone",
        "clip": "peaky_master.mp4",
        "hook_tr": "Bir tarafa bağlanan adam köleleşir, ortada duran adam ise tarafları yönetir.",
        "hook_en": "The man who commits early becomes a servant; the man who remains uncommitted commands both sides.",
        "body_tr": "Asla kimsenin davasına körü körüne bağlanma. Herkesin senin desteğini kazanmak için rekabet etmesine izin ver. Bağımsız kaldığın sürece herkes sana saygı duyar ve senin onayını arar.",
        "body_en": "Never surrender your allegiance blindly to any faction. Let all sides court your favor and compete for your nod. As long as you remain sovereign, everyone seeks your blessing.",
        "punch_tr": "Tahtında tek başına oturan adam asla devrilmez. İşte yirminci kural...",
        "punch_en": "A sovereign king who stands alone can never be outflanked. And that is the twentieth law of power...",
    },
    # Laws 21 to 48 dynamically procedurally rendered below
]

# =========================================================================
# PROCEDURAL NICHES EXPANSION (500+ EPISODES GENERATOR)
# =========================================================================
NICHES = [
    {
        "id": "dark_psychology",
        "name_tr": "Karanlık Zihin & Manipülasyon",
        "name_en": "Dark Psychology & Mental Domination",
        "clip": "peaky_master.mp4",
        "topics": [
            ("Göz Teması Hakimiyeti", "Eye Contact Dominance", "Karşındakinin gözlerinin içine 4 saniyeden fazla gözünü kırpmadan baktığında ne olur?", "What happens psychologically when you hold unflinching eye contact for over 4 seconds?"),
            ("Sessizliğin Gücü", "Tactical Silence", "Bir soru sorulduğunda 3 saniye sessiz kalmak, odadaki tüm otoriteyi sana geçirir.", "Remaining silent for 3 full seconds after a question instantly shifts all psychological leverage to you."),
            ("Tepkisizlik Kalkanı", "Emotional Non-Reaction", "Hakarete uğradığında tepki vermeyen bir adam, karşısındakini aciz duruma düşürür.", "A man who refuses to flinch in the face of an insult instantly renders the aggressor powerless."),
            ("Mikro İfadeleri Okuma", "Micro-Expression Decryption", "İnsanlar yalan söylerken yüzlerindeki sol kaş istemsizce yukarı kalkar.", "When people deceive, the subtle twitch above their left eyebrow reveals everything."),
            ("Çerçeve Kontrolü", "Conversational Frame Control", "Bir tartışmada kuralları koyan değil, soruyu soran taraf daima zihni yönetir.", "In any negotiation, the man who asks the questions secretly dictates the entire mental reality."),
        ]
    },
    {
        "id": "stoic_mind",
        "name_tr": "Stoacılık & Çelik İrade",
        "name_en": "Stoic Indifference & Iron Will",
        "clip": "gladiator_master.mp4",
        "topics": [
            ("Marcus Aurelius'un Sabah Kuralı", "Marcus Aurelius Morning Rule", "Sabah uyandığında kendine nankör, kibirli ve yalancı insanlarla karşılaşacağını söyle.", "When you awaken, prepare yourself to meet treacherous, arrogant, and ungrateful souls."),
            ("Acıyı Sıfırlama Sanatı", "Amor Fati: Loving Fate", "Başına gelen hiçbir olay kötü değildir, ona yüklediğin anlam seni yıkar.", "Nothing that occurs is inherently evil; it is solely the meaning you assign that breaks you."),
            ("Memento Mori: Ölümle Güçlenme", "Memento Mori: Power of Mortality", "Her gün ölebileceğini hatırlayan bir adam, küçük korkuların esiri olmaz.", "A man who meditates on death daily never surrenders to trivial earthly fears."),
            ("Dış Dünyaya Karşı Kayıtsızlık", "Stoic Detachment", "Senin kontrolünde olmayan şeylere üzülmek, kendi elinle kendine pranga takmaktır.", "Weeping over what lies beyond your control is forging your own mental shackles."),
            ("Zihinsel Kale", "The Inner Citadel", "Fırtına dışarıda kopabilir, fakat senin zihnin dokunulmaz bir mermer tapınak olmalıdır.", "The storm may rage outside, but your mind must remain an untouchable marble fortress."),
        ]
    },
    {
        "id": "mob_underworld",
        "name_tr": "Yeraltı Dünyası & Mafya Yasaları",
        "name_en": "Underworld Code & Mafia Loyalty",
        "clip": "godfather_master.mp4",
        "topics": [
            ("Don Corleone'nin Sadakat Testi", "Don Corleone's Loyalty Test", "Bir adama servet ver, sadakatini o zaman test etmiş olursun.", "Give a man sudden wealth; only then do you see the true fiber of his loyalty."),
            ("Thomas Shelby'nin Soğukkanlılığı", "Thomas Shelby's Cold Cadence", "Kaybettiğinde gülümseyebilen bir adamı dünyadaki hiçbir güç korkutamaz.", "A man who can smile while bleeding can never be intimidated by any earthly power."),
            ("Tony Montana'nın Tek Kuralı", "Tony Montana's Sole Creed", "Bu dünyada sahip olduğum tek şey cesaretim ve sözümdür; ikisini de çiğnetmem.", "All I have in this world is my ambition and my word, and I break neither for anyone."),
            ("İhanetin Kokusu", "The Scent of Betrayal", "Sana en yakın oturan kişi, düşmana senin kapını ilk açacak olandır.", "The man who sits closest to your table is usually the first to unlock the gates for the enemy."),
            ("Sessiz İntikam", "Cold Retribution", "Öfkeyle alınan intikam acemiliktir; profesyoneller intikamını yıllar sonra sessizce servis eder.", "Revenge served in anger is amateur; kings serve retribution ice-cold after years of silence."),
        ]
    },
    {
        "id": "cosmic_mysteries",
        "name_tr": "Kozmik Dehşet & Evrenin Sırları",
        "name_en": "Cosmic Horrors & Space Paradox",
        "clip": "interstellar_master.mp4",
        "topics": [
            ("Gargantua'nın Zaman Paradoksu", "Gargantua Time Dilation", "Karadeliğin yanındaki gezegende bir saat geçirdiğinde, dünyada yedi yıl silinir.", "One single hour on Miller's planet erases seven years of human life on Earth."),
            ("Oppenheimer'ın Kıyamet Ateşi", "Oppenheimer's Atomic Dawn", "Gökyüzü mor renge büründüğünde insanlık kendi celladını yaratmıştı.", "When the desert sky turned purple, humanity birthed its own eternal executioner."),
            ("Büyük Sessizlik & Fermi Paradoksu", "The Great Silence: Fermi Paradox", "Milyarlarca galaksi varken neden tek bir ses bile duymuyoruz? Çünkü avcılar sessizce avlanıyor.", "With billions of galaxies, why is the cosmos utterly silent? Because predators hunt in the dark."),
            ("Olay Ufkunun Ötesi", "Beyond the Event Horizon", "Işığın bile kaçamadığı o sınırın ardında zaman geriye mi akar?", "Beyond the boundary where light itself drowns, does time shatter and reverse?"),
            ("Atomların Çöküşü", "Neutron Core Collapse", "Bir çay kaşığı nötron yıldızı maddesi dünyadaki tüm dağlardan daha ağırdır.", "A single teaspoon of neutron star matter outweighs all mountains on planet Earth."),
        ]
    },
    {
        "id": "wall_street_wealth",
        "name_tr": "%1'in Para Yasaları & Wall Street",
        "name_en": "The 1% Wealth Code & Predator Finance",
        "clip": "wolf_master.mp4",
        "topics": [
            ("Jordan Belfort'un Satış Yasası", "Straight Line Conviction", "İnsanlar mantıklarıyla değil, duygularıyla satın alır ve sonra mantıkla meşrulaştırırlar.", "People buy entirely through raw emotion and justify it with logic afterward."),
            ("Asimetrik Bahisler", "Asymmetric Risk Dominance", "Kaybedersen 1 kaybedeceğin, kazanırsan 100 kazanacağın masalara otur.", "Only play games where your downside is capped at 1, but your upside is unlimited."),
            ("Bileşik Getirinin Acımasızlığı", "The Brutality of Compounding", "Zenginlik bir gecede kazanılmaz; sessizce katlanan sabırla inşa edilir.", "True sovereign wealth is never born overnight; it compounds quietly in ruthless patience."),
            ("Paranın Gerçek Doğası", "The True Nature of Currency", "Para bir kağıt parçası değil, insan iradesini yönlendiren en saf enerjidir.", "Money is not paper; it is the purest form of stored human energy and sovereign will."),
            ("Yoksulluk Zihniyeti Tuzağı", "Breaking the Scarcity Mindset", "Harcamaktan korkan adam asla kazanamaz; paranı asker gibi savaşa göndereceksin.", "The man who fears spending will never conquer; send your capital into battle like soldiers."),
        ]
    },
    {
        "id": "matrix_glitch",
        "name_tr": "Sistemin İllüzyonu & Matrix",
        "name_en": "The Matrix Illusion & Reality Glitch",
        "clip": "matrix_master.mp4",
        "topics": [
            ("Modern Kölelik Tuzağı", "The Modern 9-5 Prison", "Sana maaş vermiyorlar; hayallerini satın almak için aylık rüşvet ödüyorlar.", "They do not pay you a salary; they hand you a monthly bribe to surrender your destiny."),
            ("Dopamin Esareti", "The Dopamine Leash", "Ekran başında saatlerce kaydırma yapan bir insan, zihinsel olarak çoktan ölmüştür.", "A man scrolling endlessly for cheap dopamine hits is already spiritually dead."),
            ("Kırmızı Hapın Bedeli", "The Weight of the Red Pill", "Gerçeği gördüğünde artık eski konforlu yalanlara asla geri dönemezsin.", "Once you glimpse raw reality, you can never sleep peacefully in sweet illusions again."),
            ("Algı Simülasyonu", "Manufactured Reality", "Sana ne düşünmen gerektiğini haberler değil, görünmeyen algı mimarları fısıldıyor.", "It is not the news that shapes your mind, but invisible architects of collective perception."),
            ("Özgürlüğün Tek Yolu", "The Sole Path to Sovereignty", "Finansal ve zihinsel bağımsızlığını kazanmayan herkes sistemin piyonudur.", "Anyone who lacks financial and mental sovereignty remains a disposable pawn on their board."),
        ]
    },
    {
        "id": "warrior_discipline",
        "name_tr": "Gladyatör Disiplini & Çelik İrade",
        "name_en": "Gladiator Discipline & Relentless Will",
        "clip": "gladiator_master.mp4",
        "topics": [
            ("Maximus'un Arenadaki Yemini", "The Gladiator's Oath", "Bugün ölmek istemiyorsan, acının içine doğru koşacaksın.", "If you wish to survive the arena today, you must run straight into the teeth of pain."),
            ("Konfor Alanının Ölümü", "The Poison of Comfort", "Sıcak bir yatak ve tatlı sözler, bir erkeğin içindeki savaşçıyı öldüren en zehirli silahtır.", "A soft bed and sweet compliments are the deadliest poison to the warrior within."),
            ("Tükenmişlik Eşiği", "Beyond the Breaking Point", "Bittiğini sandığın an, gerçek potansiyelinin sadece yüzde kırkına ulaşmışsındır.", "When you believe you are completely finished, you have only reached forty percent of your capacity."),
            ("Yalnız Kurt Disiplini", "Lone Wolf Consistency", "Kimse alkışlamadığında bile her sabah aynı disiplinle ayağa kalkabilen adam yenilmezdir.", "The man who rises in silence with zero applause is mathematically unbeatable."),
            ("Yaraların Zaferi", "Scars as Crown", "Yaraların senin zayıflığın değil, hangi savaşlardan sağ çıktığının madalyasıdır.", "Your scars are not flaws; they are medals proving which battles you survived."),
        ]
    },
]


# =========================================================================
# WINNING HIGH-VELOCITY NICHES: 50 CLIFFHANGER DIALOGUE EPISODES
# (Thomas Shelby: 17, 48 Laws of Power: 17, Cosmic Mysteries: 16)
# =========================================================================
WINNING_NICHES_CLIFFHANGER_EPISODES = [
    {
        "id": "shelby_silent_01",
        "series_title": "👑 Thomas Shelby | Odanın En Zayıf Adamı #shorts",
        "theme_name": "Thomas Shelby - Sessiz Güç ve Odanın En Zayıf Adamı",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Arthur: Neden sana hakaret ettiklerinde silahını çekmiyorsun Tommy?",
            "Shelby: Bir odadaki en zayıf adam, sesini ilk yükseltendir Arthur.",
            "Arthur: Seni korkak sanacaklar! Bir Shelby asla geri adım atmaz!",
            "Shelby: Bırak öyle sansınlar. Sessizlik bir zayıflık değil, avını izleyen kurdun nefesidir.",
            "Shelby: Saygı bağırmakla değil, gözünü bile kırpmadan masada oturabilmekle kazanılır.",
            "Arthur: Peki ya sonra Tommy? Ne zaman vuracağız?",
            "Shelby: Onlar kazandıklarını sandıkları an... Ve işte bu yüzden, tam da sessizliğin başladığı yerde fırtına yeniden kopar.",
        ],
        "scenes_en": [
            "Arthur: Why don't you draw your gun when they insult you, Tommy?",
            "Shelby: The weakest man in the room is always the first one to raise his voice, Arthur.",
            "Arthur: They will think you are weak! A Shelby never retreats!",
            "Shelby: Let them think so. Silence is never cowardice; it is the breath of the stalking wolf.",
            "Shelby: Respect is never won through shouting, but through icy, unblinking stillness at the table.",
            "Arthur: And then what, Tommy? When do we strike?",
            "Shelby: The exact second they believe they have won... And that is why, precisely where silence begins, the tempest resets.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Weakest Man in the Room #shorts",
                        "description": "The weakest man in the room is always the one who reacts first.\n\n#shorts #thomasshelby #peakyblinders #silentpower #sigma #mindset #darkpsychology"
            },
            "es": {
                        "title": "👑 Thomas Shelby | El Hombre Más Débil #shorts",
                        "description": "El hombre más débil de la habitación es el que reacciona primero.\n\n#shorts #thomasshelby #peakyblinders #psicologia"
            },
            "de": {
                        "title": "👑 Thomas Shelby | Der Schwächste Mann im Raum #shorts",
                        "description": "Der schwächste Mann im Raum ist immer der, der zuerst reagiert.\n\n#shorts #thomasshelby #peakyblinders #macht"
            }
},
        "pinned_comment_tr": "👑 Sence bir tartışmada susmak zayıflık mıdır yoksa en büyük güç mü? Yorumunu bırak.",
        "pinned_comment_en": "👑 Is remaining silent in conflict a weakness or the ultimate power move? Drop your thoughts below.",
    },
    {
        "id": "shelby_silent_02",
        "series_title": "👑 Thomas Shelby | Asla Özür Dileme #shorts",
        "theme_name": "Thomas Shelby - Zayıflık ve Özür Dilememe Kuralı",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Michael: Hata yaptığımızı kabul edip adamlardan özür dilemeliydik Tommy.",
            "Shelby: Bir kurt, öldürdüğü avından asla özür dilemez Michael.",
            "Michael: Ama bu bir ticaret! Onların desteğine ihtiyacımız var!",
            "Shelby: Eğer bir kez özür dilersen, boynuna kendi ellerinle tasma takmış olursun.",
            "Shelby: Güçlü insanlar hata yaptıklarında açıklama yapmaz; durumu kendi lehlerine çevirirler.",
            "Michael: Peki bu acımasızlık değil mi?",
            "Shelby: Bu hayatta kalmak. Çünkü özür zayıflara aittir; krallar sadece sonuçları yeniden yazar.",
        ],
        "scenes_en": [
            "Michael: We should have admitted our mistake and apologized to them, Tommy.",
            "Shelby: A wolf never apologizes to the prey it hunts, Michael.",
            "Michael: But this is business! We need their alliance!",
            "Shelby: If you apologize even once, you place a collar around your own neck.",
            "Shelby: Sovereign minds never justify their missteps; they reconstruct reality until victory is inevitable.",
            "Michael: Isn't that ruthless cruelty?",
            "Shelby: That is raw survival. For apologies belong to the frail; kings simply rewrite the consequence.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Never Apologize #shorts",
                        "description": "A wolf never apologizes to the prey it hunts.\n\n#shorts #thomasshelby #peakyblinders #sigma #power #mindset"
            }
},
        "pinned_comment_tr": "👑 Güçlü bir lider hata yaptığında özür dilemeli mi yoksa sonucu mu düzeltmeli? Fikrini yaz.",
        "pinned_comment_en": "👑 Should a sovereign leader apologize or simply engineer the solution? Comment below.",
    },
    {
        "id": "shelby_silent_03",
        "series_title": "👑 Thomas Shelby | Gözünü Kırpmayan Adam #shorts",
        "theme_name": "Thomas Shelby - Göz Teması ve Soğuk Hakimiyet",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Alfie: Gözlerime öyle bakma Tommy, sanki ruhumun arkasındaki mezarı görüyorsun.",
            "Shelby: Gözlerini ilk kaçıran adam, masadaki tüm parayı çoktan kaybetmiştir Alfie.",
            "Alfie: Bu bir tehdit mi yani, ha? Karşında Alfie Solomons var!",
            "Shelby: Tehdit korkakların işidir. Ben sadece gerçeği söylüyorum.",
            "Shelby: Karşındakinin gözlerinin içine dört saniye kırpmadan bakarsan, içindeki bütün korkuyu okursun.",
            "Alfie: Peki senin korkunu kim okuyacak Tommy?",
            "Shelby: İçinde korku kalmayan bir adamın gözlerinde sadece uçurum vardır... Ve tam bu anda, avcı ile avın yer değiştirdiği sonsuz döngü başlar.",
        ],
        "scenes_en": [
            "Alfie: Don't look at me like that, Tommy, as if you can see the open grave behind my soul.",
            "Shelby: The man who blinks first has already forfeited every shilling on the table, Alfie.",
            "Alfie: Is that a bloody threat, eh? You're staring at Alfie Solomons!",
            "Shelby: Threats are for frightened men. I merely speak the invoice of reality.",
            "Shelby: Hold unflinching eye contact for four seconds, and you dissect their deepest insecurity.",
            "Alfie: And who is supposed to read your fear, Tommy?",
            "Shelby: A man stripped of fear holds nothing in his eyes except an abyss... And that is the exact second predator and prey trade places forever.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Unflinching Eye Contact #shorts",
                        "description": "The man who blinks first forfeits the game.\n\n#shorts #thomasshelby #peakyblinders #eyecontact #darkpsychology #sigma"
            }
},
        "pinned_comment_tr": "👑 Biriyle konuşurken göz temasını ne kadar süre koruyabiliyorsun? Yorumunu bırak.",
        "pinned_comment_en": "👑 How long can you hold unflinching eye contact in high-stakes negotiations? Comment below.",
    },
    {
        "id": "shelby_silent_04",
        "series_title": "👑 Thomas Shelby | İntikamın Sıcaklığı #shorts",
        "theme_name": "Thomas Shelby - Buz Gibi İntikam ve Sabır",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Polly: Kanımız kaynıyor Thomas, bu gece o aileyi haritadan silmeliyiz!",
            "Shelby: Öfkeyle vurursan sadece kendini yakarsın Polly.",
            "Polly: Bize saldırdılar! Sokaklar intikam diye bağırıyor!",
            "Shelby: Sokaklar bağırır çünkü sokaklar aptaldır.",
            "Shelby: İntikam sıcakken yenirse boğazını yakar. Onu dondurucuda, buz gibi olana kadar bekleteceksin.",
            "Polly: Ne kadar bekleyeceğiz?",
            "Shelby: Onlar bizi unutup güvende hissettikleri güne kadar... Çünkü intikam bir öfke patlaması değil, matematiksel bir kader döngüsüdür.",
        ],
        "scenes_en": [
            "Polly: Our blood is boiling, Thomas, we must wipe that family out tonight!",
            "Shelby: Strike with fury, and you only incinerate your own house, Polly.",
            "Polly: They attacked our blood! The streets are howling for revenge!",
            "Shelby: Streets howl because the mob is inherently reckless.",
            "Shelby: Revenge served hot burns your own throat. You store it in the freezer until it turns to ice.",
            "Polly: How long do we wait?",
            "Shelby: Until the hour they forget our name and feel invulnerable... For retribution is never passion, but an inescapable geometric loop.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Revenge Served Cold #shorts",
                        "description": "Revenge served hot burns your own throat.\n\n#shorts #thomasshelby #peakyblinders #revenge #patience #sigma"
            }
},
        "pinned_comment_tr": "👑 Sence intikam hemen mi alınmalı yoksa yıllar sonra sessizce mi? Yorumunu bırak.",
        "pinned_comment_en": "👑 Should revenge be swift or served ice-cold years later? Drop your verdict.",
    },
    {
        "id": "shelby_silent_05",
        "series_title": "👑 Thomas Shelby | Düşmanını Seçmek #shorts",
        "theme_name": "Thomas Shelby - Asil Düşmanlar ve Stratejik Saygı",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Luca: Karşıma diz çöküp af dileyeceğini sanıyordum Bay Shelby.",
            "Shelby: Ben sadece beni yok edebilecek kadar zeki olanlara saygı duyarım Luca.",
            "Luca: New York mafyası senin küçük Birmingham krallığını tek bir gecede yakar!",
            "Shelby: Bir imparatorluğu yakmak kibrit gerektirir Luca, ama onu yönetmek çelik gibi bir sabır ister.",
            "Shelby: Sen buraya kan dökmeye geldin, bense senin hanedanını satın almaya.",
            "Luca: Bizden hiç mi korkmuyorsun?",
            "Shelby: Korku bir lükstür ve benim zamanım yok... Ve işte bu yüzden, en büyük zafer düşmanınla aynı aynaya baktığın andır.",
        ],
        "scenes_en": [
            "Luca: I expected you on your knees begging for mercy, Mr. Shelby.",
            "Shelby: I only grant respect to adversaries cunning enough to pose a lethal threat, Luca.",
            "Luca: The New York families will torch your petty Birmingham syndicate in a single evening!",
            "Shelby: Burning an empire takes a common match, Luca; governing it takes unyielding steel.",
            "Shelby: You sailed here to spill blood; I arrived to purchase your entire lineage.",
            "Luca: Do you feel no terror at all?",
            "Shelby: Fear is a luxury for idle men, and my schedule is full... And that is why supreme triumph arrives when you gaze into your enemy's mirror.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Choosing Your Enemy #shorts",
                        "description": "Fear is a luxury for idle men.\n\n#shorts #thomasshelby #peakyblinders #lucachangretta #sigma #power"
            }
},
        "pinned_comment_tr": "👑 Bir erkeğin kalitesini dostları mı belirler yoksa düşmanları mı? Tartışalım.",
        "pinned_comment_en": "👑 Does a man's caliber get measured by his allies or by his enemies? Let's discuss.",
    },
    {
        "id": "shelby_silent_06",
        "series_title": "👑 Thomas Shelby | Sırrını Kimseye Verme #shorts",
        "theme_name": "Thomas Shelby - Mutlak Gizlilik ve Yalnız İrade",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Grace: Bana her şeyi anlatacağını söylemiştin Thomas... Neden hala bir duvarın arkasındasın?",
            "Shelby: Kendi gölgesine bile sır veren bir adam, karanlıkta tek başına ölür Grace.",
            "Grace: Ben senin düşmanın değilim, sana yardım etmek istiyorum!",
            "Shelby: En ölümcül darbeler daima seni en çok sevenlerin dikkatsizliğinden gelir.",
            "Shelby: Planını bir kişiye bile fısıldarsan, artık plan senin değil, onun merhametine kalmıştır.",
            "Grace: Zirvede yaşamanın bedeli bu kadar mı ağır?",
            "Shelby: Evet. Sırrın senin efendin, suskunluğun senin ordundur... Ve o ordu sessiz kaldıkça taht asla devrilmez.",
        ],
        "scenes_en": [
            "Grace: You promised you would tell me everything, Thomas... Why do you remain behind that fortress?",
            "Shelby: A man who confides even in his own shadow dies alone in the dark, Grace.",
            "Grace: I am not your adversary; I only desire to shield you!",
            "Shelby: The deadliest blows are inevitably born from the carelessness of those who love you most.",
            "Shelby: Whisper your strategy to a single ear, and your fate is no longer yours, but hostage to their discretion.",
            "Grace: Is the toll of sovereign power truly that desolate?",
            "Shelby: Indeed. Your secret is your master, your silence your private army... And as long as that legion holds its breath, the throne never falls.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Guard Your Secrets #shorts",
                        "description": "A man who confides even in his shadow dies alone in the dark.\n\n#shorts #thomasshelby #peakyblinders #secrecy #sigma #mindset"
            }
},
        "pinned_comment_tr": "👑 Hayatta en büyük hedefini en yakınına bile anlatmalı mısın? Yorumunu bırak.",
        "pinned_comment_en": "👑 Should you reveal your grandest vision even to your closest confidant? Comment below.",
    },
    {
        "id": "shelby_silent_07",
        "series_title": "👑 Thomas Shelby | Korkunun Para Birimi #shorts",
        "theme_name": "Thomas Shelby - Korkunun Değeri ve Otorite",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Campbell: Bu şehir seni asla sevmeyecek Shelby! Arkandan lanet okuyorlar!",
            "Shelby: Sevgi ucuzdur müfettiş. Bir gecede tükenir ve buharlaşır.",
            "Campbell: O halde neyin peşindesin? Sokakları zorbalıkla mı yöneteceksin?",
            "Shelby: Korku... Asla enflasyona uğramayan ve değer kaybetmeyen tek para birimidir.",
            "Shelby: Seni seven bir adam yarın çıkarları için satabilir. Ama senden korkan bir adam, rüyasında bile sana itaat eder.",
            "Campbell: Bir gün o korku nefrete dönüştüğünde ne yapacaksın?",
            "Shelby: Nefret de bir itaattir müfettiş... Çünkü saygı korkuyla doğar, ve korku her sabah yeniden uyanır.",
        ],
        "scenes_en": [
            "Campbell: This city will never love you, Shelby! They curse your name in every back alley!",
            "Shelby: Love is inexpensive, Inspector. It evaporates with the morning mist.",
            "Campbell: Then what currency do you trade in? Pure tyranny on the cobblestones?",
            "Shelby: Fear... The solitary currency immune to inflation and decay.",
            "Shelby: A man who loves you will trade your head for profit tomorrow. A man who fears you obeys in his sleep.",
            "Campbell: And when that fear curdles into unvarnished hatred, what then?",
            "Shelby: Hatred is merely another dialect of submission, Inspector... For respect is fathered by fear, and fear rises every dawn anew.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Currency of Fear #shorts",
                        "description": "Fear is the only currency that never depreciates.\n\n#shorts #thomasshelby #peakyblinders #fear #respect #power"
            }
},
        "pinned_comment_tr": "👑 Liderlikte sevgi mi daha kalıcıdır yoksa saygı ve korku mu? Fikrini yaz.",
        "pinned_comment_en": "👑 In leadership, is love more enduring or respect anchored by fear? Drop your take.",
    },
    {
        "id": "shelby_silent_08",
        "series_title": "👑 Thomas Shelby | Fırtınada Sigara Yakmak #shorts",
        "theme_name": "Thomas Shelby - Kaosta Soğukkanlılık ve Yavaşlık",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Arthur: Her şey yanıyor Tommy! Polis sokakta, mermiler bitiyor, ne yapacağız?!",
            "Shelby: Çakmağını ver Arthur. Önce şu sigarayı yakalım.",
            "Arthur: Çıldırdın mı sen?! Beş dakikamız bile kalmadı!",
            "Shelby: Panik yapan adam, kurşundan önce kendi korkusuyla ölür.",
            "Shelby: Kaosun içinde ne kadar yavaş hareket edersen, etrafındaki herkes o kadar dehşete düşer.",
            "Arthur: Yavaşlık bizi kurtaracak mı sanıyorsun?",
            "Shelby: Fırtına dindiğinde ayakta kalan tek kişi, nefesini kontrol edebilen adamdır... Ve fırtına tam da o sigara bittiğinde yön değiştirir.",
        ],
        "scenes_en": [
            "Arthur: Everything is burning down, Tommy! Police outside, ammunition gone, what do we do?!",
            "Shelby: Hand me the match, Arthur. Let us light this cigarette first.",
            "Arthur: Have you lost your bloody mind?! We don't have five minutes left on this earth!",
            "Shelby: The man who panics perishes by his own terror long before the bullet strikes.",
            "Shelby: The slower you calibrate your movements amid catastrophe, the deeper the dread you inflict upon rivals.",
            "Arthur: You think deliberate calm is going to deliver us from slaughter?",
            "Shelby: When the tempest settles, the sole survivor is the one who masterfully paced his breath... And the hurricane shifts the second that flame dies.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Smoking in the Tempest #shorts",
                        "description": "The slower you move in chaos, the deadlier you become.\n\n#shorts #thomasshelby #peakyblinders #calm #stoic #mindset"
            }
},
        "pinned_comment_tr": "👑 Kriz anında panikleyen biri misin yoksa donup kalan mı? Yorumunu bırak.",
        "pinned_comment_en": "👑 In a sudden crisis, do you stay dead calm or react impulsively? Comment below.",
    },
    {
        "id": "shelby_silent_09",
        "series_title": "👑 Thomas Shelby | Masadaki Boş Sandalye #shorts",
        "theme_name": "Thomas Shelby - Yokluğun Yarattığı Manyetik Güç",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Michael: O barış yemeğine katılmazsan ortaklarımız bunu hakaret sayacak Tommy.",
            "Shelby: Tam olarak bunu saymalarını istiyorum Michael.",
            "Michael: Bizi yok sayabilirler, güçsüz olduğumuzu düşünebilirler!",
            "Shelby: Masada oturan on adam sadece konuşur ve birbirini tüketir.",
            "Shelby: Ama masadaki boş sandalye... O sandalyede kimin oturmadığı, odadaki herkesin aklını kemirir.",
            "Michael: Yani yokluğunla mı masayı yöneteceksin?",
            "Shelby: Görünmeyen adamın gölgesi, bağıran adamın sesinden daha uzundur... Ve onlar korktukça sandalye daha da ağırlaşır.",
        ],
        "scenes_en": [
            "Michael: If you boycott the council banquet, the associates will interpret it as an insult, Tommy.",
            "Shelby: That is precisely the psychological conclusion I designed for them, Michael.",
            "Michael: They might discard us, convince themselves we have grown powerless!",
            "Shelby: Ten men packed around mahogany merely chatter and bleed each other's stamina.",
            "Shelby: But an empty chair at the head of the table... The absence of the king gnaws at every skull in the room.",
            "Michael: You intend to govern their verdict purely through withdrawal?",
            "Shelby: The phantom of the unseen commander casts a longer shadow than any loud orator... And the more they tremble, the heavier the empty throne becomes.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Empty Chair #shorts",
                        "description": "The shadow of the unseen commander commands the entire room.\n\n#shorts #thomasshelby #peakyblinders #absence #power #sigma"
            }
},
        "pinned_comment_tr": "👑 Yokluğunla değerini artırma kuralını hiç denedin mi? Yorumunu yaz.",
        "pinned_comment_en": "👑 Have you ever commanded power simply by withdrawing your presence? Share below.",
    },
    {
        "id": "shelby_silent_10",
        "series_title": "👑 Thomas Shelby | İhanetin Saati #shorts",
        "theme_name": "Thomas Shelby - İhaneti Hesaplamak ve Ters Köşe",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Alfie: Benim sana ihanet edeceğimi biliyordun değil mi Tommy? Neden silah çekmedin?",
            "Shelby: İhanet bir sürpriz değildir Alfie. Sadece doğru zamanı bekleyen bir ticarettir.",
            "Alfie: O zaman neden bana o limanın anahtarlarını teslim ettin?",
            "Shelby: Çünkü senin açgözlülüğün, düşmanımın kurduğu tuzağın en kusursuz yemidir.",
            "Shelby: Sen beni sattığını sandığın an, aslında benim planımın son parçasını yerine koyuyordun.",
            "Alfie: Sen gerçekten şeytanın ta kendisisin Shelby.",
            "Shelby: Hayır Alfie. Ben sadece satranç tahtasında insanların zaaflarını oynatıyorum... Ve ihanet başladığı yerde kendi avcısını yutar.",
        ],
        "scenes_en": [
            "Alfie: You anticipated I would double-cross you, didn't you Tommy? Why didn't you draw first?",
            "Shelby: Treachery is never a revelation, Alfie. It is merely merchandise awaiting the optimal auction.",
            "Alfie: Then why hand me the ledger and keys to the eastern docks, eh?",
            "Shelby: Because your predictable greed is the most irresistible bait for the ambush I prepared for our rivals.",
            "Shelby: The second you presumed you had sold my empire, you slotted the final bullet into my chamber.",
            "Alfie: You are genuinely the devil reincarnate, Shelby.",
            "Shelby: No, Alfie. I merely permit human frailty to execute its natural course... And treachery invariably devours its own broker at the origin.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Hour of Betrayal #shorts",
                        "description": "Betrayal is never a surprise; it is merely commerce.\n\n#shorts #thomasshelby #alfiesolomons #peakyblinders #chess #strategy"
            }
},
        "pinned_comment_tr": "👑 Bir insanın sana ihanet edeceğini hissettiğinde ne yaparsın? Yorumlarda buluşalım.",
        "pinned_comment_en": "👑 How do you counter an associate when you anticipate their betrayal? Let's discuss.",
    },
    {
        "id": "shelby_silent_11",
        "series_title": "👑 Thomas Shelby | Gülümsemeyen Adam #shorts",
        "theme_name": "Thomas Shelby - Duygusal Soğukluk ve Kalkan",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Tatiana: Neden hiç kahkaha atmıyorsun Thomas? Kalbin tamamen taşa mı döndü?",
            "Shelby: Gülümsemek, düşmanına zırhının çatlağını göstermektir prenses.",
            "Tatiana: İnsanların duyguları vardır Thomas! Acıyı da sevinci de paylaşırlar!",
            "Shelby: Fransa siperlerinde duygularını kaybetmeyen adamlar, ilk hafta toprağın altına girdi.",
            "Shelby: Bir adam ne kadar az duygu gösterirse, karşısındaki o kadar tedirgin olur ve hata yapar.",
            "Tatiana: Hiçbir şey hissetmemek seni yormuyor mu?",
            "Shelby: Ruhunu donduran adamı dünyada hiçbir hançer yaralayamaz... Ve buz çözülmediği sürece kış asla bitmez.",
        ],
        "scenes_en": [
            "Tatiana: Why do you never laugh, Thomas? Has your heart petrified into solid granite?",
            "Shelby: Smiling offers the adversary a panoramic view of the breach in your armor, Princess.",
            "Tatiana: Living beings experience passion, Thomas! They celebrate joy and mourn defeat!",
            "Shelby: Men who clung to sentiment in the trenches of France were shoveled beneath the mud in seven days.",
            "Shelby: The less emotion a sovereign radiates, the deeper his rivals stumble into catastrophic blunders.",
            "Tatiana: Doesn't perpetual numbness exhaust your soul?",
            "Shelby: A man who chills his own spirit is impervious to every blade... And as long as the frost endures, winter never retreats.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Man Who Never Smiles #shorts",
                        "description": "Smiling offers the adversary a view of your cracks.\n\n#shorts #thomasshelby #peakyblinders #stoic #coldmind #sigma"
            }
},
        "pinned_comment_tr": "👑 Duygularını gizlemek bir güç müdür yoksa yük mü? Fikrini paylaş.",
        "pinned_comment_en": "👑 Is concealing all emotional vulnerability supreme power or a heavy prison? Share below.",
    },
    {
        "id": "shelby_silent_12",
        "series_title": "👑 Thomas Shelby | Pazarlığın 3 Saniyesi #shorts",
        "theme_name": "Thomas Shelby - Müzakere Psikolojisi ve Sessiz Baskı",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Churchill: Size tüm krallığın korumasını ve dokunulmazlığı teklif ediyorum Bay Shelby.",
            "Shelby: Teklifinizi duydum Başbakan.",
            "Churchill: Neden cevap vermiyorsunuz? Bu her erkeğin hayal edeceği bir lütuftur!",
            "Shelby: İlk teklifi hemen kabul eden adam, masanın ortağı değil dilencisidir.",
            "Shelby: Bir tekliften sonra üç saniye sessiz kalırsanız, karşı taraf kendi teklifini yetersiz bulup artırmaya başlar.",
            "Churchill: Yani imparatorluğa şantaj mı yapıyorsunuz?",
            "Shelby: Hayır efendim. Sadece vazgeçilmez olmanın bedelini tahsil ediyorum... Ve zirvedeki adam asla talep etmez, şartları fısıldar.",
        ],
        "scenes_en": [
            "Churchill: I am offering you the supreme mantle of royal immunity, Mr. Shelby.",
            "Shelby: I have heard your terms, Prime Minister.",
            "Churchill: Why do you remain mute? That charter is a miraculous prize any subject would die for!",
            "Shelby: A man who pounces on the opening bid acts as a pauper, never a sovereign partner.",
            "Shelby: Hold three seconds of absolute stillness after a proposition, and your opponent starts bidding against himself.",
            "Churchill: Are you presuming to blackmail the British Empire?",
            "Shelby: Never blackmail, sir. Merely invoicing the cost of indispensability... And the king at the summit never begs; he whispers the terms.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The 3-Second Negotiation Rule #shorts",
                        "description": "Never jump at the first offer at the table.\n\n#shorts #thomasshelby #negotiation #darkpsychology #peakyblinders #sigma"
            }
},
        "pinned_comment_tr": "👑 Pazarlık yaparken hemen kabul eder misin yoksa sessizlikle karşı tarafı terletir misin? Yorumunu bırak.",
        "pinned_comment_en": "👑 Do you accept deals swiftly or let silence force the other side to raise their bid? Comment below.",
    },
    {
        "id": "shelby_silent_13",
        "series_title": "👑 Thomas Shelby | Cenaze ve Taht #shorts",
        "theme_name": "Thomas Shelby - Kayıplardan İmparatorluk İnşa Etmek",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Arthur: Kardeşimizi kaybettik Tommy... Yüreğim parçalanıyor, artık ne için savaşıyoruz?",
            "Shelby: Bir Shelby mezarın başında gözyaşı dökmez Arthur.",
            "Arthur: Taş mısın sen Tommy?! Canımız gitti!",
            "Shelby: Ağlamak onu geri getirmeyecek. Ama o mezarın üstüne inşa edeceğimiz krallık, adını ölümsüz kılacak.",
            "Shelby: Acını gözyaşına değil, rakiplerini ezecek çelik bir iradeye dönüştür.",
            "Arthur: Bu savaş hiç bitmeyecek mi?",
            "Shelby: Savaş ancak biz durduğumuzda biter... Ve her ölüm, zirveye çıkan merdivenin yeni bir basamağıdır.",
        ],
        "scenes_en": [
            "Arthur: We lost our brother, Tommy... My chest is torn apart, what are we fighting for anymore?",
            "Shelby: A Shelby does not spill futile tears over fresh dirt, Arthur.",
            "Arthur: Are you carved of stone, Tommy?! Our own blood has been extinguished!",
            "Shelby: Weeping will not resurrect him. But the empire we erect atop this tomb will make his name immortal.",
            "Shelby: Transmute raw sorrow not into tears, but into unyielding discipline that crushes every rival.",
            "Arthur: Will this bloody war never conclude?",
            "Shelby: War ceases only when we surrender the ground... And every loss is merely another stone laid for the throne.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Tomb and the Throne #shorts",
                        "description": "Transmute your pain into ruthless empire building.\n\n#shorts #thomasshelby #peakyblinders #pain #mindset #sigma"
            }
},
        "pinned_comment_tr": "👑 Hayatta en büyük acını güce dönüştürmeyi başardın mı? Fikrini yaz.",
        "pinned_comment_en": "👑 Have you managed to turn your deepest grief into relentless fuel? Drop your story.",
    },
    {
        "id": "shelby_silent_14",
        "series_title": "👑 Thomas Shelby | Ses Tonunun Geometrisi #shorts",
        "theme_name": "Thomas Shelby - Fısıltının Tehdidi ve Ses Kontrolü",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Campbell: Karşında İngiliz İmparatorluğu'nun adaleti duruyor Shelby, sesini yükselt!",
            "Shelby: Bir adam bağırmaya başladığında müfettiş, aklının bittiğini itiraf eder.",
            "Campbell: Masaya yumruk vurarak beni yıldıramazsın!",
            "Shelby: Masaya yumruk vurmam. Sadece gözlerine bakar ve ne olacağını fısıldarım.",
            "Shelby: En derin yarayı barut değil, buz gibi bir ses tonuyla söylenen tek bir cümle açar.",
            "Campbell: Senden korktuğumu mu sanıyorsun?",
            "Shelby: Korkmasaydın sesini bu kadar yükseltmezdin... Çünkü en ölümcül darbe, en sessiz dudaklardan dökülür.",
        ],
        "scenes_en": [
            "Campbell: You stand before the crown's supreme authority, Shelby, elevate your voice!",
            "Shelby: The second a man screams, Inspector, he confesses the bankruptcy of his intellect.",
            "Campbell: Pounding the table will not intimidate the magistrate!",
            "Shelby: I never pound timber. I merely hold your gaze and whisper the inevitable verdict.",
            "Shelby: The deepest mortal laceration is never inflicted by shrapnel, but by a solitary line delivered in frozen cadence.",
            "Campbell: You believe I harbor dread for you?",
            "Shelby: If you harbored none, you wouldn't be howling like a wounded hound... For the lethal strike always slips from the quietest lips.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Geometry of Voice #shorts",
                        "description": "The second a man screams, he confesses his mind has emptied.\n\n#shorts #thomasshelby #peakyblinders #voice #alpha #dominance"
            }
},
        "pinned_comment_tr": "👑 Bir insan sesini yükselttiğinde ona fısıltıyla cevap vermeyi denedin mi? Yorumunu bırak.",
        "pinned_comment_en": "👑 Have you tried responding in a dead whisper when someone yells at you? Comment below.",
    },
    {
        "id": "shelby_silent_15",
        "series_title": "👑 Thomas Shelby | Düşmanın Dostu #shorts",
        "theme_name": "Thomas Shelby - İki Yüzlü İttifaklar ve Yakın Takip",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Polly: O adamın arkamızdan kuyu kazdığını hepimiz biliyoruz Thomas! Neden hala masamızda?",
            "Shelby: Çünkü kuyuyu ne kadar kazdığını ancak masamda otururken görebilirim Polly.",
            "Polly: Sırtımızı döndüğümüz an hançeri saplayacak!",
            "Shelby: Ona sırtımızı dönmeyeceğiz. Onu en pahalı içkilerle ve sahte dostlukla sarhoş edeceğiz.",
            "Shelby: Bir düşmanı kovarsan karanlıkta pusu kurar. Ama onu sofrana oturtursan, ne zaman vuracağını sen belirlersin.",
            "Polly: Bu çok tehlikeli bir kumar Thomas.",
            "Shelby: Bütün hayat bir kumar Polly... Ve düşmanını burnunun dibinde tutan adam, asla arkasından vurulamaz.",
        ],
        "scenes_en": [
            "Polly: We all know that man is engineering our downfall, Thomas! Why is he still drinking at our table?",
            "Shelby: Because I can only measure the depth of his shovel while he is seated in my parlor, Polly.",
            "Polly: The second we turn our shoulders, his blade will pierce our spine!",
            "Shelby: We will never turn our shoulders. We will intoxicate him with vintage scotch and fraudulent intimacy.",
            "Shelby: Banish a traitor, and he stalks you from the fog. Host him at your banquet, and you dictate the hour of his funeral.",
            "Polly: That is a razor-thin gamble, Thomas.",
            "Shelby: Existence is an unending wager, Polly... And the commander who holds his enemy under his nose is never stabbed from behind.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | Keep Enemies Closer #shorts",
                        "description": "Seat your rival at your banquet to dictate his funeral.\n\n#shorts #thomasshelby #peakyblinders #strategy #alliances #power"
            }
},
        "pinned_comment_tr": "👑 Düşmanını uzaklaştırmak mı daha akıllıcadır yoksa dibinde tutmak mı? Tartışalım.",
        "pinned_comment_en": "👑 Is it wiser to banish an adversary or keep him sitting at your table? Let's discuss.",
    },
    {
        "id": "shelby_silent_16",
        "series_title": "👑 Thomas Shelby | Saat 11 Kuralı #shorts",
        "theme_name": "Thomas Shelby - Son Dakika Baskısı ve Çelik Sinirler",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Arthur: Neden hep son saniyeye kadar bekliyoruz Tommy? Sinirlerim harap oldu!",
            "Shelby: Çünkü insan iradesi on birinci saatte çatlar Arthur.",
            "Arthur: Erken davranıp baskın yapsak işi hemen bitirebiliriz!",
            "Shelby: Erken saldıran adam acelecidir ve acele eden adam daima açık verir.",
            "Shelby: Son saniyeye kadar kımıldamadan beklersen, rakibin kendi kuruntularıyla kendini tüketir.",
            "Arthur: Ya biz de tükenirsek?",
            "Shelby: Biz tükenmeyiz çünkü biz karanlığı evimiz yaptık... Ve herkesin vazgeçtiği o son nefes, zaferin ilk nefesidir.",
        ],
        "scenes_en": [
            "Arthur: Why do we always hold until the eleventh hour, Tommy? My nerves are shredded!",
            "Shelby: Because human resolve fractures under the psychological weight of the final minute, Arthur.",
            "Arthur: If we ambush them early we can finish this bloody business by noon!",
            "Shelby: The man who lunges prematurely acts on impulse, and impulse invariably exposes a flank.",
            "Shelby: Endure without flinching until the absolute deadline, and the enemy tears himself apart with phantom panic.",
            "Arthur: What if our own marrow cracks first?",
            "Shelby: Our marrow does not buckle because we made the abyss our home... And the very breath where others surrender is the first inhalation of empire.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Eleventh Hour #shorts",
                        "description": "Human resolve fractures in the final minute.\n\n#shorts #thomasshelby #peakyblinders #nerves #timing #patience"
            }
},
        "pinned_comment_tr": "👑 En zor kararlarını son saniyeye kadar bekletir misin yoksa erkenden mi alırsın? Yorumunu bırak.",
        "pinned_comment_en": "👑 Do you hold your ground until the final second or act prematurely? Share below.",
    },
    {
        "id": "shelby_silent_17",
        "series_title": "👑 Thomas Shelby | Yalnızlığın Ağırlığı #shorts",
        "theme_name": "Thomas Shelby - Zirvenin Yalnızlığı ve Mutlak İrade",
        "category": "Sessiz Güç & Thomas Shelby",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Michael: Neden kimseye gerçekten güvenmiyorsun Tommy? Hepimiz aynı kanı taşıyoruz.",
            "Shelby: Güven, bir başkasına seni yok etme silahını hediye etmektir Michael.",
            "Michael: Hiç kimseye yaslanmadan nasıl dik durabiliyorsun?",
            "Shelby: Bir ağaç ne kadar yüksekse, rüzgarı o kadar yalnız karşılar.",
            "Shelby: Zirvede dostluk arayan adam, ilk fırtınada aşağı yuvarlanır.",
            "Michael: Peki bu yalnızlığın ödülü nedir?",
            "Shelby: Özgürlük. Çünkü hiç kimseye borcu olmayan adamın tahtı asla sarsılmaz... Ve döngü başladığı yerde yine tek başına biter.",
        ],
        "scenes_en": [
            "Michael: Why do you refuse to trust anyone with your core, Tommy? We share the identical bloodline.",
            "Shelby: Trust is simply presenting an adversary with the precise blade to sever your carotid, Michael.",
            "Michael: How can any man remain upright without ever leaning upon an ally?",
            "Shelby: The taller the cedar climbs, the more solitary it stands against the hurricane.",
            "Shelby: The ruler who craves companionship upon the summit tumbles down at the first gale.",
            "Michael: And what is the ultimate dividend of such isolation?",
            "Shelby: Sovereign liberty. For the king who owes nothing to any soul sits upon an unshakeable throne... And the loop closes right where it started, alone.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Thomas Shelby | The Weight of Solitude #shorts",
                        "description": "The taller the tree climbs, the more solitary it stands against the gale.\n\n#shorts #thomasshelby #peakyblinders #solitude #freedom #sovereign"
            }
},
        "pinned_comment_tr": "👑 Zirveye giden yol gerçekten mutlak bir yalnızlıktan mı geçer? Fikrini paylaş.",
        "pinned_comment_en": "👑 Does the road to the true peak demand absolute solitude? Drop your thoughts.",
    },
    {
        "id": "power_clash_01",
        "series_title": "👑 48 Güç Yasası | Machiavelli vs Aurelius: Korku mu Sevgi mi? #shorts",
        "theme_name": "48 Güç Yasası - Machiavelli ve Marcus Aurelius Karşılaşması",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Machiavelli: İnsanların sevgisine bel bağlamak intihardır Marcus! Korkulmak daima daha güvenlidir.",
            "Aurelius: Korkuyla inşa edilen taht Niccolò, kendi çürümesini kendi içinde taşır.",
            "Machiavelli: Sevgi çıkarlar değiştiğinde bir gecede uçar, ama ceza korkusu asla terk etmez!",
            "Aurelius: İnsanların zihnini zorbalıkla değil, erdemle ve adaletle fethedersin.",
            "Machiavelli: Erdem mezarlıkları doldurur imparator. Tarihi kazananlar yazar, haklılar değil.",
            "Aurelius: Peki ya ruhun Niccolò? Dünyayı kazanıp kendini kaybettiğinde elinde ne kalır?",
            "Machiavelli: Taht kalır Marcus... Ve işte güç bu iki kutup arasında sonsuza dek dönüp durur.",
        ],
        "scenes_en": [
            "Machiavelli: Relying on the fickle affection of men is suicide, Marcus! To be feared is infinitely safer.",
            "Aurelius: A throne erected upon terror, Niccolò, conceals the rot of its own destruction.",
            "Machiavelli: Love evaporates the instant self-interest shifts; the terror of retribution never wavers!",
            "Aurelius: You conquer the minds of subjects through unyielding virtue and justice, not brute whip.",
            "Machiavelli: Virtue fills the graveyards, Caesar. History is carved by victors, not moralists.",
            "Aurelius: And what of your sovereign soul, Niccolò? When you gain the world but forfeit yourself, what remains?",
            "Machiavelli: The crown remains, Marcus... And between these two poles, power loops for eternity.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Machiavelli vs Aurelius #shorts",
                        "description": "Is it better to be loved or feared?\n\n#shorts #48lawsofpower #machiavelli #marcusaurelius #stoic #power"
            }
},
        "pinned_comment_tr": "👑 Sence bir lider için korkulmak mı daha etkilidir yoksa sevilmek mi? Tartışalım.",
        "pinned_comment_en": "👑 For a true leader, is it superior to be feared or to be loved? Let's debate below.",
    },
    {
        "id": "power_clash_02",
        "series_title": "👑 48 Güç Yasası | Sun Tzu: Savaşmadan Kazanmak #shorts",
        "theme_name": "48 Güç Yasası - Sun Tzu ve Savaş Sanatı Zirvesi",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "General: Ordumuz düşmandan iki kat daha kalabalık usta! Şafakta neden hücum etmiyoruz?",
            "Sun Tzu: Kılıcını kınından çektiğin an, zihninin tükendiğini ilan edersin general.",
            "General: Ama kan dökmeden zafer nasıl kazanılır?",
            "Sun Tzu: En üstün savaş ustası, düşmanın stratejisini daha doğmadan felç edendir.",
            "Sun Tzu: Onların erzak yollarını kes, ittifaklarını şüpheyle zehirle ve sabırla bekle.",
            "General: Yani tek bir ok bile atmayacak mıyız?",
            "Sun Tzu: Düşman savaşa girmeden önce teslim olmuşsa kılıca ne hacet... Kılıçsız kazanılan zafer, tarihin en kalıcı zaferidir.",
        ],
        "scenes_en": [
            "General: Our legions outnumber the enemy two to one, Master! Why do we not charge at sunrise?",
            "Sun Tzu: The moment you unsheathe your blade, General, you confess the bankruptcy of your intellect.",
            "General: But how can conquest be secured without spilling crimson blood?",
            "Sun Tzu: The supreme warrior is he who shatters the adversary's strategy before it even crystallizes.",
            "Sun Tzu: Sever their grain lines, poison their alliances with quiet paranoia, and wait in stillness.",
            "General: So we release not a single arrow?",
            "Sun Tzu: When the opponent kneels before the war has even commenced, steel is redundant... Victory without bloodshed remains history's only eternal triumph.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Sun Tzu: Supreme Strategy #shorts",
                        "description": "To subdue the enemy without fighting is supreme excellence.\n\n#shorts #suntzu #artofwar #48lawsofpower #strategy #mindset"
            }
},
        "pinned_comment_tr": "👑 Hayatta kavga etmeden kazanmayı başardığın bir an oldu mu? Yorumunu bırak.",
        "pinned_comment_en": "👑 Have you ever won a massive conflict without uttering a single hostile word? Comment below.",
    },
    {
        "id": "power_clash_03",
        "series_title": "👑 48 Güç Yasası | Greene & Machiavelli: Sahte Masumiyet #shorts",
        "theme_name": "48 Güç Yasası - Yasa 21: Aptal Görünerek Akıllı Avlamak",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Greene: Güç sarayında en ölümcül maske nedir Niccolò?",
            "Machiavelli: Kendini zararsız, hatta biraz aptal göstermek Robert.",
            "Greene: İnsanlar dehalarını sergilemek için yanıp tutuşurken neden aptal görünsünler?",
            "Machiavelli: Çünkü kibirli insanlar sadece zekilerden korkar. Seni saf sanırlarsa gardlarını tamamen indirirler.",
            "Greene: Ve o an bütün kartlar senin eline geçer.",
            "Machiavelli: Aynen öyle. Kurnazlığını gizleyen adam, bütün sarayı parmağında oynatır.",
            "Greene: Ve tuzak kapandığında kurban, avcısına teşekkür ederek celladına yürür.",
        ],
        "scenes_en": [
            "Greene: What is the most devastating mask in the court of power, Niccolò?",
            "Machiavelli: To project harmlessness, even deliberate foolishness, Robert.",
            "Greene: When men burn with desire to flaunt their intellect, why choose to appear simple?",
            "Machiavelli: Because arrogant men fear only visible geniuses. Persuade them you are dull, and their defenses collapse.",
            "Greene: And in that precise second, you command every card on the table.",
            "Machiavelli: Precisely. The strategist who disguises his cunning orchestrates the entire court like marionettes.",
            "Greene: And when the snare snaps shut, the victim marches toward the scaffold thanking his executioner.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Play a Sucker to Catch a Sucker #shorts",
                        "description": "Disguise your cunning to disarm the arrogant.\n\n#shorts #48lawsofpower #machiavelli #robertgreene #strategy #sigma"
            }
},
        "pinned_comment_tr": "👑 Karşındakinin gerçek niyetini anlamak için hiç saf numarası yaptın mı? Fikrini yaz.",
        "pinned_comment_en": "👑 Have you ever played dumb to expose another person's true intentions? Drop your story.",
    },
    {
        "id": "power_clash_04",
        "series_title": "👑 48 Güç Yasası | Sokrates & Aurelius: Zihnin Kalesi #shorts",
        "theme_name": "48 Güç Yasası - Zihinsel Egemenlik ve İçsel Kale",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Sokrates: Seni zincire vurup zindana atabilirler Marcus, peki iradeni nasıl zapt edecekler?",
            "Aurelius: Bir imparatorun sarayı mermerden değil, sarsılmaz zihninden inşa edilir Sokrates.",
            "Sokrates: Dış dünyadaki kaosun, iftiraların ve kayıpların ruhuna dokunmasına nasıl engel oluyorsun?",
            "Aurelius: Olayların kendisi bizi yaralayamaz. Bizi yaralayan, o olaylara verdiğimiz hükümdür.",
            "Sokrates: Yani acıyı bile bir güç kaynağına mı dönüştürüyorsun?",
            "Aurelius: Amor Fati... Başına gelen her şeyi kaderin bir hediyesi gibi kucakla.",
            "Sokrates: Ve zihnini fetheden bir adamı hiçbir imparatorluk asla esir alamaz.",
        ],
        "scenes_en": [
            "Socrates: They can bind your flesh in chains and throw you into iron, Marcus; but how shall they shackle your resolve?",
            "Aurelius: An emperor's true palace is carved from fortress mind, not polished marble, Socrates.",
            "Socrates: How do you prevent external bedlam, betrayal, and catastrophic ruin from penetrating your soul?",
            "Aurelius: Circumstances possess zero power to wound us. What damages a man is solely the judgment he attaches to them.",
            "Socrates: You transmute even agony into fuel for sovereignty?",
            "Aurelius: Amor Fati... Embrace whatever strikes your chest as divine timber for the inner flame.",
            "Socrates: And the man who masters his own consciousness can never be enslaved by any earthly empire.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Socrates & Aurelius: The Inner Citadel #shorts",
                        "description": "Circumstances possess no power to wound your mind.\n\n#shorts #socrates #marcusaurelius #stoic #innercitadel #philosophy"
            }
},
        "pinned_comment_tr": "👑 Dış dünyadaki zorluklara karşı zihnini koruyabiliyor musun? Yorumunu bırak.",
        "pinned_comment_en": "👑 Can you keep your inner citadel intact when chaos storms outside? Comment below.",
    },
    {
        "id": "power_clash_05",
        "series_title": "👑 48 Güç Yasası | Sezar & Brutus: Yakındaki Hançer #shorts",
        "theme_name": "48 Güç Yasası - Yasa 2: Dostlara Asla Çok Güvenme",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Caesar: Sen de mi Brutus? Bütün dünya önümde diz çökerken hançer senin elinde mi?",
            "Brutus: Zirveye çıkan adam Sezar, en yakın dostunun gözlerindeki gölgeyi göremez hale gelir.",
            "Caesar: Ben seni bir evlat gibi büyüttüm, sana senatoyu verdim!",
            "Brutus: İşte hatan buydu. İnsanlara çok fazla şey verirsen, sana borçlu olduklarını değil, hakkını aldıklarını düşünürler.",
            "Caesar: Düşmanlarımdan korkmadım ama dostumun kıskançlığı beni vurdu.",
            "Brutus: Gücün acımasız kanunu budur Sezar. Sırtına saplanan hançer asla bir yabancıya ait değildir.",
            "Caesar: Ve o hançer saplandığı an, yeni bir tiran aynı döngüyü başlatmak için doğar.",
        ],
        "scenes_en": [
            "Caesar: Even you, Brutus? While legions bow across the earth, the blade rests in your hand?",
            "Brutus: The conqueror who ascends to the pinnacle, Caesar, goes blind to the lengthening shadow of his dearest confidant.",
            "Caesar: I nurtured you as my own flesh; I crowned you with the senate!",
            "Brutus: That was your fatal arrogance. Bestow too much upon a friend, and he assumes you merely returned what was his by right.",
            "Caesar: I laughed at barbarian legions, yet the envy of an intimate brought me to the marble.",
            "Brutus: That is the merciless axiom of power, Caesar. The knife between your ribs is never held by a stranger.",
            "Caesar: And the second that iron sinks in, another tyrant is born to perpetuate the identical loop.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Caesar & Brutus: The Intimate Knife #shorts",
                        "description": "The knife between your ribs is never held by a stranger.\n\n#shorts #juliuscaesar #brutus #betrayal #48lawsofpower #history"
            }
},
        "pinned_comment_tr": "👑 Hayatta seni en çok yaralayan darbe bir dosttan mı geldi yabancıdan mı? Paylaş.",
        "pinned_comment_en": "👑 Did your deepest wound arrive from an intimate companion or an outsider? Drop your take.",
    },
    {
        "id": "power_clash_06",
        "series_title": "👑 48 Güç Yasası | Greene: Efendiyi Asla Aşma #shorts",
        "theme_name": "48 Güç Yasası - Yasa 1: Efendini Asla Gölgede Bırakma",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Çırak: Robert, patronuma bütün dehamı kanıtladım, şirketi ben kurtardım! Neden beni terfi ettirmedi?",
            "Greene: Çünkü ona zekanı göstererek kurtarıcı değil, bir tehdit olduğunu ilan ettin.",
            "Çırak: Ama ben ona sadece yardım etmek istedim!",
            "Greene: Güç hiyerarşisinde üstündekilerin en büyük korkusu yetersiz görünmektir.",
            "Greene: Sen parladıkça onun gölgesi karardı ve korkusu kıskançlığa dönüştü.",
            "Çırak: O halde ne yapmalıydım?",
            "Greene: Başarını onun aklına mal etmeliydin... Kendi ışığını gizlemeyi bilmeyen çırak, asla efendi olamaz.",
        ],
        "scenes_en": [
            "Apprentice: Robert, I demonstrated absolute genius to my master; I single-handedly saved the enterprise! Why was I not elevated?",
            "Greene: Because in parading your brilliance, you revealed yourself not as a servant, but as an existential threat.",
            "Apprentice: But my sole intention was to render loyal assistance!",
            "Greene: In the hierarchy of power, the supreme terror of every monarch is to appear mediocre in public view.",
            "Greene: The brighter your radiance flared, the darker his shadow fell, transmuting his insecurity into lethal envy.",
            "Apprentice: What then was the sovereign move?",
            "Greene: You should have attributed your triumphs to his guidance... An apprentice who cannot veil his light will never inherit the throne.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Never Outshine the Master #shorts",
                        "description": "Never reveal your brilliance in a way that humiliates the master.\n\n#shorts #48lawsofpower #law1 #robertgreene #corporate #career"
            }
},
        "pinned_comment_tr": "👑 İş hayatında patronunu gölgede bırakıp bedelini ödeyen oldu mu? Yorumlarda buluşalım.",
        "pinned_comment_en": "👑 Have you ever outshined your boss and paid a harsh professional price? Share below.",
    },
    {
        "id": "power_clash_07",
        "series_title": "👑 48 Güç Yasası | Napolyon: Hata Yapan Düşman #shorts",
        "theme_name": "48 Güç Yasası - Napolyon'un Askeri Sabır Kuralı",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Subay: İmparatorum! Düşman ordusu kanadını açtı ve batağa saplandı, süvarileri derhal sürelim mi?",
            "Napoleon: Asla. Düşman kendi hatasıyla kendini yok ederken onu sakın bölmeyin.",
            "Subay: Ama zaferi hemen ilan edebiliriz efendim!",
            "Napoleon: Erken müdahale edersen hatasını fark edip toparlanabilir.",
            "Napoleon: Bırak kendi kibrine yenilsin, bırak tuzağını kendi elleriyle derinleştirsin.",
            "Subay: Sabretmek bu kadar zor olmamalıydı.",
            "Napoleon: Sabır en yıkıcı top bataryasından daha ölümcüldür... Çünkü kendi mezarını kazan bir adamı durdurmak ahmaklıktır.",
        ],
        "scenes_en": [
            "Officer: Emperor! The enemy army has overextended its flank into the marsh; shall we unleash the cavalry immediately?",
            "Napoleon: Never. When your adversary is busy annihilating himself through his own folly, do not dare interrupt him.",
            "Officer: But we can proclaim absolute victory this very hour, Sire!",
            "Napoleon: Strike prematurely, and you startle him into recognizing his catastrophe and retreating.",
            "Napoleon: Allow his hubris to blossom; let him dig his own abyss deeper with every frantic stride.",
            "Officer: Exercising such restraint feels agonizing.",
            "Napoleon: Cold patience is deadlier than a hundred siege batteries... For interrupting a man digging his own grave is sheer amateurism.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Napoleon: Never Interrupt an Error #shorts",
                        "description": "Never interrupt your enemy when he is making a mistake.\n\n#shorts #napoleon #strategy #warfare #patience #48lawsofpower"
            }
},
        "pinned_comment_tr": "👑 Bir rakibinin kendi hatasıyla batmasını izledin mi hiç? Fikrini paylaş.",
        "pinned_comment_en": "👑 Have you ever sat back and watched an opponent destroy himself with his own moves? Drop a comment.",
    },
    {
        "id": "power_clash_08",
        "series_title": "👑 48 Güç Yasası | Seneca & Aurelius: Öfkenin Aptallığı #shorts",
        "theme_name": "48 Güç Yasası - Öfke Kontrolü ve Stratejik Hakimiyet",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Seneca: Öfke Marcus, bir anlık deliliktir ve geride ömür boyu enkaz bırakır.",
            "Aurelius: Bir adam öfkesine yenildiği an, tahtının anahtarlarını düşmanına teslim eder Seneca.",
            "Seneca: İnsanlar hakarete uğradığında neden hemen kılıca sarılmak ister?",
            "Aurelius: Çünkü egoları kırılgandır ve güçlerini dışarıdan onay beklemeye bağlamışlardır.",
            "Seneca: Hakarete gülümseyebilen bir adam karşısında saldırgan aciz kalır.",
            "Aurelius: Kesinlikle. Öfkeni yönetirsen tüm savaşı yönetirsin.",
            "Seneca: Ve kendi içindeki canavarı dize getiren adam, bütün dünyayı dize getirmiş sayılır.",
        ],
        "scenes_en": [
            "Seneca: Rage, Marcus, is a temporary bout of madness that leaves behind a lifetime of irreversible ruins.",
            "Aurelius: The instant a ruler surrenders to fury, Seneca, he hands the keys to his fortress over to the rival.",
            "Seneca: Why do mortals leap to unsheathe steel the second an insult brushes their pride?",
            "Aurelius: Because their ego is paper-thin, anchoring its worth entirely to external validation.",
            "Seneca: A man who meets an insult with icy indifference disarms the aggressor on the spot.",
            "Aurelius: Precisely. Master your temper, and you orchestrate the cadence of the entire battlefield.",
            "Seneca: And he who subdues the beast within his own ribcage has already subdued the known world.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Seneca & Aurelius: Conquering Rage #shorts",
                        "description": "Master your temper to command the entire room.\n\n#shorts #seneca #marcusaurelius #stoicism #anger #emotionalcontrol"
            }
},
        "pinned_comment_tr": "👑 Öfkeni kontrol edebilmek için uyguladığın en etkili yöntem nedir? Yorumunu bırak.",
        "pinned_comment_en": "👑 What is your personal technique for subduing anger before it wrecks your plans? Comment below.",
    },
    {
        "id": "power_clash_09",
        "series_title": "👑 48 Güç Yasası | Machiavelli: Aslan ve Tilki #shorts",
        "theme_name": "48 Güç Yasası - Hükümdarın İkili Doğası",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Prens: Sadece aslan gibi cesur olmak bir devleti korumaya yetmez mi Niccolò?",
            "Machiavelli: Yetmez efendim. Aslan tuzakları tanıyamaz, tilki ise kurtları kovamaz.",
            "Prens: Yani bir hükümdar iki yüzlü mü olmalıdır?",
            "Machiavelli: Bir hükümdar şartlara göre form değiştirmelidir. Gerektiğinde aslan gibi kükremeli, gerektiğinde tilki gibi görünmez olmalıdır.",
            "Prens: Halk bundan nefret etmez mi?",
            "Machiavelli: Halk sadece sonuca bakar. Başarılı olduysan seni alkışlarlar, yenildiysen erdemini kimse hatırlamaz.",
            "Prens: O halde kural nedir?",
            "Machiavelli: Tilki gibi kokla, aslan gibi ez... Ve tuzaklar her çağda aynı kurnazlıkla yeniden kurulur.",
        ],
        "scenes_en": [
            "Prince: Is lion-hearted courage alone insufficient to defend the republic, Niccolò?",
            "Machiavelli: Hopelessly insufficient, my lord. The lion cannot discern snares, and the fox cannot banish wolves.",
            "Prince: Are you prescribing two-faced duplicity for the sovereign?",
            "Machiavelli: A sovereign must fluidly adapt his form. Roar like a lion when force demands; dissolve like a fox when traps await.",
            "Prince: Will the subjects not despise such deceit?",
            "Machiavelli: The masses look only upon the final tally. If you triumph, they adore you; if you fall, no one mourns your morality.",
            "Prince: What then is the ultimate axiom?",
            "Machiavelli: Scent the snare like a fox, crush the quarry like a lion... And traps are laid anew in every generation.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | The Fox and the Lion #shorts",
                        "description": "Be the fox to recognize traps, the lion to terrify wolves.\n\n#shorts #machiavelli #theprince #48lawsofpower #leadership #power"
            }
},
        "pinned_comment_tr": "👑 Hayatta güç mü daha önemlidir yoksa kurnazlık mı? Fikrini paylaş.",
        "pinned_comment_en": "👑 In the real world, is sheer force or calculated cunning more indispensable? Drop your vote.",
    },
    {
        "id": "power_clash_10",
        "series_title": "👑 48 Güç Yasası | Bismarck: Demir ve Kanın Gerçeği #shorts",
        "theme_name": "48 Güç Yasası - Realpolitik ve Mutlak Kararlılık",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Diplomat: Şansölye Bismarck, Avrupa'daki sınır krizini parlamentoda konuşarak ve oylamayla çözemez miyiz?",
            "Bismarck: Tarihin büyük meseleleri nutuklarla ve meclis çoğunluğuyla çözülmez bayım.",
            "Diplomat: Peki neyle çözülür efendim?",
            "Bismarck: Demirle ve kanla. Güç dengesi kağıt üzerinde değil, sahada kurulur.",
            "Diplomat: Ama bu yaklaşım çok acımasız ve diplomatik nezakete aykırı!",
            "Bismarck: Nezaket zayıfların kalkanıdır. Güçlü olan kuralları koyar, zayıf olan müzakere eder.",
            "Diplomat: Tarih sizi nasıl hatırlayacak?",
            "Bismarck: Tarih konuşanları değil, haritayı yeniden çizenleri hatırlar... Ve demir soğuduğunda gerçek ortaya çıkar.",
        ],
        "scenes_en": [
            "Diplomat: Chancellor Bismarck, can we not resolve the continental borders through parliamentary debate and democratic ballots?",
            "Bismarck: The great questions of the epoch are never settled through eloquent speeches and committee majorities, sir.",
            "Diplomat: By what instrument are they decided then, Chancellor?",
            "Bismarck: By iron and blood. The balance of power is established in blood-soaked reality, never upon parchment.",
            "Diplomat: But that doctrine violates civilized diplomatic etiquette!",
            "Bismarck: Etiquette is the fragile armor of the weak. The strong dictate terms; the vulnerable plead at negotiations.",
            "Diplomat: How will history judge your legacy?",
            "Bismarck: History remembers the architects who redraw maps, not those who chatter... And when iron cools, destiny remains.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Bismarck: Blood and Iron #shorts",
                        "description": "Great questions are settled by iron and blood, not speeches.\n\n#shorts #bismarck #realpolitik #48lawsofpower #history #power"
            }
},
        "pinned_comment_tr": "👑 Sonuç odaklı olmak mı yoksa nezaketi korumak mı daha çok kazandırır? Yorumunu bırak.",
        "pinned_comment_en": "👑 Does ruthless execution or graceful diplomacy deliver longer-lasting victory? Comment below.",
    },
    {
        "id": "power_clash_11",
        "series_title": "👑 48 Güç Yasası | Greene: Yasa 15 - Düşmanı Tamamen Ez #shorts",
        "theme_name": "48 Güç Yasası - Yasa 15: Düşmanını Tamamen Yok Et",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Komutan: Robert, rakip ordunun liderini mağlup ettik ama teslim oldu. Onu affedip barış yapalım mı?",
            "Greene: Yaralı bir yılan iyileştiğinde dişlerini ilk kime saplar sanıyorsun komutan?",
            "Komutan: Belki minnet duyar ve bize sadık kalır!",
            "Greene: Güç oyununda minnet değil, intikam ateşi büyür. Onu yarı canlı bırakırsan gücünü toplayıp geri döner.",
            "Komutan: Yani merhamet göstermek bir zaaf mıdır?",
            "Greene: Zirveye oynuyorsan evet. Ya hiç saldırmayacaksın ya da bir daha asla ayağa kalkamayacak şekilde ezeceksin.",
            "Komutan: Bu kural hiç değişmez mi?",
            "Greene: Asla. Merhamet güçsüzlerin süsüdür; zirvede ikinci raunt yoktur.",
        ],
        "scenes_en": [
            "Commander: Robert, we defeated the opposing chieftain, but he yielded his sword. Shall we extend mercy and seal an accord?",
            "Greene: When a wounded viper heals, Commander, into whose ankle does it sink its fangs first?",
            "Commander: Perhaps he will harbor gratitude and remain a loyal tributary!",
            "Greene: In the arena of dominance, gratitude withers while thirst for vengeance flourishes. Spare half his life, and he regroups in secret.",
            "Commander: So clemency is an unmitigated liability?",
            "Greene: At the summit, absolutely. Either decline battle entirely, or crush the adversary so completely he never rises again.",
            "Commander: Does this doctrine ever relent?",
            "Greene: Never. Mercy is an ornament for the powerless; the peak grants no second round.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Law 15: Crush Your Enemy Totally #shorts",
                        "description": "Leave an adversary wounded and he recovers with venom.\n\n#shorts #48lawsofpower #law15 #robertgreene #crushyourenemy #strategy"
            }
},
        "pinned_comment_tr": "👑 Düşmanına ikinci şans verip pişman olan var mı aranızda? Yorumunu bırak.",
        "pinned_comment_en": "👑 Have you ever granted mercy to a rival only to regret it later? Share your experience.",
    },
    {
        "id": "power_clash_12",
        "series_title": "👑 48 Güç Yasası | Epiktetos: Prangadaki Özgürlük #shorts",
        "theme_name": "48 Güç Yasası - Epiktetos ve İçsel Özgürlük",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Efendi: Sen benim kölemsin Epiktetos! Bu demir prangalar senin sahibinin kim olduğunu gösteriyor!",
            "Epiktetos: Bacağımı kırabilirsin efendi, fakat irademi kırmaya gücün yetmez.",
            "Efendi: Seni öldürebilirim, hayatın iki dudağımın arasında!",
            "Epiktetos: Bedenimi öldürebilirsin, ama düşüncelerime dokunamazsın. Asıl köle olan sensin.",
            "Efendi: Ben mi köleyim? Bu saray, bu altınlar benim!",
            "Epiktetos: Öfkenin, korkularının ve kibrinin kölesisin. Kendi arzularını yönetemeyen bir adam asla efendi olamaz.",
            "Efendi: Sen nasıl bu kadar sakin kalabiliyorsun?",
            "Epiktetos: Çünkü zihninin efendisi olan bir adama, dünyadaki hiçbir güç tasma takamaz.",
        ],
        "scenes_en": [
            "Master: You are my property, Epictetus! These iron shackles proclaim who commands your life!",
            "Epictetus: You may shatter my leg, Master, yet your fury cannot fracture my resolve.",
            "Master: I can order your execution; your very breath hinges upon my whim!",
            "Epictetus: You may sever my flesh, yet you cannot touch my sovereign mind. The true captive in this room is you.",
            "Master: I, a slave? This palace and these coffers belong to my name!",
            "Epictetus: You are enslaved to rage, paranoia, and vanity. A man who cannot govern his desires will never be a master.",
            "Master: Whence comes this infuriating tranquility?",
            "Epictetus: Because the soul that reigns within its own citadel can never be tethered by any collar.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Epictetus: The Unshackled Mind #shorts",
                        "description": "You may chain my flesh, but Zeus himself cannot shackle my will.\n\n#shorts #epictetus #stoicism #freedom #mindset #philosophy"
            }
},
        "pinned_comment_tr": "👑 Gerçek zenginlik maddiyat mıdır yoksa zihinsel bağımsızlık mı? Fikrini yaz.",
        "pinned_comment_en": "👑 Is true sovereignty founded upon material empire or absolute mental autonomy? Comment below.",
    },
    {
        "id": "power_clash_13",
        "series_title": "👑 48 Güç Yasası | Sun Tzu: Boşluk ve Doluluk #shorts",
        "theme_name": "48 Güç Yasası - Sun Tzu ve Zayıf Noktaya Vurma",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Öğrenci: Usta, düşmanın ana kalesi aşılmaz duvarlarla ve binlerce okçuyla korunuyor, orayı nasıl alırız?",
            "Sun Tzu: Asla oraya saldırmayarak evlat.",
            "Öğrenci: Peki zafer nasıl elde edilir?",
            "Sun Tzu: Su gibi olacaksın. Su taştan kaçar, en zayıf yarıktan aşağı akar.",
            "Sun Tzu: Düşmanın en güçlü olduğu yere değil, korumasız bıraktığı arka kapısına saldır.",
            "Öğrenci: O zaman ordusunu oraya kaydıracaktır!",
            "Sun Tzu: İşte o an ana kale boşalır. Düşmanı sürekli koştur ve kendi yorgunluğunda boğ... Boşluğu bulan su, en sert taşı bile zamanla deler.",
        ],
        "scenes_en": [
            "Student: Master, the enemy's citadel is fortified with impenetrable granite and legions of archers; how do we breach it?",
            "Sun Tzu: By never directing a single battalion toward it, my son.",
            "Student: How then is the citadel conquered?",
            "Sun Tzu: Emulate the water. Water evades the boulder and pours through the vulnerable fissure.",
            "Sun Tzu: Never crash against their armored bastion; strike the unguarded supply gate left behind.",
            "Student: But then he will shift his garrison to defend the rear!",
            "Sun Tzu: And at that exact moment the citadel empties. Keep him scrambling until he drowns in his own fatigue... Water flowing into the void carves through stone.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Sun Tzu: Empty and Full #shorts",
                        "description": "Strike what is weak, avoid what is strong.\n\n#shorts #suntzu #artofwar #strategy #48lawsofpower #water"
            }
},
        "pinned_comment_tr": "👑 Karşılaştığın zorluklara kafa kafaya çarpmak yerine arkasından dolaşmayı dener misin? Yorumunu bırak.",
        "pinned_comment_en": "👑 Do you batter head-on against problems or outmaneuver them like water? Share below.",
    },
    {
        "id": "power_clash_14",
        "series_title": "👑 48 Güç Yasası | Machiavelli: Vaatlerin Sonu #shorts",
        "theme_name": "48 Güç Yasası - Yasa 20: Kimseye Bağlanmama Sanatı",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Kral: Niccolò, komşu krallığa sadakat yemini ettik. Şartlar aleyhimize dönse bile sözümüzü tutmak zorunda değil miyiz?",
            "Machiavelli: Akıllı bir hükümdar efendim, verdiği söz kendi aleyhine döndüğünde o sözü unutur.",
            "Kral: Ama bu şerefsizlik değil midir?",
            "Machiavelli: Dünya meleklerden kurulu olsaydı evet. Fakat insanlar sana verdikleri sözleri ilk fırsatta çiğnerken, senin sadık kalman ahmaklıktır.",
            "Kral: Yani çıkarlarımız değiştiğinde antlaşmayı yırtmalı mıyız?",
            "Machiavelli: Antlaşmayı yırtma; yeni şartlara göre yeniden tanımla.",
            "Kral: Tahtın bedeli bu kadar mı?",
            "Machiavelli: Sözlerine esir olan bir kral, tahtını kendi elleriyle yıkar... Ve bağımsız kalan daima ayakta kalır.",
        ],
        "scenes_en": [
            "King: Niccolò, we swore an oath of eternal alliance to the neighboring principality. Must we honor our word when tides turn against us?",
            "Machiavelli: A prudent ruler, Sire, discards past pledges the instant honoring them threatens his sovereignty.",
            "King: Does that not constitute dishonor?",
            "Machiavelli: If the world were populated by saints, yes. But since mortals betray their pledges at first convenience, remaining naive is fatal.",
            "King: So when interests diverge, we tear up the treaty?",
            "Machiavelli: Never tear it openly; reinterpret it under the light of fresh necessity.",
            "King: Is that truly the price of the crown?",
            "Machiavelli: A king enslaved to his previous promises dismantles his own fortress... And he who remains uncommitted stands forever.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Machiavelli: Do Not Commit #shorts",
                        "description": "A prudent ruler breaks promises when they threaten his throne.\n\n#shorts #machiavelli #theprince #48lawsofpower #oaths #power"
            }
},
        "pinned_comment_tr": "👑 Sözünü tutmak her şartta erdem midir yoksa bazen stratejik intihar mıdır? Fikrini paylaş.",
        "pinned_comment_en": "👑 Is keeping an oath always noble, or can it become strategic suicide? Drop your verdict.",
    },
    {
        "id": "power_clash_15",
        "series_title": "👑 48 Güç Yasası | Nietzsche & Aurelius: Canavarlar #shorts",
        "theme_name": "48 Güç Yasası - Gücün Yozlaştırıcı Doğası",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Nietzsche: Canavarlarla savaşan bir adam Marcus, kendisinin de bir canavara dönüşmemesine dikkat etmelidir.",
            "Aurelius: En asil intikam Friedrich, düşmanının yaptığı kötülüğün aynısını yapmamaktır.",
            "Nietzsche: Ama uçuruma uzun süre bakarsan, uçurum da senin içine bakmaya başlar!",
            "Aurelius: Eğer zihninin kalesini sağlam tutarsan, uçurum sadece ayaklarının altındaki bir manzaradır.",
            "Nietzsche: Çoğu lider adaleti savunarak yola çıkar, fakat zirveye ulaştığında bir zorbaya dönüşür.",
            "Aurelius: Çünkü güç bir ayna gibidir; adamın içinde ne varsa onu büyütür.",
            "Nietzsche: Ve o aynaya bakan herkes, sonunda kendi yarattığı gölgenin içinde kaybolur.",
        ],
        "scenes_en": [
            "Nietzsche: He who fights monsters, Marcus, must take immense care lest he become a monster himself.",
            "Aurelius: The noblest vengeance, Friedrich, is to refuse to resemble the adversary who injured you.",
            "Nietzsche: But gaze too long into the abyss, and the abyss gazes straight back into you!",
            "Aurelius: Keep the citadel of your intellect fortified, and the abyss remains merely scenery beneath your boots.",
            "Nietzsche: Most monarchs embark preaching righteousness, yet upon ascending the summit, metamorphose into tyrants.",
            "Aurelius: Because power acts as a cosmic mirror; it amplifies whatever darkness already lurked within.",
            "Nietzsche: And every soul staring into that glass eventually dissolves inside the shadow he conjured.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Nietzsche & Aurelius: Fighting Monsters #shorts",
                        "description": "Beware becoming the monster you set out to destroy.\n\n#shorts #nietzsche #marcusaurelius #philosophy #monsters #power"
            }
},
        "pinned_comment_tr": "👑 İnsan güç kazandıkça değişir mi yoksa sadece gerçek yüzü mü ortaya çıkar? Yorumunu bırak.",
        "pinned_comment_en": "👑 Does power corrupt an individual or merely unveil who they were all along? Comment below.",
    },
    {
        "id": "power_clash_16",
        "series_title": "👑 48 Güç Yasası | Greene: Yasa 33 - Zayıf Nokta #shorts",
        "theme_name": "48 Güç Yasası - Yasa 33: Herkesin Zayıf Vidasını Keşfet",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Casus: Robert, hedefimizdeki general çelikten bir iradeye sahip. Ne rüşvet alıyor ne de korkutulabiliyor!",
            "Greene: Her insanın gizli bir zayıf vidası vardır evlat. İstisnasız her insanın.",
            "Casus: Ama bu adamda hiçbir zaaf bulamadık!",
            "Greene: O zaman yanlış yere bakıyorsun. Belki bir çocuğuna olan zaafı, belki gizli bir kibri, belki de alkışlanma arzusu vardır.",
            "Casus: O vidayı bulduğumuzda ne olacak?",
            "Greene: O vidayı tek bir parmağınla çevirdiğinde, o yenilmez kale bir iskambil evi gibi yıkılır.",
            "Casus: İnsan doğası gerçekten bu kadar kırılgan mı?",
            "Greene: Evet... Ve en sağlam zırhın bile altında, dokunulmayı bekleyen bir yara vardır.",
        ],
        "scenes_en": [
            "Spy: Robert, the general we target commands an iron constitution. He refuses bribes and laughs at intimidation!",
            "Greene: Every living soul conceals a hidden thumbscrew, my friend. Without a single exception in history.",
            "Spy: Yet we uncovered zero vices in his armor!",
            "Greene: Then you are scrutinizing the wrong horizon. Perhaps his obsession with lineage, an unchecked ego, or thirst for acclaim.",
            "Spy: And once that thumbscrew is located?",
            "Greene: Turn it with a single fingertip, and that impenetrable fortress collapses like a house of cards.",
            "Spy: Is human nature truly that fragile?",
            "Greene: Invariably... And beneath the heaviest armor waits a raw nerve begging to be pressed.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | Law 33: Find the Thumbscrew #shorts",
                        "description": "Every man has a weakness waiting to be pressed.\n\n#shorts #48lawsofpower #law33 #robertgreene #weakness #psychology"
            }
},
        "pinned_comment_tr": "👑 Sence bir insanın en zor gizlediği zayıf noktası nedir? Kibir mi, sevgi mi, korku mu? Fikrini yaz.",
        "pinned_comment_en": "👑 What is the hardest weakness for a human to conceal? Pride, affection, or fear? Share below.",
    },
    {
        "id": "power_clash_17",
        "series_title": "👑 48 Güç Yasası | Don Corleone: Reddedilemeyecek Teklif #shorts",
        "theme_name": "48 Güç Yasası - Don Corleone ve Nihai Güç Sanatı",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Michael: Baba, o adam senin sunduğun anlaşmayı imzalamazsa ne yapacağız?",
            "Vito: Ona reddedemeyeceği bir teklif yapacağım Michael.",
            "Michael: Onu tehdit mi edeceksin?",
            "Vito: Bir erkeğe asla öfkeyle tehdit savurma Michael. Tehdit zayıfların bağırmasıdır.",
            "Vito: Ona masada iki seçenek sunacaksın: Ya imzası o kağıtta olacak, ya da beyni.",
            "Michael: Peki bu adalete sığar mı baba?",
            "Vito: Aileni korumak dünyadaki tek gerçek adalettir... Ve gerçek güç, asla sesini yükseltmeden son sözü söyler.",
        ],
        "scenes_en": [
            "Michael: Father, what transpires if that man refuses to endorse the contract you presented?",
            "Vito: I'm going to make him an offer he cannot refuse, Michael.",
            "Michael: You intend to issue threats?",
            "Vito: Never broadcast threats in anger, Michael. Empty threats are merely the shrill cry of the impotent.",
            "Vito: Present him with two crystal clear outcomes: Either his signature occupies the parchment, or his brains do.",
            "Michael: Does that satisfy justice, Father?",
            "Vito: Protecting your family is the only pristine justice on this earth... And supreme power delivers the final verdict without ever raising its voice.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 48 Laws of Power | An Offer He Cannot Refuse #shorts",
                        "description": "True power delivers the final verdict without ever shouting.\n\n#shorts #thegodfather #vitocorleone #48lawsofpower #power #mafia"
            }
},
        "pinned_comment_tr": "👑 Gerçek otorite sesini yükseltmeden nasıl hissettirilir? Yorumunu bırak.",
        "pinned_comment_en": "👑 How does true sovereign authority make itself felt without shouting? Comment below.",
    },
    {
        "id": "cosmic_abyss_01",
        "series_title": "👑 Kozmik Dehşet | Miller Gezegeni: 1 Saat = 7 Yıl #shorts",
        "theme_name": "Kozmik Dehşet - Miller Gezegeni ve Zaman Kayması",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Cooper: Brand, ufuktaki o yükselen şeyler dağ değil... Onlar su duvarı!",
            "Brand: Cooper, gemiye hemen dönmeliyiz! Burada kaybettiğimiz her saniye Dünya'da günlere mal oluyor!",
            "Cooper: Bir saat burada geçirmek yedi yılımızı çaldı Brand!",
            "Brand: Gargantua'nın yerçekimi o kadar devasa ki zamanın dokusunu çiğneyip büküyor.",
            "Cooper: Çocuklarım yaşlanıyor Brand... Ben burada nefes alırken onlar mezara giriyor!",
            "Brand: Evren insan kalbini umursamaz Cooper. Fizik kuralları merhamet tanımaz.",
            "Cooper: Zaman en acımasız avcıdır... Ve arkana baktığında geride hiçbir şey kalmamıştır.",
        ],
        "scenes_en": [
            "Cooper: Brand, those silhouettes rising on the horizon aren't mountains... They're walls of water!",
            "Brand: Cooper, get to the lander now! Every second squandered down here costs human years on Earth!",
            "Cooper: One single hour on this reef just incinerated seven years of our lives, Brand!",
            "Brand: Gargantua's gravitational maw is so gargantuan it physically shreds and stretches the fabric of time.",
            "Cooper: My children are aging into ghosts, Brand... While I take three breaths, their youth is extinguished!",
            "Brand: The cosmos harbors no sympathy for the human heart, Cooper. The laws of relativity know no remorse.",
            "Cooper: Time is the supreme cosmic predator... And when you turn back, only dust remains.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Miller's Planet: 1 Hour = 7 Years #shorts",
                        "description": "One hour on Miller's planet erases seven years of human life.\n\n#shorts #interstellar #blackhole #timedilation #cosmic #space"
            }
},
        "pinned_comment_tr": "👑 Dünyada 7 yıl kaybetme pahasına o gezegene iner miydin? Yorumunu bırak.",
        "pinned_comment_en": "👑 Would you sacrifice seven earthly years for a single hour exploring that ocean? Comment below.",
    },
    {
        "id": "cosmic_abyss_02",
        "series_title": "👑 Kozmik Dehşet | Gargantua'nın Olay Ufku #shorts",
        "theme_name": "Kozmik Dehşet - Olay Ufku ve Tekillik",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Cooper: TARS, karadeliğin olay ufkunu geçtik mi?",
            "TARS: Evet Cooper. Geri dönüşü olmayan son sınırı çoktan aştık.",
            "Cooper: Neden hiçbir şey görmüyorum? Neden her yer zifiri karanlık?",
            "TARS: Çünkü ışık bile bu yerçekiminden kaçamıyor. İleriye doğru baktığında aslında zamanın sonunu görüyorsun.",
            "Cooper: Zamanın sonu mu? Buradan bir çıkış yok mu?",
            "TARS: Bütün uzaysal yollar artık tekilliğe çıkıyor Cooper. Kaçamazsın.",
            "Cooper: Ve işte bu yüzden kara delik bir ölüm değil, sonsuz bir başlangıç kapısıdır.",
        ],
        "scenes_en": [
            "Cooper: TARS, did we cross the event horizon of the black hole?",
            "TARS: Confirmed, Cooper. We passed the point of absolute no return thirty seconds ago.",
            "Cooper: Why is the canopy pitch black? Where did the starlight go?",
            "TARS: Because photons cannot escape this gravity well. When you peer forward, you are literally witnessing the termination of time.",
            "Cooper: The termination of time? Is there no vector out of here?",
            "TARS: All spatial trajectories now lead irrevocably to the singularity, Cooper. Flight is geometrically impossible.",
            "Cooper: And that is why the abyss is not annihilation, but the gateway to an unending loop.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Crossing the Event Horizon #shorts",
                        "description": "Beyond the event horizon, all paths lead to the singularity.\n\n#shorts #interstellar #blackhole #gargantua #eventhorizon #physics"
            }
},
        "pinned_comment_tr": "👑 Bir kara deliğin içine düşsen neyle karşılaşacağını düşünüyorsun? Fikrini paylaş.",
        "pinned_comment_en": "👑 What do you believe waits beyond the singularity inside a supermassive black hole? Drop your theory.",
    },
    {
        "id": "cosmic_abyss_03",
        "series_title": "👑 Kozmik Dehşet | Oppenheimer: Dünyayı Yakan Denklem #shorts",
        "theme_name": "Kozmik Dehşet - Oppenheimer ve Kıyamet Ateşi",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Oppenheimer: Albert, yaptığımız son termonükleer hesaplamalar atmosferin alev alma ihtimali olduğunu gösteriyor.",
            "Einstein: O ihtimal sıfır değilse Robert, o testi derhal durdurmalısınız.",
            "Oppenheimer: İhtimal milyonda bir Albert... Neredeyse sıfır.",
            "Einstein: Neredeyse sıfır demek, insanlığın kaderiyle rulet oynamak demektir Robert.",
            "Oppenheimer: Düğmeye bastığımızda gökyüzü mor renge döndü ve bir fısıltı duydum: 'Ben artık dünyaları yok eden ölümün ta kendisiyim.'",
            "Einstein: Kendi yarattığınız canavarı asla bir daha kafese koyamayacaksınız.",
            "Oppenheimer: Ve insanlık o gün, kendi kıyametini ayakta alkışladı.",
        ],
        "scenes_en": [
            "Oppenheimer: Albert, our final thermonuclear equations indicate an atmospheric chain reaction cannot be mathematically ruled out.",
            "Einstein: If that probability is anything above zero, Robert, abort the Trinity test immediately.",
            "Oppenheimer: The odds are near one in a million, Albert... Virtually zero.",
            "Einstein: Virtually zero means wagering the entire biosphere on a loaded roulette wheel, Robert.",
            "Oppenheimer: When we triggered the blast, the heavens curdled into violet fire and I whispered: 'Now I am become Death, the destroyer of worlds.'",
            "Einstein: The genie unbottled on that desert sand will never re-enter its lamp.",
            "Oppenheimer: And on that morning, humanity gave a standing ovation to its own extinction.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Oppenheimer's Atmospheric Fire #shorts",
                        "description": "Now I am become Death, the destroyer of worlds.\n\n#shorts #oppenheimer #einstein #atomic #trinitytest #history"
            }
},
        "pinned_comment_tr": "👑 Sence insanlık kendi yarattığı teknolojinin kurbanı mı olacak? Yorumunu bırak.",
        "pinned_comment_en": "👑 Will humanity inevitably be consumed by the very technological fire it ignited? Share below.",
    },
    {
        "id": "cosmic_abyss_04",
        "series_title": "👑 Kozmik Dehşet | Fermi Paradoksu: Neden Herkes Sessiz? #shorts",
        "theme_name": "Kozmik Dehşet - Fermi Paradoksu ve Karanlık Orman",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Fermi: Evrende milyarlarca galaksi ve trilyonlarca yaşanabilir gezegen varken, neden tek bir uzaylı sesi bile duymuyoruz Carl?",
            "Sagan: Çünkü Enrico, evren sessiz ve huzurlu bir bahçe değil; karanlık bir av ormanıdır.",
            "Fermi: Karanlık orman mı? Ne demek bu?",
            "Sagan: Ormandaki her gelişmiş medeniyet bir avcıdır. Ve avcılar karanlıkta nefesini tutarak bekler.",
            "Fermi: Neden hiçbiri varlığını belli etmiyor?",
            "Sagan: Çünkü varlığını haykıran ilk aptal, ormandaki diğer avcıların ilk hedefi olur.",
            "Fermi: Ve tam da bu yüzden, o ölümcül sessizlik asla bozulmaz.",
        ],
        "scenes_en": [
            "Fermi: With billions of galaxies and trillions of habitable exoplanets, why is the radio sky dead silent, Carl?",
            "Sagan: Because Enrico, the cosmos is not a tranquil park; it is a pitch-black primeval forest.",
            "Fermi: A dark forest? What does that imply?",
            "Sagan: Every advanced civilization lurking out there is an armed predator holding its breath in the shadows.",
            "Fermi: Why does none of them transmit their coordinates?",
            "Sagan: Because the first naive fool who ignites a flare in the dark becomes the immediate target for extinction.",
            "Fermi: And precisely for that reason, the terrifying cosmic silence remains unbroken.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | The Fermi Paradox: Dark Forest #shorts",
                        "description": "Why is the universe utterly silent? The Dark Forest theory.\n\n#shorts #fermiparadox #darkforest #space #aliens #carlsagan"
            }
},
        "pinned_comment_tr": "👑 Sence uzaylılar neden sessiz? Karanlık orman teorisi doğru olabilir mi? Yorumunu bırak.",
        "pinned_comment_en": "👑 Why is the cosmos utterly silent? Could the Dark Forest hypothesis be the truth? Comment below.",
    },
    {
        "id": "cosmic_abyss_05",
        "series_title": "👑 Kozmik Dehşet | Tesseract: Zamanın 5. Boyutu #shorts",
        "theme_name": "Kozmik Dehşet - Tesseract ve Beşinci Boyut",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Cooper: Burası neresi TARS?! Kızımın çocukluk odasını her saniyesiyle sonsuz bir koridor gibi görüyorum!",
            "TARS: Beşinci boyuttayız Cooper. Onlar bizim için zamanı fiziksel bir mekana dönüştürdü.",
            "Cooper: Kitaplığın arkasından ona sesleniyorum ama duymuyor!",
            "TARS: Üç boyutlu ses dalgaları zaman bariyerini aşamaz Cooper.",
            "Cooper: Peki ya yerçekimi TARS? Yerçekimi geçebilir mi?",
            "TARS: Evet Cooper. Yerçekimi boyutları aşabilen tek dildir.",
            "Cooper: Ve geçmiş, şimdi ve gelecek... Hepsi aynı anda nefes alıyor.",
        ],
        "scenes_en": [
            "Cooper: Where are we, TARS?! I see my daughter's childhood bedroom stretching into an infinite lattice across every second!",
            "TARS: We are inside a five-dimensional tesseract, Cooper. They constructed time as physical architecture for your perception.",
            "Cooper: I'm pounding against the bookcase screaming her name, but she cannot hear me!",
            "TARS: Three-dimensional acoustic waves cannot penetrate the temporal bulkhead, Cooper.",
            "Cooper: But what about gravitation, TARS? Can gravity breach the barrier?",
            "TARS: Affirmative, Cooper. Gravity is the solitary force capable of traversing dimensions.",
            "Cooper: And past, present, and future... They are all breathing at the identical instant.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | The 5th Dimension Tesseract #shorts",
                        "description": "Time constructed as physical architecture.\n\n#shorts #interstellar #tesseract #5thdimension #physics #time"
            }
},
        "pinned_comment_tr": "👑 Zaman bir çizgi mi yoksa aynı anda var olan bir oda mı? Fikrini paylaş.",
        "pinned_comment_en": "👑 Is time a forward river or a physical chamber where every second coexists? Share below.",
    },
    {
        "id": "cosmic_abyss_06",
        "series_title": "👑 Kozmik Dehşet | Büyük Donma: Evrenin Son Saniyesi #shorts",
        "theme_name": "Kozmik Dehşet - Evrenin Isı Ölümü ve Entropi",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Feynman: Stephen, yüz trilyon yıl sonra uzaya baksaydık ne görürdük?",
            "Hawking: Tek bir ışık zerresi bile göremezdik Richard.",
            "Feynman: Yıldızlar nereye gitti? Galaksiler nasıl yok oldu?",
            "Hawking: Bütün hidrojen tükendi, son beyaz cüce söndü ve kara delikler bile buharlaştı.",
            "Feynman: Geriye ne kaldı peki?",
            "Hawking: Mutlak sıfır noktasına yakın, sonsuz bir karanlık ve birbirine asla değmeyen parçacıklar.",
            "Feynman: Işık söndüğünde karanlık yeniden tek ve mutlak hükümdar olur.",
        ],
        "scenes_en": [
            "Feynman: Stephen, if an observer looked out into the void one hundred trillion years from tonight, what would greet him?",
            "Hawking: Not a single photon of starlight, Richard.",
            "Feynman: Where did the furnaces go? How did the galaxies perish?",
            "Hawking: All stellar nurseries exhausted their fuel, the final white dwarfs chilled to black, and even gargantuan black holes evaporated.",
            "Feynman: What remains in the expanse then?",
            "Hawking: An endless sea hovering near absolute zero, where orphan particles drift never colliding for eternity.",
            "Feynman: When the light is extinguished, the darkness resumes its solitary, sovereign throne.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | The Big Freeze: Heat Death #shorts",
                        "description": "When the final stars fade into absolute zero.\n\n#shorts #heatdeath #bigfreeze #stephenhawking #cosmology #astronomy"
            }
},
        "pinned_comment_tr": "👑 Evrenin nihai sonunun mutlak bir soğukluk olması seni korkutuyor mu? Yorumunu bırak.",
        "pinned_comment_en": "👑 Does the inescapable thermodynamic heat death of the universe evoke dread? Comment below.",
    },
    {
        "id": "cosmic_abyss_07",
        "series_title": "👑 Kozmik Dehşet | Mann Gezegeni: İhanetin Buzulu #shorts",
        "theme_name": "Kozmik Dehşet - Mann Gezegeni ve Hayatta Kalma İçgüdüsü",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Cooper: Dr. Mann, gezegen yaşanabilir değil! Neden bize sahte veri gönderdin?!",
            "Mann: Çünkü ölmek istemedim Cooper! Bu buz cehenneminde yapayalnız çürümek istemedim!",
            "Cooper: Sen insanlığın en iyisi olarak seçilmiştin Mann!",
            "Mann: Ölüm anında insanlığın idealleri buharlaşır Cooper. Geriye sadece vahşi hayatta kalma arzusu kalır.",
            "Cooper: Kaskımın camını kırdın... Beni burada ölüme mi terk edeceksin?",
            "Mann: Kişisel algılama Cooper. Evrenin kanunu budur: Ya sen ölürsün ya da ben.",
            "Cooper: Ve uzayın derinliğindeki en korkunç canavar, insanın kendi bencilliğidir.",
        ],
        "scenes_en": [
            "Cooper: Dr. Mann, this world has no surface! Why did you falsify the atmospheric telemetry?!",
            "Mann: Because I refused to perish alone, Cooper! I couldn't endure slowly decaying in this frozen tomb!",
            "Cooper: You were chosen as the paragon of humanity, Mann!",
            "Mann: At the threshold of death, altruistic philosophies dissolve, Cooper. Nothing remains except primal biological survival.",
            "Cooper: You cracked my faceplate... You intend to suffocate me upon these ammonia clouds?",
            "Mann: Do not make this personal, Cooper. The iron law of natural selection commands: either you perish, or I survive.",
            "Cooper: And the most horrifying beast in the interstellar abyss is the human instinct itself.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Mann's Treachery on the Ice #shorts",
                        "description": "At the brink of death, morality dissolves into raw survival.\n\n#shorts #interstellar #drmann #survival #betrayal #space"
            }
},
        "pinned_comment_tr": "👑 Ölümle burun buruna geldiğinde bir insan en asil ideallerini çiğner mi? Fikrini yaz.",
        "pinned_comment_en": "👑 When confronted with certain doom, does the human psyche abandon all noble vows? Share below.",
    },
    {
        "id": "cosmic_abyss_08",
        "series_title": "👑 Kozmik Dehşet | Nötron Yıldızı: Bir Kaşık Kıyamet #shorts",
        "theme_name": "Kozmik Dehşet - Nötron Yıldızı ve Ezici Yoğunluk",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Feynman: Robert, bir nötron yıldızının kalbinden tek bir çay kaşığı madde alsak ne olurdu?",
            "Oppenheimer: O tek kaşık madde Richard, dünyadaki tüm dağların toplamından daha ağır olurdu.",
            "Feynman: Peki onu masanın üstüne koysak ne olurdu?",
            "Oppenheimer: Masayı, binayı ve kıtayı delip geçer, Dünya'nın tam merkezine kadar durmadan düşerdi.",
            "Feynman: Atomların arasındaki boşluk tamamen çöktüğünde maddenin aldığı dehşet verici form bu mu?",
            "Oppenheimer: Evet. Atomlar ezildiğinde madde kendi canavarına dönüşür.",
            "Feynman: Kainatın kudreti karşısında insan kibirinden sadece bir toz zerresi kalır.",
        ],
        "scenes_en": [
            "Feynman: Robert, what would manifest if we extracted a single teaspoon of matter from the core of a neutron star?",
            "Oppenheimer: That solitary spoonful, Richard, would outweigh all the mountain ranges on planet Earth combined.",
            "Feynman: What would happen if we set it upon a laboratory bench?",
            "Oppenheimer: It would tear through the mahogany, shear through the mantle, and plummet straight to the iron core of Earth.",
            "Oppenheimer: When atomic electron clouds are crushed into naked neutrons, matter reveals its terrifying density.",
            "Feynman: That is the cosmic crucible when geometry collapses entirely.",
            "Oppenheimer: And before such titanic mechanics, human vanity dissolves into insignificant vapor.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | A Teaspoon of a Neutron Star #shorts",
                        "description": "One teaspoon of neutron matter outweighs all mountains on Earth.\n\n#shorts #neutronstar #astrophysics #oppenheimer #feynman #space"
            }
},
        "pinned_comment_tr": "👑 Evrenin bu akıl almaz yoğunluğu hakkında ne düşünüyorsun? Yorumunu bırak.",
        "pinned_comment_en": "👑 Does the sheer density of collapsed neutron cores blow your mind? Comment below.",
    },
    {
        "id": "cosmic_abyss_09",
        "series_title": "👑 Kozmik Dehşet | Schrödinger'in Kedisi: Gerçeklik İllüzyonu #shorts",
        "theme_name": "Kozmik Dehşet - Kuantum Belirsizliği ve Gözlemci Etkisi",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Einstein: Tanrı evrenle kumar oynamaz Niels! Ay'a biz bakmasak da Ay oradadır!",
            "Bohr: Albert, gerçeğin biz ona bakmadan önce var olduğunu nereden biliyorsun?",
            "Einstein: O kutudaki kedi ya ölüdür ya da canlı! İkisi birden olamaz!",
            "Bohr: Sen kutuyu açıp bakana kadar evren karar vermez Albert. Her iki olasılık da aynı anda gerçektir.",
            "Einstein: Yani gerçeklik sadece bizim gözlemimize mi bağlı?",
            "Bohr: Gerçeklik katı bir mermer değil, bilinçle çöken bir olasılık dalgasıdır.",
            "Einstein: Ve gözünü kapattığın an, evren yeniden olasılıklar okyanusuna döner.",
        ],
        "scenes_en": [
            "Einstein: God does not play dice with the cosmos, Niels! The moon remains in the heavens even when no eye observes it!",
            "Bohr: Albert, how can you verify reality exists independently prior to measurement?",
            "Einstein: The feline locked in that chamber is either dead or alive! It cannot occupy both states simultaneously!",
            "Bohr: Until you lift the lid, the quantum wave function refuses to collapse, Albert. Both realities coexist.",
            "Einstein: You argue that objective reality hinges entirely upon an observer?",
            "Bohr: Reality is not carved granite; it is an ocean of probability materialized only through awareness.",
            "Einstein: And the second you shutter your eyes, the universe reverts into an unmapped sea of potential.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Schrödinger's Cat: The Illusion of Reality #shorts",
                        "description": "Does reality exist before you look at it?\n\n#shorts #quantumphysics #schrodingerscat #einstein #nielsbohr #simulation"
            }
},
        "pinned_comment_tr": "👑 Sence gerçeklik biz ona baktığımız için mi var yoksa bağımsız mı? Yorumunu bırak.",
        "pinned_comment_en": "👑 Does objective physical reality exist independent of conscious observers? Drop your thoughts.",
    },
    {
        "id": "cosmic_abyss_10",
        "series_title": "👑 Kozmik Dehşet | Solucan Deliği: Uzayın Katlanması #shorts",
        "theme_name": "Kozmik Dehşet - Solucan Deliği ve Uzay Zaman Bükülmesi",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Cooper: Romilly, neden bu solucan deliği uzayda iki boyutlu bir delik değil de küre şeklinde görünüyor?",
            "Romilly: Bir kağıdı ikiye katladığını düşün Cooper. İki boyutlu bir düzlemdeki delik dairedir.",
            "Cooper: Ama biz üç boyutlu bir uzaydayız.",
            "Romilly: Aynen öyle. Üç boyutlu uzayda bir deliğin her yönden girişi vardır; yani o delik bir küredir.",
            "Cooper: O kürenin içine girdiğimizde bizi ne bekliyor?",
            "Romilly: Milyarlarca ışık yılı uzaktaki başka bir galaksi... Ve zamanın büküldüğü o kapıdan geçtiğinde, evrenin diğer ucundaki kaderin seni karşılar.",
        ],
        "scenes_en": [
            "Cooper: Romilly, why does this wormhole present itself as a radiant sphere rather than a flat circular aperture?",
            "Romilly: Imagine folding a sheet of parchment in half, Cooper. A puncture in two-dimensional space is a circle.",
            "Cooper: But we exist within three-dimensional space.",
            "Romilly: Precisely. A puncture in three-dimensional space possesses entrances from every vector; hence it manifests as a sphere.",
            "Cooper: What greets our vessel once we cross the sphere's event boundary?",
            "Romilly: Another galaxy separated by billions of light-years... And stepping through that warp, destiny awaits on the opposite shore.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | The Geometry of Wormholes #shorts",
                        "description": "Why a 3D wormhole is an impossible sphere.\n\n#shorts #interstellar #wormhole #space #einsteinrosen #astrophysics"
            }
},
        "pinned_comment_tr": "👑 Bir solucan deliğinden geçip bilinmeyen bir galaksiye atlamaya cesaret eder miydin? Yorumunu bırak.",
        "pinned_comment_en": "👑 Would you volunteer to plunge your vessel through a wormhole into an uncharted galaxy? Share below.",
    },
    {
        "id": "cosmic_abyss_11",
        "series_title": "👑 Kozmik Dehşet | Hawking Işıması: Karadeliklerin Ölümü #shorts",
        "theme_name": "Kozmik Dehşet - Hawking Işıması ve Karadeliğin Buharlaşması",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "TARS: Stephen, karadelikler ışığı bile yutuyorsa evrende sonsuza kadar nasıl hayatta kalamazlar?",
            "Hawking: Kuantum mekaniği TARS. Hiçbir canavar mutlak değildir.",
            "TARS: Olay ufkunda ne oluyor peki?",
            "Hawking: Olay ufkunda sanal parçacık çiftleri oluşur. Biri deliğe düşerken diğeri uzaya kaçar.",
            "TARS: Bu kaçış karadeliğe ne yapar?",
            "Hawking: Karadelik trilyonlarca yıl boyunca kütlesini kaybeder ve sonunda sessiz bir patlamayla buharlaşır.",
            "Hawking: Hiçbir karanlık sonsuza kadar hüküm süremez; ışık her zaman geri döner.",
        ],
        "scenes_en": [
            "TARS: Stephen, if black holes trap even light within their horizon, how can they ever face death?",
            "Hawking: Quantum mechanics, TARS. No cosmic leviathan is exempt from thermodynamic decay.",
            "TARS: What physical transaction occurs upon the event horizon?",
            "Hawking: Virtual particle pairs materialize from the quantum vacuum; one plunges inwards while the sibling escapes into the void.",
            "TARS: What consequence does this escape inflict upon the singularity?",
            "Hawking: The black hole bleeds mass across trillions of millennia until it evaporates in a final whisper.",
            "Hawking: No darkness reigns for eternity; light invariably reclaims the stage.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Hawking Radiation: Death of Black Holes #shorts",
                        "description": "Even the hungriest black holes evaporate in silence.\n\n#shorts #stephenhawking #blackholes #hawkingradiation #physics #space"
            }
},
        "pinned_comment_tr": "👑 Karadeliklerin bile ölümlü olması sana ne hissettiriyor? Fikrini yaz.",
        "pinned_comment_en": "👑 Does knowing that even supermassive black holes evaporate alter your perspective? Comment below.",
    },
    {
        "id": "cosmic_abyss_12",
        "series_title": "👑 Kozmik Dehşet | Büyük Çöküş: Zamanın Tersine Akması #shorts",
        "theme_name": "Kozmik Dehşet - Büyük Çöküş ve Zaman Oku",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Einstein: Stephen, evrenin genişlemesi bir gün durup kütleçekimi her şeyi geri çekmeye başlarsa ne olur?",
            "Hawking: Büyük Çöküş başlar Albert. Bütün galaksiler tek bir noktaya doğru tersine akar.",
            "Einstein: Peki zamanın yönü değişir mi?",
            "Hawking: Termodinamiğin ikinci yasası tersine dönerse evet. Kırılan bardaklar birleşir, ölen yıldızlar yeniden parlar.",
            "Einstein: Bu akıl almaz bir paradoks!",
            "Hawking: Evren için başlangıç ile son aynı şeydir Albert.",
            "Hawking: Ve son, aslında en başından beri başlangıcın ta kendisidir.",
        ],
        "scenes_en": [
            "Einstein: Stephen, if cosmic expansion halts and gravity pulls the fabric back inwards, what transpires?",
            "Hawking: The Big Crunch initiates, Albert. Every cluster reverses its trajectory toward a primordial singularity.",
            "Einstein: Does the thermodynamic arrow of time invert as well?",
            "Hawking: If entropy reverses its vector, yes. Shattered crystal reassembles, and extinguished stars ignite from their ashes.",
            "Einstein: That is a staggering cosmological paradox!",
            "Hawking: For the cosmos, the omega and alpha are identical points on the loop, Albert.",
            "Hawking: And the ending has always been the beginning itself.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | The Big Crunch: Reversing Time #shorts",
                        "description": "Can the arrow of time flow backwards into the singularity?\n\n#shorts #bigcrunch #timeparadox #einstein #hawking #entropy"
            }
},
        "pinned_comment_tr": "👑 Zaman geriye aksaydı hayatında ilk neyi değiştirmek isterdin? Yorumunu bırak.",
        "pinned_comment_en": "👑 If time inverted and flowed backwards, what would be the first error you'd undo? Share below.",
    },
    {
        "id": "cosmic_abyss_13",
        "series_title": "👑 Kozmik Dehşet | Süpernova: Yıldızın Son Çığlığı #shorts",
        "theme_name": "Kozmik Dehşet - Betelgeuse ve Süpernova Patlaması",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Brand: Carl, gökyüzündeki dev bir yıldız patladığında bunu dünyadan ne zaman görürüz?",
            "Sagan: Işık bize ulaşana kadar yüzlerce, belki binlerce yıl boyunca habersiz yaşarız Brand.",
            "Brand: Yani şu an baktığımız yıldız çoktan ölmüş olabilir mi?",
            "Sagan: Betelgeuse belki de Fatih Sultan Mehmet zamanında patladı ama ışığı hala yolda.",
            "Brand: Gökyüzü bir mezarlık gibi...",
            "Sagan: Kesinlikle. Baktığımız her yıldız, geçmişin milyonlarca yıl önceki hayaletidir.",
            "Sagan: Ve insanlık bu hayaletlerin ışığında geleceğini aramaya devam eder.",
        ],
        "scenes_en": [
            "Brand: Carl, when a supergiant star detonates in the galaxy, when do our eyes register the flare?",
            "Sagan: We remain completely oblivious for centuries, perhaps millennia, until the photon front arrives, Brand.",
            "Brand: So the brilliant beacon we observe tonight might already be annihilated?",
            "Sagan: Betelgeuse may have detonated during the Middle Ages, yet its radiant death knell still journeys across the void.",
            "Brand: The night sky is essentially a cosmic necropolis...",
            "Sagan: Precisely. Every pinpoint of illumination is a spectral echo dispatched epochs ago.",
            "Sagan: And beneath the glow of these phantoms, humanity navigates its destiny.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Supernova Ghost Light #shorts",
                        "description": "Every star you gaze upon is a phantom from millions of years ago.\n\n#shorts #supernova #betelgeuse #astronomy #carlsagan #starlight"
            }
},
        "pinned_comment_tr": "👑 Gökyüzüne baktığında ölü yıldızların ışığını izlediğini bilmek nasıl hissettiriyor? Yorumunu bırak.",
        "pinned_comment_en": "👑 Knowing you are gazing upon light emitted by long-dead stars, how does that make you feel? Share below.",
    },
    {
        "id": "cosmic_abyss_14",
        "series_title": "👑 Kozmik Dehşet | Planck Sınırı: Evrenin Pikselleri #shorts",
        "theme_name": "Kozmik Dehşet - Planck Uzunluğu ve Simülasyon Sınırı",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Feynman: Niels, uzayı ve mesafeyi sonsuza kadar daha küçük parçalara bölebilir miyiz?",
            "Bohr: Hayır Richard. Planck sınırına geldiğinde fizik kuralları parçalanır.",
            "Feynman: Ne demek parçalanır? O sınırın altında ne var?",
            "Bohr: Uzay ve zaman artık pürüzsüz bir örtü değildir. Tıpkı bir ekranın pikselleri gibi taneciklere ayrılır.",
            "Feynman: Yani evrenin bir çözünürlük sınırı mı var?",
            "Bohr: Evet Richard. Ve o sınırın ötesinde mesafe diye bir kavram yoktur.",
            "Bohr: Ve o sınırın ötesinde hiçbir şey yoktur; sadece simülasyonun saf matematiği.",
        ],
        "scenes_en": [
            "Feynman: Niels, can we partition spatial intervals into infinitesimal increments indefinitely?",
            "Bohr: Negative, Richard. Upon striking the Planck length, continuum physics disintegrates.",
            "Feynman: Disintegrates in what manner? What lies beneath that scale?",
            "Bohr: Spacetime ceases to be a smooth geometric manifold. It atomizes into granular foam, like screen pixels.",
            "Feynman: Are you suggesting the cosmos possesses a maximum rendering resolution?",
            "Bohr: Exactly, Richard. Beneath that metric threshold, the very concept of distance dissolves.",
            "Bohr: And beyond that horizon lies nothing material; merely the pure code of the simulation.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | The Planck Boundary: Universe's Pixels #shorts",
                        "description": "The universe has a maximum resolution limit.\n\n#shorts #plancklength #quantumphysics #simulationtheory #reality #space"
            }
},
        "pinned_comment_tr": "👑 Sence evren gerçekten devasa bir simülasyon kodu mu? Fikrini paylaş.",
        "pinned_comment_en": "👑 Do you think the universe is fundamentally rendered code inside a simulation? Comment below.",
    },
    {
        "id": "cosmic_abyss_15",
        "series_title": "👑 Kozmik Dehşet | Karanlık Madde: Görünmez İskelet #shorts",
        "theme_name": "Kozmik Dehşet - Karanlık Madde ve Karanlık Enerji",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Cooper: Brand, görebildiğimiz trilyonlarca yıldız ve galaksi evrenin sadece yüzde beşi mi?",
            "Brand: Evet Cooper. Kalan yüzde doksan beş karanlık madde ve karanlık enerjidir.",
            "Cooper: Dokunamadığımız, göremediğimiz bir şey evreni nasıl bir arada tutuyor?",
            "Brand: Karanlık madde galaksileri dağılmaktan koruyan görünmez bir kütleçekim iskeletidir.",
            "Cooper: Yani biz aslında evrenin içinde kör gibi mi yaşıyoruz?",
            "Brand: Biz sadece okyanusun yüzeyindeki köpüğü görebilen varlıklarız Cooper.",
            "Brand: Ve o devasa karanlık okyanus, sessizce genişlemeye devam ediyor.",
        ],
        "scenes_en": [
            "Cooper: Brand, are you saying the entirety of observable stars and nebulae constitutes merely five percent of cosmic mass?",
            "Brand: Confirmed, Cooper. The remaining ninety-five percent consists of dark matter and dark energy.",
            "Cooper: How does an invisible, untraceable substance hold the spinning spirals together?",
            "Brand: Dark matter provides the unseen gravitational scaffolding preventing galaxies from flying apart into dust.",
            "Cooper: So humanity wanders through this cosmos effectively blind?",
            "Brand: We are merely creatures skimming the froth atop an abyss, Cooper.",
            "Brand: And that gargantuan dark ocean expands in silence forever.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Dark Matter: The Phantom Scaffold #shorts",
                        "description": "95% of the cosmos is completely invisible to human eyes.\n\n#shorts #darkmatter #darkenergy #interstellar #astronomy #cosmos"
            }
},
        "pinned_comment_tr": "👑 Evrenin yüzde doksan beşini göremiyor oluşumuz sana ne düşündürüyor? Yorumunu bırak.",
        "pinned_comment_en": "👑 How does it feel knowing humanity can only observe five percent of reality? Comment below.",
    },
    {
        "id": "cosmic_abyss_16",
        "series_title": "👑 Kozmik Dehşet | Murph'ün Saati: Boyutlararası Dil #shorts",
        "theme_name": "Kozmik Dehşet - Kuantum İletişimi ve Sevginin Boyutu",
        "category": "Kozmik Dehşet & Evrenin Gizemleri",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Murph: Baba... Saatimin saniye kolu rastgele titreşmiyor. Bu mors alfabesi!",
            "Cooper: TARS, verileri yerçekimi dalgalarıyla saniye koluna aktar!",
            "Murph: Karadeliğin kuantum verilerini bana gönderiyorsun... Bizi kurtarmak için!",
            "Cooper: Yerçekimi boyutları aşabilen tek kuvvettir Murph. Sana geri döneceğime söz vermiştim.",
            "Murph: Yıllardır beni terk ettiğini sanıyordum baba...",
            "Cooper: Zaman ne kadar acımasız olursa olsun kızım, gerçek bağlar asla kopmaz.",
            "Murph: Ve döngü tamamlandığında, insanlık yıldızlara doğru yeniden yürür.",
        ],
        "scenes_en": [
            "Murph: Dad... The second hand on my wristwatch isn't malfunctioning. It's pulsating Morse code!",
            "Cooper: TARS, transmit the quantum singularity parameters across the gravitational conduit!",
            "Murph: You're dictating the black hole equations from across spacetime... To deliver humanity!",
            "Cooper: Gravity is the solitary force capable of breaching dimensional barriers, Murph. I promised I would return.",
            "Murph: For decades I wept believing you had abandoned us, Dad...",
            "Cooper: No matter how mercilessly time dilates, my girl, unbreakable bonds never fracture.",
            "Murph: And when the loop closes, humanity marches toward the stars once more.",
        ],
        "localizations": {
            "en": {
                        "title": "👑 Cosmic Horror | Murph's Watch: Interdimensional Morse #shorts",
                        "description": "Gravity is the only force capable of crossing dimensions.\n\n#shorts #interstellar #murph #cooper #gravity #morsecode #love"
            }
},
        "pinned_comment_tr": "👑 Interstellar'ın bu sahnesinde gözleri dolmayan var mı? Yorumunu bırak.",
        "pinned_comment_en": "👑 Did this scene in Interstellar send chills down your spine? Drop your reaction below.",
    },
]

def generate_mega_campaign_catalog(total_target: int = 584) -> list[dict]:
    """Generates 500+ rich procedural episodic campaigns with full dual-language support."""
    catalog = []
    
    # 0. Hollywood Multi-Voice Dialogue Masterpieces
    hollywood_dialogues = [
        {
            "id": "breaking_bad_danger",
            "series_title": "👑 Walter White | Tehlikenin Kendisi Benim #shorts",
            "theme_name": "Walter White - Masumiyetin Ölümü ve Heisenberg",
            "category": "Karanlık Psikoloji & Güç Dönüşümü",
            "source_clip": "breaking_bad_master.mp4",
            "scenes": [
                "Skyler: Walter, lütfen dur... Tehlikedeyiz, kapıyı biri çalabilir!",
                "Walter: Tehlikede olduğumu mu sanıyorsun Skyler? Asıl tehlike benim.",
                "Birisi kapısını açıp vurulduğunda, vurulan adam ben değilim.",
                "O kapıyı çalan adam benim.",
                "Zayıf bir adam köşeye sıkıştığında ya pes eder ya da bir canavara dönüşür.",
                "Ve bir kez o sınırı geçtiğinizde, geriye asla dönemezsiniz.",
                "İşte saygının korkuyla kazanıldığı o acımasız kural...",
            ],
            "scenes_en": [
                "Skyler: Walter, please stop... Someone could knock on that door, we are in danger!",
                "Walter: Who are you talking to right now? You think I am in danger, Skyler?",
                "Walter: I am not in danger, Skyler. I am the danger.",
                "A guy opens his door and gets shot, and you think that of me? No.",
                "Walter: I am the one who knocks.",
                "When pushed into a corner, a weak man either surrenders or becomes the monster.",
                "And that is the brutal law of power...",
            ],
            "localizations": {
                "en": {
                    "title": "👑 Walter White | I Am The Danger #shorts",
                    "description": "I am not in danger, Skyler. I am the danger.\n\nDark psychology and power transformation.\n\n#shorts #breakingbad #walterwhite #heisenberg #sigma"
                }
            },
            "pinned_comment_tr": "👑 Sence Walter White Heisenberg'e dönüştüğünde haklı mıydı? Yorumunu bırak.",
            "pinned_comment_en": "👑 Was Walter White justified in becoming Heisenberg? Comment below.",
        },
        {
            "id": "interstellar_abyss",
            "series_title": "👑 Interstellar | Zamanın Acımasız Paradoksu #shorts",
            "theme_name": "Interstellar - Karadelik ve Zaman Paradoksu",
            "category": "Kozmik Dehşet & Evrenin Gizemleri",
            "source_clip": "interstellar_master.mp4",
            "scenes": [
                "Brand: Cooper, o gezegendeki her saniye dünyada günlere mal olacak!",
                "Gargantua karadeliğinin olay ufkuna yaklaştığınızda zaman parçalanır.",
                "Sizin orada geçirdiğiniz sadece bir saat, dünyada yedi yıla eşittir.",
                "Aileniz yaşlanıp ölürken, siz sadece tek bir nefes almış olursunuz.",
                "Yerçekimi o kadar acımasızdır ki, uzay ve zaman birbirine düğümlenir.",
                "İşte evrenin insan aklını aşan en korkunç doğa kanunu...",
            ],
            "scenes_en": [
                "Brand: Cooper, every second on that planet costs years on Earth!",
                "Near the event horizon of Gargantua, time itself is ripped apart.",
                "One single hour down there equals seven full years on Earth.",
                "While your children grow old and perish, you have barely taken a single breath.",
                "Gravity is so immense that space and time are twisted into an abyss.",
                "And that is the most terrifying natural law of the cosmos...",
            ],
            "localizations": {
                "en": {
                    "title": "👑 Interstellar | The Brutal Time Paradox #shorts",
                    "description": "One hour on Miller's planet is seven years on Earth.\n\nCosmic mysteries and time dilation.\n\n#shorts #interstellar #blackhole #physics #space"
                }
            },
            "pinned_comment_tr": "👑 Böyle bir gezegene gitmeyi göze alabilir miydin? Yorumunu bırak.",
            "pinned_comment_en": "👑 Would you ever dare visit a planet with that level of time dilation? Comment below.",
        },
        {
            "id": "shelby_power",
            "series_title": "👑 Thomas Shelby | Sessiz Gücün ve Saygının Bedeli #shorts",
            "theme_name": "Thomas Shelby - Sessiz Gücün ve Saygının Bedeli",
            "category": "Karanlık Psikoloji & Güç Yasaları",
            "source_clip": "peaky_master.mp4",
            "scenes": [
                "Kadın: Neden kimseye bir şey anlatmıyorsun Thomas?",
                "Thomas: Bir odadaki en zayıf insan, her şeye hemen tepki veren insandır.",
                "Saygı bağırmakla değil, gözünü bile kırpmadan sessiz kalabilmekle kazanılır.",
                "Asla öfkeni düşmanına gösterme, çünkü öfke açık bir zayıflıktır.",
                "Planını kimseye anlatma, sadece sonucun yarattığı fırtınayı izlet.",
                "İşte bu yüzden zeki bir adamın asla yapmayacağı hata...",
            ],
            "scenes_en": [
                "Woman: Why do you never explain yourself, Thomas?",
                "Thomas: The weakest man in the room is always the one who reacts first.",
                "Respect is never won through shouting, but through icy, unyielding silence.",
                "Never display anger to your adversary; rage is an open declaration of vulnerability.",
                "Keep your strategy buried in silence, and let only the hurricane of results speak.",
                "And that is the fatal mistake a sovereign mind never commits...",
            ],
            "localizations": {
                "en": {
                    "title": "👑 Thomas Shelby | The Cost of Silent Power #shorts",
                    "description": "The weakest man in the room is always the one who reacts first.\n\n#shorts #thomasshelby #peakyblinders #darkpsychology #sigma"
                }
            },
            "pinned_comment_tr": "👑 Thomas Shelby'nin en sevdiğin kuralı hangisi? Yorumunu bırak.",
            "pinned_comment_en": "👑 What is your favorite rule from Thomas Shelby? Drop your thoughts.",
        }
    ]
    catalog.extend(hollywood_dialogues)
    catalog.extend(WINNING_NICHES_CLIFFHANGER_EPISODES)

    # 1. Add All Primary 48 Laws of Power (Laws 1 to 20 detailed + 21 to 48 procedural)
    for law in THE_48_LAWS_OF_POWER:
        catalog.append({
            "id": f"power_law_{law['num']:02d}",
            "series_title": f"👑 48 Güç Yasası | Bölüm {law['num']}: {law['title_tr']}",
            "theme_name": f"48 Güç Yasası - Bölüm {law['num']}: {law['title_tr']}",
            "category": "Gücün 48 Yasası Serisi",
            "source_clip": law["clip"],
            "scenes": [
                law["hook_tr"],
                *law["body_tr"].split(". "),
                law["punch_tr"],
            ],
            "scenes_en": [
                law["hook_en"],
                *law["body_en"].split(". "),
                law["punch_en"],
            ],
            "localizations": {
                "en": {
                    "title": f"👑 48 Laws of Power | Law {law['num']}: {law['title_en']} #shorts",
                    "description": f"Law {law['num']}: {law['title_en']}.\n\nRobert Greene's 48 Laws of Power and Dark Psychology.\n\n#shorts #48lawsofpower #powerlaws #sigma #darkpsychology #thomasshelby #stoic"
                },
                "es": {
                    "title": f"👑 Las 48 Leyes del Poder | Ley {law['num']}: {law['title_en']} #shorts",
                    "description": f"Ley {law['num']}: {law['title_en']}.\n\n#shorts #48leyesdelpoder #psicologiaoscura #poder"
                },
                "de": {
                    "title": f"👑 48 Gesetze der Macht | Gesetz {law['num']}: {law['title_en']} #shorts",
                    "description": f"Gesetz {law['num']}: {law['title_en']}.\n\n#shorts #48gesetzedermacht #macht #erfolg"
                }
            },
            "pinned_comment_tr": f"👑 Sence bu yasa günlük hayatta en çok nerede çiğneniyor? Yorumlarda tartışalım.",
            "pinned_comment_en": f"👑 In your experience, where is this law violated the most? Let's discuss below.",
        })

    # Procedural generation for remaining Laws 21 to 48
    laws_21_48_titles = [
        ("Kendini Akıllı Sananları Avlamak İçin Aptal Görün", "Play a Sucker to Catch a Sucker"),
        ("Teslim Olma Taktığını Kullan: Zayıflığı Güce Dönüştür", "Use the Surrender Tactic"),
        ("Güçlerini Tek Bir Noktada Yoğunlaştır", "Concentrate Your Forces"),
        ("Mükemmel Bir Saray Mensubu Ol", "Play the Perfect Courtier"),
        ("Kendini Yeniden Yarat", "Re-Create Yourself"),
        ("Ellerini Asla Kirletme", "Keep Your Hands Clean"),
        ("İnsanların İnanma İhtiyacını Kullanarak Müritler Yarat", "Play on People's Need to Believe"),
        ("Cesaretle Harekete Geç", "Enter Action with Boldness"),
        ("Sonuna Kadar Plan Yap", "Plan All the Way to the End"),
        ("Başarılarını Zahmetsiz Göster", "Make Your Accomplishments Seem Effortless"),
        ("Seçenekleri Sen Belirle: Diğerlerinin Senin Kartlarınla Oynamasını Sağla", "Control the Options"),
        ("İnsanların Hayal Gücüne Oyna", "Play to People's Fantasies"),
        ("Herkesin Zayıf Noktasını Keşfet", "Discover Each Man's Thumbscrew"),
        ("Kraliyet Tavrı Takın: Kral Gibi Muamele Görmek İçin Kral Gibi Davran", "Be Royal in Your Own Fashion"),
        ("Zamanlama Sanatında Ustalaş", "Master the Art of Timing"),
        ("Sahip Olamadığın Şeyleri Küçümse", "Disdain Things You Cannot Have"),
        ("Büyüleyici Gösteriler Yarat", "Create Compelling Spectacles"),
        ("Başkaları Gibi Düşün, Kendin Gibi Yaşa", "Think as You Like but Behave Like Others"),
        ("Balık Avlamak İçin Suları Bulandır", "Stir Up Waters to Catch Fish"),
        ("Bedava Öğle Yemeğini Küçümse", "Despise the Free Lunch"),
        ("Büyük Bir Adamın Ayakkabılarını Doldurmaya Çalışma", "Avoid Stepping into a Great Man's Shoes"),
        ("Çobanı Vur, Koyunlar Dağılsın", "Strike the Shepherd and the Sheep Will Scatter"),
        ("İnsanların Kalplerini ve Zihinlerini Fethet", "Work on the Hearts and Minds of Others"),
        ("Ayna Etkisiyle Düşmanını Çıldırt", "Disarm and Infuriate with the Mirror Effect"),
        ("Değişimin Gerekliliğini Savun, Ama Çok Fazla Reform Yapma", "Preach the Need for Change, but Never Reform Too Much"),
        ("Asla Fazla Kusursuz Görünme", "Never Appear Too Perfect"),
        ("Hedeflediğin Sınırı Aşma; Zaferde Ne Zaman Duracağını Bil", "Do Not Go Past the Mark You Aimed For"),
        ("Şekilsiz Ol: Suyun Formunu Al", "Assume Formlessness"),
    ]
    for idx, (t_tr, t_en) in enumerate(laws_21_48_titles, 21):
        catalog.append({
            "id": f"power_law_{idx:02d}",
            "series_title": f"👑 48 Güç Yasası | Bölüm {idx}: {t_tr}",
            "theme_name": f"48 Güç Yasası - Bölüm {idx}: {t_tr}",
            "category": "Gücün 48 Yasası Serisi",
            "source_clip": "peaky_master.mp4" if idx % 2 == 0 else "godfather_master.mp4",
            "scenes": [
                f"Güç oyununun {idx}. kuralı, acımasız bir gerçeği gözler önüne serer.",
                f"{t_tr}.",
                "Zirveye oynayan bir adam duygularıyla değil, stratejik aklıyla hareket eder.",
                "Eğer bu yasayı görmezden gelirsen, rakiplerinin kurduğu tuzağa kendi ayaklarınla yürürsün.",
                "Tarihteki tüm büyük liderler bu kuralı hayatlarıyla ödeyerek öğrendiler.",
                f"Ve işte bu yüzden gücün {idx}. kuralı asla unutulmaz...",
            ],
            "scenes_en": [
                f"Law {idx} in the brutal theater of power exposes an undeniable truth.",
                f"{t_en}.",
                "A mind aiming for sovereign heights moves with calculated geometry, never impulse.",
                "Disregard this law, and you walk straight into traps laid out by invisible rivals.",
                "Every great empire in history carved this exact truth into stone.",
                f"And that is why the unyielding {idx}th law of power remains absolute...",
            ],
            "localizations": {
                "en": {
                    "title": f"👑 48 Laws of Power | Law {idx}: {t_en} #shorts",
                    "description": f"Law {idx}: {t_en}.\n\nRobert Greene's 48 Laws of Power.\n\n#shorts #48lawsofpower #sigma #mindset #powerlaws"
                }
            },
            "pinned_comment_tr": f"👑 Bu yasayı kendi hayatında tecrübe ettin mi? Fikrini yaz.",
            "pinned_comment_en": f"👑 Have you ever seen this law in action? Drop your thoughts.",
        })

    # 2. Expand all 7 thematic niches procedurally to reach 500+ items
    remaining_needed = total_target - len(catalog)
    items_per_niche = (remaining_needed // len(NICHES)) + 2

    for niche in NICHES:
        for ep_num in range(1, items_per_niche + 1):
            topic_idx = (ep_num - 1) % len(niche["topics"])
            top_tr, top_en, hook_tr, hook_en = niche["topics"][topic_idx]
            ep_id = f"{niche['id']}_ep_{ep_num:03d}"
            
            catalog.append({
                "id": ep_id,
                "series_title": f"👑 {niche['name_tr']} | Bölüm {ep_num}: {top_tr}",
                "theme_name": f"{niche['name_tr']} - {top_tr} (Bölüm {ep_num})",
                "category": niche["name_tr"],
                "source_clip": niche["clip"],
                "scenes": [
                    hook_tr,
                    "Zayıf bir zihin dış dünyanın kölesi olurken, güçlü adam kendi kurallarını yazar.",
                    "Başkalarının ne düşündüğünü umursamayı bıraktığın an, gerçek özgürlük başlar.",
                    "Sessiz kal, enerjini koru ve sadece hedefine odaklan.",
                    "Unutma: kalabalıklar alkışlar, ama tarihi sadece yalnız ve kararlı zihinler yazar.",
                    "Ve işte zirvedeki adamların asla taviz vermediği o altın kural...",
                ],
                "scenes_en": [
                    hook_en,
                    "While a fragile mind remains slave to circumstance, a sovereign soul commands reality.",
                    "The exact second you cease seeking external validation, absolute mastery begins.",
                    "Stay disciplined, protect your bandwidth, and advance in absolute silence.",
                    "Remember: crowds offer empty applause, but destiny is forged by solitary resolve.",
                    "And that is why the true masters of the peak never compromise...",
                ],
                "localizations": {
                    "en": {
                        "title": f"👑 {niche['name_en']} | Part {ep_num}: {top_en} #shorts",
                        "description": f"{top_en}.\n\nMastery, stoic discipline, and psychological sovereignty.\n\n#shorts #{niche['id']} #sigma #stoic #mindset #motivation #power"
                    },
                    "es": {
                        "title": f"👑 {niche['name_en']} | Parte {ep_num}: {top_en} #shorts",
                        "description": f"{top_en}.\n\n#shorts #psicologia #exito #mente"
                    },
                    "de": {
                        "title": f"👑 {niche['name_en']} | Teil {ep_num}: {top_en} #shorts",
                        "description": f"{top_en}.\n\n#shorts #psychologie #erfolg #macht"
                    }
                },
                "pinned_comment_tr": f"👑 Sence zirvede kalmanın en zor kuralı nedir? Yorumunu bırak.",
                "pinned_comment_en": f"👑 What is the hardest discipline required to stay on top? Comment below.",
            })

    return catalog


# Pre-built singleton
MEGA_CATALOG = generate_mega_campaign_catalog(584)


def get_campaign_by_id(camp_id: str) -> dict | None:
    return next((c for c in MEGA_CATALOG if c["id"] == camp_id), None)


def get_random_campaign(category: str | None = None) -> dict:
    if category:
        filtered = [c for c in MEGA_CATALOG if category.lower() in c.get("category", "").lower()]
        if filtered:
            return random.choice(filtered)
    return random.choice(MEGA_CATALOG)


if __name__ == "__main__":
    print(f"✅ MEGA CONTENT VAULT YÜKLENDİ: Toplam {len(MEGA_CATALOG)} Adet Benzersiz Bölüm!")
    sample = random.choice(MEGA_CATALOG)
    print(f"Örnek Kampanya: {sample['id']} -> {sample['theme_name']}")
    print(f"TR Hook: {sample['scenes'][0]}")
    print(f"EN Hook: {sample['scenes_en'][0]}")
