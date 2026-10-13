"""
Channel Branding & Subscribe Badge Renderer for 'Zirvenin Kanunu'.
Renders a sleek, modern glassmorphism '👑 ZİRVENİN KANUNU • ABONE OL 🔔'
watermark badge using Playwright at retina resolution.
"""

from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def render_channel_subscribe_badge(output_path: Path = OUTPUT_DIR / "channel_subscribe_badge.png") -> Path:
    """Renders pixel-perfect glassmorphism subscribe badge."""
    html_content = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: transparent;
    display: inline-flex;
    justify-content: center;
    align-items: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    padding: 10px;
  }
  .badge-pill {
    display: inline-flex;
    align-items: center;
    background: rgba(15, 18, 24, 0.90);
    backdrop-filter: blur(16px);
    border: 1.5px solid rgba(255, 215, 0, 0.65);
    border-radius: 40px;
    padding: 10px 22px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6), 0 0 16px rgba(255, 215, 0, 0.20);
    gap: 14px;
  }
  .crown-icon {
    font-size: 26px;
    line-height: 1;
    filter: drop-shadow(0 2px 4px rgba(255, 215, 0, 0.5));
  }
  .channel-title {
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    text-shadow: 0 2px 8px rgba(0,0,0,0.8);
  }
  .divider {
    width: 2px;
    height: 22px;
    background: rgba(255, 255, 255, 0.25);
  }
  .subscribe-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #CC0000;
    color: #FFFFFF;
    font-size: 18px;
    font-weight: 800;
    padding: 7px 16px;
    border-radius: 20px;
    letter-spacing: 0.8px;
    box-shadow: 0 4px 12px rgba(204, 0, 0, 0.45);
  }
  .bell-icon {
    font-size: 16px;
  }
</style>
</head>
<body>
  <div class="badge-pill">
    <span class="crown-icon">👑</span>
    <span class="channel-title">ZİRVENİN KANUNU</span>
    <div class="divider"></div>
    <div class="subscribe-btn">
      <span>ABONE OL</span>
      <span class="bell-icon">🔔</span>
    </div>
  </div>
</body>
</html>"""

    temp_html = OUTPUT_DIR / "temp_badge.html"
    temp_html.write_text(html_content, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 800, "height": 140},
            device_scale_factor=2
        )
        page.goto(f"file:///{temp_html.resolve()}", wait_until="networkidle")
        badge = page.locator(".badge-pill").first
        badge.screenshot(path=str(output_path), omit_background=True)
        browser.close()

    if temp_html.exists():
        temp_html.unlink()

    print(f"✅ Subscribe badge rendered: {output_path} ({output_path.stat().st_size} bytes)")
    return output_path


if __name__ == "__main__":
    render_channel_subscribe_badge()
