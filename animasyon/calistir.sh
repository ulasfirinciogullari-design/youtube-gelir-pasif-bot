#!/usr/bin/env bash
# One animated episode on GitHub Actions. animasyon/tetik.txt holds "<bolum> <mod>", e.g. "k001 gizli".
set -uo pipefail
cd "$(dirname "$0")/.."
BOLUM=$(awk '{print $1}' animasyon/tetik.txt)
MOD=$(awk '{print $2}' animasyon/tetik.txt)
MOD=${MOD:-deneme}

git fetch -q origin channel-data
mkdir -p channel-data/audio/music channel-data/audio/sfx
for f in music/karakter_2.mp3 sfx/impact.mp3 sfx/whoosh.mp3 sfx/riser.mp3 sfx/pop.mp3; do
  git show "origin/channel-data:audio/$f" > "channel-data/audio/$f"
done

sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends ffmpeg >/dev/null
pip install -q numpy requests
[ -f package.json ] || npm init -y >/dev/null
npm install -q --no-save playwright@1.55.0 && npx playwright install --with-deps chromium >/dev/null

python animasyon/uret.py --bolum "$BOLUM" --mod "$MOD" --data channel-data
status=$?

git config user.name "zk-animasyon-bot"
git config user.email "zk-animasyon-bot@users.noreply.github.com"
git add -f animasyon/rapor.json animasyon/cikti animasyon/ses 2>/dev/null || true  # the repo ignores *.mp3
msg=$(python3 -c "import json;r=json.load(open('animasyon/rapor.json'));print((r.get('url') or r.get('error') or 'deneme')[:80])")
git commit -q -m "Animasyon $BOLUM ($MOD): $msg [skip ci]" || echo "değişiklik yok"
for i in 1 2 3; do
  git pull -q --rebase origin zk-animasyon && git push -q origin HEAD:zk-animasyon && break
  sleep $((i * 5))
done
exit $status
