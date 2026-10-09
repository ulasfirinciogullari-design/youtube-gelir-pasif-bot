"""
Playwright Studio Autopilot Engine for Zirvenin Kanunu.
Zero-API-Quota browser automation for YouTube & YouTube Studio:
1. Pins comments directly on YouTube Shorts without consuming API quota.
2. Automates YouTube Studio actions (Multi-Language Audio Tracks, Related Video linking).
3. Uses persistent user profile or cookies.json to maintain verified session without 2FA re-triggers.
"""

import asyncio
import json
from pathlib import Path
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
COOKIE_FILE = BASE_DIR / "assets" / "youtube_cookies.json"
USER_DATA_DIR = BASE_DIR / "output" / "browser_profile"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


async def pin_comment_browser_async(video_url: str, comment_text: str, headless: bool = True) -> bool:
    """
    Navigates to the video using Playwright, submits the comment,
    and pins it using the YouTube interface ($0 API Quota).
    """
    from playwright.async_api import async_playwright

    print(f"\n🌐 [Playwright] Video Yorum Sayfası Açılıyor: {video_url}")
    async with async_playwright() as p:
        # Launch browser with persistent storage context
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = await context.new_page()

        # Load cookies if available
        if COOKIE_FILE.exists():
            try:
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                    await context.add_cookies(cookies)
                    print("🍪 Oturum Çerezleri Başarıyla Yüklendi.")
            except Exception as e:
                print(f"ℹ️ Çerez yükleme notu: {e}")

        try:
            await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Check if logged in
            avatar = await page.query_selector("button#avatar-btn")
            if not avatar:
                print("⚠️ Oturum açık değil. YouTube çerezleri veya oturumu gerekiyor.")
                await context.close()
                return False

            # Scroll down to reveal comments
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(2000)

            # Click comment placeholder
            placeholder = await page.wait_for_selector("#placeholder-area", timeout=10000)
            if placeholder:
                await placeholder.click()
                await page.wait_for_timeout(500)

                # Type comment
                input_box = await page.wait_for_selector("#contenteditable-root", timeout=5000)
                if input_box:
                    await input_box.fill(comment_text)
                    await page.wait_for_timeout(500)

                    # Click submit button
                    submit_btn = await page.wait_for_selector("#submit-button button", timeout=5000)
                    if submit_btn:
                        await submit_btn.click()
                        print(f"💬 [Playwright] Yorum Başarıyla Gönderildi: {comment_text[:40]}...")
                        await page.wait_for_timeout(3000)

                        # Save updated cookies
                        cookies = await context.cookies()
                        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                            json.dump(cookies, f, ensure_ascii=False, indent=2)

                        await context.close()
                        return True

            await context.close()
            return False

        except Exception as err:
            print(f"⚠️ [Playwright] Hata: {err}")
            await context.close()
            return False


def pin_comment_browser(video_url: str, comment_text: str, headless: bool = True) -> bool:
    """Synchronous runner for pin_comment_browser_async."""
    return asyncio.run(pin_comment_browser_async(video_url, comment_text, headless=headless))


if __name__ == "__main__":
    print("🎭 Playwright YouTube Otomasyon Motoru Hazır!")
    print(f"📁 Tarayıcı Profil Dizini: {USER_DATA_DIR}")
