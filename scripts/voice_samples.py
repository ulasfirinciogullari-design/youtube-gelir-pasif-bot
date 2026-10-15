"""
Voice samples for choosing the narrator of Zirvenin Kanunu.

The narrator is the critic's most-cited problem ("monoton, enerjisiz ve yavaş", 14 voice issues in 8 critiques,
2026-10-14), and only Ulaş can judge a voice by ear. This script voices the same four-line owl script with a few
voices and settings (at most six samples of about 190 characters each, so roughly 1k ElevenLabs credits) and writes
them with an index.json to channel-data/history/voice_samples/, which the workflow commits to the channel-data branch.

Every line is voiced separately and tightened exactly as in a Short (original_factory.build_narration, with the
0.15 s pause between lines), then brought to the narration's level in the Short (-16 LUFS, the same chain
original_factory.mix_levels gives the narration), so loudness does not decide the comparison.

  1  Alp as the Shorts sound today (eleven_multilingual_v2, stability 0.32, style 0.45, ffmpeg 1.1x)
  2  Alp speaking faster and livelier on his own (speed 1.15, style 0.6, stability 0.35, no ffmpeg speed-up)
  3  Alp on eleven_v3 with audio tags ([excited] on line 1, [surprised] on line 3)
  4-6 other Turkish voices with today's settings: the voices in ELEVENLABS_VOICE_MAP first, then the most cloned
      narration voices of the ElevenLabs shared library

The winner's settings go into the repo Variables listed under "repo_variables" in index.json.

--set maliyet answers Ulaş's cost question (2026-10-15, "maliyeti düşürmek ... kalite düşmeden"): at 24 Shorts a day
Alp needs the ~$99 ElevenLabs Pro plan, so it puts today's Alp next to cheaper narrators, each with its monthly cost:
Alp on eleven_flash_v2_5 (half the credits), three Gemini voices and Gemini Pro (a few dollars a month,
app/services/gemini_tts.py) and the free edge-tts voice the Shorts fall back to.

  python scripts/voice_samples.py --out channel-data/history/voice_samples
  python scripts/voice_samples.py --set maliyet --out channel-data/history/voice_samples_maliyet
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import original_factory as factory  # noqa: E402
from app.services import elevenlabs_engine as eleven  # noqa: E402
from app.services import gemini_tts  # noqa: E402

API = "https://api.elevenlabs.io/v1"
SHARED_VOICES_PATH = "/shared-voices?language=tr&page_size=30"
DEFAULT_OUT = BASE_DIR / "channel-data" / "history" / "voice_samples"
MAX_SAMPLES = 6
# The owl opener the new first-sentence rule asks for: a claim about what is on screen, with a number.
LINES = [
    "Bu baykuş boynunu kırmadan 270 derece döner.",
    "Bizim boynumuzda 7 omur var, baykuşta tam 14.",
    "Ama asıl sır, kafasına kan taşıyan damarlarda.",
    "Başı arkaya dönükken bile beynine kan gitmeye devam eder.",
]
V3_TAGS = {0: "[excited]", 2: "[surprised]"}
VOICE_NAMES = {"tr_alpha": "Alp", "tr_whisper": "Taner Torun", "tr_emrullah": "Emrullah Tuna", "tr_warm": "Özgür"}
NARRATION_USE_CASES = ("narrat", "social", "informative", "educational")
ENERGETIC_WORDS = ("energetic", "enerjik", "upbeat", "excited", "heyecan", "dynamic", "dinamik", "lively", "canlı",
                   "hype", "confident", "intense")
SETTING_VARIABLES = (("model", "ELEVENLABS_MODEL"), ("speed", "ELEVENLABS_SPEED"), ("style", "ELEVENLABS_STYLE"),
                     ("stability", "ELEVENLABS_STABILITY"))
ACCOUNT_ERRORS = ("quota_exceeded", "invalid_api_key")  # ElevenLabs' detail.status for these refusals
SAMPLE_SETS = ("karsilastirma", "maliyet")
# Gemini's prebuilt voices that fit an energetic narrator (Google lists Fenrir as excitable, Puck as upbeat, Charon as
# informative). Costs at 24 Shorts a day, ~480 characters and ~30 s of speech each (prices of 2026-10-15): Alp
# ~345k ElevenLabs credits a month; Gemini ~540k audio tokens a month at ~$9-10 (Flash) or ~$20 (Pro) a million.
GEMINI_VOICES = (("Fenrir", "heyecanlı"), ("Puck", "neşeli"), ("Charon", "bilgilendirici"))


class VoiceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"HTTP {code}: {message}" if code else message)
        self.code, self.message = code, message or ""


def slug(name: str) -> str:
    """An ASCII file-name part a Turkish owner can still read: "Özgür Can" -> "ozgur_can"."""
    folded = name.translate(str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")).lower()
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_")[:30] or "ses"


def production_settings() -> dict:
    """How the Shorts sound today, with no repo Variable set."""
    return {"model": eleven.DEFAULT_MODEL_ID, "stability": eleven.DEFAULT_STABILITY, "style": eleven.DEFAULT_STYLE,
            "speed": None, "tempo": factory.SERIES["merak"]["tempo"], "tags": {}}


def voice_sample(name: str, voice_id: str, **extra) -> dict:
    return {"slug": slug(name), "label": f"{name}, şimdiki ayarlar", "voice_name": name, "voice_id": voice_id,
            **production_settings(), **extra}


def sample_plan(voice_map: dict | None = None, default_voice_id: str | None = None) -> list[dict]:
    """The three Alp samples, then the other Turkish voices of the voice map, in the order they are tried."""
    voice_map = eleven.ELEVENLABS_VOICE_MAP if voice_map is None else voice_map
    default = default_voice_id or eleven.DEFAULT_TURKISH_VOICE_ID
    alp = {"voice_name": "Alp", "voice_id": default}
    plan = [
        {"slug": "alp_simdiki", "label": "Alp, Short'lardaki şimdiki ses (1,1x hızlandırılmış)", **alp,
         **production_settings()},
        {"slug": "alp_hizli_enerjik", "label": "Alp, kendi hızıyla 1,15 ve daha enerjik", **alp,
         **production_settings(), "speed": 1.15, "style": 0.6, "stability": 0.35, "tempo": 1.0},
        {"slug": "alp_v3_duygulu", "label": "Alp, eleven_v3 ve duygu etiketleri", **alp, **production_settings(),
         "model": "eleven_v3", "stability": 0.5, "tags": dict(V3_TAGS),  # v3 takes stability 0.0 / 0.5 / 1.0 only
         "note": "Duygu etiketleri şimdilik sadece bu örnekte var; Short senaryoları etiket yazmıyor."},
    ]
    for key, voice_id in voice_map.items():
        if key.startswith("tr_") and voice_id != default:
            plan.append(voice_sample(VOICE_NAMES.get(key, key), voice_id))
    return plan


def maliyet_plan(default_voice_id: str | None = None) -> list[dict]:
    """Today's Alp, then the cheaper narrators, each with its monthly cost at 24 Shorts a day."""
    alp = {"voice_name": "Alp", "voice_id": default_voice_id or eleven.DEFAULT_TURKISH_VOICE_ID,
           **production_settings(), "speed": 1.15, "style": 0.6, "stability": 0.35, "tempo": 1.0}
    other = {"tempo": 1.0, "tags": {}, "stability": None, "style": None, "speed": None,
             "note": "Üretimde kullanmak için küçük bir kod değişikliği gerekir."}
    plan = [
        {"slug": "alp_simdiki", "label": "Alp, Short'lardaki şimdiki ses", **alp,
         "cost": "~$99/ay (ElevenLabs Pro)"},
        {"slug": "alp_flash", "label": "Alp, eleven_flash_v2_5 (yarı kredi)", **alp, "model": "eleven_flash_v2_5",
         "cost": "yaklaşık yarısı (karakter başına 0,5 kredi)"},
    ]
    for name, mood in GEMINI_VOICES:
        plan.append({"slug": f"gemini_{slug(name)}", "label": f"Gemini {name} ({mood})", "engine": "gemini",
                     "tier": "flash", "voice_name": name, "voice_id": name, "model": None, **other,
                     "cost": "~$5/ay"})
    name = GEMINI_VOICES[0][0]
    plan.append({"slug": f"gemini_pro_{slug(name)}", "label": f"Gemini Pro {name} (daha kaliteli model)",
                 "engine": "gemini", "tier": "pro", "voice_name": name, "voice_id": name, "model": None, **other,
                 "cost": "~$11/ay"})
    # An all edge-tts Short keeps the series speed-up (original_factory.narration_tempo).
    plan.append({"slug": "edge_ahmet", "label": "Microsoft Ahmet (kredi bitince devreye giren yedek ses)",
                 "engine": "edge", "voice_name": "Ahmet", "voice_id": factory.VOICE["name"], "model": "edge-tts",
                 **other, "tempo": factory.SERIES["merak"]["tempo"], "cost": "ücretsiz",
                 "note": "Şu an ElevenLabs hata verince Short'lar bu sesle çıkıyor."})
    return plan


def rank_shared(voices: list, exclude: set | frozenset = frozenset()) -> list[dict]:
    """Shared-library voices for the free slots: narration or social-media voices first, energetic ones next, the
    most cloned first within each. Voices that cost more than one credit a character are left out."""
    def score(v: dict) -> tuple:
        use = str(v.get("use_case") or "").lower()
        text = " ".join(str(v.get(k) or "") for k in ("descriptive", "description", "name")).lower()
        try:
            clones = int(v.get("cloned_by_count") or 0)
        except (TypeError, ValueError):
            clones = 0
        return any(u in use for u in NARRATION_USE_CASES), any(w in text for w in ENERGETIC_WORDS), clones

    def cheap(v: dict) -> bool:
        try:
            return float(v.get("rate") or 1) <= 1
        except (TypeError, ValueError):
            return True

    fitting = [v for v in voices if isinstance(v, dict) and v.get("voice_id") and v["voice_id"] not in exclude
               and cheap(v)]
    return [voice_sample(str(v.get("name") or v["voice_id"]).strip(), v["voice_id"],
                         public_owner_id=v.get("public_owner_id"), source="paylaşılan kütüphane")
            for v in sorted(fitting, key=score, reverse=True)]


def repo_variables(sample: dict) -> dict:
    """The repo Variables that make the Shorts sound like this sample (empty: today's sound, or a voice outside
    ElevenLabs that needs a code change first)."""
    if sample.get("engine", "elevenlabs") != "elevenlabs":
        return {}
    base = production_settings()
    variables = {}
    if sample["voice_id"] != eleven.DEFAULT_TURKISH_VOICE_ID:
        variables["ELEVENLABS_VOICE_ID"] = sample["voice_id"]
    for field, name in SETTING_VARIABLES:
        if sample.get(field) is not None and sample.get(field) != base[field]:
            variables[name] = str(sample[field])
    return variables


def _api(path: str, key: str, payload: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def fetch_shared_voices(key: str) -> list:
    try:
        voices = _api(SHARED_VOICES_PATH, key).get("voices")
    except Exception as e:
        print(f"ℹ️ ElevenLabs'in paylaşılan Türkçe sesleri okunamadı: {str(e)[:200]}")
        return []
    return voices if isinstance(voices, list) else []


def add_shared_voice(key: str, sample: dict) -> str | None:
    """Adds a shared-library voice to the account (some voices speak only from there); its id in the account."""
    owner = sample.get("public_owner_id")
    if not owner:
        return None
    try:
        data = _api(f"/voices/add/{urllib.parse.quote(owner)}/{urllib.parse.quote(sample['voice_id'])}", key,
                    {"new_name": sample["voice_name"][:60]})
    except Exception as e:
        print(f"ℹ️ {sample['voice_name']} hesaba eklenemedi: {str(e)[:200]}")
        return None
    return str(data.get("voice_id") or sample["voice_id"])


def credits(key: str) -> tuple[int, int] | None:
    """(characters used, character limit) of the ElevenLabs plan this month."""
    try:
        data = _api("/user/subscription", key)
        return int(data.get("character_count")), int(data.get("character_limit"))
    except Exception:
        return None


def account_error(error: Exception) -> bool:
    """A refusal no other voice gets past (credits used up, a wrong key). The run stops there: otherwise it would go
    on through some thirty library voices, adding each one it has not seen to the account before being refused."""
    text = str(getattr(error, "message", "") or error).lower()
    return any(status in text for status in ACCOUNT_ERRORS)


def voice_not_found(error: Exception) -> bool:
    text = str(getattr(error, "message", "") or error).lower()
    return getattr(error, "code", None) == 404 or "voice_not_found" in text or "voice not found" in text


def make_sample(sample: dict, number: int, out_dir: Path) -> dict:
    """Voices, tightens and levels one sample; its index.json entry. Raises VoiceError when ElevenLabs refuses."""
    scenes = [{"text": f"{sample['tags'][i]} {line}" if i in sample["tags"] else line} for i, line in enumerate(LINES)]
    engine = sample.get("engine", "elevenlabs")

    def voice_line(text: str, out: Path, previous_text: str = "", next_text: str = "") -> Path:
        if engine == "gemini":
            ok = gemini_tts.synthesize(text, out, voice=sample["voice_name"], model=sample["model"],
                                       key=gemini_tts.get_api_key())
            error = gemini_tts.last_error
        elif engine == "edge":
            try:
                factory.synthesize_line(text, out, edge_only=True)
                ok, error = True, {}
            except RuntimeError as e:
                ok, error = False, {"message": str(e)}
        else:
            # The engine sends previous/next text only to models that read it (not eleven_v3).
            ok = eleven.synthesize_elevenlabs_speech(
                text, out, voice_id=sample["voice_id"], model_id=sample["model"], previous_text=previous_text,
                next_text=next_text, stability=sample["stability"], style=sample["style"], speed=sample["speed"])
            error = eleven.last_error
        if not ok or not out.exists() or out.stat().st_size < 1000:
            raise VoiceError(error.get("code"), error.get("message") or "ses dosyası dönmedi")
        return out

    with tempfile.TemporaryDirectory(prefix="voice_sample_") as tmp:
        voice, durations, _ = factory.build_narration(scenes, Path(tmp), synthesize=voice_line, tempo=sample["tempo"])
        chain, _ = factory.mix_levels(voice, None, sum(durations))
        dest = out_dir / f"{number}_{sample['slug']}.mp3"
        subprocess.run(["ffmpeg", "-y", "-i", str(voice), "-af", chain, "-ac", "1", "-ar", "44100",
                        "-c:a", "libmp3lame", "-b:a", "128k", str(dest)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    duration = factory._probe_duration(dest)
    words = sum(len(line.split()) for line in LINES)
    level = factory.loudness(dest)
    return {**({"note": sample["note"]} if sample.get("note") else {}),
        **({"cost_24_a_day": sample["cost"]} if sample.get("cost") else {}),
        "file": dest.name,
        "label": sample["label"],
        "engine": engine,
        "voice_name": sample["voice_name"],
        "voice_id": sample["voice_id"],
        "model": sample["model"],
        "settings": {"stability": sample["stability"], "style": sample["style"], "speed": sample["speed"],
                     "atempo": sample["tempo"], "tags": {str(i + 1): tag for i, tag in sample["tags"].items()}},
        "source": sample.get("source", "ses haritası"),
        "characters": sum(len(scene["text"]) for scene in scenes),
        "duration_seconds": round(duration, 2),
        "words_per_second": round(words / duration, 2) if duration else None,
        "lufs": round(level, 1) if level is not None else None,
        "repo_variables": repo_variables(sample),
    }


def describe(settings: dict) -> str:
    parts = [f"{name} {settings[field]}" for field, name in (("stability", "stabilite"), ("style", "stil"))
             if settings.get(field) is not None]
    if settings.get("speed"):
        parts.append(f"hız {settings['speed']}")
    if settings.get("atempo", 1.0) != 1.0:
        parts.append(f"ffmpeg {settings['atempo']}x")
    if settings.get("tags"):
        parts.append("etiket " + " ".join(settings["tags"].values()))
    return ", ".join(parts) or "-"


def try_sample(sample: dict, number: int, out_dir: Path, key: str) -> dict:
    """make_sample; a shared-library voice ElevenLabs does not know yet is added to the account and tried once more."""
    try:
        return make_sample(sample, number, out_dir)
    except VoiceError as e:
        if not (sample.get("public_owner_id") and voice_not_found(e)):
            raise
        print(f"       {sample['voice_name']} hesapta yok, ses kütüphanesinden ekleniyor...")
        added = add_shared_voice(key, sample)
        if not added:
            raise
        return make_sample({**sample, "voice_id": added}, number, out_dir)


def summary(made: list[dict], skipped: list[dict], out_dir: Path, spent: str) -> str:
    costs = any(m.get("cost_24_a_day") for m in made)
    rows = [f"| {m['file']} | {m['voice_name']} | {m['model']} | {describe(m['settings'])} | "
            f"{m['duration_seconds']:.1f} sn | {m['words_per_second']} |"
            + (f" {m.get('cost_24_a_day', '')} |" if costs else "") for m in made]
    lines = [f"\n### Ses örnekleri ({out_dir})\n",
             "| Dosya | Ses | Model | Ayarlar | Süre | Kelime/sn |" + (" Maliyet (günde 24) |" if costs else ""),
             "|---|---|---|---|---|---|" + ("---|" if costs else ""), *rows]
    if skipped:
        lines += ["", "Atlananlar:"] + [f"- {s['label']}: {s['reason']}" for s in skipped]
    lines += ["", f"Harcanan ElevenLabs kredisi: {spent}",
              "Beğendiğin örneğin ayarlarını index.json içindeki repo_variables alanında yazan repo Variables "
              "değerleriyle kaydet (boşsa bugünkü ses; ElevenLabs dışındaki sesler önce kod değişikliği ister)."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aynı senaryoyu birkaç ses ve ayarla seslendirip karşılaştırır")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Örneklerin yazılacağı klasör")
    parser.add_argument("--max", type=int, default=MAX_SAMPLES,
                        help=f"En fazla örnek sayısı (en çok {MAX_SAMPLES}; maliyet setinde hepsi)")
    parser.add_argument("--set", dest="sample_set", choices=SAMPLE_SETS, default=SAMPLE_SETS[0],
                        help="karsilastirma = Alp ayarları ve diğer ElevenLabs sesleri, maliyet = ucuz anlatıcılar")
    args = parser.parse_args(argv)
    cost_set = args.sample_set == "maliyet"
    limit = max(1, min(MAX_SAMPLES, args.max))

    key = eleven.get_elevenlabs_api_key()
    if not key and not cost_set:
        print("❌ ELEVENLABS_API_KEY yok; ses örnekleri üretilemedi.")
        return 1
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("❌ ffmpeg kurulu değil; ses örnekleri üretilemedi.")
        return 1
    # Each sample carries its own settings: the repo Variables must not turn sample 1 into something other than
    # today's sound (an empty variable counts as unset).
    for _, name in SETTING_VARIABLES:
        os.environ[name] = ""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    before = credits(key) if key else None
    plan, made, skipped, tried = sample_plan(), [], [], set()
    shared_fetched = False
    if cost_set:  # a fixed list of narrators, no library voices
        plan, shared_fetched = maliyet_plan(), True
        limit = len(plan)
        gemini_key, models = gemini_tts.get_api_key(), {}
        for sample in plan:
            if sample.get("engine") == "gemini" and gemini_key:
                if sample["tier"] not in models:
                    models[sample["tier"]] = gemini_tts.resolve_model(gemini_key, sample["tier"])
                    print(f"ℹ️ Gemini {sample['tier']} ses modeli: {models[sample['tier']] or 'bulunamadı'}")
                sample["model"] = models[sample["tier"]]
    while len(made) < limit:
        if not plan:
            if shared_fetched:
                break
            shared_fetched = True
            plan = rank_shared(fetch_shared_voices(key), exclude=tried)
            continue
        sample = plan.pop(0)
        tried.add(sample["voice_id"])
        engine = sample.get("engine", "elevenlabs")
        missing = ("ELEVENLABS_API_KEY yok" if engine == "elevenlabs" and not key
                   else "GEMINI_API_KEY yok" if engine == "gemini" and not gemini_tts.get_api_key()
                   else f"Gemini'de {sample.get('tier')} ses modeli bulunamadı "
                        f"({gemini_tts.last_error.get('message') or 'listede yok'})"
                   if engine == "gemini" and not sample.get("model") else None)
        if missing:
            print(f"ℹ️ {sample['label']} atlandı: {missing}")
            skipped.append({"label": sample["label"], "voice_id": sample["voice_id"], "reason": missing})
            continue
        print(f"🎙️ {len(made) + 1}. örnek: {sample['label']}", flush=True)
        try:
            made.append(try_sample(sample, len(made) + 1, out_dir, key))
        except Exception as e:  # one refused voice never stops the others
            reason = str(e)[:200]
            print(f"ℹ️ {sample['label']} atlandı: {reason}")
            skipped.append({"label": sample["label"], "voice_id": sample["voice_id"], "reason": reason})
            if engine == "elevenlabs" and account_error(e):
                print("❌ ElevenLabs kredisi bitmiş veya anahtar geçersiz; kalan ElevenLabs sesleri denenmedi.")
                if not cost_set:
                    break
                skipped += [{"label": s["label"], "voice_id": s["voice_id"], "reason": reason} for s in plan
                            if s.get("engine", "elevenlabs") == "elevenlabs"]
                plan = [s for s in plan if s.get("engine", "elevenlabs") != "elevenlabs"]

    after = credits(key) if key else None
    spent = (f"{after[0] - before[0]} karakter (bu ay kalan: {after[1] - after[0]})" if before and after
             else f"okunamadı (yaklaşık {sum(m['characters'] for m in made)} karakter)")
    print(summary(made, skipped, out_dir, spent))
    if not made:  # the last set and its index stay as they were
        print("❌ Hiç ses örneği üretilemedi (anahtarlar, kredi veya bağlantı kontrol edilmeli).")
        return 1
    index = {"created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "script": LINES, "samples": made,
             "skipped": skipped}
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    keep = {m["file"] for m in made}
    for old in out_dir.glob("*.mp3"):  # the new set replaces the last one, so no stale sample is compared
        if old.name not in keep:
            old.unlink()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # never a bare traceback for the owner
        print(f"❌ Ses örnekleri üretilirken beklenmeyen hata: {str(e)[:300]}")
        sys.exit(1)
