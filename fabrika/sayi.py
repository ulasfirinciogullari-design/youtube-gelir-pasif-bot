"""Turkish numbers for the narrator: "3,8 santim" -> "üç virgül sekiz santim", "%40'ı" -> "yüzde kırkı".

The display text keeps digits (subtitles and labels show "3,8"); the voice reads words. spoken() returns
the text to send to the voice and, for every display word, how many spoken words it became, so word
timings from the voice can be mapped back onto the display words.
"""
import re

ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
BIG = [(10 ** 12, "trilyon"), (10 ** 9, "milyar"), (10 ** 6, "milyon"), (1000, "bin")]
UNITS = {"km": "kilometre", "cm": "santim", "mm": "milimetre", "m": "metre", "kg": "kilogram", "g": "gram",
         "sn": "saniye", "dk": "dakika", "°C": "derece", "°": "derece", "km/s": "kilometre bölü saniye",
         "km/sa": "kilometre bölü saat", "ml": "mililitre", "lt": "litre", "l": "litre", "t": "ton"}


def under_thousand(n):
    h, r = divmod(n, 100)
    out = []
    if h:
        out += ([ONES[h]] if h > 1 else []) + ["yüz"]
    t, o = divmod(r, 10)
    if t:
        out.append(TENS[t])
    if o:
        out.append(ONES[o])
    return out


def words(n):
    """Integer -> list of Turkish words (bin, not bir bin; yüz, not bir yüz)."""
    if n == 0:
        return ["sıfır"]
    out = []
    for size, name in BIG:
        q, n = divmod(n, size)
        if q:
            out += ([] if q == 1 and size == 1000 else under_thousand(q) if q < 1000 else words(q)) + [name]
    return out + under_thousand(n)


NUM = re.compile(r"^(-|−)?(%)?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?([°]C?|[a-zA-Z/]+)?(?:['’](\w+))?([.,!?;:]*)$")


def locative(word):
    """'üç' -> 'üçte', 'on' -> 'onda' (vowel harmony and consonant hardening)."""
    vowels = [c for c in word if c in "aeıioöuü"]
    back = vowels and vowels[-1] in "aıou"
    hard = word[-1] in "fstkçşhp"
    return word + ("t" if hard else "d") + ("a" if back else "e")


FRACTION = re.compile(r"^(\d+)/(\d+)(['’]\w+)?([.,!?;:]*)$")


def say_word(w):
    """One display word -> its spoken form (unchanged when it has no number)."""
    f = FRACTION.match(w)
    if f:  # 1/3 -> üçte bir
        num, den, suffix, punct = f.groups()
        out = [locative(" ".join(words(int(den))))] + words(int(num))
        out[-1] += (suffix or "").lstrip("'’") + (punct or "")
        return " ".join(out)
    m = NUM.match(w)
    if not m:
        if re.search(r"\d", w):  # numbers glued to other text: read each digit run
            return re.sub(r"\d+", lambda d: " ".join(words(int(d.group()))), w.replace("%", "yüzde "))
        return w
    minus, pct, whole, dec, unit, suffix, punct = m.groups()
    out = (["eksi"] if minus else []) + (["yüzde"] if pct else []) + words(int(whole.replace(".", "")))
    if dec:
        out += ["virgül"] + (words(int(dec)) if not dec.startswith("0") else [ONES[int(c)] or "sıfır" for c in dec])
    if unit:
        out += UNITS.get(unit, unit).split(" ") if unit in UNITS else [unit]
    if suffix:
        out[-1] += suffix
    out[-1] += punct or ""
    return " ".join(out)


def spoken(text):
    """-> (text for the voice, [(display word, number of spoken words)])"""
    groups, said, prev_num = [], [], False
    for w in text.split():
        bare = w.rstrip(".,!?;:")
        if prev_num and bare in UNITS:  # "384.400 km" -> kilometre
            s = UNITS[bare] + w[len(bare):]
        else:
            s = say_word(w)
        prev_num = s != w and bool(re.search(r"\d", w))
        said.append(s)
        groups.append((w, len(s.split())))
    return " ".join(said), groups


if __name__ == "__main__":
    for t in ["Ay her yıl bizden 3,8 santim uzaklaşıyor.", "Işık 384.400 km yolu 1,3 saniyede alır.",
              "Vücudunun %60'ı su.", "1990'da 8 milyar insan yoktu, 2025'te 8,2 milyar.", "Sıcaklık -50°C olurdu.",
              "Kalbin günde 100.000 kez atar.", "0,05 saniye", "23,4° eğik", "1/3 oranında", "11 bin 2 yüz"]:
        print(t, "->", spoken(t)[0])
