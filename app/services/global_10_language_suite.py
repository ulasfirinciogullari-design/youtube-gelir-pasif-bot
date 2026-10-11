"""
Global 10-Language Dubbing, Multi-Audio & Closed Caption Suite.
Expands 'Zirvenin Kanunu' (@zirveninkanunu) into a worldwide media empire.

The 10 Canonical Global Languages:
1. 🇹🇷 tr (Turkish) - Türkçe
2. 🇺🇸 en (English) - İngilizce
3. 🇪🇸 es (Spanish) - İspanyolca
4. 🇧🇷 pt (Portuguese - Brazilian) - Portekizce
5. 🇩🇪 de (German) - Almanca
6. 🇫🇷 fr (French) - Fransızca
7. 🇮🇹 it (Italian) - İtalyanca
8. 🇷🇺 ru (Russian) - Rusça
9. 🇸🇦 ar (Arabic) - Arapça
10. 🇮🇳 hi (Hindi) - Hintçe
"""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import edge_tts
from app.services.elevenlabs_engine import synthesize_elevenlabs_speech, get_elevenlabs_api_key

GLOBAL_10_LANGUAGES = {
    "tr": {
        "name": "Turkish (Türkçe)",
        "voice": "tr-TR-AhmetNeural",
        "studio_name": "Türkçe",
        "hl": "tr",
        "eleven_voice": "HV5rAif1q1ITHdvxqyMw" # Alp Bariton
    },
    "en": {
        "name": "English (US)",
        "voice": "en-US-ChristopherNeural",
        "studio_name": "İngilizce",
        "hl": "en",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB" # Adam Deep Baritone
    },
    "es": {
        "name": "Spanish (Español)",
        "voice": "es-ES-AlvaroNeural",
        "studio_name": "İspanyolca",
        "hl": "es",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "pt": {
        "name": "Portuguese (Português)",
        "voice": "pt-BR-AntonioNeural",
        "studio_name": "Portekizce",
        "hl": "pt",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "de": {
        "name": "German (Deutsch)",
        "voice": "de-DE-ConradNeural",
        "studio_name": "Almanca",
        "hl": "de",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "fr": {
        "name": "French (Français)",
        "voice": "fr-FR-HenriNeural",
        "studio_name": "Fransızca",
        "hl": "fr",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "it": {
        "name": "Italian (Italiano)",
        "voice": "it-IT-DiegoNeural",
        "studio_name": "İtalyanca",
        "hl": "it",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "ru": {
        "name": "Russian (Русский)",
        "voice": "ru-RU-DmitryNeural",
        "studio_name": "Rusça",
        "hl": "ru",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "ar": {
        "name": "Arabic (العربية)",
        "voice": "ar-SA-HamedNeural",
        "studio_name": "Arapça",
        "hl": "ar",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
    "hi": {
        "name": "Hindi (हिन्दी)",
        "voice": "hi-IN-MadhurNeural",
        "studio_name": "Hintçe",
        "hl": "hi",
        "eleven_voice": "pNInz6obpgDQGcFmaJgB"
    },
}

# Complete Trilogy Master Metadata & Script Bank
TRILOGY_CATALOG = {
    "part1": {
        "video_id": "9YDe6hf8xxM",
        "duration": 21.5,
        "languages": {
            "tr": {
                "title": "👑 GÖRÜNMEZ GÜÇ #1: Asla Niyetini Belli Etme (Part 1/3) #shorts",
                "desc": (
                    "👑 Zirvenin Kanunu: Bir insanı yok etmenin en kolay yolu bağırmak değil; mezar sessizliğinde plan yapmaktır.\n\n"
                    "⚡ SONRAKİ BÖLÜM (Part 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 1080p TAM HOLLYWOOD BELGESELİ: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#thomasshelby #48güçyasası #karanlıkpsikoloji #gta5 #görünmezgüç #shorts #zirveninkanunu"
                ),
                "lines": [
                    "Bir insanı yok etmenin en kolay yolu bağırmak değil; mezar sessizliğinde plan yapmaktır.",
                    "Thomas Shelby kuralı: Asla niyetini belli etme.",
                    "Bırak seni aptal sansınlar, ta ki sen şah mat diyene kadar.",
                    "Güç gürültüde değil, sessizlikte saklıdır. Part 2 bir sonraki videoda..."
                ]
            },
            "en": {
                "title": "👑 INVISIBLE POWER #1: Never Reveal Your Intentions (Part 1/3) #shorts",
                "desc": (
                    "👑 Law of the Peak: The easiest way to destroy a man is not shouting; it is planning in grave silence.\n\n"
                    "⚡ NEXT PART (Part 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 FULL 1080p MASTER DOCUMENTARY: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#thomasshelby #48lawsofpower #darkpsychology #gta5 #shorts #power #success"
                ),
                "lines": [
                    "The easiest way to destroy a man is not by shouting; it is by planning in grave silence.",
                    "Thomas Shelby's rule: Never reveal your intentions.",
                    "Let them think you are a fool, until you deliver the checkmate.",
                    "Power is not found in noise, but in total silence. Part 2 in the next video..."
                ]
            },
            "es": {
                "title": "👑 PODER INVISIBLE #1: Nunca Reveles Tus Intenciones (Parte 1/3) #shorts",
                "desc": (
                    "👑 La Regla de Thomas Shelby: Nunca reveles tus intenciones. Deja que piensen que eres un tonto, hasta que des el jaque mate.\n\n"
                    "⚡ SIGUIENTE PARTE (Parte 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 DOCUMENTAL COMPLETO 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48leyesdelpoder #psicologia #poder #exito"
                ),
                "lines": [
                    "La forma más fácil de destruir a un hombre no es gritando; es planeando en silencio de tumba.",
                    "La regla de Thomas Shelby: Nunca reveles tus intenciones.",
                    "Deja que piensen que eres un tonto, hasta que des el jaque mate.",
                    "El poder no está en el ruido, sino en el silencio absoluto. Parte 2 en el siguiente video..."
                ]
            },
            "pt": {
                "title": "👑 PODER INVISÍVEL #1: Nunca Revele Suas Intenções (Parte 1/3) #shorts",
                "desc": (
                    "👑 Lei do Topo: A maneira mais fácil de destruir alguém não é gritando; é planejando no silêncio do túmulo.\n\n"
                    "⚡ PRÓXIMA PARTE (Parte 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 DOCUMENTÁRIO COMPLETO 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48leisdopoder #psicologia #sucesso #poder"
                ),
                "lines": [
                    "A maneira mais fácil de destruir um homem não é gritando; é planejando no silêncio do túmulo.",
                    "A regra de Thomas Shelby: Nunca revele suas intenções.",
                    "Deixe que pensem que você é um tolo, até que você dê o xeque-mate.",
                    "O poder não está no barulho, mas no silêncio absoluto. Parte 2 no próximo vídeo..."
                ]
            },
            "de": {
                "title": "👑 UNSICHTBARE MACHT #1: Offenbare Niemals Deine Absichten (Teil 1/3) #shorts",
                "desc": (
                    "👑 Das Gesetz des Gipfels: Der einfachste Weg, jemanden zu zerstören, ist nicht zu schreien; es ist die Planung in Grabesstille.\n\n"
                    "⚡ NÄCHSTER TEIL (Teil 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 VOLLSTÄNDIGE 1080p DOKUMENTATION: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48gesetzedermacht #psychologie #erfolg"
                ),
                "lines": [
                    "Der einfachste Weg, einen Mann zu zerstören, ist nicht Schreien; es ist Planung in Grabesstille.",
                    "Thomas Shelbys Regel: Offenbare niemals deine wahren Absichten.",
                    "Lass sie glauben, du seist ein Narr, bis du sie schachmatt setzt.",
                    "Macht liegt nicht im Lärm, sondern in der Stille. Teil 2 im nächsten Video..."
                ]
            },
            "fr": {
                "title": "👑 POUVOIR INVISIBLE #1: Ne Révèle Jamais Tes Intentions (Partie 1/3) #shorts",
                "desc": (
                    "👑 La Loi du Sommet: La façon la plus simple de détruire un homme n'est pas de crier; c'est de planifier dans un silence de mort.\n\n"
                    "⚡ PARTIE SUIVANTE (Partie 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 DOCUMENTAIRE COMPLET 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48loisdupouvoir #psychologie #succes"
                ),
                "lines": [
                    "La manière la plus simple de détruire un homme n'est pas de crier; c'est de planifier dans un silence de tombe.",
                    "La règle de Thomas Shelby: Ne révèle jamais tes intentions.",
                    "Laisse-les penser que tu es un imbécile, jusqu'à ce que tu portes l'échec et mat.",
                    "Le pouvoir n'est pas dans le bruit, mais dans le silence. Partie 2 dans la prochaine vidéo..."
                ]
            },
            "it": {
                "title": "👑 POTERE INVISIBILE #1: Non Rivelare Mai Le Tue Intenzioni (Parte 1/3) #shorts",
                "desc": (
                    "👑 La Legge del Vertice: Il modo più semplice per distruggere un uomo non è gridare; è pianificare nel silenzio tombale.\n\n"
                    "⚡ PROSSIMA PARTE (Parte 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 DOCUMENTARIO COMPLETO 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48leggedelpotere #psicologia #potere"
                ),
                "lines": [
                    "Il modo più semplice per distruggere un uomo non è gridare; è pianificare nel silenzio tombale.",
                    "La regola di Thomas Shelby: Non rivelare mai le tue intenzioni.",
                    "Lascia che ti credano uno sciocco, fino a quando non fai scacco matto.",
                    "Il potere non risiede nel rumore, ma nel silenzio. Parte 2 nel prossimo video..."
                ]
            },
            "ru": {
                "title": "👑 НЕВИДИМАЯ ВЛАСТЬ #1: Никогда Не Раскрывай Своих Намерений (Часть 1/3) #shorts",
                "desc": (
                    "👑 Закон Вершины: Самый простой способ уничтожить человека — не кричать, а планировать в могильной тишине.\n\n"
                    "⚡ СЛЕДУЮЩАЯ ЧАСТЬ (Часть 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 ПОЛНЫЙ ДОКУМЕНТАЛЬНЫЙ ФИЛЬМ 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #томашелби #48законоввласти #психология #успех"
                ),
                "lines": [
                    "Самый легкий способ сокрушить человека — не кричать; это строить планы в могильной тишине.",
                    "Правило Томаса Шелби: Никогда не раскрывай своих намерений.",
                    "Пусть они считают тебя дураком, пока ты не поставишь шах и мат.",
                    "Сила кроется не в шуме, а в абсолютном безмолвии. Часть 2 в следующем видео..."
                ]
            },
            "ar": {
                "title": "👑 القوة الخفية #1: لا تكشف نواياك أبداً (الجزء 1/3) #shorts",
                "desc": (
                    "👑 قانون القمة: أسهل طريقة لتدمير شخص ليست الصراخ؛ بل التخطيط في صمت القبور.\n\n"
                    "⚡ الجزء التالي (الجزء 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 الوثائقي الكامل بدقة 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #توماس_شيلبي #قوانين_السطوة #علم_النفس"
                ),
                "lines": [
                    "أسهل طريقة لتدمير رجل ليست الصراخ، بل التخطيط في صمت القبور.",
                    "قاعدة توماس شيلبي: إياك أن تكشف عن نواياك الحقيقية.",
                    "دعهم يعتقدون أنك أحمق، حتى توجه لهم ضربة كش ملك القاضية.",
                    "القوة لا تكمن في الضجيج، بل في الصمت المطلق. الجزء الثاني في الفيديو القادم..."
                ]
            },
            "hi": {
                "title": "👑 अदृश्य शक्ति #1: अपने इरादे कभी मत बताओ (भाग 1/3) #shorts",
                "desc": (
                    "👑 शीर्ष का नियम: किसी को नष्ट करने का सबसे आसान तरीका चिल्लाना नहीं, बल्कि कब्र की शांति में योजना बनाना है।\n\n"
                    "⚡ अगला भाग (भाग 2): https://youtube.com/shorts/4VN_YmNMd6s\n"
                    "🎬 1080p पूर्ण वृत्तचित्र: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #थॉमसशेल्बी #48शक्तिनियम #मनोविज्ञान #सफलता"
                ),
                "lines": [
                    "किसी इंसान को नष्ट करने का सबसे आसान तरीका चिल्लाना नहीं है, बल्कि गहरी शांति में योजना बनाना है।",
                    "थॉमस शेल्बी का नियम: अपने इरादों को कभी किसी पर ज़ाहिर मत होने दो।",
                    "उन्हें आपको मूर्ख समझने दें, जब तक कि आप उन्हें शह और मात न दे दें।",
                    "ताकत कभी शोर में नहीं, बल्कि गहरी खामोशी में छिपी होती है। भाग 2 अगले वीडियो में..."
                ]
            }
        }
    },
    "part2": {
        "video_id": "4VN_YmNMd6s",
        "duration": 20.89,
        "languages": {
            "tr": {
                "title": "👑 GÖRÜNMEZ GÜÇ #2: En Yakın Dostuna Asla Güvenme (Part 2/3) #shorts",
                "desc": (
                    "En ölümcül darbeyi asla düşmanın vurmaz. O darbe, her gün aynı masada oturduğun adamdan gelir.\n"
                    "Thomas Shelby'nin görünmez güç kuralı.\n\n"
                    "⚡ ÖNCEKİ BÖLÜM (Part 1): https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 SONRAKİ BÖLÜM (Part 3): https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 1080p TAM HOLLYWOOD BELGESELİ: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #güç #psikoloji #peakyblinders #motivasyon #başarı #kural"
                ),
                "lines": [
                    "Sana en ölümcül darbeyi asla düşmanın vurmaz.",
                    "O darbe, her gün aynı masada çay içtiğin adamdan gelir.",
                    "Çünkü düşmanına karşı daima tetiktesindir; ama dostun senin tüm zaaflarını ezbere bilir.",
                    "Thomas Shelby kuralı: Herkese gülümse, ama sırtını sadece duvara yasla.",
                    "Peki masadan ne zaman kalkmalısın? Final kuralı Part 3'te, alttaki videoda..."
                ]
            },
            "en": {
                "title": "👑 INVISIBLE POWER #2: Never Trust Your Closest Friend (Part 2/3) #shorts",
                "desc": (
                    "👑 Thomas Shelby Rule: Your worst enemy will never deliver the fatal blow. That blow comes from whoever sits at your table every day.\n\n"
                    "⚡ PREVIOUS (Part 1): https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 FINAL (Part 3): https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 MASTER DOCUMENTARY: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48lawsofpower #psychology #success #mindset"
                ),
                "lines": [
                    "Your greatest enemy will never strike the fatal blow.",
                    "That blow always comes from the person who sits at your table every single day.",
                    "You keep your guard up against enemies, but friends know your every weakness.",
                    "Thomas Shelby's rule: Smile at everyone, but only lean your back against a stone wall.",
                    "The final rule is in Part 3 below..."
                ]
            },
            "es": {
                "title": "👑 PODER INVISIBLE #2: Nunca Confíes en Tu Mejor Amigo (Parte 2/3) #shorts",
                "desc": (
                    "👑 Regla de Thomas Shelby: Tu peor enemigo nunca te dará el golpe mortal. Ese golpe viene de quien se sienta a tu mesa todos los días.\n\n"
                    "⚡ PARTE ANTERIOR (Parte 1): https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 PARTE FINAL (Parte 3): https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 DOCUMENTAL COMPLETO 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48leyesdelpoder #psicologia #subwaysurfers"
                ),
                "lines": [
                    "Tu peor enemigo nunca te dará el golpe mortal.",
                    "Ese golpe siempre viene de quien se sienta a tu mesa todos los días.",
                    "Contra tu enemigo estás alerta, pero un amigo conoce todas tus debilidades.",
                    "La regla de Thomas Shelby: Sonríe a todos, pero solo apoya tu espalda en una pared de piedra.",
                    "La regla final está en la Parte 3 abajo..."
                ]
            },
            "pt": {
                "title": "👑 PODER INVISÍVEL #2: Nunca Confie No Seu Melhor Amigo (Parte 2/3) #shorts",
                "desc": (
                    "👑 Regra de Thomas Shelby: Seu pior inimigo nunca lhe dará o golpe fatal. Esse golpe vem de quem se senta à sua mesa todos os dias.\n\n"
                    "⚡ PARTE ANTERIOR (Parte 1): https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 PARTE FINAL (Parte 3): https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 DOCUMENTÁRIO 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48leisdopoder #psicologia #sucesso"
                ),
                "lines": [
                    "Seu pior inimigo nunca lhe dará o golpe fatal.",
                    "Esse golpe sempre vem de quem se senta à sua mesa todos os dias.",
                    "Você fica em alerta contra inimigos, mas os amigos conhecem suas fraquezas.",
                    "Regra de Thomas Shelby: Sorria para todos, mas encoste suas costas apenas na parede.",
                    "A regra final está na Parte 3 abaixo..."
                ]
            },
            "de": {
                "title": "👑 UNSICHTBARE MACHT #2: Vertraue Niemals Deinem Besten Freund (Teil 2/3) #shorts",
                "desc": (
                    "👑 Thomas Shelby Regel: Dein Feind wird dir niemals den tödlichen Schlag versetzen. Dieser Schlag kommt von demjenigen, der täglich an deinem Tisch sitzt.\n\n"
                    "⚡ TEIL 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 FINALER TEIL 3: https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 DOKUMENTATION: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48gesetzedermacht #psychologie"
                ),
                "lines": [
                    "Dein größter Feind wird dir niemals den tödlichen Schlag versetzen.",
                    "Dieser Schlag kommt immer von demjenigen, der jeden Tag an deinem Tisch sitzt.",
                    "Gegen deine Feinde bist du wachsam, aber deine Freunde kennen jede deiner Schwächen.",
                    "Thomas Shelbys Regel: Lächle jeden an, aber lehne deinen Rücken nur an Stein.",
                    "Die finale Regel erfährst du in Teil 3 unten..."
                ]
            },
            "fr": {
                "title": "👑 POUVOIR INVISIBLE #2: Ne Fais Jamais Confiance À Ton Meilleur Ami (Partie 2/3) #shorts",
                "desc": (
                    "👑 Règle de Thomas Shelby: Ton pire ennemi ne te portera jamais le coup fatal. Ce coup vient de celui qui est assis à ta table tous les jours.\n\n"
                    "⚡ PARTIE 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 FINALE PARTIE 3: https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 DOCUMENTAIRE 1080p: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48loisdupouvoir #psychologie"
                ),
                "lines": [
                    "Ton pire ennemi ne te portera jamais le coup fatal.",
                    "Ce coup fatal vient toujours de la personne assise à ta table chaque jour.",
                    "Tu es sur tes gardes face aux ennemis, mais tes amis connaissent toutes tes faiblesses.",
                    "Règle de Thomas Shelby: Souris à tout le monde, mais n'appuie ton dos qu'au mur.",
                    "La règle finale est dans la Partie 3 ci-dessous..."
                ]
            },
            "it": {
                "title": "👑 POTERE INVISIBILE #2: Non Fidarti Mai Del Tuo Migliore Amico (Parte 2/3) #shorts",
                "desc": (
                    "👑 Regola di Thomas Shelby: Il tuo peggior nemico non ti darà mai il colpo fatale. Quel colpo arriva da chi siede alla tua tavola ogni giorno.\n\n"
                    "⚡ PARTE 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 PARTE 3: https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 DOCUMENTARIO: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48leggedelpotere #psicologia"
                ),
                "lines": [
                    "Il tuo peggior nemico non ti sferrerà mai il colpo mortale.",
                    "Quel colpo arriva da chi siede alla tua tavola ogni singolo giorno.",
                    "Con i nemici tieni la guardia alta, ma gli amici conoscono ogni tua debolezza.",
                    "Regola di Thomas Shelby: Sorridi a tutti, ma appoggia la schiena solo al muro.",
                    "La regola finale è nella Parte 3 qui sotto..."
                ]
            },
            "ru": {
                "title": "👑 НЕВИДИМАЯ ВЛАСТЬ #2: Никогда Не Доверяй Лучшему Другу (Часть 2/3) #shorts",
                "desc": (
                    "👑 Правило Томаса Шелби: Твой злейший враг никогда не нанесет смертельный удар. Этот удар исходит от того, кто сидит за твоим столом каждый день.\n\n"
                    "⚡ ЧАСТЬ 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 ФИНАЛ ЧАСТЬ 3: https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 ДОКУМЕНТАЛЬНЫЙ ФИЛЬМ: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #томашелби #48законоввласти #психология"
                ),
                "lines": [
                    "Твой враг никогда не нанесет тебе смертельный удар.",
                    "Этот удар всегда наносит тот, кто каждый день сидит с тобой за одним столом.",
                    "Против врагов ты всегда настороже, но друзья знают каждую твою слабость.",
                    "Правило Томаса Шелби: Улыбайся всем, но спиной прислоняйся только к каменной стене.",
                    "Финальное правило ждет тебя в Части 3 ниже..."
                ]
            },
            "ar": {
                "title": "👑 القوة الخفية #2: لا تثق بأقرب أصدقائك أبداً (الجزء 2/3) #shorts",
                "desc": (
                    "👑 قاعدة توماس شيلبي: ألد أعدائك لن يوجه لك الضربة القاضية. تلك الضربة تأتي دائماً ممن يجلس معك على نفس الطاولة يومياً.\n\n"
                    "⚡ الجزء 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 الجزء الأخير (الجزء 3): https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 الوثائقي: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #توماس_شيلبي #قوانين_السطوة #علم_النفس"
                ),
                "lines": [
                    "ألد أعدائك لن يوجه لك الضربة القاضية أبداً.",
                    "تلك الضربة تأتي دائماً ممن يجلس معك على نفس المائدة كل يوم.",
                    "أمام العدو تكون متيقظاً، لكن الصديق يعرف جميع نقاط ضعفك عن ظهر قلب.",
                    "قاعدة توماس شيلبي: ابتسم للجميع، لكن اسند ظهرك للجدار فقط.",
                    "القاعدة الذهبية الأخيرة في الجزء الثالث بالأسفل..."
                ]
            },
            "hi": {
                "title": "👑 अदृश्य शक्ति #2: अपने सबसे करीबी दोस्त पर कभी भरोसा न करें (भाग 2/3) #shorts",
                "desc": (
                    "👑 थॉमस शेल्बी का नियम: आपका सबसे बड़ा दुश्मन कभी भी घातक वार नहीं करेगा। वह वार हमेशा उसी से आता है जो रोज़ आपके साथ बैठता है।\n\n"
                    "⚡ पिछला भाग (भाग 1): https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "🔥 अंतिम भाग (भाग 3): https://youtube.com/shorts/-SqRHo1GrF8\n"
                    "🎬 पूर्ण वृत्तचित्र: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #थॉमसशेल्बी #48शक्तिनियम #मनोविज्ञान"
                ),
                "lines": [
                    "आपका सबसे बड़ा दुश्मन कभी भी आपको जानलेवा वार नहीं देगा।",
                    "वह वार हमेशा उसी इंसान की तरफ से आता है जो रोज़ आपके साथ एक ही मेज़ पर बैठता है।",
                    "दुश्मन के सामने आप हमेशा सतर्क रहते हैं, लेकिन दोस्त आपकी हर कमज़ोरी को जानता है।",
                    "थॉमस शेल्बी का नियम: मुस्कुराओ सबके साथ, लेकिन अपनी पीठ केवल पत्थर की दीवार से टिकाओ।",
                    "अंतिम और सबसे बड़ा नियम नीचे भाग 3 में है..."
                ]
            }
        }
    },
    "part3": {
        "video_id": "-SqRHo1GrF8",
        "duration": 23.86,
        "languages": {
            "tr": {
                "title": "👑 GÖRÜNMEZ GÜÇ #3: Masadan Ne Zaman Kalkacağını Bil (Part 3/3 FİNAL) #shorts",
                "desc": (
                    "👑 Zirvenin Kanunu: Görünmez gücün en acımasız kuralı masadan kalkmayı bilmektir.\n"
                    "Bir insan senin suskunluğunu zayıflık, sabrını ise çaresizlik sanmaya başladığı an, o masada kalmak intihardır.\n\n"
                    "⚡ ÖNCEKİ BÖLÜMLER:\n"
                    "Part 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "Part 2: https://youtube.com/shorts/4VN_YmNMd6s\n\n"
                    "🎬 1080p TAM HOLLYWOOD BELGESELİ: Alttaki ilgili videoda izle! (30 Dakika Kesintisiz Güç Yasaları)\n"
                    "https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #güç #psikoloji #peakyblinders #gta5 #zirveninkanunu #kural #başarı"
                ),
                "lines": [
                    "Görünmez gücün en acımasız kuralı masadan kalkmayı bilmektir.",
                    "Bir insan senin suskunluğunu zayıflık, sabrını ise çaresizlik sanmaya başladığı an...",
                    "O masada kalmak artık erdem değil, intihardır.",
                    "Thomas Shelby der ki: Güçlü adam kavga etmez.",
                    "Sadece arkasını döner ve yokluğuyla seni bitirir.",
                    "Büyük güç yasalarının 30 dakikalık tam belgeseli aşağıdaki ilgili videoda. İzle ve uyan."
                ]
            },
            "en": {
                "title": "👑 INVISIBLE POWER #3: Knowing When to Leave the Table (Part 3/3 FINAL) #shorts",
                "desc": (
                    "👑 Law of the Peak: The most brutal rule of power is knowing when to leave the table.\n"
                    "The moment they mistake your silence for weakness and patience for helplessness, staying is suicide.\n\n"
                    "⚡ PREVIOUS PARTS:\n"
                    "Part 1: https://youtube.com/shorts/9YDe6hf8xxM\n"
                    "Part 2: https://youtube.com/shorts/4VN_YmNMd6s\n\n"
                    "🎬 FULL 1080p MASTER DOCUMENTARY: https://youtu.be/iFdWgrnk3ik\n\n"
                    "#shorts #thomasshelby #48lawsofpower #psychology #success #gta5"
                ),
                "lines": [
                    "The most brutal rule of invisible power is knowing when to leave the table.",
                    "The moment someone mistakes your silence for weakness and your patience for helplessness...",
                    "Staying at that table is no longer loyalty, it is suicide.",
                    "Thomas Shelby said: A powerful man never argues.",
                    "He turns around and destroys you with his absence.",
                    "The full 30-minute Master Documentary is linked below. Watch it and wake up."
                ]
            },
            "es": {
                "title": "👑 PODER INVISIBLE #3: Saber Cuándo Levantarse de la Mesa (Parte 3/3 FINAL) #shorts",
                "desc": (
                    "👑 Regla de Thomas Shelby: La regla más brutal del poder es saber cuándo levantarse de la mesa. "
                    "Un hombre poderoso no discute; da la espalda y te destruye con su ausencia.\n\n"
                    "🎬 DOCUMENTAL COMPLETO 1080p: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ PARTES ANTERIORES: Part 1 y Part 2 en el canal!\n\n"
                    "#shorts #thomasshelby #48leyesdelpoder #psicologia #gta5"
                ),
                "lines": [
                    "La regla más brutal del poder invisible es saber cuándo levantarse de la mesa.",
                    "En el momento en que confunden tu silencio con debilidad y tu paciencia con resignación...",
                    "Quedarse en esa mesa ya no es lealtad, es suicidio.",
                    "Thomas Shelby dijo: Un hombre poderoso no discute.",
                    "Simplemente se da la vuelta y te destruye con su ausencia.",
                    "El documental completo de 30 minutos está en el video relacionado abajo. Despierta."
                ]
            },
            "pt": {
                "title": "👑 PODER INVISÍVEL #3: Saber Quando Se Levantar da Mesa (Parte 3/3 FINAL) #shorts",
                "desc": (
                    "👑 Lei do Topo: A regra mais implacável do poder é saber quando se levantar da mesa.\n"
                    "Quando confundem seu silêncio com fraqueza, permanecer é suicídio.\n\n"
                    "🎬 DOCUMENTÁRIO COMPLETO 1080p: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ PARTES ANTERIORES: Parte 1 e Parte 2 no canal!\n\n"
                    "#shorts #thomasshelby #48leisdopoder #psicologia #gta5"
                ),
                "lines": [
                    "A regra mais brutal do poder invisível é saber quando se levantar da mesa.",
                    "No momento em que confundem seu silêncio com fraqueza e sua paciência com resignação...",
                    "Continuar nessa mesa não é mais lealdade, é suicídio.",
                    "Thomas Shelby diz: O homem poderoso não discute.",
                    "Ele apenas vira as costas e destrói você com a sua ausência.",
                    "O documentário completo de 30 minutos está no vídeo relacionado abaixo. Assista e desperte."
                ]
            },
            "de": {
                "title": "👑 UNSICHTBARE MACHT #3: Wisse, Wann Du Den Tisch Verlässt (Teil 3/3 FINAL) #shorts",
                "desc": (
                    "👑 Gesetz des Gipfels: Die brutalste Regel der Macht ist zu wissen, wann man den Tisch verlässt.\n"
                    "Wenn sie deine Stille für Schwäche halten, ist Bleiben Selbstmord.\n\n"
                    "🎬 1080p DOKUMENTATION: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ TEIL 1 & 2 auf dem Kanal!\n\n"
                    "#shorts #thomasshelby #48gesetzedermacht #psychologie #gta5"
                ),
                "lines": [
                    "Die brutalste Regel unsichtbarer Macht ist zu wissen, wann man den Tisch verlässt.",
                    "In dem Moment, in dem jemand deine Stille für Schwäche und deine Geduld für Hilflosigkeit hält...",
                    "Ist das Bleiben an diesem Tisch keine Loyalität mehr, sondern Selbstmord.",
                    "Thomas Shelby sagt: Ein starker Mann streitet nicht.",
                    "Er dreht sich um und zerstört dich allein durch seine Abwesenheit.",
                    "Die 30-minütige Meisterdokumentation findest du unten im verlinkten Video. Schau zu und wach auf."
                ]
            },
            "fr": {
                "title": "👑 POUVOIR INVISIBLE #3: Savoir Quand Quitter La Table (Partie 3/3 FINAL) #shorts",
                "desc": (
                    "👑 La Loi du Sommet: La règle la plus impitoyable du pouvoir est de savoir quand quitter la table.\n"
                    "Quand ils prennent ton silence pour de la faiblesse, rester est un suicide.\n\n"
                    "🎬 DOCUMENTAIRE 1080p: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ PARTIE 1 & 2 sur la chaîne!\n\n"
                    "#shorts #thomasshelby #48loisdupouvoir #psychologie #gta5"
                ),
                "lines": [
                    "La règle la plus brutale du pouvoir invisible est de savoir quand quitter la table.",
                    "Dès l'instant où l'on prend ton silence pour de la faiblesse et ta patience pour du désespoir...",
                    "Rester à cette table n'est plus de la loyauté, c'est un suicide.",
                    "Thomas Shelby dit: Un homme puissant ne discute pas.",
                    "Il tourne le dos et te détruit simplement par son absence.",
                    "Le documentaire complet de 30 minutes est lié ci-dessous. Regarde et réveille-toi."
                ]
            },
            "it": {
                "title": "👑 POTERE INVISIBILE #3: Sapere Quando Alzarsi Dal Tavolo (Parte 3/3 FINALE) #shorts",
                "desc": (
                    "👑 Legge del Vertice: La regola più brutale del potere è sapere quando alzarsi dal tavolo.\n"
                    "Quando scambiano il tuo silenzio per debolezza, restare è suicidio.\n\n"
                    "🎬 DOCUMENTARIO COMPLETO: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ PARTE 1 & 2 sul canale!\n\n"
                    "#shorts #thomasshelby #48leggedelpotere #psicologia #gta5"
                ),
                "lines": [
                    "La regola più spietata del potere invisibile è sapere quando alzarsi dal tavolo.",
                    "Nel momento in quegli altri scambiano il tuo silenzio per debolezza e la pazienza per rassegnazione...",
                    "Rimanere a quel tavolo non è lealtà, è un suicidio.",
                    "Thomas Shelby dice: L'uomo potente non discute.",
                    "Si volta semplicemente e ti distrugge con la sua assenza.",
                    "Il documentario completo di 30 minuti è collegato qui sotto. Guarda e risvegliati."
                ]
            },
            "ru": {
                "title": "👑 НЕВИДИМАЯ ВЛАСТЬ #3: Знай, Когда Вставать Из-За Стола (Часть 3/3 ФИНАЛ) #shorts",
                "desc": (
                    "👑 Закон Вершины: Самое беспощадное правило власти — знать, когда нужно встать из-за стола.\n"
                    "Когда твое молчание принимают за слабость, оставаться — самоубийство.\n\n"
                    "🎬 ПОЛНЫЙ ДОКУМЕНТАЛЬНЫЙ ФИЛЬМ: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ ЧАСТИ 1 и 2 на канале!\n\n"
                    "#shorts #томашелби #48законоввласти #психология #gta5"
                ),
                "lines": [
                    "Самое жестокое правило невидимой власти — знать, когда встать из-за стола.",
                    "В тот миг, когда твое молчание принимают за слабость, а терпение за беспомощность...",
                    "Оставаться за этим столом — уже не преданность, а самоубийство.",
                    "Томас Шелби говорит: Сильный человек не вступает в споры.",
                    "Он молча поворачивается и уничтожает тебя своим отсутствием.",
                    "Полный 30-минутный документальный фильм в видео ниже. Смотри и пробуждайся."
                ]
            },
            "ar": {
                "title": "👑 القوة الخفية #3: اعرف متى تغادر الطاولة (الجزء 3/3 الأخير) #shorts",
                "desc": (
                    "👑 قانون القمة: أقسى قاعدة في القوة هي أن تعرف متى تغادر الطاولة.\n"
                    "حين يظنون أن صمتك ضعف وصبرك عجز، البقاء هناك هو انتحار.\n\n"
                    "🎬 الوثائقي الكامل بدقة 1080p: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ الجزآن الأول والثاني على القناة!\n\n"
                    "#shorts #توماس_شيلبي #قوانين_السطوة #علم_النفس #gta5"
                ),
                "lines": [
                    "أقسى قاعدة في القوة الخفية هي معرفة متى يجب أن تغادر الطاولة.",
                    "في اللحظة التي يظنون فيها أن صمتك ضعف وأن صبرك عجز...",
                    "فإن البقاء على تلك الطاولة لم يعد وفاءً، بل هو انتحار حقيقي.",
                    "توماس شيلبي يقول: الرجل القوي لا يجادل أبداً.",
                    "إنه يدير ظهره بكل برود، ويدمرك تماماً بغيابه.",
                    "الفيلم الوثائقي الكامل لقوانين القوة متاح في الفيديو المرتبط بالأسفل. شاهده الآن."
                ]
            },
            "hi": {
                "title": "👑 अदृश्य शक्ति #3: जानें कि मेज़ से कब उठना है (भाग 3/3 अंतिम) #shorts",
                "desc": (
                    "👑 शीर्ष का नियम: शक्ति का सबसे क्रूर नियम यह जानना है कि मेज़ से कब उठना है।\n"
                    "जब वे आपकी चुप्पी को कमज़ोरी समझने लगें, तो वहां रुकना आत्महत्या है।\n\n"
                    "🎬 पूर्ण 1080p वृत्तचित्र: https://youtu.be/iFdWgrnk3ik\n"
                    "⚡ भाग 1 और भाग 2 चैनल पर उपलब्ध हैं!\n\n"
                    "#shorts #थॉमसशेल्बी #48शक्तिनियम #मनोविज्ञान #gta5"
                ),
                "lines": [
                    "अदृश्य शक्ति का सबसे क्रूर नियम यह जानना है कि मेज़ से कब उठना चाहिए।",
                    "जिस पल कोई आपकी चुप्पी को कमज़ोरी और आपके धैर्य को लाचारी समझने लगे...",
                    "उस मेज़ पर बने रहना वफ़ादारी नहीं, बल्कि खुली आत्महत्या है।",
                    "थॉमस शेल्बी कहते हैं: एक शक्तिशाली व्यक्ति कभी बहस नहीं करता।",
                    "वह बस मुड़ता है और अपनी अनुपस्थिति से आपको पूरी तरह तबाह कर देता है।",
                    "शक्ति के नियमों पर 30 मिनट का पूरा वृत्तचित्र नीचे दिया गया है। इसे देखें और जागें."
                ]
            }
        }
    }
}

DEFAULT_BG_MUSIC = BASE_DIR / "assets" / "music" / "mystery_documentary.mp3"


def generate_srt_track(lines: List[str], duration: float, output_srt: Path):
    """Generates standard .srt Closed Caption track with exact timings."""
    chunk_dur = duration / max(1, len(lines))
    srt_lines = []

    def to_time(s):
        hrs = int(s // 3600)
        mins = int((s % 3600) // 60)
        secs = int(s % 60)
        millis = int(round((s - int(s)) * 1000))
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    for i, line in enumerate(lines, 1):
        st = (i - 1) * chunk_dur
        en = min(duration, i * chunk_dur)
        srt_lines.append(str(i))
        srt_lines.append(f"{to_time(st)} --> {to_time(en)}")
        srt_lines.append(line.strip())
        srt_lines.append("")

    output_srt.parent.mkdir(parents=True, exist_ok=True)
    with open(output_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))


def generate_vtt_track(lines: List[str], duration: float, output_vtt: Path):
    """Generates standard .vtt Closed Caption track."""
    chunk_dur = duration / max(1, len(lines))
    vtt_lines = ["WEBVTT", ""]

    def to_time(s):
        hrs = int(s // 3600)
        mins = int((s % 3600) // 60)
        secs = int(s % 60)
        millis = int(round((s - int(s)) * 1000))
        return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"

    for i, line in enumerate(lines, 1):
        st = (i - 1) * chunk_dur
        en = min(duration, i * chunk_dur)
        vtt_lines.append(f"{to_time(st)} --> {to_time(en)}")
        vtt_lines.append(line.strip())
        vtt_lines.append("")

    output_vtt.parent.mkdir(parents=True, exist_ok=True)
    with open(output_vtt, "w", encoding="utf-8") as f:
        f.write("\n".join(vtt_lines))


async def synthesize_voice_stem(text: str, lang_code: str, output_voice_path: Path) -> bool:
    """
    Synthesizes clean neural voice stem using ElevenLabs (primary) or Edge-TTS (fallback).
    """
    output_voice_path.parent.mkdir(parents=True, exist_ok=True)
    lang_info = GLOBAL_10_LANGUAGES.get(lang_code, GLOBAL_10_LANGUAGES["en"])

    # 1. Primary Tier: ElevenLabs Multilingual V2
    if get_elevenlabs_api_key():
        voice_id = lang_info.get("eleven_voice", "pNInz6obpgDQGcFmaJgB")
        success = synthesize_elevenlabs_speech(
            text=text,
            output_path=output_voice_path,
            voice_id=voice_id,
            model_id="eleven_multilingual_v2"
        )
        if success and output_voice_path.exists() and output_voice_path.stat().st_size > 1000:
            return True

    # 2. Secondary Tier: Edge-TTS Neural Voice
    voice = lang_info["voice"]
    try:
        comm = edge_tts.Communicate(text, voice, rate="-3%", pitch="-1Hz")
        await comm.save(str(output_voice_path))
        if output_voice_path.exists() and output_voice_path.stat().st_size > 1000:
            return True
    except Exception as e:
        print(f"⚠️ Edge-TTS notu ({lang_code}): {e}")

    return False


def master_broadcast_audio_track(
    raw_voice_path: Path,
    output_master_path: Path,
    target_duration: float,
    bg_music_path: Optional[Path] = None
) -> bool:
    """
    Mixes voice with background music and masters to broadcast specification:
    Loudness: -15.0 LUFS
    True Peak: -0.7 dB TP
    Format: 320 kbps 48kHz Stereo MP3
    """
    if not raw_voice_path.exists():
        print(f"❌ Ham ses dosyası bulunamadı: {raw_voice_path}")
        return False

    bg_music = bg_music_path or DEFAULT_BG_MUSIC
    if not bg_music.exists():
        bg_music = DEFAULT_BG_MUSIC

    output_master_path.parent.mkdir(parents=True, exist_ok=True)

    # Professional multi-stage mastering filter graph
    filter_complex = (
        "[0:a]volume=2.2,loudnorm=I=-14:TP=-1.0:LRA=7[v_voice];"
        "[1:a]volume=0.06,lowpass=f=5000,highpass=f=120[v_bg];"
        "[v_voice][v_bg]amix=inputs=2:duration=first:weights=5 1:dropout_transition=0,"
        "aformat=channel_layouts=stereo,loudnorm=I=-15.0:TP=-0.7:LRA=7[a_out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_voice_path),
        "-stream_loop", "-1", "-i", str(bg_music),
        "-filter_complex", filter_complex,
        "-map", "[a_out]",
        "-t", f"{target_duration:.2f}",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        "-ar", "48000",
        str(output_master_path)
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_master_path.exists() and output_master_path.stat().st_size > 2000
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg mastering hatası: {e}")
        return False


async def produce_10_language_suite_for_episode(episode_key: str, force_rebuild: bool = False) -> Dict:
    """
    Synthesizes neural voice tracks, masters broadcast audio tracks (-15.0 LUFS / -0.7 dB TP),
    and generates SRT/VTT subtitles for all 10 languages for the target episode.
    """
    if episode_key not in TRILOGY_CATALOG:
        raise ValueError(f"Bilinmeyen bölüm: {episode_key}")

    ep = TRILOGY_CATALOG[episode_key]
    vid = ep["video_id"]
    duration = ep["duration"]
    out_dir = BASE_DIR / "output" / f"global_10lang_{episode_key}_{vid}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print(f"🌍 10 DİL KÜRESEL SUITE ÜRETİMİ: {episode_key.upper()} ({vid})")
    print(f"⏱️ Hedef Süre: {duration:.2f}s | Hedef LUFS: -15.0 LUFS | True Peak: -0.7 dB")
    print("=" * 70)

    results = {}
    for lang_code, lang_data in ep["languages"].items():
        title = lang_data["title"]
        desc = lang_data["desc"]
        lines = lang_data["lines"]
        full_text = " ".join(lines)

        raw_voice_file = out_dir / f"{episode_key}_voice_{lang_code}.mp3"
        master_audio_file = out_dir / f"{episode_key}_audio_track_{lang_code}.mp3"
        srt_file = out_dir / f"{episode_key}_subtitles_{lang_code}.srt"
        vtt_file = out_dir / f"{episode_key}_subtitles_{lang_code}.vtt"

        # 1. Subtitles
        generate_srt_track(lines, duration, srt_file)
        generate_vtt_track(lines, duration, vtt_file)

        # 2. Voice Stem Synthesis
        if not raw_voice_file.exists() or force_rebuild:
            print(f"🎙️ [{lang_code.upper()}] Ses sentezleniyor ({GLOBAL_10_LANGUAGES[lang_code]['name']})...")
            await synthesize_voice_stem(full_text, lang_code, raw_voice_file)

        # 3. Broadcast Audio Track Mastering (-15 LUFS, -0.7 TP)
        if not master_audio_file.exists() or force_rebuild:
            print(f"🎚️ [{lang_code.upper()}] Broadcast miksaj & mastering (-15.0 LUFS, -0.7 dB TP)...")
            master_broadcast_audio_track(raw_voice_file, master_audio_file, duration)

        results[lang_code] = {
            "title": title,
            "desc": desc,
            "raw_voice": raw_voice_file,
            "master_audio": master_audio_file,
            "srt": srt_file,
            "vtt": vtt_file,
            "ready": master_audio_file.exists() and srt_file.exists()
        }

        print(f"  ✅ [{lang_code.upper()}] Hazır: Audio: {master_audio_file.name} ({master_audio_file.stat().st_size / 1024:.1f} KB)")

    # Save metadata dictionary manifest
    manifest_path = out_dir / "suite_manifest.json"
    manifest_data = {
        "episode_key": episode_key,
        "video_id": vid,
        "duration": duration,
        "languages": {
            lc: {
                "title": r["title"],
                "desc": r["desc"],
                "audio_track": str(r["master_audio"]),
                "srt": str(r["srt"]),
                "vtt": str(r["vtt"])
            }
            for lc, r in results.items()
        }
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    print(f"\n📦 Manifest kaydedildi: {manifest_path}")
    return results


async def produce_all_trilogy_10_language_suites():
    """Generates all 10 languages for the entire Trilogy (Part 1, Part 2, Part 3)."""
    for ep_key in ["part1", "part2", "part3"]:
        await produce_10_language_suite_for_episode(ep_key)


if __name__ == "__main__":
    asyncio.run(produce_all_trilogy_10_language_suites())
