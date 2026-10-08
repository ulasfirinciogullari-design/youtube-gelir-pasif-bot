"""
Infinite Procedural Series & Universe Engine.
Generates multi-part episodic viral series for 'Zirvenin Kanunu':
- Seri 1: Gücün 48 Yasası (The 48 Laws of Power - Bölüm 1..10)
- Seri 2: Karanlık Psikoloji & Sessiz Güç (Dark Psychology & Manipulation - Bölüm 1..10)
- Seri 3: Stoacılık & Çelik Zihin (Stoic Indifference & Marcus Aurelius - Bölüm 1..10)
- Seri 4: Kozmik Dehşet & Zaman Paradoksu (Interstellar / Oppenheimer - Bölüm 1..10)
- Seri 5: %1'in Para ve Zirve Yasaları (Wolf of Wall Street / Scarface - Bölüm 1..10)
"""

import json
from pathlib import Path
import random

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 50+ Rich Procedural Episodic Master Vault
MASTER_SERIES_VAULT = [
    # =========================================================================
    # SERİ 1: GÜCÜN 48 YASASI (EPISODIC)
    # =========================================================================
    {
        "id": "power_law_01",
        "series_title": "👑 48 Güç Yasası | Bölüm 1: Efendini Asla Gölgede Bırakma",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "Güç oyunundaki en tehlikeli hata, patronundan daha zeki görünmeye çalışmaktır.",
            "Üstündeki insanları her zaman kendilerini daha üstün ve güvende hissettir.",
            "Yeteneklerini sergilerken aşırıya kaçarsan, onların korku ve kıskançlığını uyandırırsın.",
            "Tarihteki en büyük vezirler, başarılarını daima hükümdarlarına mal etmiştir.",
            "Kendi ışığını gizlemeyi bilmeyen bir adam, zirveyi asla göremez.",
            "İşte bu yüzden gücün birinci ve en ölümcül yasası...",
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | Part 1: Never Outshine the Master #shorts",
                "description": "Law 1: Always make those above you feel comfortably superior.\n\nDark psychology and Robert Greene's 48 Laws of Power.\n\n#shorts #48lawsofpower #powerlaws #darkpsychology #thomasshelby #stoic #mindset"
            },
            "es": {
                "title": "👑 Las 48 Leyes del Poder | Parte 1: Nunca Eclipses al Maestro #shorts",
                "description": "Ley 1: Haz que los que están por encima de ti se sientan cómodamente superiores.\n\n#shorts #48leyesdelpoder #psicologiaoscura #poder #exito"
            },
            "de": {
                "title": "👑 48 Gesetze der Macht | Teil 1: Stelle den Meister Nie in den Schatten #shorts",
                "description": "Gesetz 1: Gib deinen Vorgesetzten stets das Gefühl von Überlegenheit.\n\n#shorts #48gesetzedermacht #psychologie #macht #erfolg"
            }
        }
    },
    {
        "id": "power_law_02",
        "series_title": "👑 48 Güç Yasası | Bölüm 2: Dostlarına Asla Çok Güvenme",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "scarface_master.mp4",
        "scenes": [
            "Dostların sana en çabuk ihanet edecek kişilerdir, çünkü kıskançlık sessizce büyür.",
            "Sana kötülük yapmaya cesaret edemeyen bir düşmanı yanına al, sana kendini kanıtlamak için daha sadık olur.",
            "Bir dostuna iş verirsen, sana borçlu olduğunu değil, senin ona borçlu olduğunu düşünür.",
            "Fakat affettiğin bir düşman, sadakatini hayatıyla ödemeye hazırdır.",
            "Zirvede kalmak istiyorsan sevgiyi değil, saygıyı ve mesafeyi yöneteceksin.",
            "Çünkü sırtına saplanan hançerin sahibi asla yabancı değildir...",
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | Part 2: Never Put Too Much Trust in Friends #shorts",
                "description": "Law 2: Learn how to use enemies.\n\nFriends will betray you faster because envy rises silently.\n\n#shorts #48lawsofpower #loyalty #betrayal #powerlaws #sigma #peakyblinders"
            },
            "es": {
                "title": "👑 Las 48 Leyes del Poder | Parte 2: Nunca Confíes Demasiado en Amigos #shorts",
                "description": "Ley 2: Aprende a usar a los enemigos.\n\n#shorts #48leyesdelpoder #lealtad #poder #traicion"
            },
            "de": {
                "title": "👑 48 Gesetze der Macht | Teil 2: Vertraue Freunden Nicht Zu Sehr #shorts",
                "description": "Gesetz 2: Lerne deine Feinde zu nutzen.\n\n#shorts #48gesetzedermacht #loyalität #macht"
            }
        }
    },
    {
        "id": "power_law_03",
        "series_title": "👑 48 Güç Yasası | Bölüm 3: Niyetini Her Zaman Gizle",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "İnsanları ne yapacağın konusunda sürekli karanlıkta ve tahminde bırak.",
            "Niyetini açık eden bir adam, düşmanına kendi kalbini nişan tahtası olarak sunar.",
            "Onlara sahte hedefler göster, yanlış ipuçları ver ve asıl vuruşunu gölgelerden yap.",
            "Ne planladığını anladıklarında, artık savunma yapmak için çok geç olmalıdır.",
            "Büyük liderler çok konuşur ama asla asıl hamlelerini söylemezler.",
            "İşte bu yüzden Thomas Shelby'nin asla çiğnemediği o kural...",
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | Part 3: Conceal Your Intentions #shorts",
                "description": "Law 3: Keep people off-balance and in the dark.\n\nNever reveal the purpose behind your actions.\n\n#shorts #48lawsofpower #thomasshelby #strategy #power #darkpsychology"
            },
            "es": {
                "title": "👑 Las 48 Leyes del Poder | Parte 3: Oculta Tus Intenciones #shorts",
                "description": "Ley 3: Mantén a la gente en la incertidumbre y nunca reveles tu propósito.\n\n#shorts #48leyesdelpoder #estrategia #poder"
            },
            "de": {
                "title": "👑 48 Gesetze der Macht | Teil 3: Verschleiere Deine Absichten #shorts",
                "description": "Gesetz 3: Lass deine wahren Pläne niemals erkennen.\n\n#shorts #48gesetzedermacht #strategie #macht"
            }
        }
    },
    {
        "id": "power_law_04",
        "series_title": "👑 48 Güç Yasası | Bölüm 4: Her Zaman Gerektiğinden Az Konuş",
        "category": "Gücün 48 Yasası Serisi",
        "source_clip": "godfather_master.mp4",
        "scenes": [
            "İnsanları kelimelerle etkilemeye çalıştıkça, o kadar sıradan görünürsün.",
            "Güçlü insanlar sessiz kalır; her duraklamaları karşı tarafta derin bir endişe yaratır.",
            "Ne kadar az konuşursan, ağzından aptalca bir kelime çıkma riski o kadar azalır.",
            "Kısa ve gizemli cümleler kuran biri, etrafındaki herkesi savunmaya zorlar.",
            "Zirvedeki bir liderin sessizliği, yüz adamın bağırışından daha gürültülüdür.",
            "İşte Don Corleone'nin masayı yönettiği o yazılmamış kanun...",
        ],
        "localizations": {
            "en": {
                "title": "👑 48 Laws of Power | Part 4: Always Say Less Than Necessary #shorts",
                "description": "Law 4: Powerful people impress and intimidate by saying less.\n\n#shorts #48lawsofpower #godfather #doncorleone #silence #respect #mindset"
            },
            "es": {
                "title": "👑 Las 48 Leyes del Poder | Parte 4: Di Siempre Menos de lo Necesario #shorts",
                "description": "Ley 4: Las personas poderosas impresionan diciendo menos.\n\n#shorts #48leyesdelpoder #silencio #respeto #poder"
            },
            "de": {
                "title": "👑 48 Gesetze der Macht | Teil 4: Sage Immer Weniger als Notwendig #shorts",
                "description": "Gesetz 4: Mächtige Menschen schweigen mehr als sie reden.\n\n#shorts #48gesetzedermacht #respekt #disziplin #macht"
            }
        }
    },

    # =========================================================================
    # SERİ 2: KARANLIK PSİKOLOJİ & SESSİZ GÜÇ (EPISODIC)
    # =========================================================================
    {
        "id": "dark_psych_01",
        "series_title": "🧠 Karanlık Zihin | Bölüm 1: Tepkisizlik En Büyük Silahındır",
        "category": "Karanlık Psikoloji Serisi",
        "source_clip": "breaking_bad_master.mp4",
        "scenes": [
            "Biri seni kışkırtmaya çalıştığında sana bir yem atar.",
            "Öfkelendiğin saniye kontrolü tamamen onun eline verirsin.",
            "Fakat gözünü bile kırpmadan sadece gözlerinin içine bakıp sessiz kalırsan, panikleyen taraf o olur.",
            "Tepkisizlik, bir insanın egosuna vurulabilecek en ölümcül darbedir.",
            "Çünkü umursanmamak, bir insan için nefret edilmekten çok daha ağırdır.",
            "İşte psikolojik üstünlüğü tek bir kelime etmeden kurmanın yolu...",
        ],
        "localizations": {
            "en": {
                "title": "🧠 Dark Psychology | Part 1: Non-Reaction is Your Ultimate Weapon #shorts",
                "description": "When someone tries to provoke you, silence destroys their ego.\n\nMaster emotional detachment and power dynamics.\n\n#shorts #darkpsychology #walterwhite #stoicism #sigma #mindset #control"
            },
            "es": {
                "title": "🧠 Psicología Oscura | Parte 1: La Falta de Reacción es Tu Mayor Arma #shorts",
                "description": "El silencio destruye el ego del que intenta provocarte.\n\n#shorts #psicologiaoscura #control #mentalidad #fuerza"
            },
            "de": {
                "title": "🧠 Dunkle Psychologie | Teil 1: Keine Reaktion ist Deine Stärkste Waffe #shorts",
                "description": "Reaktionslosigkeit ist die mächtigste Waffe gegen Provokation.\n\n#shorts #psychologie #mindset #kontrolle #stoizismus"
            }
        }
    },
    {
        "id": "dark_psych_02",
        "series_title": "🧠 Karanlık Zihin | Bölüm 2: Göz Temasının Acımasız Gücü",
        "category": "Karanlık Psikoloji Serisi",
        "source_clip": "peaky_master.mp4",
        "scenes": [
            "Bir odadaki hiyerarşiyi belirleyen ilk şey, gözlerini ilk kimin kaçırdığıdır.",
            "Zayıf bir insan karşısındakinin bakışlarına üç saniyeden fazla dayanamaz.",
            "Fakat bir alfa, gözlerini kırpmadan doğrudan iki kaşın arasına odaklanır.",
            "Bu bakış, karşı tarafın bilinçaltında ilkel bir boyun eğme refleksi tetikler.",
            "Kelimelere gerek kalmadan odayı teslim almak işte böyle bir sanattır.",
            "Ve Thomas Shelby'nin düşmanlarına baktığı o saniye...",
        ],
        "localizations": {
            "en": {
                "title": "🧠 Dark Psychology | Part 2: The Ruthless Power of Eye Contact #shorts",
                "description": "The hierarchy in any room is decided by who breaks eye contact first.\n\n#shorts #darkpsychology #eyecontact #bodylanguage #thomasshelby #peakyblinders #alpha"
            },
            "es": {
                "title": "🧠 Psicología Oscura | Parte 2: El Poder Despiadado del Contacto Visual #shorts",
                "description": "La jerarquía se decide por quién aparta la mirada primero.\n\n#shorts #psicologiaoscura #lenguajecorporal #mirada #poder"
            },
            "de": {
                "title": "🧠 Dunkle Psychologie | Teil 2: Die Macht des Blickkontakts #shorts",
                "description": "Blickkontakt entscheidet über die Hierarchie im Raum.\n\n#shorts #psychologie #körpersprache #dominanz #macht"
            }
        }
    },

    # =========================================================================
    # SERİ 3: STOACILIK & ÇELİK İRADE (EPISODIC)
    # =========================================================================
    {
        "id": "stoic_will_01",
        "series_title": "⚔️ Çelik Zihin | Bölüm 1: Acıyı ve Korkuyu Sıfırla",
        "category": "Stoacı İrade Serisi",
        "source_clip": "gladiator_master.mp4",
        "scenes": [
            "Seni inciten şey başına gelen olaylar değil, o olaylar hakkındaki düşüncelerindir.",
            "Birisi seni aldattığında veya terk ettiğinde, sana zarar veremez; sen üzülmeyi seçtiğin için acı çekersin.",
            "Roma arenasında binlerce kılıcın arasında duran Marcus Aurelius şunu biliyordu:",
            "Zihnini bir kale gibi inşa edersen, hiçbir düşman ordusu o surları aşamaz.",
            "Sadece kontrol edebildiğin şeylere odaklan, gerisini rüzgara bırak.",
            "İşte iki bin yıldır yenilmeyen o çelik zihniyet...",
        ],
        "localizations": {
            "en": {
                "title": "⚔️ Iron Will | Part 1: Erase Pain and Fear #shorts",
                "description": "You have power over your mind - not outside events. Realize this, and you will find strength.\n\n#shorts #stoicism #marcusaurelius #gladiator #mindset #discipline #resilience"
            },
            "es": {
                "title": "⚔️ Mente de Hierro | Parte 1: Borra el Dolor y el Miedo #shorts",
                "description": "Tienes poder sobre tu mente, no sobre los acontecimientos externos.\n\n#shorts #estoicismo #marcoaurelio #disciplina #resiliencia"
            },
            "de": {
                "title": "⚔️ Eiserner Wille | Teil 1: Lösche Schmerz und Angst #shorts",
                "description": "Du hast Macht über deinen Geist, nicht über äußere Ereignisse.\n\n#shorts #stoizismus #marcaurel #disziplin #wille"
            }
        }
    },

    # =========================================================================
    # SERİ 4: MODERN DÜNYANIN İLLÜZYONU & GERÇEKLER
    # =========================================================================
    {
        "id": "matrix_truth_01",
        "series_title": "🕶️ Sistemin İllüzyonu | Bölüm 1: Uyanışın Bedeli",
        "category": "Sistemin İllüzyonu Serisi",
        "source_clip": "matrix_master.mp4",
        "scenes": [
            "Sana doğduğun günden beri bir yalan satıldı: Çalış, tüket ve sessiz kal.",
            "Toplum senin başarılı olmanı istemez; toplum senin borçlu ve bağımlı kalmanı ister.",
            "Kırmızı hapı seçtiğin an geri dönüş kapısı arkandan sonsuza dek kilitlenir.",
            "Fakat bir kafeste doğan kuşlar, uçmayı bir hastalık zanneder.",
            "Gerçeği gördüğünde artık asla eski sen olamazsın.",
            "İşte bu yüzden matrixin senden en çok korktuğu an...",
        ],
        "localizations": {
            "en": {
                "title": "🕶️ The Matrix Illusion | Part 1: The Cost of Awakening #shorts",
                "description": "You were born into a prison you cannot smell or taste or touch.\n\n#shorts #thematrix #redpill #wakeuptoreality #system #freedom #mindset"
            },
            "es": {
                "title": "🕶️ La Ilusión de Matrix | Parte 1: El Precio de Despertar #shorts",
                "description": "Naciste en una prisión para tu mente.\n\n#shorts #matrix #despierta #libertad #mentalidad"
            },
            "de": {
                "title": "🕶️ Die Matrix Illusion | Teil 1: Der Preis des Erwachens #shorts",
                "description": "Du wurdest in ein Gefängnis für deinen Geist geboren.\n\n#shorts #matrix #wachauf #freiheit #realität"
            }
        }
    },

    # =========================================================================
    # SERİ 5: PARANIN VE ZİRVENİN %1 YASALARI
    # =========================================================================
    {
        "id": "wealth_power_01",
        "series_title": "💵 Zirvedeki %1 | Bölüm 1: Para Saygıyı Satın Alır Mı?",
        "category": "Zirvedeki %1 Yasaları",
        "source_clip": "wolf_master.mp4",
        "scenes": [
            "İnsanlar sana paranın mutluluk getirmediğini söyler; çünkü kendileri asla kazanamamıştır.",
            "Fakat bir hastane faturasını ödeyemediğinde o sahte felsefe yerle bir olur.",
            "Para sadece güç değildir; para senin zamanını ve sevdiklerini koruyan tek kalkandır.",
            "Zirveye çıkmak istiyorsan insanların fikrini değil, piyasanın kurallarını dinleyeceksin.",
            "Ve bir gün o tepeye ulaştığında bahanelerle dolu o dünyayı arkanda bırakırsın.",
            "İşte Jordan Belfort'un Wall Street'te herkesi diz çöktürdüğü kural...",
        ],
        "localizations": {
            "en": {
                "title": "💵 The Top 1% | Part 1: Does Money Buy Respect? #shorts",
                "description": "There is no nobility in poverty. Money gives you options and freedom.\n\n#shorts #wolfofwallstreet #jordanbelfort #money #wealth #success #grindset"
            },
            "es": {
                "title": "💵 El 1% Superior | Parte 1: ¿El Dinero Compra Respeto? #shorts",
                "description": "No hay nobleza en la pobreza. El dinero te da opciones.\n\n#shorts #lobodewallstreet #dinero #exito #riqueza"
            },
            "de": {
                "title": "💵 Die Oberen 1% | Teil 1: Kauft Geld Respekt? #shorts",
                "description": "Es gibt keinen Adel in der Armut. Geld bedeutet Freiheit.\n\n#shorts #wolfofwallstreet #geld #erfolg #reichtum"
            }
        }
    }
]


def get_series_vault():
    return MASTER_SERIES_VAULT
