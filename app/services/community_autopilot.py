"""
YouTube Community Tab Autopilot for 'Zirvenin Kanunu' (@zirveninkanunu).
Automates viral psychological dilemma poll posting using headless Playwright and authenticated profile.
"""

import asyncio
import sys
import json
from pathlib import Path
from playwright.async_api import async_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROFILE_DIR = Path(r"C:\youtube_studio_profile")
CHROME_BIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"

VIRAL_POLL_VAULT = [
    {
        "id": "poll_enemy_forgive_or_wait",
        "question": "Düşmanını affeder misin, yoksa doğru anı mı beklersin? ♟️",
        "options": [
            "Doğru anı ve zayıf anını beklerim (Stratejik Sabır)",
            "Affederim ama asla unutmam (Soğuk Mesafe)",
            "Affetmek zayıflıktır, tamamen yok ederim (48 Güç Yasası)",
            "Umursamam, kendi zirveme odaklanırım (Stoacı Güç)"
        ],
        "category": "power_strategy",
        "virality_score": 98
    },
    {
        "id": "poll_shelby_vs_corleone",
        "question": "Thomas Shelby mi, Michael Corleone mi? Aynı masada otursalar kim kazanır? 👑",
        "options": [
            "Thomas Shelby (Soğukkanlı Zeka & Gözükaralık)",
            "Michael Corleone (Stratejik Ağırlık & Acımasızlık)",
            "Masadan ikisi de ortak olarak kalkar",
            "Birbirlerini tamamen yok ederler"
        ],
        "category": "cinema_power",
        "virality_score": 99
    },
    {
        "id": "poll_first_eye_contact_break",
        "question": "Bir odaya girdiğinde bakışlarını ilk kaçıran sen mi olursun, karşı taraf mı? 👁️",
        "options": [
            "Karşı taraf (Göz temasında asla geri adım atmam)",
            "Bazen ben (Gereksiz güç savaşına girmem)",
            "Göz teması yerine beden dilini okurum",
            "Umursamam, sessizce odayı tartar ve yönetirim"
        ],
        "category": "body_language",
        "virality_score": 97
    },
    {
        "id": "poll_silence_power_or_weakness",
        "question": "Sessizlik bir güç gösterisi midir, yoksa zayıflık mı? 🤫",
        "options": [
            "En büyük güç gösterisidir (Zihni okunamayan kazanır)",
            "Duruma göre zayıflık gibi algılanabilir",
            "Sadece arkasında stratejik eylem varsa güçtür",
            "Zayıfların kaçış noktasıdır"
        ],
        "category": "psychology",
        "virality_score": 96
    },
    {
        "id": "poll_48_laws_law_1",
        "question": "48 Güç Yasası #1: Asla Efendini Gölgede Bırakma. Bu kurala katılıyor musun? ♟️",
        "options": [
            "Kesinlikle evet (Kıskançlık en ölümcül tuzaktır)",
            "Hayır (Yetenekli olan her zaman parlamalı)",
            "Kural doğrudur ama efendini geçme anını iyi seçmelisin",
            "Kendi efendim benim, kimsenin gölgesinde kalmam"
        ],
        "category": "power_laws",
        "virality_score": 98
    }
]

def get_posted_poll_ids():
    state_file = BASE_DIR / "output" / "posted_polls.json"
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def record_posted_poll_id(poll_id):
    state_file = BASE_DIR / "output" / "posted_polls.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    posted = get_posted_poll_ids()
    if poll_id not in posted:
        posted.append(poll_id)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(posted, f, indent=2, ensure_ascii=False)

async def post_next_viral_poll(force_poll_id=None):
    posted_ids = get_posted_poll_ids()
    target_poll = None
    
    if force_poll_id:
        target_poll = next((p for p in VIRAL_POLL_VAULT if p["id"] == force_poll_id), None)
    else:
        for p in VIRAL_POLL_VAULT:
            if p["id"] not in posted_ids:
                target_poll = p
                break
                
    if not target_poll:
        print("ℹ️ Tüm viral anketler yayınlanmış durumda. İlk ankete dönülüyor...")
        target_poll = VIRAL_POLL_VAULT[0]

    question = target_poll["question"]
    options = target_poll["options"]
    
    print("\n" + "=" * 70)
    print("🚀 YOUTUBE TOPLULUK SEKME ANKETİ YAYINLANIYOR")
    print(f"📊 Poll ID: {target_poll['id']}")
    print(f"❓ Soru: {question}")
    for idx, opt in enumerate(options, 1):
        print(f"   {idx}. {opt}")
    print("=" * 70)

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            executable_path=CHROME_BIN,
            headless=True,
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            ignore_default_args=['--enable-automation'],
            args=['--disable-blink-features=AutomationControlled']
        )
        page = await browser.new_page()
        await page.goto('https://studio.youtube.com', wait_until='domcontentloaded', timeout=40000)
        await page.wait_for_timeout(3000)
        
        # Click 'Oluştur'
        create_btn = page.locator('#create-icon, button:has-text("Oluştur")').first
        await create_btn.click()
        await page.wait_for_timeout(1500)
        
        # Click 'Gönderi oluştur'
        post_item = page.locator('tp-yt-paper-item:has-text("Gönderi oluştur")').first
        async with page.expect_popup() as pinfo:
            await post_item.click()
        posts_page = await pinfo.value
        await posts_page.wait_for_load_state('domcontentloaded')
        await posts_page.wait_for_timeout(4000)
        
        # Click 'Metin anket'
        poll_btn = posts_page.locator('button:has-text("Metin anket"), ytd-button-renderer:has-text("Metin anket")').first
        await poll_btn.click()
        await posts_page.wait_for_timeout(2000)
        
        # Fill question box
        q_box = posts_page.locator('#contenteditable-root, #contenteditable-textarea, [placeholder*="Topluluğunuza"]').first
        await q_box.click()
        await posts_page.wait_for_timeout(500)
        await posts_page.keyboard.insert_text(question)
        await posts_page.wait_for_timeout(1000)
        print("✍️ Anket sorusu başarıyla yazıldı.")
        
        # Click 'Bir seçenek daha ekle' twice to have 4 options
        add_more_link = posts_page.locator('text="Bir seçenek daha ekle"').first
        for i in range(2):
            if await add_more_link.is_visible():
                await add_more_link.click()
                await posts_page.wait_for_timeout(1000)
                print(f"➕ Seçenek kutusu {i+3} eklendi.")
                
        # Fill all available option fields
        option_inputs = await posts_page.locator('input[placeholder="Seçenek ekle"]').all()
        print(f"Toplam seçenek kutusu: {len(option_inputs)}")
        
        for idx, opt_text in enumerate(options):
            if idx < len(option_inputs):
                await option_inputs[idx].click()
                await posts_page.wait_for_timeout(200)
                await posts_page.keyboard.insert_text(opt_text)
                await posts_page.wait_for_timeout(300)
                print(f"   Seçenek {idx+1} girildi: {opt_text}")
                
        await posts_page.wait_for_timeout(2000)
        
        # Click 'Gönder'
        all_send_btns = await posts_page.locator('button:has-text("Gönder")').all()
        clicked = False
        for btn in all_send_btns:
            if await btn.is_visible():
                print("🚀 Görünür 'Gönder' butonu bulundu, yayınlanıyor...")
                await btn.click()
                clicked = True
                break
                
        if clicked:
            await posts_page.wait_for_timeout(5000)
            record_posted_poll_id(target_poll["id"])
            screenshot_path = BASE_DIR / "output" / f"poll_published_{target_poll['id']}.png"
            await posts_page.screenshot(path=str(screenshot_path))
            print(f"🎉 Anket Başarıyla Yayınlandı! Ekran görüntüsü: {screenshot_path.name}")
        else:
            print("⚠️ Gönder butonu bulunamadı.")
            
        await browser.close()
        return clicked

if __name__ == "__main__":
    asyncio.run(post_next_viral_poll())
