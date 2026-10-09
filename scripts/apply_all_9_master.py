import sys, os, json
sys.path.insert(0, os.path.abspath("."))
sys.stdout.reconfigure(encoding='utf-8')
from publish_to_youtube import get_authenticated_service

ALL_LOCALIZATIONS = {
    "en_US": {
        "title": "Law of the Peak | Unwritten Laws of Power",
        "description": "Unseen Power, Dark Psychology, and Masterpieces of Cinema History.\n\nNo cliché advice here. Only ruthless truths, the unwritten rules of human nature, and the price of staying at the peak.\nFrom ancient battlefield strategists (Sun Tzu, Musashi, Marcus Aurelius) to cinematic masterminds and quantum cosmic enigmas.\n\n⚡ Daily 60 FPS cinematic philosophy, strategic breakdowns, and mind-expanding revelations.\n\n#psychology #motivation #cinema #power #sigma #stoicism #mindset"
    },
    "de_DE": {
        "title": "Das Gesetz des Gipfels | Gesetze der Macht",
        "description": "Unsichtbare Macht, dunkle Psychologie und legendäre Momente der Kinogeschichte.\n\nKeine oberflächlichen Ratschläge. Nur ungeschönte Wahrheiten, die ungeschriebenen Gesetze des Lebens und der wahre Preis für den Erfolg an der Spitze.\nVon antiken Meisterstrategen (Sun Tzu, Musashi, Marcus Aurelius) bis hin zu genialen Filmikonen und kosmischen Mysterien.\n\n⚡ Täglich neue cineastische 60-FPS-Analysen, strategische Prinzipien und tiefgründige Einblicke.\n\n#psychologie #macht #erfolg #kino #philosophie #stoizismus"
    },
    "es_ES": {
        "title": "La Ley de la Cima | Leyes Ocultas del Poder",
        "description": "Poder invisible, psicología oscura y los momentos cumbre de la historia del cine.\n\nAquí no hay clichés ni consejos vacíos. Solo verdades implacables, las leyes no escritas de la realidad y el precio de mantenerse en la cima.\nDesde los más grandes estrategas de la historia (Sun Tzu, Musashi, Marco Aurelio) hasta los enigmas del cosmos y mentes maestras del cine.\n\n⚡ Análisis cinematográficos diarios a 60 FPS, tácticas de alta psicología y reflexiones transformadoras.\n\n#psicologia #motivacion #cine #poder #exito #estoicismo"
    },
    "fr_FR": {
        "title": "La Loi du Sommet | Règles Secrètes du Pouvoir",
        "description": "Pouvoir invisible, psychologie sombre et chefs-d'œuvre de l'histoire du cinéma.\n\nPas de discours convenus. Seulement des vérités impitoyables, les lois non écrites du monde et le prix de la domination absolue.\nDes stratèges antiques légendaires (Sun Tzu, Musashi, Marc Aurèle) aux énigmes cosmiques et génies de l'écran.\n\n⚡ Analyses quotidiennes en 60 FPS, décryptages stratégiques et leçons de puissance mentale.\n\n#psychologie #pouvoir #cinema #motivation #philosophie #stoicisme"
    },
    "ru_RU": {
        "title": "Закон Вершины | Тайные Законы Власти",
        "description": "Невидимая власть, тёмная психология и культовые моменты мирового кинематографа.\n\nНикаких банальных советов. Только суровая правда, неписаные законы реальности и цена превосходства на вершине.\nОт великих полководцев прошлого (Сунь-цзы, Мусаси, Марк Аврелий) до квантовых парадоксов вселенной и гениальных киногероев.\n\n⚡ Ежедневные кинематографичные разборы 60 FPS, законы власти и железная дисциплина.\n\n#психология #власть #кино #саморазвитие #философия #стоицизм"
    },
    "pt_BR": {
        "title": "A Lei do Topo | Leis Ocultas do Poder",
        "description": "Poder invisível, psicologia obscura e os maiores clássicos do cinema mundial.\n\nSem conselhos vazios. Apenas verdades cruas, as regras não escritas do jogo e o preço de permanecer invicto no topo.\nDos maiores estrategistas militares (Sun Tzu, Musashi, Marco Aurélio) aos mistérios do universo e mentes implacáveis.\n\n⚡ Análises cinematográficas diárias em 60 FPS e filosofia estratégica de alto impacto.\n\n#psicologia #poder #cinema #estoicismo #motivacao #mente"
    },
    "it_IT": {
        "title": "La Legge del Vertice | Regole del Potere",
        "description": "Potere invisibile, psicologia oscura e i momenti più alti del cinema mondiale.\n\nNessun consiglio banale. Solo verità spietate, le leggi non scritte del mondo e il prezzo del comando assoluto.\nDai grandi maestri di strategia (Sun Tzu, Musashi, Marco Aurelio) alle profondità del cosmo e icone cinematografiche.\n\n⚡ Analisi cinematografiche quotidiane a 60 FPS e percorsi di dominio psicologico.\n\n#psicologia #potere #cinema #successo #filosofia #stoicismo"
    },
    "ja_JP": {
        "title": "頂点の掟 | 権力と帝王学の真実",
        "description": "見えざる権力、ダークサイコロジー、そして映画史に刻まれた伝説的名場面。\n\nありきたりな自己啓発はありません。冷徹な真実、人生の不文律、そして頂点に立ち続ける者だけが知る代償。\n宮本武蔵、孫子、マルクス・アウレリウスといった戦略の巨人から、宇宙の深淵と不朽の名作まで。\n\n⚡ 毎日更新：60 FPS シネマティック哲学、知略分析、至高の自己規律。\n\n#帝王学 #心理学 #映画 #成功哲学 #宮本武蔵 #武士道"
    },
    "ar_EG": {
        "title": "قانون القمة | القواعد غير المكتوبة للقوة",
        "description": "القوة الخفية، علم النفس المظلم، وأعظم لحظات السينما العالمية.\n\nلا نصائح تقليدية هنا. فقط الحقائق القاسية، قوانين الحياة غير المكتوبة، وثمن البقاء على القمة.\nمن أعظم استراتيجيي التاريخ (صن تزو، موساشي، ماركوس أوريليوس) إلى ألغاز الكون وعقول السينما الفذة.\n\n⚡ تحليلات سينمائية يومية بدقة 60 إطار في الثانية، استراتيجيات عقلية، وبناء الهيبة الشخصية.\n\n#علم_النفس #قوة #سينما #تحفيز #هيبة #فلسفة #عقلية"
    }
}

try:
    yt = get_authenticated_service()
    resp = yt.channels().list(part="localizations", mine=True).execute()
    ch_id = resp["items"][0]["id"]
    
    body = {
        "id": ch_id,
        "localizations": ALL_LOCALIZATIONS
    }
    
    print("Güncelleme gönderiliyor...")
    res = yt.channels().update(part="localizations", body=body).execute()
    
    locs = res.get("localizations", {})
    print("\n" + "="*70)
    print("🏆 KANAL LOKALİZASYONU TÜM DİLLERDE CANLI VE AKTİF!")
    print(f"Kanal ID: {ch_id}")
    print(f"Toplam Dil Sayısı: {len(locs)}")
    print("="*70)
    for code, data in locs.items():
        print(f"🌍 [{code.upper()}]: {data.get('title')}")
        print(f"   ↳ {data.get('description', '').splitlines()[0]}")
    print("="*70)

except Exception as e:
    print("Hata:", e)
