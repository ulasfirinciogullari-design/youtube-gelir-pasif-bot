import sys, json
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from publish_to_youtube import get_authenticated_service

yt = get_authenticated_service()

# Target additions:
assignments = [
    ("YG5F_i1wMlk", "PLVCa56yB4xNk"), # Kozmik Dehşet
    ("Eqj1OWsZ6rs", "PLHm5HJBArNG8"), # Godfather
    ("JsYP-J8i7ng", "PLU3Ps6ctDLeE"), # English Masters
    ("JsYP-J8i7ng", "PLfpXGpE89vUI"), # 48 Güç Yasası
    ("6Wj-x_l9qQE", "PLWp2bBxJInPI"), # Thomas Shelby
    ("mcjAB-IzW1I", "PLWp2bBxJInPI"), # Thomas Shelby
    ("cDzCHF9JjD8", "PLWp2bBxJInPI"), # Thomas Shelby
    ("-vqPZgMEXJE", "PLfpXGpE89vUI"), # 48 Güç Yasası
    ("4DotMDpYJ3M", "PLfpXGpE89vUI"), # 48 Güç Yasası
    ("Go1brUNX40k", "PLAjTUW8DRKlE"), # Stoacılık
]

for vid, pl_id in assignments:
    try:
        yt.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": pl_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": vid
                    }
                }
            }
        ).execute()
        print(f"✅ Eklendi: {vid} -> {pl_id}")
    except Exception as e:
        if "playlistContainsVideo" in str(e) or "already in playlist" in str(e).lower():
            print(f"ℹ️ Zaten listede: {vid} in {pl_id}")
        else:
            print(f"⚠️ Hata: {vid} -> {pl_id}: {e}")
