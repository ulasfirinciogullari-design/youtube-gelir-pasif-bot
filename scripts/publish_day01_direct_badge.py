import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.studio_browser_bridge import launch_stealth_context

async def publish_badge():
    p, context = await launch_stealth_context(headless=True)
    page = await context.new_page()

    try:
        print("🌐 YouTube Studio Shorts sayfasına gidiliyor...")
        await page.goto("https://studio.youtube.com/channel/UCgvESYtYbn2w9R2ExBOF_cw/videos/short", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)

        first_row = page.locator("ytcp-video-row").first
        await first_row.hover()
        await page.wait_for_timeout(1000)

        # Click Taslağı düzenle
        edit_draft = first_row.locator('button:has-text("Taslağı düzenle"), ytcp-button:has-text("Taslağı düzenle")').first
        print("👉 Taslağı düzenle butonuna tıklandı...")
        await edit_draft.click(force=True)
        await page.wait_for_timeout(4000)

        # Check 'Çocuklara özel değil'
        not_for_kids = page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first
        if await not_for_kids.count() > 0 and await not_for_kids.is_visible():
            await not_for_kids.click(force=True)
            print("👶 'Çocuklara özel değil' seçildi.")
            await page.wait_for_timeout(1000)

        # Directly click Step Badge 3: Görünürlük
        badge_vis = page.locator('#step-badge-3, button:has-text("Görünürlük")').first
        if await badge_vis.count() > 0 and await badge_vis.is_visible():
            print("🎯 'Görünürlük' sekmesine tıklandı (#step-badge-3)...")
            await badge_vis.click(force=True)
            await page.wait_for_timeout(2500)
        else:
            # Fallback to next button
            for _ in range(3):
                nxt = page.locator('#next-button').first
                if await nxt.count() > 0 and await nxt.is_visible():
                    await nxt.click(force=True)
                    await page.wait_for_timeout(2000)

        await page.screenshot(path="output/step3_visibility_view.png")

        # Select Public
        pub_radio = page.locator('tp-yt-paper-radio-button[name="PUBLIC"]').first
        if await pub_radio.count() > 0 and await pub_radio.is_visible():
            await pub_radio.click(force=True)
            print("🌐 'Herkese açık' seçildi!")
            await page.wait_for_timeout(1500)

        # Done button
        done_btn = page.locator('#done-button, button:has-text("Kaydet"), ytcp-button:has-text("Kaydet")').first
        if await done_btn.count() > 0 and await done_btn.is_visible():
            await done_btn.click(force=True)
            print("💾 'Kaydet' butonuna tıklandı!")
            await page.wait_for_timeout(6000)

        # Handle 'Yine de yayınla' if appears
        yine_de = page.locator('ytcp-button:has-text("Yine de yayınla"), button:has-text("Yine de yayınla")').first
        if await yine_de.count() > 0 and await yine_de.is_visible():
            await yine_de.click(force=True)
            print("👉 'Yine de yayınla' onaylandı!")
            await page.wait_for_timeout(5000)

        # Close
        close_btn = page.locator('#close-button, button:has-text("Kapat"), ytcp-button:has-text("Kapat")').first
        if await close_btn.count() > 0 and await close_btn.is_visible():
            await close_btn.click(force=True)
            print("✅ Modal kapatıldı.")
            await page.wait_for_timeout(3000)

        # Verification
        await page.goto("https://studio.youtube.com/channel/UCgvESYtYbn2w9R2ExBOF_cw/videos/short", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        await page.screenshot(path="output/verified_day01_published_public_badge.png")

        first_row = page.locator("ytcp-video-row").first
        title = await first_row.locator("#video-title").inner_text()
        vis = await first_row.locator(".cell-body.style-scope.ytcp-video-row.tablecell-visibility").inner_text()
        link = await first_row.locator("a#video-title").get_attribute("href")
        print("\n🎉 [SONUÇ]")
        print(f"Başlık: {title.strip()}")
        print(f"Görünürlük: {vis.strip()}")
        print(f"Link: {link}")

    except Exception as e:
        print(f"❌ Hata: {e}")
        await page.screenshot(path="output/badge_error.png")
    finally:
        await context.close()
        await p.stop()

if __name__ == "__main__":
    asyncio.run(publish_badge())
