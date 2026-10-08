"""
Playlist Master Factory for Zirvenin Kanunu.
Creates and manages high-SEO niche binge-watch playlists via YouTube Data API v3.
Features:
1. Automated Niche Playlists Creation (48 Laws, Stoicism, Shelby, Walter White, Cosmic, etc.)
2. Auto-Binge-Watch Insertion: Inserts every published video into matching thematic playlists.
3. Retroactive Sync: Sorts all past and current videos into their optimal playlists.
"""

import json
from pathlib import Path
import sys
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import get_authenticated_service

OFFICIAL_PLAYLIST_DEFINITIONS = [
    {
        "key": "master",
        "title": "👑 Zirvenin Kanunları | Tüm Master Bölümler (Official)",
        "description": "Görünmeyen Güç, Karanlık Psikoloji, Stoacılık ve Sinema Tarihinin Zirve Anları. Kesintisiz 60 FPS Binge-Watch listesi.\n\n#shorts #güçyasaları #motivasyon #stoacılık #sinema",
        "keywords": ["all", "master", "zirve"]
    },
    {
        "key": "power_laws",
        "title": "♟️ 48 Güç Yasası | Robert Greene (Tüm Bölümler)",
        "description": "İktidarın 48 Yasası serisi. Efendini asla gölgede bırakma, niyetini gizle, daha az konuş ve masayı yönet. Tüm kuralların derin psikolojik analizi.\n\n#48güçyasası #iktidarın48yasası #robertgreene #güçyasaları #shorts",
        "keywords": ["48 güç", "power law", "yasa", "efendini", "greene"]
    },
    {
        "key": "shelby_power",
        "title": "🚬 Thomas Shelby & Peaky Blinders | Sessiz Güç ve Saygı",
        "description": "Thomas Shelby'nin soğukkanlı zihni, sessiz otoritesi ve saygının bedeli. Göz kırpmama sanatı, beden dili ve alfa psikolojisi.\n\n#thomasshelby #peakyblinders #cillianmurphy #sessizgüç #sigma #shorts",
        "keywords": ["shelby", "peaky", "sessiz güç", "saygı", "cillian murphy"]
    },
    {
        "key": "stoic_mind",
        "title": "🏛️ Stoacılık & Çelik İrade | Marcus Aurelius & Seneca",
        "description": "Zihnini yenilmez bir kaleye dönüştür. Marcus Aurelius, Seneca ve Epiktetos'tan modern hayatta duygusal dokunulmazlık ve disiplin dersleri.\n\n#stoacılık #marcusaurelius #felsefe #çelikirade #meditasyon #shorts",
        "keywords": ["stoa", "stoic", "aurelius", "irade", "memento", "zihinsel kale"]
    },
    {
        "key": "walter_white",
        "title": "🧪 Walter White & Heisenberg | Karanlık Dönüşüm & Güç",
        "description": "Masumiyetin ölümü ve Heisenberg dönüşümü. Breaking Bad'in en unutulmaz güç sahneleri ve manipulasyon psikolojisi.\n\n#walterwhite #heisenberg #breakingbad #karanlıkpsikoloji #shorts",
        "keywords": ["walter", "heisenberg", "breaking bad", "tehlike"]
    },
    {
        "key": "underworld_mob",
        "title": "🕶️ Sinematik Yeraltı Dünyası | The Godfather & Scarface",
        "description": "Don Vito Corleone, Michael Corleone ve Tony Montana'nın masadaki güç, aile sadakati ve strateji kuralları.\n\n#godfather #scarface #alpacino #mafyayasaları #sadakat #shorts",
        "keywords": ["godfather", "corleone", "scarface", "montana", "mafya", "sadakat"]
    },
    {
        "key": "cosmic_abyss",
        "title": "🌌 Kozmik Dehşet & Karadelikler | Evrenin Gizemleri",
        "description": "Interstellar, Gargantua karadeliği, Fermi Paradoksu ve zamanın bükülmesi. İnsan aklını aşan evrenin en karanlık yasaları.\n\n#interstellar #karadelik #evren #kozmikdehşet #bilim #shorts",
        "keywords": ["interstellar", "karadelik", "kozmik", "fermi", "gargantua", "evren"]
    },
    {
        "key": "global_english",
        "title": "🌍 Worldwide Alpha Mindset & Power Laws (English Masters)",
        "description": "Original English voices, 48 Laws of Power, high-value psychology, and cinema master scenes in 60 FPS for international audiences.\n\n#shorts #powerlaws #48lawsofpower #stoic #mindset #sigma",
        "keywords": ["never outshine", "english", "part", "law", "stoic indifference"]
    }
]


def get_existing_playlists(youtube) -> Dict[str, str]:
    """Returns mapping of lowercase playlist title -> playlist ID."""
    playlists = {}
    next_page = None
    while True:
        resp = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50,
            pageToken=next_page
        ).execute()
        for pl in resp.get("items", []):
            title = pl["snippet"]["title"]
            pl_id = pl["id"]
            playlists[title.lower()] = pl_id
        next_page = resp.get("nextPageToken")
        if not next_page:
            break
    return playlists


def ensure_niche_playlists() -> Dict[str, str]:
    """
    Creates any missing niche playlists from OFFICIAL_PLAYLIST_DEFINITIONS
    and returns a dict of {key: playlist_id}.
    """
    youtube = get_authenticated_service()
    existing = get_existing_playlists(youtube)

    print("\n" + "=" * 70)
    print("📁 OYNATMA LİSTELERİ FABRİKASI: Niş Binge-Watch Listeleri Denetleniyor")
    print("=" * 70)

    playlist_map = {}

    for pldef in OFFICIAL_PLAYLIST_DEFINITIONS:
        key = pldef["key"]
        title = pldef["title"]
        title_lower = title.lower()

        # Check if already exists (fuzzy match title prefix)
        matched_id = None
        for ex_title, ex_id in existing.items():
            if title_lower[:25] in ex_title or ex_title[:25] in title_lower:
                matched_id = ex_id
                break

        if matched_id:
            playlist_map[key] = matched_id
            print(f"✅ Mevcut: {title[:45]}... (ID: {matched_id})")
        else:
            # Create playlist
            body = {
                "snippet": {
                    "title": title[:100],
                    "description": pldef["description"][:5000],
                },
                "status": {
                    "privacyStatus": "public"
                }
            }
            try:
                res = youtube.playlists().insert(part="snippet,status", body=body).execute()
                new_id = res["id"]
                playlist_map[key] = new_id
                print(f"🎉 Yeni Oluşturuldu: {title[:45]}... -> ID: {new_id}")
            except Exception as e:
                print(f"⚠️ Liste oluşturma hatası ({key}): {e}")

    print("=" * 70 + "\n")
    return playlist_map


def add_video_to_playlist(youtube, video_id: str, playlist_id: str) -> bool:
    """Adds a video to a playlist if not already present."""
    # Check if already in playlist
    try:
        items = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50
        ).execute()
        for it in items.get("items", []):
            if it["snippet"]["resourceId"]["videoId"] == video_id:
                return False # already exists
    except Exception:
        pass

    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id
                    }
                }
            }
        ).execute()
        return True
    except Exception as e:
        return False


def sync_all_videos_to_playlists():
    """
    Scans all published videos on channel and sorts every video into its
    matching niche playlist and the master playlist.
    """
    youtube = get_authenticated_service()
    pl_map = ensure_niche_playlists()

    ch = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_id = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    vids_resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_id,
        maxResults=50
    ).execute()

    print("=" * 70)
    print("🔄 VİDEOLAR NİŞ OYNATMA LİSTELERİNE DİZİLİYOR (Binge-Watch Otomasyonu)")
    print("=" * 70)

    inserted_total = 0

    for it in vids_resp.get("items", []):
        video_id = it["snippet"]["resourceId"]["videoId"]
        title = it["snippet"]["title"]
        title_lower = title.lower()

        # Always add to master list
        if "master" in pl_map:
            if add_video_to_playlist(youtube, video_id, pl_map["master"]):
                inserted_total += 1

        # Match specific niche playlists
        for pldef in OFFICIAL_PLAYLIST_DEFINITIONS:
            key = pldef["key"]
            if key == "master" or key not in pl_map:
                continue

            # Check keyword match
            if any(k in title_lower for k in pldef["keywords"]):
                if add_video_to_playlist(youtube, video_id, pl_map[key]):
                    print(f"➕ [{pldef['title'][:25]}] -> {title[:40]}")
                    inserted_total += 1

    print("\n" + "=" * 70)
    print(f"👑 BİLANÇO: Toplam {inserted_total} video-liste eşleşmesi başarıyla tamamlandı!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    sync_all_videos_to_playlists()
