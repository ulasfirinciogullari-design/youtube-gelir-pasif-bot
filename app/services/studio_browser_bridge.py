"""
YOUTUBE STUDIO BROWSER BRIDGE & CDP AUTOMATION ENGINE.
Automates actions restricted from the standard YouTube Data API v3:
1. Linking "İlgili Video" (Related Video) on Shorts to funnel viewers to the Master Documentary (PY47MUbB71c).
2. Uploading Multi-Language Audio Tracks (Secondary Audio Dubbing).
3. Publishing YouTube Community Tab Polls.
Supports both Persistent Chromium Profile and Chrome DevTools Protocol (CDP) on port 9222.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CHROME_PROFILE_DIR = BASE_DIR / "output" / "chrome_studio_profile"
CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

CHROME_BIN = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
if not CHROME_BIN.exists():
    CHROME_BIN = Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")


def launch_interactive_chrome_login():
    """
    Launches Google Chrome visibly with the dedicated persistent profile.
    The user logs in ONCE to YouTube Studio. All session tokens and cookies
    are permanently saved in output/chrome_studio_profile for headless automation.
    """
    print("\n" + "=" * 70)
    print("🌐 GOOGLE CHROME YOUTUBE STUDIO GİRİŞ PENCERESİ AÇILIYOR...")
    print(f"📁 Profil Dizini: {CHROME_PROFILE_DIR}")
    print("👉 Açılan pencerede YouTube Studio hesabınıza bir kez giriş yapın.")
    print("=" * 70 + "\n")

    cmd = [
        str(CHROME_BIN),
        f"--user-data-dir={str(CHROME_PROFILE_DIR)}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://studio.youtube.com"
    ]
    subprocess.Popen(cmd)


async def check_studio_auth_status(headless: bool = True) -> bool:
    """Verifies whether the persistent profile is logged into YouTube Studio."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE_DIR),
                executable_path=str(CHROME_BIN) if CHROME_BIN.exists() else None,
                headless=headless,
                viewport={"width": 1920, "height": 1080},
                args=["--disable-blink-features=AutomationControlled"]
            )
            page = await context.new_page()
            await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            url = page.url
            is_logged_in = "studio.youtube.com" in url and "signin" not in url
            await context.close()
            return is_logged_in
        except Exception as e:
            print(f"ℹ️ Studio kontrol hatası: {e}")
            return False


async def link_related_video_async(short_id: str, target_query: str = "PY47MUbB71c", headless: bool = True) -> bool:
    """
    Automates setting the 'İlgili Video' (Related Video) on a YouTube Short.
    Funnels Shorts traffic directly to the target documentary or previous episode.
    """
    from playwright.async_api import async_playwright

    edit_url = f"https://studio.youtube.com/video/{short_id}/edit"
    print(f"\n🔗 [STUDIO BRIDGE] {short_id} için İlgili Video Bağlanıyor: '{target_query}'...")

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE_DIR),
                executable_path=str(CHROME_BIN) if CHROME_BIN.exists() else None,
                headless=headless,
                viewport={"width": 1920, "height": 1080},
                args=["--disable-blink-features=AutomationControlled"]
            )
            page = await context.new_page()
            await page.goto(edit_url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)

            # Check if logged in
            if "signin" in page.url:
                print("⚠️ Oturum açık değil. Lütfen önce `launch_interactive_chrome_login()` ile giriş yapın.")
                await context.close()
                return False

            # Click Related Video dropdown trigger
            trigger = page.locator('ytcp-text-dropdown-trigger:has-text("İlgili video"), ytcp-dropdown-trigger:has-text("İlgili video"), [aria-label*="İlgili video"]').first
            if not await trigger.is_visible():
                # Scroll down in details panel
                await page.evaluate("window.scrollBy(0, 500)")
                await page.wait_for_timeout(1000)

            if await trigger.is_visible():
                await trigger.click()
                await page.wait_for_timeout(2000)

                # Find dialog search input
                dialog = page.locator('ytcp-video-pick-dialog, [role="dialog"]').first
                if await dialog.is_visible():
                    search_input = dialog.locator('input').first
                    if await search_input.is_visible():
                        await search_input.fill(target_query)
                        await page.wait_for_timeout(2000)

                    # Select first result row
                    first_row = dialog.locator('ytcp-video-row, ytcp-entity-card, [role="row"]').first
                    if await first_row.is_visible():
                        await first_row.click()
                        await page.wait_for_timeout(2000)

                    # Click Save (Kaydet)
                    save_btn = page.locator('#save-button, button:has-text("Kaydet")').first
                    if await save_btn.is_enabled():
                        await save_btn.click()
                        await page.wait_for_timeout(3000)
                        print(f"✅ [STUDIO BRIDGE] İlgili Video Başarıyla Kaydedildi! ({short_id} -> {target_query})")
                        await context.close()
                        return True
                    else:
                        print("ℹ️ Kaydet butonu aktifleşmedi veya video zaten seçili.")
            else:
                print("ℹ️ İlgili video tetikleyici elementi sayfada bulunamadı.")

            await context.close()
            return False
        except Exception as e:
            print(f"❌ [STUDIO BRIDGE] Hata ({short_id}): {e}")
            return False


def link_related_video(short_id: str, target_query: str = "PY47MUbB71c", headless: bool = True) -> bool:
    """Synchronous wrapper for link_related_video_async."""
    return asyncio.run(link_related_video_async(short_id, target_query, headless=headless))
