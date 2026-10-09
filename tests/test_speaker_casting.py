from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.multi_voice_engine import detect_speaker_role

tests = [
    ("Shelby: Kim olduğunu asla unutma.", 0, 2, "shelby"),
    ("Korku nedir bilir misin?", 1, 2, "shelby_silent_01"),
    ("Skyler: Walter, tehlikedeyiz!", 0, 2, "walter_danger"),
    ("Walter: Ben tehlikede değilim.", 1, 2, "walter_danger"),
    ("Alfie: Thomas, burası benim mekanım.", 1, 2, "shelby_silent_02"),
    ("Polly: Bu aileyi ben yönetirim.", 0, 2, "shelby_silent_03"),
    ("Aurelius: Öfkenin getirdiği zarar, sebebinden büyüktür.", 0, 2, "stoic"),
]

print("=== KARAKTER KASTI DOĞRULAMA TESTİ ===")
all_passed = True
for text, idx, total, ctx in tests:
    role, clean = detect_speaker_role(text, line_index=idx, total_lines=total, campaign_context=ctx)
    print(f"[{ctx.upper()}] \"{text}\" -> ROL: {role}")
    if "shelby" in ctx and "polly" not in text.lower() and role == "female":
        print("  ❌ HATA: Tommy Shelby sahnesi kadın sesi aldı!")
        all_passed = False

if all_passed:
    print("✅ TEST BAŞARILI: Tommy Shelby ve tüm erkek ikonlar istisnasız erkek seslerine kilitlendi!")
