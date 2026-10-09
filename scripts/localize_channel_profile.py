"""
Applies all 10 major world languages to the YouTube Channel Profile.
"""
import sys
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

with open('token.json', 'r', encoding='utf-8') as f:
    token_data = json.load(f)

creds = Credentials.from_authorized_user_info(token_data)
yt = build('youtube', 'v3', credentials=creds)

channel_id = "UCgvESYtYbn2w9R2ExBOF_cw"

# 10 major global languages (excluding default language 'tr' to avoid redundant key in localizations)
LOCALIZATIONS = {
    "en": {
        "title": "Law of the Peak | Unwritten Rules of Power",
        "description": "Unseen Power, Dark Psychology, and the Greatest Cinematic Masterpieces.\n\nNo cliché advice here. Only ruthless truths, the unwritten rules of reality, and the price of staying at the peak.\n\n⚡ Daily 60 FPS cinematic philosophy, strategic breakdowns, and mind-expanding revelations.\n\n#psychology #motivation #cinema #power #sigma #stoicism"
    },
    "de": {
        "title": "Das Gesetz des Gipfels | Ungeschriebene Gesetze der Macht",
        "description": "Unsichtbare Macht, dunkle Psychologie und legendäre Momente der Kinogeschichte.\n\nKeine Standard-Ratschläge. Nur ungeschönte Wahrheiten, die ungeschriebenen Gesetze des Lebens und der Preis für den Erfolg an der Spitze.\n\n⚡ Täglich neue cineastische 60-FPS-Analysen und tiefgründige Einblicke.\n\n#psychologie #macht #erfolg #kino #philosophie"
    },
    "es": {
        "title": "La Ley de la Cima | Leyes Ocultas del Poder",
        "description": "Poder invisible, psicología oscura y los momentos más intensos de la historia del cine.\n\nAquí no hay consejos comunes. Solo verdades implacables, las reglas no escritas de la realidad y el precio de mantenerse en la cima.\n\n⚡ Análisis cinematográficos diarios a 60 FPS y reflexiones estratégicas.\n\n#psicologia #motivacion #cine #poder #exito"
    },
    "fr": {
        "title": "La Loi du Sommet | Règles Secrètes du Pouvoir",
        "description": "Pouvoir invisible, psychologie sombre et chefs-d'œuvre cinématographiques.\n\nPas de conseils banals. Seulement des vérités impitoyables, les règles non écrites de la vie et le prix du pouvoir absolu.\n\n⚡ Analyses quotidiennes en 60 FPS et leçons de stratégie.\n\n#psychologie #pouvoir #cinema #motivation #philosophie"
    },
    "pt": {
        "title": "A微 Lei do Topo | Leis Ocultas do Poder",
        "description": "Poder invisível, psicologia obscura e momentos épicos do cinema mundial.\n\nSem conselhos clichês. Apenas verdades cruas, as regras não escritas da vida e o preço de permanecer no topo.\n\n⚡ Análises cinematográficas diárias em 60 FPS.\n\n#psicologia #poder #cinema #estoicismo #motivacao"
    },
    "it": {
        "title": "La Legge del Vertice | Regole del Potere",
        "description": "Potere invisibile, psicologia oscura e i momenti più alti del cinema.\n\nNessun consiglio banale. Solo verità spietate, le leggi non scritte del mondo e il prezzo del comando.\n\n⚡ Analisi quotidiane a 60 FPS.\n\n#psicologia #potere #cinema #successo"
    },
    "ru": {
        "title": "Закон Вершины | Тайные Законы Власти",
        "description": "Невидимая власть, тёмная психология и культовые моменты мирового кино.\n\nНикаких банальных советов. Только суровая правда, неписаные законы жизни и цена превосходства.\n\n⚡ Ежедневные кинематографичные разборы 60 FPS.\n\n#психология #власть #кино #саморазвитие"
    },
    "ja": {
        "title": "頂点の掟 | 権力と帝王学の真実",
        "description": "見えざる権力、ダークサイコロジー、そして映画史に輝く不朽の名作。\n\nありきたりな教訓はありません。冷徹な現実、人生の不文律、そして頂点に立ち続けるための代償。\n\n⚡ 毎日更新：60 FPS シネマティック哲学＆戦略分析。\n\n#帝王学 #心理学 #映画 #成功哲学"
    },
    "ar": {
        "title": "قانون القمة | القواعد غير المكتوبة للقوة",
        "description": "القوة الخفية، علم النفس المظلم، وأعظم لحظات السينما العالمية.\n\nلا نصائح تقليدية هنا. فقط الحقائق القاسية، قوانين الحياة غير المكتوبة، وثمن البقاء في القمة.\n\n⚡ تحليلات سينمائية يومية بدقة 60 إطار في الثانية.\n\n#علم_النفس #قوة #سينما #تحفيز #هيبة"
    },
    "hi": {
        "title": "शिखर का कानून | सत्ता और शक्ति के नियम",
        "description": "अदृश्य शक्ति, डार्क साइकोलॉजी, और सिनेमाई इतिहास के महानतम दृश्य।\n\nकोई साधारण सलाह नहीं। केवल कठोर सच्चाई, दुनिया के अनकहे नियम और शिखर पर बने रहने की कीमत।\n\n⚡ दैनिक 60 FPS सिनेमाई विश्लेषण और रणनीतिक सबक।\n\n#मनोविज्ञान #शक्ति #सिनेमा #सफलता"
    }
}

try:
    res = yt.channels().update(
        part="localizations",
        body={
            "id": channel_id,
            "localizations": LOCALIZATIONS
        }
    ).execute()
    print("SUCCESS: 10 Global Languages successfully injected into YouTube Channel Profile!")
    locs = res.get("localizations", {})
    for lang, val in locs.items():
        print(f" - [{lang.upper()}] {val.get('title')}")
except Exception as e:
    print(f"Update failed: {e}")
