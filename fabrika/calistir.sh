#!/usr/bin/env bash
# Runs one factory tick on GitHub Actions: comment replies -> gate -> tools -> episode -> commit the outcome.
# Usage: bash fabrika/calistir.sh saatlik|zorla|deneme|yorum|yorumdeneme|metrik
set -uo pipefail
MOD="${1:-saatlik}"
cd "$(dirname "$0")/.."

push() {
  git config user.name "ya-olsaydi-bot"
  git config user.email "ya-olsaydi-bot@users.noreply.github.com"
  git add fabrika/durum.json fabrika/rapor.json fabrika/yorum_rapor.json fabrika/metrikler.json 2>/dev/null || true
  git add fabrika/bolumler fabrika/ses fabrika/deneme 2>/dev/null || true
  git commit -q -m "$1 [skip ci]" || { echo "değişiklik yok"; return 0; }
  for i in 1 2 3; do
    git pull -q --rebase origin ya-olsaydi && git push -q origin HEAD:ya-olsaydi && return 0
    sleep $((i * 5))
  done
}

if [ "$MOD" = "metrik" ]; then
  pip install -q requests
  out=$(python3 fabrika/metrik.py | tail -n 1); echo "$out"
  push "$out"; exit 0
fi

case "$MOD" in saatlik|yorum|yorumdeneme)
  pip install -q requests
  [ "$MOD" = "saatlik" ] && python3 fabrika/metrik.py
  flag=""; [ "$MOD" = "yorumdeneme" ] && flag="--deneme"
  python3 fabrika/yorum.py $flag | tee fabrika/_yorum.out
  out=$(tail -n 1 fabrika/_yorum.out)
  if [ "$MOD" != "saatlik" ]; then push "$out"; exit 0; fi
esac

if [ "$MOD" = "saatlik" ]; then
  go=$(python3 fabrika/uret.py --kapi 2> fabrika/_kapi.out | sed -n 's/^go=//p')
  cat fabrika/_kapi.out
  if [ "$go" != "true" ]; then echo "Bu tur üretim yok."; push "$out; $(head -n 1 fabrika/_kapi.out)"; exit 0; fi
fi

sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends ffmpeg >/dev/null
pip install -q numpy requests
[ -f package.json ] || npm init -y >/dev/null
npm install -q --no-save playwright@1.55.0 && npx playwright install --with-deps chromium >/dev/null

python fabrika/uret.py --mod "$MOD"
status=$?

title=$(python3 -c "import json;r=json.load(open('fabrika/rapor.json'));print((r.get('title') or r.get('error') or r.get('skipped') or 'tur')[:60])")
push "Fabrika ($MOD): $title"
exit $status
