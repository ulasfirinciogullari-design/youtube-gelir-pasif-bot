"""
Direct GitHub Sync Engine using GitHub REST API.
Commits and pushes files directly to ulasfirinciogullari-design/youtube-gelir-pasif-bot
using the authenticated gh CLI token.
"""

import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent

GH_FALLBACK = r"C:\Users\ULAŞ\.gemini\antigravity\bin\gh.exe"
OWNER = "ulasfirinciogullari-design"
REPO = "youtube-gelir-pasif-bot"
BRANCH = "main"


def get_gh_token() -> str:
    gh_bin = shutil.which("gh") or GH_FALLBACK
    out = subprocess.check_output([gh_bin, "auth", "token"]).decode().strip()
    return out


def get_remote_sha(rel_path: str, token: str) -> str | None:
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{rel_path}?ref={BRANCH}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Antigravity",
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            return data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def push_file_to_github(local_path: Path, rel_path: str, token: str, commit_msg: str, max_retries: int = 3) -> bool:
    if not local_path.exists():
        print(f"❌ Yerel dosya bulunamadı: {local_path}")
        return False

    with open(local_path, "rb") as f:
        content_bytes = f.read()
    b64_content = base64.b64encode(content_bytes).decode("ascii")

    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{rel_path}"

    for attempt in range(1, max_retries + 1):
        sha = get_remote_sha(rel_path, token)
        
        payload = {
            "message": commit_msg,
            "content": b64_content,
            "branch": BRANCH,
        }
        if sha:
            payload["sha"] = sha

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "Antigravity",
            }
        )

        try:
            with urllib.request.urlopen(req) as resp:
                res_data = json.loads(resp.read().decode())
                commit_sha = res_data.get("commit", {}).get("sha", "")[:7]
                print(f"✅ Başarıyla yüklendi: {rel_path} -> Commit: {commit_sha}")
                time.sleep(1.0)
                return True
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode()
            if e.code == 409 and attempt < max_retries:
                print(f"⏳ Çakışma algılandı ({rel_path}), yeniden deneniyor ({attempt}/{max_retries})...")
                time.sleep(2.0)
                continue
            print(f"⚠️ Hata ({rel_path}): {e.code} - {err_msg}")
            if attempt < max_retries:
                time.sleep(1.5)
                continue
            return False
        except Exception as e:
            print(f"⚠️ Beklenmeyen hata ({rel_path}): {e}")
            if attempt < max_retries:
                time.sleep(1.5)
                continue
            return False

    return False


def sync_core_factory_to_github():
    token = get_gh_token()
    print("\n" + "=" * 70)
    print(f"🚀 GITHUB CLOUD SENKRONİZASYONU: {OWNER}/{REPO} ({BRANCH})")
    print("=" * 70)

    files_to_sync = [
        ("requirements.txt", "requirements.txt"),
        ("ultimate_factory.py", "ultimate_factory.py"),
        ("continuous_autopilot.py", "continuous_autopilot.py"),
        ("publish_to_youtube.py", "publish_to_youtube.py"),
        (".github/workflows/viral_shorts_autopilot.yml", ".github/workflows/viral_shorts_autopilot.yml"),
        ("app/services/mega_content_vault.py", "app/services/mega_content_vault.py"),
        ("app/services/multi_voice_engine.py", "app/services/multi_voice_engine.py"),
        ("app/services/video_quality_analyst.py", "app/services/video_quality_analyst.py"),
        ("app/services/cinema_harvester.py", "app/services/cinema_harvester.py"),
        ("app/services/cinema_voice_master.py", "app/services/cinema_voice_master.py"),
        ("app/services/visual_fx_engine.py", "app/services/visual_fx_engine.py"),
        ("app/services/analytics_brain.py", "app/services/analytics_brain.py"),
        ("app/services/multilingual_captions.py", "app/services/multilingual_captions.py"),
        ("app/services/community_autopilot.py", "app/services/community_autopilot.py"),
        ("app/services/viral_keyword_matrix.json", "app/services/viral_keyword_matrix.json"),
        ("app/services/playlist_master_factory.py", "app/services/playlist_master_factory.py"),
        ("app/services/global_10_language_suite.py", "app/services/global_10_language_suite.py"),
        ("app/services/sound_fx_engine.py", "app/services/sound_fx_engine.py"),
        ("app/services/kinetic_subtitle_fx.py", "app/services/kinetic_subtitle_fx.py"),
        ("app/services/hype_comment_engine.py", "app/services/hype_comment_engine.py"),
        ("app/services/community_poll_master.py", "app/services/community_poll_master.py"),
        ("app/services/algorithm_beast_engine.py", "app/services/algorithm_beast_engine.py"),
        ("app/services/multi_track_audio_packager.py", "app/services/multi_track_audio_packager.py"),
        ("scripts/optimize_channel_seo.py", "scripts/optimize_channel_seo.py"),
        ("app/services/episodic_series_engine.py", "app/services/episodic_series_engine.py"),
        ("app/services/longform_cinema_compiler.py", "app/services/longform_cinema_compiler.py"),
        ("app/services/viral_topic_multiplier.py", "app/services/viral_topic_multiplier.py"),
        ("app/services/cross_link_router.py", "app/services/cross_link_router.py"),
        ("app/services/parallel_render_swarm.py", "app/services/parallel_render_swarm.py"),
        ("app/services/nonstop_swarm_publisher.py", "app/services/nonstop_swarm_publisher.py"),
        ("scripts/build_authentic_hollywood_short.py", "scripts/build_authentic_hollywood_short.py"),
        ("app/services/pro_audio_engine.py", "app/services/pro_audio_engine.py"),
        ("app/services/subtitle_engine.py", "app/services/subtitle_engine.py"),
        ("app/services/viral_script_master.py", "app/services/viral_script_master.py"),
        ("scripts/sync_to_github.py", "scripts/sync_to_github.py"),
    ]

    success_count = 0
    for local_rel, github_rel in files_to_sync:
        local_p = BASE_DIR / local_rel
        try:
            if push_file_to_github(local_p, github_rel, token, f"Deploy God-Tier Engine: {github_rel}"):
                success_count += 1
        except Exception as e:
            print(f"⚠️ Hata atlandı ({github_rel}): {e}")

    print("\n" + "=" * 70)
    print(f"👑 SENKRONİZASYON TAMAMLANDI: {success_count}/{len(files_to_sync)} Dosya GitHub'a Aktarıldı!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    sync_core_factory_to_github()
