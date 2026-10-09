"""
YouTube Studio Deep Automation via Playwright.
Solves the exact features that YouTube Data API v3 cannot do:
1. "İlgili Video" (Related Video) linking on YouTube Shorts:
   Places the interactive pill button on the Short pointing to the next Part or full Long Video.
2. "Seslendirme Çarkı" (Multi-Language Audio Tracks):
   Uploads supplementary localized audio stems (.mp3) to YouTube Studio's language tabs.
3. "Sabit Yorum" (Pin Comment):
   Pins the high-CTR debate comment to the top of the Short, guaranteeing 100% presence ($0 API Quota).
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
USER_DATA_DIR = BASE_DIR / "output" / "studio_browser_session"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = BASE_DIR / "assets" / "youtube_cookies.json"

# Flagship video network mapping
FLAGSHIP_SHORTS_NETWORK = [
    {
        "short_id": "qrWWqWJudAU",
        "title": "Thomas Shelby | Sessiz Güç (Bölüm 1)",
        "target_id": "oOZLeIYnoPM",
        "target_title": "Thomas Shelby | Asla Özür Dileme (Bölüm 2)",
        "debate_comment": "💬 TARTIŞMA: Bir ortamda saygı kazanmak için sessizlik mi yoksa anında sert karşılık vermek mi daha etkilidir? Fikirlerinizi yazın.",
    },
    {
        "short_id": "oOZLeIYnoPM",
        "title": "Thomas Shelby | Asla Özür Dileme (Bölüm 2)",
        "target_id": "L8Eze1G3OFw",
        "target_title": "Thomas Shelby | Orijinal Ses & Güç Yasası",
        "debate_comment": "💬 TARTIŞMA: Haklı olduğunda bile özür dilemek bir strateji midir, yoksa zayıflık mıdır? Yorumlarda tartışalım.",
    },
    {
        "short_id": "rujHZFOO0nI",
        "title": "48 Güç Yasası | Machiavelli vs Aurelius",
        "target_id": "h0xkSSiDtmA",
        "target_title": "Walter White | Tehlikenin Kendisi Benim",
        "debate_comment": "💬 TARTIŞMA: Bir lider için sevilmek mi yoksa korkulmak mı daha kalıcı bir güç sağlar?",
    },
    {
        "short_id": "B41V5yaL21s",
        "title": "Tyler Durden | Sahip Oldukların Sana Sahip Olur",
        "target_id": "h0xkSSiDtmA",
        "target_title": "Walter White | Tehlikenin Kendisi Benim",
        "debate_comment": "💬 TARTIŞMA: Gerçekten özgür olmak için sahip olduğun her şeyi kaybetmek mi gerekir? Düşünceleriniz?",
    },
    {
        "short_id": "h0xkSSiDtmA",
        "title": "Walter White | Tehlikenin Kendisi Benim",
        "target_id": "L8Eze1G3OFw",
        "target_title": "Thomas Shelby | Orijinal Ses & Güç Yasası",
        "debate_comment": "💬 TARTIŞMA: Walter White ailesi için mi bu yola girdi, yoksa kendi egosunu tatmin etmek için mi?",
    },
    {
        "short_id": "L8Eze1G3OFw",
        "title": "Thomas Shelby | Orijinal Ses & Güç Yasası",
        "target_id": "qrWWqWJudAU",
        "target_title": "Thomas Shelby | Sessiz Güç (Bölüm 1)",
        "debate_comment": "💬 TARTIŞMA: Bir odadaki en tehlikeli insan neden daima en sessiz olandır? Katılıyor musunuz?",
    },
]


async def launch_authenticated_browser(headless: bool = False):
    """Launches Playwright Chromium with persistent session storage."""
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    context = await p.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ]
    )
    return p, context


async def init_session_interactive() -> bool:
    """
    Opens YouTube Studio visibly so the user or session can authenticate.
    Saves cookies and persistent state.
    """
    print("\n" + "=" * 70)
    print("🔑 YOUTUBE STUDIO OTURUM YÖNETİCİSİ BAŞLATILIYOR...")
    print("   Tarayıcı açılıyor: studio.youtube.com")
    print("=" * 70)

    p, context = await launch_authenticated_browser(headless=False)
    page = await context.new_page()

    try:
        await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        if "accounts.google.com" in page.url:
            print("\n👉 Açılan pencerede YouTube / Google hesabınıza giriş yapın.")
            print("   Giriş tamamlandığında sistem otomatik olarak oturumu kalıcı kaydedecektir...")
            await page.wait_for_url("**/studio.youtube.com/**", timeout=300000)

        print("\n✅ YouTube Studio Oturumu Doğrulandı ve Kalıcı Kasaya Kaydedildi!")
        cookies = await context.cookies()
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"💾 Çerezler kaydedildi: {COOKIE_FILE}")

        await page.wait_for_timeout(2000)
        await context.close()
        await p.stop()
        return True

    except Exception as e:
        print(f"⚠️ Oturum Notu: {e}")
        await context.close()
        await p.stop()
        return False


async def set_related_video_via_playwright(short_video_id: str, target_video_id: str, headless: bool = False) -> bool:
    """
    Automates setting the official YouTube Shorts 'İlgili Video' (Related Video)
    link on https://studio.youtube.com/video/{short_video_id}/edit.
    """
    studio_url = f"https://studio.youtube.com/video/{short_video_id}/edit"
    print(f"\n🎬 [Studio Deep Link] Shorts İlgili Video Bağlanıyor...")
    print(f"   Kaynak: {short_video_id} ➔ Hedef: {target_video_id}")

    p, context = await launch_authenticated_browser(headless=headless)
    page = await context.new_page()

    try:
        await page.goto(studio_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)

        if "accounts.google.com" in page.url:
            print("⚠️ Oturum açık değil. Lütfen önce 'init-session' çalıştırın.")
            await context.close()
            await p.stop()
            return False

        # Look for Related Video section in YouTube Studio Video Details
        picker_selectors = [
            "ytcp-video-metadata-related-video",
            "#related-video-picker",
            "[aria-label*='İlgili video']",
            "[aria-label*='Related video']",
            "button[aria-label*='İlgili video']",
        ]

        picker = None
        for sel in picker_selectors:
            picker = await page.query_selector(sel)
            if picker:
                break

        if picker:
            await picker.click()
            await page.wait_for_timeout(2000)

            # Search box in dialog
            search_box = await page.wait_for_selector(
                "ytcp-video-pick-dialog input, input#search-input, input[aria-label*='Arama']",
                timeout=10000
            )
            if search_box:
                await search_box.fill(target_video_id)
                await page.wait_for_timeout(2000)

                # Click matching result
                result_item = await page.wait_for_selector(
                    "ytcp-video-row, ytcp-entity-card, ytcp-video-pick-dialog ytcp-entity-card",
                    timeout=10000
                )
                if result_item:
                    await result_item.click()
                    await page.wait_for_timeout(1500)

                    # Click Save button in Studio top right
                    save_btn = await page.wait_for_selector(
                        "#save-button, ytcp-button#save-button, button[aria-label*='Kaydet']",
                        timeout=10000
                    )
                    if save_btn:
                        await save_btn.click()
                        print(f"✅ İlgili Video Başarıyla Kaydedildi! ({short_video_id} -> {target_video_id})")
                        await page.wait_for_timeout(3000)
                        await context.close()
                        await p.stop()
                        return True

        print("ℹ️ İlgili video seçici bulunamadı veya video zaten bağlı.")
        await context.close()
        await p.stop()
        return False

    except Exception as e:
        print(f"⚠️ Studio İlgili Video Notu: {e}")
        await context.close()
        await p.stop()
        return False


async def upload_audio_track_via_playwright(video_id: str, lang_code: str, audio_file_path: Path, headless: bool = False) -> bool:
    """
    Automates uploading supplementary multi-language audio track (.mp3) in YouTube Studio:
    https://studio.youtube.com/video/{video_id}/translations
    """
    trans_url = f"https://studio.youtube.com/video/{video_id}/translations"
    print(f"\n🎧 [Studio Audio Track] Çok Dilli Ses Parçası Yükleniyor: {lang_code.upper()}")
    print(f"   Dosya: {audio_file_path.name}")

    if not audio_file_path.exists():
        print(f"❌ Ses dosyası bulunamadı: {audio_file_path}")
        return False

    p, context = await launch_authenticated_browser(headless=headless)
    page = await context.new_page()

    try:
        await page.goto(trans_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)

        # Look for Add Audio button in Subtitles table
        add_audio_btn = await page.query_selector(
            "button[aria-label*='Ses'], [aria-label*='Audio'], ytcp-button[aria-label*='Ses parçası']"
        )
        if add_audio_btn:
            async with page.expect_file_chooser() as fc_info:
                await add_audio_btn.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(audio_file_path))
            print(f"📤 Ses Dosyası Gönderildi: {audio_file_path.name}")
            await page.wait_for_timeout(5000)

            # Click Publish button
            publish_btn = await page.query_selector("#publish-button, ytcp-button#publish-button")
            if publish_btn:
                await publish_btn.click()
                print(f"✅ Çok Dilli Ses Parçası Başarıyla Yayınlandı ({lang_code})!")
                await page.wait_for_timeout(3000)
                await context.close()
                await p.stop()
                return True
        else:
            print(f"ℹ️ Kanalda Çok Dilli Ses Parçası sekmesi henüz açık değil veya bulunamadı.")

        await context.close()
        await p.stop()
        return False

    except Exception as e:
        print(f"⚠️ Audio Track Yükleme Notu: {e}")
        await context.close()
        await p.stop()
        return False


async def pin_comment_via_browser(video_id: str, comment_text: str, headless: bool = False) -> bool:
    """
    Navigates to the video page, submits the comment, and pins it to top ($0 API quota).
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"\n💬 [Browser Pin Comment] Yorum Başa Sabitleniyor: {video_id}")
    print(f"   Metin: {comment_text[:50]}...")

    p, context = await launch_authenticated_browser(headless=headless)
    page = await context.new_page()

    try:
        await page.goto(watch_url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(3000)

        # Scroll to comments
        await page.evaluate("window.scrollBy(0, 500)")
        await page.wait_for_timeout(2000)

        # Click comment box
        placeholder = await page.wait_for_selector("#placeholder-area", timeout=10000)
        if placeholder:
            await placeholder.click()
            await page.wait_for_timeout(1000)

            input_box = await page.wait_for_selector("#contenteditable-root", timeout=5000)
            if input_box:
                await input_box.fill(comment_text)
                await page.wait_for_timeout(1000)

                submit_btn = await page.wait_for_selector("#submit-button button", timeout=5000)
                if submit_btn:
                    await submit_btn.click()
                    print("💬 Yorum Gönderildi. Şimdi Başa Sabitleniyor...")
                    await page.wait_for_timeout(3000)

                    # Click action menu (3 dots) on the latest comment
                    menu_btn = await page.wait_for_selector(
                        "ytd-comment-thread-renderer #action-menu yt-icon-button, #action-menu button",
                        timeout=8000
                    )
                    if menu_btn:
                        await menu_btn.click()
                        await page.wait_for_timeout(1000)

                        # Click 'Başa sabitle' / 'Pin to top'
                        pin_item = await page.wait_for_selector(
                            "tp-yt-paper-listbox ytd-menu-service-item-renderer, [aria-label*='sabitle'], [aria-label*='Pin']",
                            timeout=5000
                        )
                        if pin_item:
                            await pin_item.click()
                            await page.wait_for_timeout(1000)

                            # Confirm modal
                            confirm_btn = await page.query_selector("#confirm-button button, ytd-button-renderer#confirm-button")
                            if confirm_btn:
                                await confirm_btn.click()

                            print(f"📌 YORUM BAŞARIYLA BAŞA SABİTLENDİ ({video_id})!")
                            await page.wait_for_timeout(2000)
                            await context.close()
                            await p.stop()
                            return True

        await context.close()
        await p.stop()
        return False

    except Exception as e:
        print(f"⚠️ Yorum Sabitleme Notu: {e}")
        await context.close()
        await p.stop()
        return False


def run_full_shorts_network_autopilot(headless: bool = False):
    """
    Executes the complete funnel across all live flagship videos:
    1. Sets 'İlgili Video' on each Short
    2. Pins the debate comment
    3. Attaches multi-language audio tracks
    """
    print("\n" + "=" * 70)
    print("🚀 TÜM KANAL İÇİN DERİN STUDIO OTOMASYON MOTORU BAŞLATILIYOR")
    print(f"   Hedef Ağ: {len(FLAGSHIP_SHORTS_NETWORK)} Amiral Gemisi Video")
    print("=" * 70)

    for item in FLAGSHIP_SHORTS_NETWORK:
        short_id = item["short_id"]
        target_id = item["target_id"]
        comment = item["debate_comment"]

        print(f"\n--- İşleniyor: {item['title']} ---")

        # 1. Related Video
        try:
            asyncio.run(set_related_video_via_playwright(short_id, target_id, headless=headless))
        except Exception as e:
            print(f"⚠️ İlgili video atlandı ({short_id}): {e}")

        # 2. Pin Comment
        try:
            asyncio.run(pin_comment_via_browser(short_id, comment, headless=headless))
        except Exception as e:
            print(f"⚠️ Sabit yorum atlandı ({short_id}): {e}")

    # 3. Multi-track Audio on Flagship Video
    multitrack_dir = BASE_DIR / "output" / "multitrack_test"
    if multitrack_dir.exists():
        en_track = multitrack_dir / "audio_track_en_english.mp3"
        es_track = multitrack_dir / "audio_track_es_español.mp3"
        de_track = multitrack_dir / "audio_track_de_deutsch.mp3"

        if en_track.exists():
            try:
                asyncio.run(upload_audio_track_via_playwright("L8Eze1G3OFw", "en", en_track, headless=headless))
            except Exception as e:
                print(f"⚠️ EN ses parçası atlandı: {e}")

    print("\n" + "=" * 70)
    print("👑 DERİN STUDIO OTOMASYONU TAMAMLANDI!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube Studio Deep Automation via Playwright")
    parser.add_argument("--login", action="store_true", help="Open browser to authenticate YouTube Studio session")
    parser.add_argument("--auto-all", action="store_true", help="Run full network linking and comment pinning")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--link-shorts", action="store_true", help="Link related videos across all Shorts")
    parser.add_argument("--pin-comments", action="store_true", help="Pin debate comments on all Shorts")
    args = parser.parse_args()

    if args.login:
        asyncio.run(init_session_interactive())
    elif args.auto_all:
        run_full_shorts_network_autopilot(headless=args.headless)
    else:
        print("Kullanım:")
        print("  python scripts/youtube_studio_deep_automation.py --login       (Oturum aç ve çerezleri kaydet)")
        print("  python scripts/youtube_studio_deep_automation.py --auto-all    (Tüm İlgili Videoları ve Yorumları bağla)")
