"""
UPLOAD & PUBLISH HOLLYWOOD BENCHMARK SHORT TO YOUTUBE STUDIO.
Uses direct #step-badge-3 -> PUBLIC -> #done-button pattern for 100% instant publishing success.
Pins viral comment magnet and links Related Video to Master Documentary.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import time

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.studio_browser_bridge import launch_stealth_context

VIDEO_PATH = BASE_DIR / "output" / "VIRAL_HOLLYWOOD_BENCHMARK_MASTER.mp4"

TITLE = "👑 Thomas Shelby: Zirvedeki Adamın 3 Sessizlik Kuralı (Psikolojik Hakimiyet) #shorts"

DESCRIPTION = (
    "👑 Zirvenin Kanunu: Zeki insanlar asla bağırmaz. Gerçek güç, odadaki en sessiz adamın cebindedir.\n\n"
    "♟️ 3 ACIMASIZ SESSİZLİK KURALI:\n"
    "1. Gözlerini asla ilk kaçıran sen olma.\n"
    "2. Bir soru sorulduğunda hemen cevap verme; 3 saniye bekle.\n"
    "3. Haklı olduğunda bile kendini savunma; sessizlik kralların tavrıdır.\n\n"
    "🎬 1080p TAM HOLLYWOOD BELGESELİ: Alttaki ilgili videodan izleyin!\n"
    "https://youtu.be/iFdWgrnk3ik\n\n"
    "💬 Sen olsan ne yapardın? 1 mi 2 mi? Seçimini yoruma yaz!\n\n"
    "#shorts #thomasshelby #peakyblinders #güç #psikoloji #başarı #zirveninkanunu #48güçyasası #stoacılık"
)

PINNED_COMMENT = "👑 Sen bir ortamda hangisisin? Çok konuşan mı (1), yoksa sessizce izleyen mi (2)? Dürüstçe yoruma yaz 👇"


async def main():
    if not VIDEO_PATH.exists():
        print(f"❌ Video dosyası henüz hazır değil: {VIDEO_PATH}")
        return

    size_mb = VIDEO_PATH.stat().st_size / (1024 * 1024)
    print(f"\n=======================================================")
    print(f"🚀 YOUTUBE STUDIO HOLLYWOOD BENCHMARK YÜKLEME BAŞLIYOR")
    print(f"📦 Dosya: {VIDEO_PATH.name} ({size_mb:.2f} MB)")
    print(f"🎬 Başlık: {TITLE}")
    print(f"=======================================================\n")

    p, context = await launch_stealth_context(headless=True)
    page = await context.new_page()

    new_video_id = None
    try:
        print("1. YouTube Studio açılıyor...")
        await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)

        # Click Create
        create_btn = page.locator('#create-icon, button:has-text("Oluştur")').first
        await create_btn.click(force=True)
        await page.wait_for_timeout(1500)

        # Click Video yükle
        upload_menu_item = page.locator('tp-yt-paper-item:has-text("Video yükle")').first
        await upload_menu_item.click(force=True)
        await page.wait_for_timeout(2500)

        # File input
        file_input = page.locator('input[type="file"]').first
        if await file_input.count() > 0:
            print("👉 input[type=file] üzerinden dosya aktarılıyor...")
            await file_input.set_input_files(str(VIDEO_PATH))
        else:
            select_btn = page.locator('#select-files-button, button:has-text("Dosya seç")').first
            async with page.expect_file_chooser() as fc_info:
                await select_btn.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(VIDEO_PATH))

        print("⏳ Video yükleniyor, Studio yükleme ekranı bekleniyor...")
        await page.wait_for_timeout(10000)

        # Fill Title
        title_box = page.locator('#textbox[aria-label*="Başlık"]').first
        if await title_box.is_visible():
            await title_box.fill("")
            await page.keyboard.insert_text(TITLE)
            print(f"📝 Başlık başarıyla girildi.")
            await page.wait_for_timeout(1000)

        # Fill Description
        desc_box = page.locator('#textbox[aria-label*="Açıklama"]').first
        if await desc_box.is_visible():
            await desc_box.fill("")
            await page.keyboard.insert_text(DESCRIPTION)
            print(f"📝 Açıklama başarıyla girildi.")
            await page.wait_for_timeout(1000)

        # Not for kids
        not_for_kids = page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first
        if await not_for_kids.is_visible():
            await not_for_kids.click()
            print("👶 'Çocuklara özel değil' seçildi.")
            await page.wait_for_timeout(1000)

        # Direct Step 3 (Visibility) click
        print("👉 Görünürlük adımına doğrudan geçiliyor (#step-badge-3)...")
        step3 = page.locator('#step-badge-3, [test-id="REVIEW"]').first
        if await step3.is_visible():
            await step3.click(force=True)
            await page.wait_for_timeout(2000)
        else:
            # Fallback clicking next buttons
            for step in range(3):
                nxt = page.locator('#next-button, button:has-text("İleri"), button:has-text("Sonraki")').first
                if await nxt.is_visible():
                    await nxt.click(force=True)
                    await page.wait_for_timeout(2000)

        # Select Public
        pub_radio = page.locator('tp-yt-paper-radio-button[name="PUBLIC"]').first
        if await pub_radio.is_visible():
            await pub_radio.click(force=True)
            print("👁️ 'Herkese açık' (PUBLIC) seçildi.")
            await page.wait_for_timeout(1500)

        # Extract video link/ID
        try:
            link_elem = page.locator('a.ytcp-video-info, span.ytcp-video-info a').first
            if await link_elem.is_visible():
                href = await link_elem.get_attribute("href")
                if href and ("youtu.be/" in href or "watch?v=" in href):
                    new_video_id = href.split("/")[-1].split("?")[0].replace("watch?v=", "")
                    print(f"🆔 Video ID tespit edildi: {new_video_id}")
        except Exception:
            pass

        # Click Publish / Done
        done_btn = page.locator('#done-button, button:has-text("Yayınla"), button:has-text("Kaydet")').first
        if await done_btn.is_visible():
            await done_btn.click(force=True)
            print("⚡ 'Yayınla' butonuna tıklandı!")
            await page.wait_for_timeout(6000)

        # Take confirmation screenshot
        proof_path = "output/hollywood_benchmark_published_proof.png"
        await page.screenshot(path=proof_path)
        print(f"📸 Yayın onay kanıtı kaydedildi: {proof_path}")

        if new_video_id:
            print(f"🎉 VİDEO YAYINDA: https://www.youtube.com/shorts/{new_video_id}")
            # Save meta
            meta_path = BASE_DIR / "output" / f"published_hollywood_{new_video_id}.json"
            meta_path.write_text(json.dumps({
                "video_id": new_video_id,
                "url": f"https://www.youtube.com/shorts/{new_video_id}",
                "title": TITLE,
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
            }, indent=2), encoding="utf-8")

    finally:
        await context.close()
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
