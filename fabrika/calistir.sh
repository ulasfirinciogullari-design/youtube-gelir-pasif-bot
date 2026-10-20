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
  git add -A fabrika/kuyruk 2>/dev/null || true  # used and broken queue files leave the queue
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

sudo apt-get update -qq || echo "apt-get update hata verdi, kuruluma yine de geçiliyor"  # one broken mirror must not stop it
sudo apt-get install -y --no-install-recommends ffmpeg fonts-noto-color-emoji >/dev/null
pip install -q numpy requests cryptography
[ -f package.json ] || npm init -y >/dev/null
npm install -q --no-save playwright@1.55.0
# 2026-10-20 ubuntu-latest became Ubuntu 26.04, which Playwright 1.55 refuses ("does not support chromium on
# ubuntu26.04-x64"): newer systems get the 24.04 build, which runs there.
. /etc/os-release
case "${VERSION_ID:-}" in 2[0-4].*) ;; *) export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 ;; esac
# --with-deps needs apt; when that step fails the browser alone still downloads
npx playwright install --with-deps chromium > fabrika/_pw.log 2>&1 \
  || { echo "--with-deps olmadı, yalnız tarayıcı kuruluyor" >> fabrika/_pw.log; npx playwright install chromium >> fabrika/_pw.log 2>&1; }
if ! ls "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell >/dev/null 2>&1; then
  export CHROME_PATH="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || true)"
  echo "Playwright tarayıcısı yok, sistemdeki kullanılıyor: ${CHROME_PATH:-yok}" | tee -a fabrika/_pw.log
fi

if [ "$MOD" = "igbolum" ]; then  # tetik.txt: "igbolum y001" posts that earlier episode to Instagram
  python fabrika/uret.py --mod igbolum --bolum "$(awk '{print $2}' fabrika/tetik.txt)"
else
  python fabrika/uret.py --mod "$MOD"
fi
status=$?

title=$(python3 -c "import json;r=json.load(open('fabrika/rapor.json'));print((r.get('title') or r.get('error') or r.get('skipped') or 'tur')[:60])")
push "Fabrika ($MOD): $title"
exit $status
