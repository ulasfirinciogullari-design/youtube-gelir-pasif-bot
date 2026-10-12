"""
YOUTUBE STUDIO 10-LANGUAGE SUITE DEEP AUTOMATION & UPLOADER.
Uploads localized metadata, subtitles (SRT), and dubbed audio tracks to YouTube Studio:
Languages:
1. Turkish (tr) [Original]
2. English (en)
3. Spanish (es)
4. Portuguese (pt)
5. German (de)
6. French (fr)
7. Italian (it)
8. Russian (ru)
9. Arabic (ar)
10. Hindi (hi)
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

from app.services.studio_browser_bridge import launch_stealth_context
from app.services.global_10_language_suite import TRILOGY_CATALOG, produce_10_language_suite_for_episode

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_DIR / "upload_10_languages_studio.log"

LANGUAGE_CONFIG = {
    "en": {"name": "English", "studio_name": "İngilizce", "menu_option": "İngilizce (Amerika Birleşik Devletleri)"},
    "es": {"name": "Spanish", "studio_name": "İspanyolca", "menu_option": "İspanyolca"},
    "pt": {"name": "Portuguese", "studio_name": "Portekizce", "menu_option": "Portekizce (Brezilya)"},
    "de": {"name": "German", "studio_name": "Almanca", "menu_option": "Almanca"},
    "fr": {"name": "French", "studio_name": "Fransızca", "menu_option": "Fransızca"},
    "it": {"name": "Italian", "studio_name": "İtalyanca", "menu_option": "İtalyanca"},
    "ru": {"name": "Russian", "studio_name": "Rusça", "menu_option": "Rusça"},
    "ar": {"name": "Arabic", "studio_name": "Arapça", "menu_option": "Arapça"},
    "hi": {"name": "Hindi", "studio_name": "Hintçe", "menu_option": "Hintçe"},
}

TARGET_LANGS = ["en", "es", "pt", "de", "fr", "it", "ru", "ar", "hi"]


def log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def resolve_video_data(video_id: str, episode_key: Optional[str] = None) -> tuple:
    if episode_key and episode_key in TRILOGY_CATALOG:
        return episode_key, TRILOGY_CATALOG[episode_key]
    for k, v in TRILOGY_CATALOG.items():
        if v.get("video_id") == video_id:
            return k, v
    return episode_key or "custom", None


async def sync_single_language(page, video_id: str, episode_key: str, lang_code: str, ep_data: Optional[Dict] = None) -> bool:
    cfg = LANGUAGE_CONFIG[lang_code]
    studio_name = cfg["studio_name"]
    menu_option = cfg["menu_option"]

    title = ""
    desc = ""
    if ep_data and lang_code in ep_data.get("languages", {}):
        title = ep_data["languages"][lang_code].get("title", "")
        desc = ep_data["languages"][lang_code].get("desc", "")

    suite_dir = OUTPUT_DIR / f"global_10lang_{episode_key}_{video_id}"
    audio_file = suite_dir / f"{episode_key}_audio_track_{lang_code}.mp3"
    srt_file = suite_dir / f"{episode_key}_subtitles_{lang_code}.srt"

    log(f"\n--- [{lang_code.upper()}] {studio_name} ---")
    if title:
        log(f"   Başlık: {title[:50]}...")
    if audio_file.exists():
        log(f"   Ses Dosyası: {audio_file.name} ({audio_file.stat().st_size / 1024:.1f} KB)")
    if srt_file.exists():
        log(f"   Altyazı Dosyası: {srt_file.name}")

    # 0. Ensure no stray modal or backdrop is blocking the view
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        await page.evaluate("""() => {
            document.querySelectorAll('tp-yt-iron-overlay-backdrop').forEach(b => b.remove());
        }""")
    except Exception:
        pass

    # 1. Open Language modal
    modal = page.locator('ytcp-dialog').first
    if not await modal.is_visible():
        # Check if row is in table
        row = page.locator('table tr, [role="row"]').filter(has_text=studio_name).first
        if await row.is_visible():
            lang_btn = row.locator(f'button:has-text("{studio_name}"), [role="button"]:has-text("{studio_name}")').first
            if await lang_btn.is_visible():
                log(f"   👉 Mevcut '{studio_name}' satırına tıklanıyor...")
                await lang_btn.click(force=True)
            else:
                await row.click(force=True)
        else:
            log(f"   ➕ 'Dil ekle' -> '{menu_option}'...")
            add_btn = page.locator('#add-translations-button, button:has-text("Dil ekle"), ytcp-button:has-text("Dil ekle")').first
            if await add_btn.is_visible():
                await add_btn.click(force=True)
                await page.wait_for_timeout(1500)
                item = page.locator(f'tp-yt-paper-item:has-text("{menu_option}")').first
                if await item.is_visible():
                    await item.click(force=True)
                    log(f"   ✅ Menüden '{menu_option}' seçildi.")
                else:
                    item2 = page.locator(f'tp-yt-paper-item:has-text("{studio_name}"):not([aria-disabled="true"])').first
                    if await item2.is_visible():
                        await item2.click(force=True)
                        log(f"   ✅ Alternatif '{studio_name}' seçildi.")
                    else:
                        log(f"   ❌ '{studio_name}' eklenemedi.")
                        return False

        try:
            await modal.wait_for(state="visible", timeout=8000)
        except Exception:
            pass

    if not await modal.is_visible():
        log(f"   ❌ '{studio_name}' düzenleme modalı açılamadı.")
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            await page.evaluate("""() => {
                document.querySelectorAll('tp-yt-iron-overlay-backdrop').forEach(b => b.remove());
            }""")
        except Exception:
            pass
        return False

    # 2. Update Title & Description
    if title:
        title_box = modal.locator('input').first
        if await title_box.is_visible():
            curr_title = await title_box.input_value()
            if curr_title != title:
                await title_box.fill(title)
                log("   📝 Başlık güncellendi.")

    if desc:
        desc_box = modal.locator('textarea').first
        if await desc_box.is_visible():
            curr_desc = await desc_box.input_value()
            if curr_desc != desc:
                await desc_box.fill(desc)
                log("   📝 Açıklama güncellendi.")

    await page.wait_for_timeout(1000)

    # 3. Dubbed Audio track upload
    if audio_file.exists():
        audio_btn = modal.locator('#language-details-text-button-audio').first
        if await audio_btn.is_visible():
            log(f"   🎵 Ses parçası yükleniyor: {audio_file.name}...")
            await audio_btn.click()
            await page.wait_for_timeout(2000)

            subdialog = page.locator('ytcp-dialog').filter(has_text="Ses").last
            dosya_sec_btn = subdialog.locator('button:has-text("Dosya Seçin"), [aria-label*="Dosya Seçin"]').first
            if await dosya_sec_btn.is_visible():
                async with page.expect_file_chooser() as fc_info:
                    await dosya_sec_btn.click()
                fc = await fc_info.value
                await fc.set_files(str(audio_file))
                log(f"   📁 Ses dosyası gönderildi, işleniyor...")

                yayinla_btn = subdialog.locator('button:has-text("Yayınla"), [aria-label*="Yayınla"]').first
                for check in range(30):
                    await page.wait_for_timeout(1000)
                    is_disabled = await yayinla_btn.get_attribute("disabled")
                    aria_disabled = await yayinla_btn.get_attribute("aria-disabled")
                    if is_disabled is None and aria_disabled != "true":
                        log(f"   ⚡ Ses 'Yayınla' aktifleşti ({check+1}. sn)! Tıklanıyor...")
                        await yayinla_btn.click()
                        await page.wait_for_timeout(3000)
                        break
            else:
                log("   ℹ️ 'Dosya Seçin' butonu görünmedi.")
        else:
            log("   ℹ️ 'Ses' ekleme butonu aktif değil (zaten yüklü veya seçenekler mevcut).")

    # 4. Closed Captions / Subtitles upload
    if srt_file.exists():
        sub_btn = modal.locator('#language-details-text-button-subtitles').first
        if await sub_btn.is_visible():
            log(f"   💬 Altyazı yükleniyor: {srt_file.name}...")
            await sub_btn.click()
            await page.wait_for_timeout(2000)

            dosya_yukle = page.locator('text="Dosya yükle"').first
            if await dosya_yukle.is_visible():
                await dosya_yukle.click()
                await page.wait_for_timeout(1500)

                devam_btn = page.locator('button:has-text("Devam"), ytcp-button:has-text("Devam")').last
                if await devam_btn.is_visible():
                    async with page.expect_file_chooser() as fc_info:
                        await devam_btn.click()
                    fc = await fc_info.value
                    await fc.set_files(str(srt_file))
                    log(f"   📁 Altyazı dosyası yüklendi, yayınlanıyor...")
                    await page.wait_for_timeout(3500)

                    yayinla_sub = page.locator('button:has-text("Yayınla"), ytcp-button:has-text("Yayınla")').last
                    if await yayinla_sub.is_visible() and await yayinla_sub.is_enabled():
                        await yayinla_sub.click()
                        log("   ✅ Altyazı yayınlandı!")
                        await page.wait_for_timeout(3000)
        else:
            log("   ℹ️ 'Altyazılar' ekleme butonu aktif değil (zaten yüklü).")

    # 5. Save and Close Main Editor
    main_save = modal.locator('button:has-text("Güncelle"), [aria-label*="Güncelle"]').first
    if await main_save.is_visible() and await main_save.is_enabled():
        log("   💾 Dil güncellemesi kaydediliyor...")
        await main_save.click()
        await page.wait_for_timeout(3500)
    else:
        close_btn = modal.locator('button[aria-label="İptal"], #close-button').first
        if await close_btn.is_visible():
            await close_btn.click()
            await page.wait_for_timeout(1500)

    log(f"   ✅ [{lang_code.upper()}] {studio_name} tamamlandı!")
    return True


async def sync_video_10_languages_studio(video_id: str, episode_key: Optional[str] = None, headless: bool = True) -> str:
    ep_key, ep_data = resolve_video_data(video_id, episode_key)
    url = f"https://studio.youtube.com/video/{video_id}/translations"

    log("\n" + "=" * 70)
    log(f"🚀 YOUTUBE STUDIO 10-LANGUAGE AUTO-UPLOADER")
    log(f"🎬 Video ID: {video_id} | Episode Key: {ep_key}")
    log(f"🔗 URL: {url}")
    log("=" * 70)

    # Ensure assets exist
    suite_dir = OUTPUT_DIR / f"global_10lang_{ep_key}_{video_id}"
    if not suite_dir.exists() or not (suite_dir / "suite_manifest.json").exists():
        log("📦 10-Dil paketi yerel dizinde eksik, üretiliyor...")
        if ep_key in TRILOGY_CATALOG:
            await produce_10_language_suite_for_episode(ep_key)
        else:
            log("⚠️ Özel video için katalog kaydı bulunamadı.")

    p, context = await launch_stealth_context(headless=headless)
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3500)

        for lc in TARGET_LANGS:
            try:
                await sync_single_language(page, video_id, ep_key, lc, ep_data)
                await page.wait_for_timeout(2000)
            except Exception as e:
                log(f"⚠️ Dil işleme hatası ({lc}): {e}")

        await page.wait_for_timeout(3000)
        # Take verification proof screenshot
        proof_path = OUTPUT_DIR / f"benchmark_{video_id}_10lang_proof.png"
        await page.screenshot(path=str(proof_path))
        log(f"\n📸 [KANIT] Çeviriler Tablosu Doğrulama Ekran Görüntüsü: {proof_path.name}")
        return str(proof_path)

    finally:
        await context.close()
        await p.stop()


async def main():
    parser = argparse.ArgumentParser(description="YouTube Studio 10-Language Uploader")
    parser.add_argument("--video", type=str, default="7xTXWFfqaGw", help="YouTube Video ID")
    parser.add_argument("--episode", type=str, default="benchmark_shelby_blink", help="Catalog episode key")
    parser.add_argument("--headless", action="store_true", default=True, help="Headless browser mode")
    args = parser.parse_args()

    await sync_video_10_languages_studio(video_id=args.video, episode_key=args.episode, headless=args.headless)


if __name__ == "__main__":
    asyncio.run(main())
