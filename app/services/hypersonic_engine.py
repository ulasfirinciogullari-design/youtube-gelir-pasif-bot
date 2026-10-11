"""
HYPERSONIC HYPE-BREAKER ENGINE (15-18s Infinite Seamless Loop Architecture).
Calibrated specifically to break YouTube Shorts' 1.2k Seed Trap and push
retention to >130% through grammatical infinite loops and controversy engineering.
"""

from pathlib import Path
import random

HYPERSONIC_MASTER_CAMPAIGNS = [
    {
        "id": "shelby_deadly_silence",
        "theme_name": "Thomas Shelby - Odanın En Tehlikeli Adamı",
        "series_title": "👑 Thomas Shelby: Odanın En Tehlikeli Adamı #shorts",
        "category": "Güç Yasaları & Soğukkanlılık",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Bir ortamdaki en tehlikeli insanın kim olduğunu biliyor musun?",
            "Herkes sesini yükseltirken o sadece dinler.",
            "Zayıf insanlar öfkesini kusar, güçlü olanlar ise hamlesini planlar.",
            "İşte bu yüzden asla unutma: Bir masada herkes konuşurken tek bir adam susuyorsa..."
        ],
        "scenes_en": [
            "Do you know who the most dangerous person in the room is?",
            "While everyone raises their voice, he only listens.",
            "Weak men vent anger, strong men calculate moves.",
            "That's why when everyone is talking and one man stays silent..."
        ],
        "localizations": {
            "en": {
                "title": "👑 The Most Dangerous Man in The Room | Thomas Shelby #shorts",
                "description": (
                    "When everyone is talking, fear the silent man.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #thomasshelby #peakyblinders #sigmamindset #darkpsychology"
                )
            }
        },
        "pinned_comment_tr": "👑 Zayıf bir insan öfkelenir, güçlü bir insan susar ve sonucunu bekler. Sen hangisisin? Dürüstçe yaz.",
        "pinned_comment_en": "👑 A weak man yells, a strong man stays calm and wins. Which one are you? Comment below."
    },
    {
        "id": "power_law_1_shadow",
        "theme_name": "48 Güç Yasası - En Ölümcül Kariyer Hatası",
        "series_title": "👑 48 Güç Yasası: Asla Yapmaman Gereken Ölümcül Hata #shorts",
        "category": "Karanlık Psikoloji & Güç",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "İnsanların kariyerini bitiren en ölümcül hata nedir bilir misin?",
            "Liderinden daha zeki görünmeye çalışmak.",
            "Kendi yeteneğini gizle, zaferi daima ona bırak.",
            "Çünkü güç sarhoşları gölgede kalmayı asla affetmez, işte bu yüzden..."
        ],
        "scenes_en": [
            "What is the single most fatal mistake that ends careers?",
            "Trying to appear smarter than your master.",
            "Conceal your brilliance and let them take the glory.",
            "Because those in power never forgive being overshadowed, which is why..."
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | The Most Fatal Career Mistake #shorts",
                "description": (
                    "Never outshine the master. Conceal your brilliance.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #48lawsofpower #robertgreene #powerlaws #psychology"
                )
            }
        },
        "pinned_comment_tr": "👑 Liderinden daha akıllı olduğunu ona hissettirmek cesaret midir yoksa aptallık mı? Fikrini yaz.",
        "pinned_comment_en": "👑 Is outshining your boss bravery or pure stupidity? Drop your opinion below."
    },
    {
        "id": "walter_pure_danger",
        "theme_name": "Walter White - Canavarın Doğuşu",
        "series_title": "👑 Walter White: Tehlikenin Ta Kendisi #shorts",
        "category": "Karanlık Zihin & Dönüşüm",
        "source_clip": "breaking_bad_master.mp4",
        "scenes": [
            "Korkak bir adam ne zaman en büyük canavara dönüşür bilir misin?",
            "Kaybedecek hiçbir şeyi kalmadığı an.",
            "Artık o tehlikede değildir, tehlikenin ta kendisidir.",
            "Ve dünya ona diz çöktürmeye çalıştığında tek bir gerçeği anlar..."
        ],
        "scenes_en": [
            "When does a harmless man become the ultimate monster?",
            "The moment he has nothing left to lose.",
            "He is no longer in danger, he is the danger.",
            "And when the world pushes him to the edge, you will realize..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Walter White | I Am The Danger #shorts",
                "description": (
                    "I am not in danger, Skyler. I am the danger.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #walterwhite #breakingbad #heisenberg #darkmindset"
                )
            }
        },
        "pinned_comment_tr": "👑 Walter White ailesini mi korudu yoksa içindeki canavarı mı serbest bıraktı? Yorumlarda tartışalım.",
        "pinned_comment_en": "👑 Did Walter protect his family or unleash his ego? Let's settle this."
    },
    {
        "id": "interstellar_time_horror",
        "theme_name": "Interstellar - Evrenin En Korkunç Silahı",
        "series_title": "👑 Interstellar: Evrenin En Korkunç Silahı #shorts",
        "category": "Kozmik Dehşet & Bilim Kurgu",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Evrendeki en acımasız silahın ne olduğunu biliyor musun?",
            "Miller gezegenindeki bir saat, dünyada yedi yıla eşittir.",
            "Sen tek bir nefes alırken sevdiklerin yaşlanıp ölür.",
            "Yerçekimi zamanı büker ve sana asla geri kazanamayacağın tek gerçeği hatırlatır..."
        ],
        "scenes_en": [
            "Do you know what the most terrifying weapon in the universe is?",
            "One hour on Miller's planet is seven years on Earth.",
            "While you take a breath, everyone you love ages and dies.",
            "Gravity bends reality, proving that the only unstoppable enemy is..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Interstellar | The Most Terrifying Force in The Universe #shorts",
                "description": (
                    "Every hour here is seven years on Earth. Time is the enemy.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #interstellar #blackhole #timetravel #christophernolan"
                )
            }
        },
        "pinned_comment_tr": "👑 Zamanın telafisi olmayan tek servet olduğunu kaç yaşında fark ettin? Yorumunu bırak.",
        "pinned_comment_en": "👑 Would you sacrifice 7 Earth years for 1 hour of cosmic knowledge? Comment below."
    },
    {
        "id": "marcus_steel_will",
        "theme_name": "Marcus Aurelius - Yenilmez Zihin",
        "series_title": "👑 Marcus Aurelius: Zihinsel Yenilmezlik Yasası #shorts",
        "category": "Stoacılık & Çelik İrade",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Yenilmez bir zihne sahip olmanın tek sırrı nedir bilir misin?",
            "Kontrol edemediğin hiçbir şey için öfkelenmemek.",
            "Başkalarının ne düşündüğü senin gerçeğin olamaz.",
            "Çünkü seni senden başka hiçbir güç yıkamaz, işte bu yüzden..."
        ],
        "scenes_en": [
            "What is the single secret to an invincible mind?",
            "Never getting angry at things outside your control.",
            "Other people's opinions cannot touch your soul.",
            "Because nothing on earth can defeat you except yourself, which is why..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Marcus Aurelius | The Invincible Stoic Mind #shorts",
                "description": (
                    "Master your mind or become a slave to your emotions.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #stoicism #marcusaurelius #philosophy #ironwill"
                )
            }
        },
        "pinned_comment_tr": "👑 Hakaret eden birine karşı susup gülümsemek güç müdür yoksa zayıflık mı? Dürüstçe yaz.",
        "pinned_comment_en": "👑 Is smiling in silence when insulted true strength or weakness? Answer below."
    },
    {
        "id": "godfather_kiss_of_death",
        "theme_name": "The Godfather - İhanetin Tek Cezası",
        "series_title": "👑 The Godfather: Asla İkinci Bir Şans Verme #shorts",
        "category": "Mafya & Soğukkanlılık",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Bu hayatta asla affetmemen gereken tek şey nedir bilir misin?",
            "Sana bir kez ihanet eden birinin samimiyeti.",
            "Yılan derisini değiştirir ama zehrini asla unutmaz.",
            "Ve sırtından vurulan bir adam arkasına tekrar dönerse tek bir gerçeği anlar..."
        ],
        "scenes_en": [
            "What is the single thing you must never forgive in this life?",
            "The fake loyalty of someone who betrayed you once.",
            "A snake sheds its skin but never loses its poison.",
            "And if a betrayed man turns his back again, he will finally learn..."
        ],
        "localizations": {
            "en": {
                "title": "👑 The Godfather | Never Give A Second Chance #shorts",
                "description": (
                    "Never hate your enemies. It affects your judgment.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #thegodfather #doncorleone #power #loyalty"
                )
            }
        },
        "pinned_comment_tr": "👑 İhanet eden birine ikinci bir şans vermek merhamet midir yoksa aptallık mı? Yaz bakalım.",
        "pinned_comment_en": "👑 Is giving a second chance mercy or pure foolishness? Comment below."
    },
    {
        "id": "shelby_eyes_dont_lie",
        "theme_name": "Thomas Shelby - Yalanı Yakalamanın Tek Yolu",
        "series_title": "👑 Thomas Shelby: Bir Yalanı Anlamanın Tek Yolu #shorts",
        "category": "Karanlık Psikoloji & Beden Dili",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Bir insanın sana yalan söylediğini tek bir saniyede nasıl anlarsın?",
            "Ağzından çıkan kelimeleri değil, gözlerini kaçırdığı anı izle.",
            "Suçlu bir insan gereğinden fazla konuşur, masum olan ise gözünü bile kırpmaz.",
            "İşte bu yüzden zeki bir adam kimseyi sorgulamaz, sadece sessizce bekler çünkü..."
        ],
        "scenes_en": [
            "How do you catch a liar in less than one second?",
            "Stop listening to their words, watch the exact moment their eyes blink.",
            "A guilty person over-explains, a dangerous man stays completely calm.",
            "That's why a smart man never interrogates, he just waits in silence because..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Thomas Shelby | How To Catch A Liar Instantly #shorts",
                "description": (
                    "Lies are loud, truth is dead silent.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #thomasshelby #peakyblinders #bodylanguage #darkpsychology"
                )
            }
        },
        "pinned_comment_tr": "👑 Birinin yalan söylediğini anladığında yüzüne mi vurursun, yoksa sessizce oyununu mu izlersin?",
        "pinned_comment_en": "👑 When you catch someone lying, do you call them out or play along silently?"
    },
    {
        "id": "clash_shelby_vs_walter",
        "theme_name": "Thomas Shelby vs Walter White - Masanın Sahibi Kim?",
        "series_title": "👑 Thomas Shelby vs Walter White: Kim Daha Tehlikeli? #shorts",
        "category": "Karanlık Karizma & Savaş",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Aynı masaya otursalar ilk kim geri adım atar bilir misin?",
            "Walter White kimyayı ve zekayı silah olarak kullanır.",
            "Thomas Shelby ise korkuyu ve ölümü çoktan kabullenmiştir.",
            "Tarafını seç ve hemen yoruma yaz: Zeka mı kazanır yoksa çelik gibi bir irade mi çünkü..."
        ],
        "scenes_en": [
            "If they sat at the same table, who blinks first?",
            "Walter White weaponizes science and raw intellect.",
            "Thomas Shelby made peace with death a long time ago.",
            "Pick your side in the comments: pure brain or unbreakable iron will because..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Thomas Shelby vs Walter White | Who Is More Dangerous? #shorts",
                "description": (
                    "Heisenberg vs Thomas Shelby. Who walks away alive?\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #thomasshelby #walterwhite #breakingbad #peakyblinders"
                )
            }
        },
        "pinned_comment_tr": "👑 Walter White diyenler '1', Thomas Shelby diyenler '2' yazsın. Bakalım masanın gerçek sahibi kim? 👇",
        "pinned_comment_en": "👑 Type '1' for Walter White, '2' for Thomas Shelby. Let's settle who owns the room 👇"
    },
    {
        "id": "law_betrayal_controversy",
        "theme_name": "48 Güç Yasası - İkinci Şans Tuzağı",
        "series_title": "👑 48 Güç Yasası: Asla Yapmaman Gereken Ölümcül Hata #shorts",
        "category": "Karanlık Psikoloji & Güç",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Erkeklerin %90'ı bunu sadakat sanıyor ama aslında en büyük zayıflıktır.",
            "Sana bir kez yalan söyleyen birine ikinci şansı vermek.",
            "Yılan derisini değiştirir ama zehrini asla unutmaz.",
            "Yorumlara 'KANUN' yaz, gerçeği gör. Çünkü sırtından vurulan bir adam arkasına dönerse..."
        ],
        "scenes_en": [
            "90% of men think this is loyalty, but it's pure weakness.",
            "Giving a second chance to someone who lied to you once.",
            "A snake sheds its skin but never forgets its poison.",
            "Type 'LAW' in the comments to see the truth. Because a betrayed man who turns around..."
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | The Fatal Second Chance Mistake #shorts",
                "description": (
                    "Never trust a snake twice. Power requires ruthless boundaries.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #48lawsofpower #robertgreene #powerlaws #psychology"
                )
            }
        },
        "pinned_comment_tr": "👑 İkinci bir şans vermek merhamet midir yoksa aptallık mı? Yorumlarda büyük savaş başlasın 👇",
        "pinned_comment_en": "👑 Is giving a second chance mercy or pure foolishness? Let the debate begin 👇"
    },
    {
        "id": "clash_godfather_vs_scarface",
        "theme_name": "Don Vito Corleone vs Tony Montana - Masanın Gerçek Patronu",
        "series_title": "👑 Godfather vs Scarface: Zeka mı, Saf Delilik mi? #shorts",
        "category": "Karanlık Karizma & Mafya",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "İkisi aynı odaya girse ilk kurşunu kim sıkar bilir misin?",
            "Tony Montana korkuyu tanımaz, önündeki her şeyi yakıp yıkar.",
            "Don Vito Corleone ise asla bağırmaz, sadece parmağını şıklatır ve bitirir.",
            "Tony diyenler 1, Vito diyenler 2 yazsın. Bakalım masanın gerçek patronu kim çünkü..."
        ],
        "scenes_en": [
            "If they entered the same room, who pulls the trigger first?",
            "Tony Montana knows no fear, destroying everything in his path.",
            "Don Vito Corleone never shouts, he just whispers and ends empires.",
            "Type 1 for Tony, 2 for Vito. Let's see who truly rules the table because..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Godfather vs Scarface | Pure Intellect or Pure Madness? #shorts",
                "description": (
                    "Vito Corleone vs Tony Montana. Brains vs Raw Aggression.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #godfather #scarface #alpacino #sigmamindset"
                )
            }
        },
        "pinned_comment_tr": "👑 Tony Montana diyenler '1', Don Corleone diyenler '2' yazsın. Masanın gerçek sahibi kim? 👇",
        "pinned_comment_en": "👑 Type '1' for Tony Montana, '2' for Vito Corleone. Who owns the table? 👇"
    },
    {
        "id": "clash_oppenheimer_vs_interstellar",
        "theme_name": "Oppenheimer vs Cooper - Kıyamet mi Fedakarlık mı?",
        "series_title": "👑 Oppenheimer vs Interstellar: En Korkunç Zeka #shorts",
        "category": "Kozmik Bilim & Dehşet",
        "source_clip": "interstellar_master.mp4",
        "scenes": [
            "Evrenin en tehlikeli aklı kim bilir misin?",
            "Oppenheimer tek bir denklemle dünyayı yok edecek ateşi yaktı.",
            "Cooper ise insanlığı kurtarmak için zamanı ve sevdiklerini feda etti.",
            "Oppenheimer diyenler 'ATEŞ', Cooper diyenler 'ZAMAN' yazsın çünkü..."
        ],
        "scenes_en": [
            "Who possesses the most dangerous mind in human history?",
            "Oppenheimer ignited the atomic fire that could destroy mankind.",
            "Cooper sacrificed time, his family, and reality to save it.",
            "Type 'FIRE' for Oppenheimer, 'TIME' for Cooper in the comments because..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Oppenheimer vs Interstellar | The Deadliest Mind in History #shorts",
                "description": (
                    "The creator of doom vs the traveler of black holes.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #oppenheimer #interstellar #christophernolan #science"
                )
            }
        },
        "pinned_comment_tr": "👑 Oppenheimer diyenler 'ATEŞ', Cooper diyenler 'ZAMAN' yazsın. Hangisinin yükü daha ağırdı? 👇",
        "pinned_comment_en": "👑 Type 'FIRE' for Oppenheimer, 'TIME' for Cooper. Whose burden was heavier? 👇"
    },
    {
        "id": "law_toxic_friends",
        "theme_name": "48 Güç Yasası - Dostuna Asla Güvenme",
        "series_title": "👑 48 Güç Yasası: Dostuna Asla Güvenme #shorts",
        "category": "Karanlık Psikoloji & Güç",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Erkeklerin %95'inin yaptığı en büyük hata nedir bilir misin?",
            "Yıllardır tanıdığı bir insanı hala dostu sanmak.",
            "Düşmanın ne yapacağını bilirsin ama dostun seni en zayıf anında vurur.",
            "Yorumlara 'DOST' yaz, gerçeği öğren. Çünkü güç kazanmaya başladığında ilk kıskanan..."
        ],
        "scenes_en": [
            "What is the single biggest mistake 95% of men make?",
            "Assuming someone you've known for years is still your friend.",
            "You expect attacks from enemies, but friends strike when your back is turned.",
            "Type 'FRIEND' in the comments to see why. Because when you rise in power, the first to envy you is..."
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | Never Put Too Much Trust in Friends #shorts",
                "description": (
                    "Friends betray, former enemies prove loyalty. The ruthless law of power.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #48lawsofpower #robertgreene #darkpsychology #power"
                )
            }
        },
        "pinned_comment_tr": "👑 Seni en çok yaralayan darbe düşmanından mı geldi, yoksa en yakın dostundan mı? Dürüstçe yaz 👇",
        "pinned_comment_en": "👑 Did your deepest wound come from an enemy or a trusted friend? Comment below 👇"
    },
    {
        "id": "stoic_unshakable_calm",
        "theme_name": "Marcus Aurelius - Hakarete Verilecek En Ölümcül Tepki",
        "series_title": "👑 Marcus Aurelius: Hakarete En Ölümcül Cevap #shorts",
        "category": "Stoacılık & Çelik İrade",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Biri sana hakaret ettiğinde onu delirtmenin tek yolu nedir bilir misin?",
            "Öfkelenmek değil, tek kelime etmeden yüzüne bakıp gülümsemek.",
            "Öfken onun zaferidir, senin sessizliğin ise onun cehennemidir.",
            "Sen olsan bağırır mısın yoksa güler misin? Tarafını yoruma yaz çünkü..."
        ],
        "scenes_en": [
            "What is the ultimate way to destroy someone who insults you?",
            "Not by getting angry, but by looking into their eyes and smiling in pure silence.",
            "Your anger gives them victory, your calm silence gives them hell.",
            "Would you yell back or smile in cold silence? Drop your answer because..."
        ],
        "localizations": {
            "en": {
                "title": "👑 Marcus Aurelius | The Deadliest Response to an Insult #shorts",
                "description": (
                    "Silence is the ultimate weapon against disrespect.\n\n"
                    "⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n"
                    "#shorts #stoicism #marcusaurelius #philosophy #ironwill"
                )
            }
        },
        "pinned_comment_tr": "👑 Biri sana saygısızlık yaptığında bağırıp çağırmak mı güçtür, yoksa tek kelime etmeden yok saymak mı? 👇",
        "pinned_comment_en": "👑 When someone disrespects you, is screaming strength or is cold silence the ultimate power? 👇"
    }
]


def get_hypersonic_campaign(campaign_id: str | None = None) -> dict:
    if campaign_id:
        for c in HYPERSONIC_MASTER_CAMPAIGNS:
            if c["id"] == campaign_id:
                return c
    return random.choice(HYPERSONIC_MASTER_CAMPAIGNS)
