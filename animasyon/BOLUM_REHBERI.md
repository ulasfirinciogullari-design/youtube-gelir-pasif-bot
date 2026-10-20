# Writing an animated "Kanun" episode

Each episode is two files in `bolumler/`, written together:

- `<id>.json`: the script and the YouTube metadata.
- `<id>.js`: the episode's own scenes, `Object.assign(SAHNE, { name(V, L, t, lt) { ... } })`.

`uret.py` copies `<id>.js` to `motor/sahne.js` and checks that every line's scene exists.
Local stills: `python3 animasyon/uret.py --bolum <id> --js`, then from `motor/` run
`node render.js --stills 3,10,20 <outdir>` or `node render.js --story <outdir>`
(with `NODE_PATH=/opt/node22/lib/node_modules` in the cloud container).

## The script (`<id>.json`)

- `id`, `kanun` (roman numeral), `title.top` / `title.hi` (the bar on top), `yt_title`.
- `lines`: 5-9 lines, 40-100 words in total (about 35 s), each `{text, v: {t: <scene>, ...}}`.
  - The first line is `usta`. Usta wags a finger at `no` and offers a palm at `yes`. `no_label` and `yes_label`
    are the two plates. `no_prop` and `yes_prop` name an optional prop function (for example `giftBox`,
    `bigSignProp`, `stickerProp`).
  - The last line is `{"t": "usta", "end": true}`, "Bu yüzden unutma:", which loops back into the first.
  - Any other keys in `v` are the scene's own (for example a word to time a cut on).
  - `sfx`: `[[word or null, share, effect, gain, offset], ...]` adds sound cues. The effects are impact, whoosh,
    riser, pop, stamp, paper, shimmer, knock and tick.
- `facts` (checked, with numbers), `sources` (URLs), `summary`, `tags`, `hashtags`.
- `tablolar`: real public-domain paintings, used only where a real person or place is named. Leave it out
  when no painting fits.

## Story rule (Ulaş, 2026-10-19)

The story must make sense to someone hearing it once. Show the setup ("most people would..."), the act, the
result and why it worked. Each episode covers a different power or psychology rule, mostly from everyday life,
and uses history only with well-known names.

## Drawing kit

- `karakter.js`: `figure(who, x, y, s, t, pose)` draws a character with its feet at (x, y), drawn facing right
  (dir -1 mirrors it).
  - 18th-century cast: franklin, rakip, usta, uye.
  - 1960s cast: arastirmaci, arastirmaci2, evsahibi.
  - `outfit: 'suit' | 'dress'` builds new people.
- `mekan.js`: candlelit 18th-century rooms and props.
- `sokak.js`: a 1960s street at golden hour, the house, signs, small houses for results.
- Never use `ctx.filter`: it takes seconds per frame without a GPU. Use `drawSoft()`, `sepiaTone()` and
  `toned()` from `ortak.js` instead.
- Keep plates and numbers above the subtitles (y < 1440) and below the title bar (y > 450).
