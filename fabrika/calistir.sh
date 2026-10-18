#!/usr/bin/env bash
# Runs one factory tick on GitHub Actions: metrics -> reply watcher check -> gate -> tools -> episode -> commit.
# Replies run in the reply watcher (fabrika/nobet.py, ya_olsaydi_nobet.yml); the yorum modes answer once by hand.
# Usage: bash fabrika/calistir.sh saatlik|zorla|deneme|yorum|yorumdeneme|metrik|instagram|igdeneme|igdmtest|igbolum
set -uo pipefail
MOD="${1:-saatlik}"
cd "$(dirname "$0")/.."

push() {
  git config user.name "ya-olsaydi-bot"
  git config user.email "ya-olsaydi-bot@users.noreply.github.com"
  git add fabrika/durum.json fabrika/rapor.json fabrika/yorum_rapor.json fabrika/metrikler.json 2>/dev/null || true
  git add fabrika/ig_rapor.json fabrika/ig_anahtar.enc fabrika/cevap_durum.json 2>/dev/null || true
  git add fabrika/bolumler fabrika/ses fabrika/deneme 2>/dev/null || true
  git commit -q -m "$1 [skip ci]" || { echo "değişiklik yok"; return 0; }
  for i in 1 2 3; do
    git pull -q --rebase origin ya-olsaydi && git push -q origin HEAD:ya-olsaydi && return 0
    sleep $((i * 5))
  done
}

case "$MOD" in instagram|igdeneme|igdmtest)
  pip install -q requests cryptography
  flag="--kontrol"; [ "$MOD" = "igdeneme" ] && flag="--deneme"; [ "$MOD" = "igdmtest" ] && flag="--dmtest"
  out=$(python3 fabrika/instagram.py $flag | tail -n 1); echo "$out"
  push "$out"; exit 0
esac

if [ "$MOD" = "metrik" ]; then
  pip install -q requests
  out=$(python3 fabrika/metrik.py | tail -n 1); echo "$out"
  push "$out"; exit 0
fi

case "$MOD" in yorum|yorumdeneme)
  pip install -q requests cryptography
  flag=""; igflag="--cevap"; [ "$MOD" = "yorumdeneme" ] && flag="--deneme" && igflag="--deneme"
  python3 fabrika/yorum.py $flag | tee fabrika/_yorum.out
  out=$(tail -n 1 fabrika/_yorum.out)
  if [ -n "${YA_IG_TOKEN:-}" ]; then
    out="$out; $(python3 fabrika/instagram.py $igflag | tail -n 1)"
  fi
  push "$out"; exit 0
esac

if [ "$MOD" = "saatlik" ]; then
  pip install -q requests cryptography
  python3 fabrika/metrik.py
  out=$(python3 fabrika/nobet.py --sor | tail -n 1)  # restarts the reply watcher if its chain broke
  echo "$out"
fi

if [ "$MOD" = "saatlik" ]; then
  go=$(python3 fabrika/uret.py --kapi 2> fabrika/_kapi.out | sed -n 's/^go=//p')
  cat fabrika/_kapi.out
  if [ "$go" != "true" ]; then echo "Bu tur üretim yok."; push "$out; $(head -n 1 fabrika/_kapi.out)"; exit 0; fi
fi

sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends ffmpeg >/dev/null
pip install -q numpy requests cryptography
[ -f package.json ] || npm init -y >/dev/null
npm install -q --no-save playwright@1.55.0 && npx playwright install --with-deps chromium >/dev/null

if [ "$MOD" = "igbolum" ]; then  # tetik.txt: "igbolum y001" posts that earlier episode to Instagram
  python fabrika/uret.py --mod igbolum --bolum "$(awk '{print $2}' fabrika/tetik.txt)"
else
  python fabrika/uret.py --mod "$MOD"
fi
status=$?

title=$(python3 -c "import json;r=json.load(open('fabrika/rapor.json'));print((r.get('title') or r.get('error') or r.get('skipped') or 'tur')[:60])")
push "Fabrika ($MOD): $title"
exit $status
