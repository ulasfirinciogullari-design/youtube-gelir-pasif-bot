#!/usr/bin/env bash
# Runs one factory tick on GitHub Actions: gate -> tools -> episode -> commit the outcome to ya-olsaydi.
# Usage: bash fabrika/calistir.sh saatlik|zorla|deneme
set -uo pipefail
MOD="${1:-saatlik}"
cd "$(dirname "$0")/.."

if [ "$MOD" = "saatlik" ]; then
  go=$(python3 fabrika/uret.py --kapi | sed -n 's/^go=//p')
  if [ "$go" != "true" ]; then echo "Bu tur üretim yok."; exit 0; fi
fi

sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends ffmpeg >/dev/null
pip install -q numpy requests
[ -f package.json ] || npm init -y >/dev/null
npm install -q --no-save playwright@1.55.0 && npx playwright install --with-deps chromium >/dev/null

python fabrika/uret.py --mod "$MOD"
status=$?

git config user.name "ya-olsaydi-bot"
git config user.email "ya-olsaydi-bot@users.noreply.github.com"
git add fabrika/durum.json fabrika/rapor.json
git add fabrika/bolumler fabrika/ses fabrika/deneme 2>/dev/null || true
title=$(python3 -c "import json;r=json.load(open('fabrika/rapor.json'));print((r.get('title') or r.get('error') or r.get('skipped') or 'tur')[:60])")
git commit -q -m "Fabrika ($MOD): $title [skip ci]" || echo "değişiklik yok"
for i in 1 2 3; do
  git pull -q --rebase origin ya-olsaydi && git push -q origin HEAD:ya-olsaydi && break
  sleep $((i * 5))
done
exit $status
