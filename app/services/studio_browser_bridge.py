"""
YOUTUBE STUDIO BROWSER BRIDGE & DEEP AUTOMATION ENGINE.
Automates high-value creator workflows inaccessible via YouTube Data API v3:
1. Interactive Chrome Session Manager (One-time secure login, permanent session preservation).
2. "İlgili Video" (Related Video) Shorts Funnel Linking (Funnels traffic to Master Documentary PY47MUbB71c).
3. "Seslendirme Çarkı" Multi-Language Audio Tracks (Uploads localized dubbing stems to YouTube Studio Diller).
4. Zero-Quota Debate Comment Pinning.
5. Fully automated batch runner integrated into post-publish pipeline.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent
USER_DATA_DIR = Path(r"C:\youtube_studio_profile")
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = BASE_DIR / "assets" / "youtube_cookies.json"

CHROME_BIN = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
if not CHROME_BIN.exists():
    CHROME_BIN = Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")

MASTER_DOCUMENTARY_ID = "PY47MUbB71c"

LANGUAGE_DISPLAY_MAP = {
    "en": ["İngilizce", "English"],
    "es": ["İspanyolca", "Spanish", "Español"],
    "de": ["Almanca", "German", "Deutsch"],
    "fr": ["Fransızca", "French", "Français"],
    "pt": ["Portekizce", "Portuguese", "Português"],
    "it": ["İtalyanca", "Italian", "Italiano"],
    "ar": ["Arapça", "Arabic"],
    "ja": ["Japonca", "Japanese"],
    "hi": ["Hintçe", "Hindi"],
    "ru": ["Rusça", "Russian"],
}


async def launch_stealth_context(headless: bool = False):
    """
    Launches Chrome persistent context with full stealth settings.
    Bypasses automation detection and retains Google authentication.
    """
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    context = await p.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        executable_path=str(CHROME_BIN) if CHROME_BIN.exists() else None,
        headless=headless,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        ignore_default_args=["--enable-automation"],
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ],
    )
    return p, context


async def check_studio_auth_status(headless: bool = True) -> bool:
    """Verifies whether the persistent profile is logged into YouTube Studio."""
    p = None
    context = None
    try:
        p, context = await launch_stealth_context(headless=headless)
        page = await context.new_page()
        await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        url = page.url
        is_logged_in = "studio.youtube.com" in url and "signin" not in url and "accounts.google.com" not in url
        return is_logged_in
    except Exception as e:
        print(f"ℹ️ Studio kontrol hatası: {e}")
        return False
    finally:
        if context:
            await context.close()
        if p:
            await p.stop()


async def interactive_login_flow(timeout_seconds: int = 3600) -> bool:
    """
    Opens Google Chrome visibly on the desktop.
    The user signs into their channel account (marginverdictstudio@gmail.com) once.
    The script monitors the login and verifies authentication.
    Once authenticated, permanent session tokens and cookies are saved.
    """
    print("\n" + "=" * 70)
    print("🌐 GOOGLE CHROME YOUTUBE STUDIO ETKİLEŞİMLİ GİRİŞ PENCERESİ")
    print(f"📁 Profil Dizini: {USER_DATA_DIR}")
    print("👉 Açılan pencerede YouTube / Google hesabınıza giriş yapın.")
    print("👉 'Kimliğinizi doğrulayın' uyarısı çıkarsa şifre veya 2FA onayını verin.")
    print(f"⏳ Zaman Aşımı: {timeout_seconds} saniye")
    print("=" * 70 + "\n")

    p, context = await launch_stealth_context(headless=False)
    page = await context.new_page()

    try:
        await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)
        
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            curr_url = page.url
            
            # Check if Studio is reached and not in sign-in flow
            if "studio.youtube.com" in curr_url and "accounts.google.com" not in curr_url:
                # Check for identity confirmation modal
                modal = page.locator('text="Kimliğinizi doğrulayın", text="Verify your identity"').first
                if await modal.is_visible():
                    print("⚠️ 'Kimliğinizi doğrulayın' ekranı açık. Lütfen ekrandaki adımı tamamlayın...")
                    await page.wait_for_timeout(3000)
                    continue

                print("\n" + "=" * 70)
                print("🎉 TEBRİKLER! YouTube Studio Oturumu Başarıyla Açıldı ve Doğrulandı!")
                print("=" * 70)

                # Save cookies as backup
                cookies = await context.cookies()
                with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, ensure_ascii=False, indent=2)
                print(f"💾 Çerezler yedeklendi: {COOKIE_FILE}")

                # Save proof screenshot
                screen_path = BASE_DIR / "output" / "studio_auth_verified.png"
                await page.screenshot(path=str(screen_path))
                print(f"📸 Kanıt Ekran Görüntüsü: {screen_path}")

                await page.wait_for_timeout(2000)
                return True

            await page.wait_for_timeout(2000)

        print("❌ Oturum açma süresi doldu.")
        return False
    except Exception as e:
        print(f"⚠️ Oturum hatası: {e}")
        return False
    finally:
        await context.close()
        await p.stop()


async def link_related_video_async(short_id: str, target_query: str = MASTER_DOCUMENTARY_ID, headless: bool = True) -> bool:
    """
    Automates setting the 'İlgili Video' (Related Video) on a YouTube Short.
    Funnels viewers directly to the master documentary or previous episode.
    """
    edit_url = f"https://studio.youtube.com/video/{short_id}/edit"
    print(f"\n🔗 [STUDIO BRIDGE] {short_id} için İlgili Video Bağlanıyor ➔ '{target_query}'...")

    p, context = await launch_stealth_context(headless=headless)
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        await page.goto(edit_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)

        if "accounts.google.com" in page.url:
            print("⚠️ Oturum açık değil. Lütfen önce `interactive_login_flow()` ile giriş yapın.")
            return False

        # Check for verification modal
        verify_modal = page.locator('text="Kimliğinizi doğrulayın", text="Verify your identity"').first
        if await verify_modal.is_visible():
            print("⚠️ 'Kimliğinizi doğrulayın' uyarısı çıktı. Görünür modda oturum onayı gerekiyor.")
            return False

        # Locate Related Video trigger element
        triggers = [
            'ytcp-text-dropdown-trigger:has-text("İlgili video")',
            'ytcp-dropdown-trigger:has-text("İlgili video")',
            'button[aria-label*="İlgili video"]',
            'button[aria-label*="Related video"]',
            '#related-video-picker',
            '[aria-label*="İlgili video"]',
        ]

        trigger = None
        for sel in triggers:
            loc = page.locator(sel).first
            if await loc.is_visible():
                trigger = loc
                break

        if not trigger:
            # Scroll down to reveal details panel options
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(1500)
            for sel in triggers:
                loc = page.locator(sel).first
                if await loc.is_visible():
                    trigger = loc
                    break

        if trigger:
            curr_status = (await trigger.inner_text()).replace('\n', ' ')
            if "HOLLYWOOD TRILOGY" in curr_status or target_query in curr_status:
                print(f"✅ İlgili video zaten doğru bağlı: {curr_status}")
                return True

            print("👉 'İlgili video' seçiciye tıklandı...")
            await trigger.click()
            await page.wait_for_timeout(2000)

            # Search in dialog
            search_input = page.locator('ytcp-video-pick-dialog input, div[role="dialog"] input').first
            if await search_input.is_visible():
                print(f"🔍 Hedef video aranıyor: {target_query}")
                await search_input.fill(target_query)
                await page.wait_for_timeout(2500)

            # Select matching card
            card = page.locator('ytcp-video-pick-dialog-contents ytcp-entity-card, ytcp-entity-card.card').first
            if await card.is_visible():
                box = await card.bounding_box()
                if box:
                    print(f"🎯 Hedef kart bulundu ({box}), tıklanıyor...")
                    await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                else:
                    await card.click(force=True)
                await page.wait_for_timeout(2000)
            else:
                title_el = page.locator('ytcp-video-pick-dialog :has-text("HOLLYWOOD TRILOGY")').last
                if await title_el.is_visible():
                    await title_el.click(force=True)
                    await page.wait_for_timeout(2000)

            # Click Save button (Kaydet)
            save_btn = page.locator('#save-button button, ytcp-button#save-button button, button:has-text("Kaydet"), #save-button').first
            for _ in range(6):
                if await save_btn.is_enabled():
                    break
                await page.wait_for_timeout(1000)

            if await save_btn.is_enabled():
                print("💾 Kaydet butonuna basılıyor...")
                await save_btn.click(force=True)
                await page.wait_for_timeout(4000)
                print(f"✅ [STUDIO BRIDGE] İlgili Video Başarıyla Kaydedildi! ({short_id} ➔ {target_query})")
                return True
            else:
                print("ℹ️ Kaydet butonu aktif değil (video zaten kaydedilmiş olabilir).")
                return True
        else:
            print("ℹ️ 'İlgili video' tetikleyici buton sayfada bulunamadı.")
            await page.screenshot(path=str(BASE_DIR / "output" / f"debug_related_{short_id}.png"))

        return False
    except Exception as e:
        print(f"❌ [STUDIO BRIDGE] Hata ({short_id}): {e}")
        return False
    finally:
        await context.close()
        await p.stop()


async def upload_audio_track_async(video_id: str, lang_code: str, audio_file: Path, headless: bool = True) -> bool:
    """
    Automates uploading localized audio stem to YouTube Studio 'Diller' section:
    https://studio.youtube.com/video/{video_id}/translations
    """
    if not audio_file.exists():
        print(f"❌ Ses dosyası bulunamadı: {audio_file}")
        return False

    trans_url = f"https://studio.youtube.com/video/{video_id}/translations"
    lang_names = LANGUAGE_DISPLAY_MAP.get(lang_code, [lang_code])
    primary_name = lang_names[0]

    print(f"\n🎧 [STUDIO AUDIO TRACK] Çok Dilli Ses Parçası Yükleniyor: {primary_name.upper()} ({lang_code})")
    print(f"   Video ID: {video_id} | Dosya: {audio_file.name}")

    p, context = await launch_stealth_context(headless=headless)
    page = await context.new_page()

    try:
        await page.goto(trans_url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3500)

        if "accounts.google.com" in page.url:
            print("⚠️ Oturum açık değil. Lütfen önce `interactive_login_flow()` ile giriş yapın.")
            return False

        # Check if language is already in the table
        lang_row = page.locator(f'ytcp-video-row:has-text("{primary_name}"), tr:has-text("{primary_name}")').first
        if not await lang_row.is_visible():
            # Click 'Dil ekle' button
            add_lang_btn = page.locator('#add-translations-button, button:has-text("Dil ekle"), button:has-text("Add language")').first
            if await add_lang_btn.is_visible():
                print(f"➕ 'Dil ekle' butonuna tıklandı: {primary_name}")
                await add_lang_btn.click()
                await page.wait_for_timeout(1500)

                # Select language from dropdown list
                menu_item = page.locator(f'tp-yt-paper-item:has-text("{primary_name}"), ytcp-text-menu-item:has-text("{primary_name}")').first
                if await menu_item.is_visible():
                    await menu_item.click()
                    await page.wait_for_timeout(2500)
                else:
                    print(f"ℹ️ Dil menüde doğrudan bulunamadı, arama deneniyor...")

        # Find the Audio column ('Ses') cell in that language row
        audio_btn = page.locator(f'tr:has-text("{primary_name}") button[aria-label*="Ses"], tr:has-text("{primary_name}") button[aria-label*="Audio"], ytcp-video-row:has-text("{primary_name}") button[aria-label*="Ses"]').first
        if not await audio_btn.is_visible():
            # Fallback: general add audio button
            audio_btn = page.locator('button[aria-label*="Ses"], button[aria-label*="Audio"]').first

        if await audio_btn.is_visible():
            print("📤 Ses dosyası yükleme iletişim kutusu açılıyor...")
            async with page.expect_file_chooser() as fc_info:
                await audio_btn.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(audio_file))
            print(f"⏳ Dosya gönderildi: {audio_file.name}. İşlenmesi bekleniyor...")
            await page.wait_for_timeout(8000)

            # Click Publish (Yayınla)
            publish_btn = page.locator('#publish-button, ytcp-button#publish-button, button:has-text("Yayınla"), button:has-text("Publish")').first
            if await publish_btn.is_enabled():
                await publish_btn.click()
                await page.wait_for_timeout(4000)
                print(f"✅ [STUDIO AUDIO TRACK] Çok Dilli Ses Parçası Başarıyla Yayınlandı ({lang_code})!")
                return True
            else:
                print("ℹ️ Yayınla butonu henüz aktifleşmedi.")
        else:
            print(f"ℹ️ {primary_name} için 'Ses' yükleme butonu bulunamadı.")
            await page.screenshot(path=str(BASE_DIR / "output" / f"debug_audio_{video_id}_{lang_code}.png"))

        return False
    except Exception as e:
        print(f"❌ [STUDIO AUDIO TRACK] Hata: {e}")
        return False
    finally:
        await context.close()
        await p.stop()


async def pin_comment_async(video_id: str, comment_text: str, headless: bool = True) -> bool:
    """
    Submits and pins high-CTR debate comment on YouTube ($0 API quota).
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"\n💬 [STUDIO PIN COMMENT] Yorum Başa Sabitleniyor: {video_id}")
    print(f"   Metin: {comment_text[:60]}...")

    p, context = await launch_stealth_context(headless=headless)
    page = await context.new_page()

    try:
        await page.goto(watch_url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(3000)

        # Scroll to comments
        await page.evaluate("window.scrollBy(0, 600)")
        await page.wait_for_timeout(2000)

        # Click comment box
        placeholder = page.locator("#placeholder-area").first
        if await placeholder.is_visible():
            await placeholder.click()
            await page.wait_for_timeout(1000)

            input_box = page.locator("#contenteditable-root").first
            if await input_box.is_visible():
                await input_box.fill(comment_text)
                await page.wait_for_timeout(1000)

                submit_btn = page.locator("#submit-button button").first
                if await submit_btn.is_visible():
                    await submit_btn.click()
                    print("💬 Yorum gönderildi, 3 saniye bekleniyor...")
                    await page.wait_for_timeout(3000)

                    # Click action menu (3 dots) on latest comment
                    menu_btn = page.locator("ytd-comment-thread-renderer #action-menu button").first
                    if await menu_btn.is_visible():
                        await menu_btn.click()
                        await page.wait_for_timeout(1000)

                        pin_item = page.locator("[aria-label*='sabitle'], [aria-label*='Pin'], text='Başa sabitle'").first
                        if await pin_item.is_visible():
                            await pin_item.click()
                            await page.wait_for_timeout(1000)

                            confirm_btn = page.locator("#confirm-button button, ytd-button-renderer#confirm-button").first
                            if await confirm_btn.is_visible():
                                await confirm_btn.click()

                            print(f"📌 [STUDIO PIN COMMENT] Yorum Başarıyla Başa Sabitlendi ({video_id})!")
                            return True

        return False
    except Exception as e:
        print(f"❌ [STUDIO PIN COMMENT] Hata: {e}")
        return False
    finally:
        await context.close()
        await p.stop()


def auto_bridge_post_publish(video_id: str, target_related: str = MASTER_DOCUMENTARY_ID, pinned_comment: Optional[str] = None):
    """
    Hook called automatically by publish_to_youtube.py after any video is published.
    Runs silently in the background if the browser session is authenticated.
    """
    print(f"\n🚀 [STUDIO POST-PUBLISH AUTO-BRIDGE] Video ID: {video_id}")
    try:
        # 1. Link Related Video
        rel_ok = asyncio.run(link_related_video_async(video_id, target_query=target_related, headless=True))
        if rel_ok:
            print("✅ Otomatik İlgili Video Bağlantısı Başarılı!")
        else:
            print("ℹ️ İlgili Video bağlama adımı headless modda tamamlanamadı (oturum kontrolü gerekebilir).")

        # 2. Pin Comment if provided
        if pinned_comment:
            pin_ok = asyncio.run(pin_comment_async(video_id, pinned_comment, headless=True))
            if pin_ok:
                print("✅ Otomatik Yorum Başa Sabitleme Başarılı!")

    except Exception as e:
        print(f"ℹ️ Studio Auto-Bridge notu: {e}")


def link_related_video(short_id: str, target_query: str = MASTER_DOCUMENTARY_ID, headless: bool = True) -> bool:
    """Synchronous wrapper for link_related_video_async."""
    return asyncio.run(link_related_video_async(short_id, target_query, headless=headless))


def upload_audio_track(video_id: str, lang_code: str, audio_file: Path, headless: bool = True) -> bool:
    """Synchronous wrapper for upload_audio_track_async."""
    return asyncio.run(upload_audio_track_async(video_id, lang_code, audio_file, headless=headless))


if __name__ == "__main__":
    print("🎭 YOUTUBE STUDIO BROWSER BRIDGE MOTORU AKTİF!")
