import base64
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
EMOJIS_DIR = ASSETS_DIR / "emojis"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def get_emoji_data_uri(emoji_code: str) -> str:
    svg_file = EMOJIS_DIR / f"{emoji_code}.svg"
    if svg_file.exists():
        data = base64.b64encode(svg_file.read_bytes()).decode("utf-8")
        return f"data:image/svg+xml;base64,{data}"
    return ""

def main():
    target_uri = get_emoji_data_uri("1f3af")
    bubble_uri = get_emoji_data_uri("1f4ac")
    crown_uri = get_emoji_data_uri("1f451")
    fire_uri = get_emoji_data_uri("1f525")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px;
    height: 220px;
    background: transparent;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
  }}
  .card {{
    width: 1000px;
    background: rgba(18, 20, 26, 0.94);
    border: 1px solid rgba(255, 215, 0, 0.35);
    border-radius: 24px;
    padding: 20px 32px;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.7);
    display: flex;
    flex-direction: column;
    gap: 12px;
    position: relative;
    overflow: hidden;
  }}
  .card::before {{
    content: '';
    position: absolute;
    top: 0; left: 40px; right: 40px;
    height: 3px;
    background: linear-gradient(90deg, transparent, #FFD700, #FFA500, transparent);
  }}
  .row-top {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .emoji-icon {{
    width: 28px;
    height: 28px;
    display: inline-block;
    vertical-align: middle;
  }}
  .question-text {{
    color: #FFFFFF;
    font-size: 26px;
    font-weight: 800;
    letter-spacing: -0.2px;
  }}
  .row-bottom {{
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .highlight-badge {{
    display: flex;
    align-items: center;
    gap: 10px;
    color: #FFD700;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.2px;
  }}
  .action-pill {{
    background: linear-gradient(135deg, #FF0055, #E50914);
    color: #FFFFFF;
    padding: 8px 22px;
    border-radius: 14px;
    font-size: 20px;
    font-weight: 800;
    display: flex;
    align-items: center;
    gap: 8px;
    box-shadow: 0 4px 16px rgba(229, 9, 20, 0.4);
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="row-top">
      <img src="{bubble_uri}" class="emoji-icon" />
      <span class="question-text">Sen olsan ne derdin? Hedef Özgür mü Tayfun mu?</span>
      <img src="{target_uri}" class="emoji-icon" />
    </div>
    <div class="row-bottom">
      <div class="highlight-badge">
        <img src="{crown_uri}" class="emoji-icon" />
        <span>En efsane yorumu ve repliği sabitliyoruz!</span>
      </div>
      <div class="action-pill">
        <img src="{fire_uri}" class="emoji-icon" style="width: 22px; height: 22px;" />
        <span>Yoruma Yaz 👇</span>
      </div>
    </div>
  </div>
</body>
</html>
"""
    temp_html = OUTPUT_DIR / "temp_footer.html"
    temp_html.write_text(html_content, encoding="utf-8")

    out_png = OUTPUT_DIR / "kolpacino_footer_playwright.png"
    print("🎨 Rendering Glassmorphism Footer with Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 220}, device_scale_factor=2)
        page.goto(f"file:///{temp_html.resolve()}")
        page.wait_for_timeout(200)
        page.screenshot(path=str(out_png), omit_background=True)
        browser.close()

    print(f"✅ Clean Footer Rendered: {out_png} ({out_png.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
