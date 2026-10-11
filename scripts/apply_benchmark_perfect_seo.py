"""
Apply Perfect SEO & Save to Hollywood Benchmark Short (KbUcN2rQPTs)
Using proven Polymer reactivity trigger.
"""

import asyncio
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.studio_browser_bridge import launch_stealth_context

VIDEO_ID = "KbUcN2rQPTs"
TITLE = "👑 Thomas Shelby: Zirvedeki Adamın 3 Sessizlik Kuralı (Psikolojik Hakimiyet) #shorts"

DESCRIPTION = (
    "👑 Zirvenin Kanunu: Zeki insanlar asla bağırmaz. Gerçek güç, odadaki en sessiz adamın cebindedir.\n\n"
    "♟️ 3 ACIMASIZ SESSİZLİK KURALI:\n"
    "1. Gözlerini asla ilk kaçıran sen olma. Bir saniyelik tereddüt bile zayıflığını fısıldar.\n"
    "2. Bir soru sorulduğunda hemen cevap verme; 3 saniye bekle. Bu 3 saniye rakibini savunmasız bırakır.\n"
    "3. Haklı olduğunda bile kendini savunma; sessizlik kralların tavrıdır.\n\n"
    "🎬 1080p TAM HOLLYWOOD BELGESELİ: Alttaki ilgili videodan izleyin!\n"
    "https://youtu.be/iFdWgrnk3ik\n\n"
    "💬 Sen bir ortamda hangisisin? Çok konuşan mı (1), yoksa sessizce izleyen mi (2)? Dürüstçe yoruma yaz!\n\n"
    "#shorts #thomasshelby #peakyblinders #güç #psikoloji #başarı #zirveninkanunu #48güçyasası #stoacılık"
)


async def main():
    p, context = await launch_stealth_context(headless=True)
    page = await context.new_page()

    try:
        url = f"https://studio.youtube.com/video/{VIDEO_ID}/edit"
        print(f"Opening video edit page: {url}")
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(4000)

        # 1. Update Title & Description via DOM dispatch
        res = await page.evaluate('''(data) => {
            const boxes = document.querySelectorAll('div#textbox.style-scope.ytcp-social-suggestions-textbox');
            let titleSet = false, descSet = false;
            if (boxes.length >= 1 && data.title) {
                boxes[0].focus();
                boxes[0].innerText = data.title;
                boxes[0].dispatchEvent(new Event('input', { bubbles: true }));
                boxes[0].dispatchEvent(new Event('change', { bubbles: true }));
                titleSet = true;
            }
            if (boxes.length >= 2 && data.desc) {
                boxes[1].focus();
                boxes[1].innerText = data.desc;
                boxes[1].dispatchEvent(new Event('input', { bubbles: true }));
                boxes[1].dispatchEvent(new Event('change', { bubbles: true }));
                descSet = true;
            }
            return { titleSet, descSet, count: boxes.length };
        }''', {'title': TITLE, 'desc': DESCRIPTION})
        print(f"DOM Meta Güncelleme: {res}")
        await page.wait_for_timeout(1500)

        # 2. Trigger Polymer reactivity on title
        box0 = page.locator("div#textbox").first
        if await box0.is_visible():
            await box0.click()
            await page.keyboard.press("End")
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(1000)

        # 3. Trigger Polymer reactivity on description
        box1 = page.locator("div#textbox").nth(1)
        if await box1.is_visible():
            await box1.click()
            await page.keyboard.press("End")
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(1000)

        # 4. Save button
        save_btn = page.locator('#save, ytcp-button#save-button, button:has-text("Kaydet"), ytcp-button:has-text("Kaydet")').first
        if await save_btn.is_visible():
            is_enabled = await save_btn.is_enabled()
            print(f"Save button enabled: {is_enabled}")
            if is_enabled:
                await save_btn.click(force=True)
                print("💾 'Kaydet' butonuna tıklandı!")
                await page.wait_for_timeout(4000)

        # Screenshot proof
        await page.screenshot(path="output/benchmark_seo_saved_proof.png")
        print("📸 Kanıt kaydedildi: output/benchmark_seo_saved_proof.png")

    finally:
        await context.close()
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
