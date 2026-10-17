"""Reply state in fabrika/cevap_durum.json: answered YouTube comments, answered Instagram comments and DMs, viewer
topic ideas, and the reply watcher's heartbeat.

The reply watcher (fabrika/nobet.py) writes it every few minutes while the factory writes fabrika/durum.json
(episodes, used topics), so the two never edit the same file. Reply code works on one combined state:
durum.json, read only, plus the reply parts; save() writes the reply parts alone. The factory picks up viewer
ideas through merge_extra() and keeps them in durum.json once it has loaded them.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DURUM = HERE / "durum.json"
CEVAP = HERE / "cevap_durum.json"


def _read(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def own():
    """The reply file; until it exists, what durum.json held before the split (2026-10-17)."""
    c = _read(CEVAP)
    if c:
        return c
    d = _read(DURUM)
    return {"yorumlar": d.get("yorumlar") or {"done": {}, "son": []},
            "ig": d.get("instagram", {}).get("cevaplar") or {"done": {}, "son": []},
            "oneriler": []}


def merge_extra(extra, oneriler):
    ids = {t["id"] for t in extra}
    return list(extra) + [t for t in oneriler if t["id"] not in ids]


def load():
    d = _read(DURUM) or {"episodes": [], "used": [], "extra": []}
    c = own()
    state = dict(d)
    state["yorumlar"] = c.get("yorumlar") or {"done": {}, "son": []}
    state["instagram"] = dict(d.get("instagram") or {})
    state["instagram"]["cevaplar"] = c.get("ig") or {"done": {}, "son": []}
    state["extra"] = merge_extra(d.get("extra", []), c.get("oneriler", []))
    return state


def dump(state, **more):
    """-> the text of cevap_durum.json for this state; `more` sets other keys (the watcher's heartbeat)."""
    c = own()
    in_durum = {t["id"] for t in _read(DURUM).get("extra", [])}  # ideas the factory has taken over
    c.update({"yorumlar": state.get("yorumlar") or {"done": {}, "son": []},
              "ig": (state.get("instagram") or {}).get("cevaplar") or {"done": {}, "son": []},
              "oneriler": [t for t in state.get("extra", []) if t.get("oneri") and t["id"] not in in_durum]})
    c.update(more)
    return json.dumps(c, ensure_ascii=False, indent=1)


def save(state, **more):
    CEVAP.write_text(dump(state, **more), encoding="utf-8")
