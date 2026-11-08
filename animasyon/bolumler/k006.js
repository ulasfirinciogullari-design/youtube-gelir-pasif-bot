// k006 "Çünkü" (Langer, Blank & Chanowitz 1978, J. Pers. Soc. Psychol. 36(6), 635-642). At the copier of a library
// in New York a researcher stopped people about to copy and asked to go first. 5 pages: just asking, 60 % said yes;
// "because I'm in a rush", 94 %; the empty "because I have to make copies", 93 %. 20 pages: 24 % / 42 % (real) /
// 24 % (empty). The why: on a small favour the brain hears the "because" and not the reason; on a big one it looks for
// a real reason. The hook is today's office copier: Zeynep asks Burak to let her go first, from the very first frame.
const K6 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  seventies() { setLight(-.6, -.7, { rim: '#ffe2b0', rimA: .4, warm: '#ffe6c0', cool: '#3a2a3a' }); },
  // today: Zeynep in a mustard dress with five sheets, Burak in a blue jacket with a tall stack, two more waiting
  zeynep: { ...CAST.evsahibi, coat: '#e0a030', trim: '#f4efe4', sleeve: 'long', hairColor: '#2a1a12', skin: '#ebbd98', shoe: '#5a2a1a', lips: '#b8404a',
    breeches: '#ebbd98', hose: '#ebbd98' },
  burak: { ...CAST.arastirmaci2, coat: '#35598a', trim: '#35598a', shirt: '#f4f1ea', tie: '#c8a040', hairColor: '#2a1a12', skin: '#e0aa84',
    breeches: '#2a3442', hose: '#2a3442' },
  Q: [{ ...CAST.uye, outfit: 'suit', coat: '#4a7a5a', trim: '#4a7a5a', shirt: '#f1ede2', tie: '#2a2a2a', breeches: '#3a3a44', hose: '#3a3a44',
    hair: 'side', hairColor: '#6a4a2a', skin: '#f0c4a2' },
  { ...CAST.evsahibi, coat: '#7a4a8a', trim: '#f4efe4', sleeve: 'long', hairColor: '#1e1410', skin: '#d9a07c' }],
  // 1978: the researcher in a burnt-orange dress, the man at the copier in a brown suit with a wide tie
  ars: { ...CAST.evsahibi, coat: '#c0602a', trim: '#f2e2c4', sleeve: 'long', hairColor: '#6a3a1e', skin: '#f0c4a2', shoe: '#4a2a1a' },
  denek: { ...CAST.arastirmaci2, coat: '#7a5232', trim: '#7a5232', shirt: '#f2e6c8', tie: '#d08a2a', hairColor: '#3a2416', skin: '#e8b48e',
    glasses: true, glassesColor: '#3a2a1a', breeches: '#5a3c24', hose: '#5a3c24' }
};
const K6INK = '#1a1117', K6GOLD = '#9a6400', K6RED = '#b81d28';
const K6AUTO = { ...EXPR.calm, lid: .58, curve: .12, look: [0, .15] };  // on autopilot: heavy lids, a vague smile

// ---------- small things ----------
// a stack of n sheets, bottom centre at (x, y), w wide; the sheets behind the front one step up and to the right,
// so five read thin and twenty read thick
function k6Stack(x, y, w, n, o = {}) {
  const h = w * 1.3, m = Math.min(n, 24), d = o.step ?? w * .03;
  ctx.save(); ctx.translate(x, y); if (o.rot) ctx.rotate(o.rot);
  for (let i = m - 1; i >= 1; i--) {
    const ox = i * d * .6, oy = -i * d;
    ctx.fillStyle = i % 2 ? '#e4ddcc' : '#f6f2e8'; ctx.fillRect(-w / 2 + ox, -h + oy, w, h);
    ctx.strokeStyle = 'rgba(40,30,30,.45)'; ctx.lineWidth = Math.max(1.2, w * .012); ctx.strokeRect(-w / 2 + ox, -h + oy, w, h);
  }
  shape(() => ctx.rect(-w / 2, -h, w, h), '#fbf8f0', { shade: w * .05, hi: w * .03, line: o.line ?? 4, noRim: true,
    after: () => {
      ctx.fillStyle = 'rgba(40,40,60,.38)';
      for (let i = 0; i < 7; i++) ctx.fillRect(-w * .36, -h + h * .14 + i * h * .11, w * (i % 3 === 2 ? .42 : .72), Math.max(2, h * .028));
    } });
  ctx.restore();
}
// a stack held in the front hand: drawn upright whatever the arm does, the fingers on top
const k6Hold = (n, sh, el, w = 120, dy = 0) => spec => {
  ctx.save(); ctx.rotate(sh + el); k6Stack(0, w * 1.3 * .3 + dy, w, n, { line: 5 }); ctx.restore();
  hand(spec, 'hold', 1.3 * Math.sqrt(spec.build.arm || 1));
};
// a small coin held between the fingers
const k6Coin = (sh, el) => () => {
  ctx.save(); ctx.rotate(sh + el); shape(() => ctx.arc(0, 52, 22, 0, Math.PI * 2), '#d8b04a', { shade: 5, hi: 5, line: 4.5, noRim: true }); ctx.restore();
};
// a speech bubble: rows [[text, colour, alpha]], size px, tail to (tx, ty); k 0..1 pops it in. The width is fixed by
// all rows, the height grows as rows fade in, so a row added mid-line does not make the bubble jump sideways
function k6Bubble(x, y, rows, size, k, tx, ty, o = {}) {
  if (k <= 0) return;
  const lh = size * 1.22, w = Math.max(...rows.map(r => measure(up(r[0]), size, SANS, 900))) + size * 1.3;
  const vis = rows.reduce((a, r) => a + clamp((r[2] ?? 1) * 1.6), 0), h = vis * lh + size * .85, r = Math.min(30, h / 2 - 2);
  if (o.bottom) y = o.bottom - h / 2;
  const s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.translate(-x, -y);
  shape(() => {
    ctx.moveTo(x - w / 2 + r, y - h / 2); ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, r); ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, r);
    const bx = clamp(tx, x - w / 2 + r + 14, x + w / 2 - r - 30);
    ctx.lineTo(bx + 22, y + h / 2); ctx.lineTo(tx, ty); ctx.lineTo(bx - 8, y + h / 2);
    ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, r); ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, r); ctx.closePath();
  }, o.fill || '#fbf6e8', { shade: 6, hi: 4, noRim: true, line: 6 });
  if (o.border) { ctx.strokeStyle = o.border; ctx.lineWidth = 6; rr(x - w / 2 + 8, y - h / 2 + 8, w - 16, h - 16, Math.max(4, r - 6)); ctx.stroke(); }
  let yy = y - h / 2 + size * .425 + lh / 2;
  rows.forEach(([str, col, a = 1]) => {
    const ka = clamp(a * 1.6);
    if (ka <= 0) return;
    ctx.save(); ctx.globalAlpha *= clamp(a); text(up(str), x, yy + size * .04, size, col || K6INK, SANS, 900); ctx.restore();
    yy += lh * ka;
  });
  ctx.restore();
}
// a red rubber stamp, k 0..1 drops it from large onto the frame
function k6Stamp(label, x, y, size, k, rot = -.1, col = C.red) {
  if (k <= 0) return;
  const s = lerp(2.2, 1, eo(clamp(k)));
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s); ctx.globalAlpha *= clamp(k * 3);
  const w = measure(label, size, SANS, 900, size * .08) + size * .9, h = size * 1.5;
  ctx.fillStyle = 'rgba(251,246,232,.94)'; rr(-w / 2, -h / 2, w, h, size * .16); ctx.fill();
  ctx.strokeStyle = col; ctx.lineWidth = size * .1; rr(-w / 2, -h / 2, w, h, size * .16); ctx.stroke();
  ctx.lineWidth = size * .03; rr(-w / 2 + size * .14, -h / 2 + size * .14, w - size * .28, h - size * .28, size * .1); ctx.stroke();
  text(label, 0, size * .05, size, col, SANS, 900, 'center', 0, C.ink, size * .08);
  ctx.restore();
}
// a gold check mark drawn in one stroke, k 0..1
function k6Check(x, y, size, k, col = C.gold) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = col; ctx.lineWidth = size * .2;
  ctx.shadowColor = rgba(col, .7); ctx.shadowBlur = 20;
  const p = eo(clamp(k)), a = [-.5, 0], b = [-.15, .35], c = [.55, -.45];
  ctx.beginPath(); ctx.moveTo(a[0] * size, a[1] * size);
  if (p < .4) ctx.lineTo(lerp(a[0], b[0], p / .4) * size, lerp(a[1], b[1], p / .4) * size);
  else { ctx.lineTo(b[0] * size, b[1] * size); ctx.lineTo(lerp(b[0], c[0], (p - .4) / .6) * size, lerp(b[1], c[1], (p - .4) / .6) * size); }
  ctx.stroke(); ctx.restore();
}
// "=" (two bars) or "≈" (two waves) between two numbers, drawn so no font glyph is needed
function k6Sign(x, y, kind, k, col = C.gold2) {
  if (k <= 0) return;
  const s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.lineCap = 'round'; ctx.strokeStyle = col; ctx.lineWidth = 11;
  ctx.shadowColor = rgba(col, .7); ctx.shadowBlur = 16;
  [-15, 15].forEach(dy => {
    ctx.beginPath();
    if (kind === '=') { ctx.moveTo(-30, dy); ctx.lineTo(30, dy); }
    else { ctx.moveTo(-32, dy); ctx.bezierCurveTo(-14, dy - 16, -2, dy - 16, 0, dy); ctx.bezierCurveTo(2, dy + 16, 14, dy + 16, 32, dy); }
    ctx.stroke();
  });
  ctx.restore();
}

// ---------- the copier ----------
// standing on the floor at y, left edge x; s 1 is 350 wide and 424 tall. o.retro: the 1970s beige machine with a coin
// box on its side; o.scan runs the light bar under the lid; sheets slide out into the tray on the left
function k6Copier(x, y, s, t, o = {}) {
  const retro = o.retro, body = retro ? '#e6d6b2' : '#e4e7eb', cab = retro ? '#7a5634' : '#c9ced6', dark = retro ? '#4a3626' : '#39404c';
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  ctx.save(); ctx.scale(1, .12); glow(170, 0, 260, 'rgba(0,0,0,1)', .5); ctx.restore();
  // the cabinet with two paper drawers
  shape(() => rrp(10, -270, 330, 262, 10), cab, { shade: 14, hi: 8, line: 6,
    after: () => [[-250], [-140]].forEach(([dy]) => {
      ctx.strokeStyle = 'rgba(0,0,0,.28)'; ctx.lineWidth = 4; ctx.strokeRect(30, dy, 290, 96);
      ctx.fillStyle = dark; rr(140, dy + 38, 70, 16, 8); ctx.fill();
    }) });
  ctx.fillStyle = dark; ctx.fillRect(28, -10, 22, 12); ctx.fillRect(300, -10, 22, 12);
  // the machine itself, the glass and the lid
  shape(() => rrp(-6, -404, 362, 140, 16), body, { shade: 16, hi: 9, line: 6,
    after: () => {
      ctx.fillStyle = retro ? 'rgba(106,74,44,.55)' : 'rgba(57,64,76,.35)'; ctx.fillRect(-6, -300, 362, 10);
      if (retro) { ctx.fillStyle = '#6a4a2c'; ctx.fillRect(-6, -340, 362, 6); }
      else text('FOTOKOPİ', 124, -340, 36, '#24406a', SANS, 900, 'center', 0, C.ink, 2);
    } });
  shape(() => rrp(4, -426, 282, 26, 8), dark, { shade: 6, hi: 5, line: 5 });
  if (o.scan > 0) {  // the light bar sweeping under the lid
    const lx = 16 + 256 * ((t * .8) % 1);
    glow(lx, -400, 90, 'rgba(170,255,225,1)', .65 * o.scan);
    ctx.fillStyle = `rgba(215,255,240,${.95 * o.scan})`; ctx.fillRect(lx - 7, -402, 14, 6);
  }
  // the control panel at the right end
  shape(() => rrp(250, -392, 92, 56, 8), retro ? '#d8c69e' : '#2a3038', { shade: 4, hi: 3, line: 4.5, noRim: true });
  if (retro) { [270, 300].forEach(cx => shape(() => ctx.arc(cx, -364, 11, 0, Math.PI * 2), '#3a2a1c', { shade: 3, hi: 3, line: 3, noRim: true })); }
  else { ctx.fillStyle = '#7fd0ff'; ctx.fillRect(260, -384, 44, 22); glow(282, -373, 40, 'rgba(127,208,255,1)', .5); }
  shape(() => ctx.arc(322, -364, 12, 0, Math.PI * 2), '#3fbf6a', { shade: 3, hi: 4, line: 3.5, noRim: true });
  // the output tray on the left with sheets coming out
  shape(() => { ctx.moveTo(-6, -316); ctx.lineTo(-96, -296); ctx.lineTo(-96, -282); ctx.lineTo(-6, -290); ctx.closePath(); }, dark, { shade: 4, hi: 3, line: 4.5 });
  const sheet = (k, a = 1) => { if (k <= 0) return; ctx.save(); ctx.globalAlpha *= a; ctx.translate(lerp(-6, -84, eo(k)), lerp(-322, -306, eo(k))); ctx.rotate(-.2);
    ctx.fillStyle = '#fbf8f0'; ctx.fillRect(-46, -6, 92, 8); ctx.strokeStyle = 'rgba(40,30,30,.5)'; ctx.lineWidth = 2; ctx.strokeRect(-46, -6, 92, 8); ctx.restore(); };
  for (let i = 0; i < 4; i++) sheet(1);
  if (o.scan > 0) sheet(((t * .8) % 1) * 1.6, o.scan);
  if (retro) {  // the coin box hung on the right side
    shape(() => rrp(352, -386, 64, 92, 8), '#8a8e96', { shade: 6, hi: 5, line: 5,
      after: () => { ctx.fillStyle = '#1a1a1e'; ctx.fillRect(374, -366, 20, 6); ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.fillRect(366, -330, 36, 22); } });
  }
  ctx.restore();
}

// ---------- today: the office copy room ----------
function k6Office(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1330); g.addColorStop(0, '#7d9387'); g.addColorStop(1, '#5a6f64');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1330);
  // a tall window with blinds on the right, bright afternoon behind them
  shape(() => rrp(600, 430, 450, 520, 6), '#e6e8ea', { shade: 6, hi: 4, line: 5 });
  g = ctx.createLinearGradient(0, 442, 0, 940); g.addColorStop(0, '#9fcdee'); g.addColorStop(1, '#eef4f2');
  ctx.fillStyle = g; ctx.fillRect(612, 442, 426, 496);
  ctx.fillStyle = 'rgba(240,242,236,.85)';
  for (let i = 0; i < 12; i++) ctx.fillRect(612, 452 + i * 41, 426, 16);
  ctx.strokeStyle = 'rgba(80,90,100,.5)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(700, 442); ctx.lineTo(700, 938); ctx.moveTo(950, 442); ctx.lineTo(950, 938); ctx.stroke();
  // a wall clock and the copier sign
  shape(() => ctx.arc(110, 560, 48, 0, Math.PI * 2), '#f6f2e8', { shade: 5, hi: 4, line: 6 });
  ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.lineCap = 'round'; ctx.beginPath();
  ctx.moveTo(110, 560); ctx.lineTo(110 + Math.sin(t * .05 + 2) * 25, 560 - Math.cos(t * .05 + 2) * 25);
  ctx.moveTo(110, 560); ctx.lineTo(110 + Math.sin(t * .8) * 36, 560 - Math.cos(t * .8) * 36); ctx.stroke();
  // the floor
  g = ctx.createLinearGradient(0, 1330, 0, H); g.addColorStop(0, '#4a4f58'); g.addColorStop(1, '#22252c');
  ctx.fillStyle = g; ctx.fillRect(0, 1330, W, H - 1330);
  shape(() => rrp(-10, 1318, W + 20, 16, 2), '#3a4048', { shade: 3, hi: 2, line: 4 });
  ctx.strokeStyle = 'rgba(255,255,255,.07)'; ctx.lineWidth = 3;
  for (let i = 0; i < 6; i++) { ctx.beginPath(); ctx.moveTo(0, 1380 + i * 70); ctx.lineTo(W, 1380 + i * 70); ctx.stroke(); }
}

// ---------- 1978: the library ----------
function k6Library(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1330); g.addColorStop(0, '#c99c4a'); g.addColorStop(1, '#a37632');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1330);
  bookshelf(560, 560, 240, 600, t, 3);
  bookshelf(820, 560, 250, 600, t, 11);
  // the wainscot and the carpet
  ctx.fillStyle = '#6a4426'; ctx.fillRect(0, 1140, W, 190);
  shape(() => rrp(-10, 1130, W + 20, 18, 3), '#4a2e1a', { shade: 3, hi: 3, line: 4 });
  g = ctx.createLinearGradient(0, 1330, 0, H); g.addColorStop(0, '#8a4a26'); g.addColorStop(1, '#3a1e10');
  ctx.fillStyle = g; ctx.fillRect(0, 1330, W, H - 1330);
  shape(() => rrp(-10, 1318, W + 20, 16, 2), '#4a2e1a', { shade: 3, hi: 2, line: 4 });
  // an orange lamp hanging over the copier
  ctx.strokeStyle = '#2a1a10'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(250, 0); ctx.lineTo(250, 620); ctx.stroke();
  glow(250, 700, 300, 'rgba(255,214,140,1)', .35);
  shape(() => { ctx.moveTo(190, 668); ctx.quadraticCurveTo(190, 612, 250, 610); ctx.quadraticCurveTo(310, 612, 310, 668); ctx.closePath(); }, '#d8662a', { shade: 8, hi: 6, line: 5 });
}
// the reading table the researcher watched from
function k6Table(x, y) {
  shape(() => rrp(x - 150, y, 300, 26, 6), '#8a5a32', { shade: 5, hi: 5, line: 5 });
  shape(() => rrp(x - 130, y + 24, 20, 200, 4), '#5a3a20', { shade: 4, hi: 2, line: 4 });
  shape(() => rrp(x + 110, y + 24, 20, 200, 4), '#5a3a20', { shade: 4, hi: 2, line: 4 });
  [['#6b1d22', 120, 26], ['#2a4a3a', 110, 22]].forEach(([c, w, h], i) => shape(() => rrp(x - 90 + i * 8, y - h - i * 22, w, h, 3), c, { shade: 4, hi: 3, line: 4, noRim: true }));
}

// ---------- the score board under the 1978 scenes ----------
// three columns: the share who said yes for each way of asking; vals [no reason, empty reason, real reason]
// (null: not asked yet). o.head the header, o.hot the column being counted, o.cols colours, o.shake a column
const K6B = { y: 1116, h: 276, xs: [205, 540, 875], labels: ['Sebepsiz', 'Saçma sebep', 'Gerçek sebep'] };
function k6Board(vals, o = {}) {
  const { y, h, xs, labels } = K6B, cols = o.cols || ['#cfd6e4', C.gold2, C.gold2];
  shape(() => rrp(36, y, 1008, h, 18), 'rgba(14,12,18,.92)', { flat: true, line: 5 });
  ctx.save(); ctx.strokeStyle = rgba(C.gold, .7); ctx.lineWidth = 3; rr(46, y + 10, 988, h - 20, 12); ctx.stroke(); ctx.restore();
  text(up(o.head), 540, y + 42, 32, o.headCol || C.gold, SANS, 900, 'center', 0, C.ink, 3);
  xs.forEach((cx, i) => {
    const v = vals[i], hot = o.hot === i, sx = o.shake === i ? Math.sin(o.t * 60) * 7 * (o.shakeK || 0) : 0;
    ctx.save(); ctx.translate(sx, 0);
    ctx.fillStyle = hot ? rgba(C.gold, .16) : 'rgba(255,255,255,.05)'; rr(cx - 156, y + 72, 312, h - 92, 12); ctx.fill();
    ctx.strokeStyle = hot ? C.gold : 'rgba(243,234,216,.22)'; ctx.lineWidth = hot ? 4 : 2; rr(cx - 156, y + 72, 312, h - 92, 12); ctx.stroke();
    text(up(labels[i]), cx, y + 104, 30, C.ivory, SANS, 900, 'center', 0, C.ink, 1);
    if (v == null) text('?', cx, y + 174, 80, 'rgba(243,234,216,.3)', SANS, 900);
    else {
      text('%' + Math.round(v), cx, y + 176, 88, cols[i], SANS, 900, 'center', 8);
      ctx.fillStyle = 'rgba(255,255,255,.1)'; rr(cx - 124, y + 230, 248, 18, 9); ctx.fill();
      ctx.fillStyle = cols[i]; rr(cx - 124, y + 230, Math.max(18, 248 * v / 100), 18, 9); ctx.fill();
    }
    ctx.restore();
  });
}
// a number counting toward its value: 0..1 eased
const k6Count = (from, to, k) => lerp(from, to, eo(clamp(k)));

// the 1978 two-shot over the board: the man at the copier on the left, the researcher on the right
const K6P = { den: [360, 1500, .7], ars: [790, 1512, .72] };
function k6TwoShot(t, o = {}) {
  const [dx, dy, ds] = K6P.den, [ax, ay, as] = K6P.ars;
  k6Library(t);
  k6Copier(-70, 1250, .85, t, { retro: true });
  k6Stack(70, 893, 110, 12);  // his own pile, more pages than hers, waits on the lid
  if (o.denGlow > 0) glow(dx + 30, 900, 260, o.denGlowCol || 'rgba(232,181,74,1)', .45 * o.denGlow);
  figure(K6.denek, dx, dy, ds, t, { dir: 1, legs: 'hide', seed: 2, expr: o.denExpr || 'calm', nod: o.nod || 0, headTilt: o.tilt || 0,
    talk: o.denTalk || 0, front: o.denFront || { sh: .15, el: .3 }, back: { sh: .1, el: .3 } });
  const n = o.pages || 5, sh = n > 10 ? .35 : .28, el = n > 10 ? 1.5 : 1.55;
  figure(K6.ars, ax, ay, as, t, { dir: -1, legs: 'hide', seed: 5, expr: o.arsExpr || 'warm', talk: o.arsTalk || 0,
    front: { sh, el, hand: 'hold', prop: k6Hold(n, sh, el, n > 10 ? 150 : 130, n > 10 ? 40 : 10) }, back: { sh: .1, el: .3 } });
}
// the researcher's request, above the two
function k6Ask(rows, k) { k6Bubble(560, 600, rows, 42, k, 730, 790); }
// the man's short answer to the left of his head
function k6Answer(word, good, k, x = 176, y = 760, tx = 300, ty = 834) {
  k6Bubble(x, y, [[word, good ? K6GOLD : K6RED]], 46, k, tx, ty, { fill: good ? '#fff2c8' : '#fbf6e8', border: good ? C.gold : C.red });
}

Object.assign(SAHNE, {
  kanca(V, L, t, lt) {
    // the hook, from the very first frame: at the office copier Zeynep, five sheets in hand, asks Burak (a tall stack)
    // to let her go first. On "sorma" he says no; on "çünkü" her bubble gains "çünkü acelem var" and he waves her in
    K6.day();
    const tn = wordTime(L, V.no, .4), ty = wordTime(L, V.yes, .8);
    const no = prog(t, tn - .1, tn + .2), yes = prog(t, ty, ty + .3), add = prog(t, ty - .15, ty + .15);
    const zx = 790 - 40 * eio(prog(t, ty + .2, ty + .9));
    cam(540, 960, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => {
      k6Office(t);
      k6Copier(40, 1430, 1, t, { scan: 1 });
      k6Stack(150, 1004, 110, 14);
      // two more people waiting behind her, papers in hand
      figure(K6.Q[1], 1090, 1400, .54, t, { dir: -1, legs: 'stand', seed: 9, expr: 'displeased', front: { sh: .25, el: 1.5, hand: 'hold', prop: k6Hold(8, .25, 1.5, 120) }, back: { sh: .1, el: .3 } });
      figure(K6.Q[0], 960, 1410, .55, t, { dir: -1, legs: 'stand', seed: 7, expr: { ...EXPR.displeased, look: [.3, -.2] },
        front: { sh: .4 + Math.sin(t * 2) * .03, el: 2.3, hand: 'open' }, back: { sh: .1, el: .3 } });
      if (yes > 0) glow(zx, 1100, 300, 'rgba(232,181,74,1)', .35 * yes);
      figure(K6.burak, 470, 1450, .62, t, { dir: 1, legs: 'stand', seed: 4,
        expr: yes > .4 ? 'warm' : no > .4 ? 'displeased' : { ...EXPR.neutral, look: [.5, 0] }, nod: yes > 0 ? Math.sin(t * 9) * .04 * yes : 0, headTilt: no > 0 && yes <= 0 ? Math.sin(t * 14) * .03 * no * (1 - prog(t, tn + .4, tn + .8)) : 0,
        front: yes > 0 ? { sh: lerp(.5, .8, eo(yes)), el: lerp(2.0, -.25, eo(yes)), hand: 'palm' } : { sh: lerp(.2, .5, eo(no)), el: lerp(.4, 2.0, eo(no)), hand: no > .3 ? 'palm' : 'open' },
        back: { sh: .1, el: .3 } });
      figure(K6.zeynep, zx, 1450, .62, t, { dir: -1, legs: zx < 789 && zx > 751 ? 'walk' : 'stand', phase: (790 - zx) * .3, seed: 3,
        expr: yes > .4 ? 'happy' : no > .4 ? { ...EXPR.surprised, curve: -.2 } : 'warm', talk: t < tn - .1 || (t > ty - .2 && t < ty + .6) ? talk(t, L) * .7 + .2 : 0,
        front: { sh: .28, el: 1.55, hand: 'hold', prop: k6Hold(5, .28, 1.55, 120, 10) }, back: { sh: .1, el: .3 } });
    });
    k6Bubble(780, 0, [['Önce ben', K6INK], ['çekebilir miyim?', K6INK], ['Çünkü acelem var', K6GOLD, add]], 42, 1, 780, 850, { bottom: 768 });
    k6Answer('Hayır.', false, no * (1 - yes), 200, 705, 380, 770);
    k6Answer('Buyur!', true, yes, 200, 705, 380, 770);
    plate(V.no_label, 540, 520, 60, back(prog(t, tn - .05, tn + .3)), C.red);
    if (yes > 0) glow(540, 1340, 320, 'rgba(232,181,74,1)', .3 * yes);
    plate(V.yes_label, 540, 1340, 56, back(prog(t, ty - .1, ty + .25)), C.gold);
  },
  deney(V, L, t, lt) {
    // 1978, a library in New York: a man at the coin-op copier, coin in hand. The researcher gets up from her table,
    // walks over and on "durdurdu" raises a hand: "Pardon!"; he turns round, surprised
    K6.seventies();
    const tw = wordTime(L, V.who, .2), tk = wordTime(L, V.key, .85);
    const walk = prog(t, tw, tk - .15), stop = prog(t, tk - .1, tk + .25);
    const ax = lerp(1010, 820, eio(walk));
    cam(540, 980, lerp(1.0, 1.04, eio(prog(lt, 0, 5))), () => {
      k6Library(t);
      k6Table(930, 1250);
      k6Copier(40, 1430, .95, t, { retro: true, scan: stop > .5 ? 0 : 0 });
      k6Stack(160, 1028, 110, 12);
      const turned = stop > .4;
      figure(K6.denek, 500, 1460, .62, t, { dir: turned ? 1 : -1, legs: 'stand', seed: 2, expr: turned ? 'surprised' : { ...EXPR.calm, look: [.4, .2] },
        front: turned ? { sh: .5, el: .9, hand: 'hold', prop: k6Coin(.5, .9) } : { sh: 1.15, el: .55, hand: 'hold', prop: k6Coin(1.15, .55) }, back: { sh: .1, el: .3 } });
      if (walk > 0 && walk < 1) glow(ax, 1100, 260, 'rgba(255,230,180,1)', .25);
      figure(K6.ars, ax, 1470, .62, t, { dir: -1, legs: walk > 0 && walk < 1 ? 'walk' : 'stand', phase: walk * 14, seed: 5,
        expr: stop > .3 ? 'warm' : { ...EXPR.calm, look: [.4, 0] }, talk: stop > 0 && stop < 1 ? .6 : 0,
        front: { sh: lerp(.25, .9, eo(stop)), el: lerp(1.5, 1.5, stop), hand: stop > .4 ? 'palm' : 'hold', prop: stop > .4 ? null : k6Hold(5, .25, 1.5, 110, 10) },
        back: { sh: .1, el: .3 } });
    });
    k6Bubble(860, 740, [['Pardon!', K6INK]], 50, prog(t, tk - .05, tk + .25), 830, 850);
    plate(V.year, 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)));
  },
  rica(V, L, t, lt) {
    // one of the three ways of asking, five pages in her hand. The bubble says it (a "çünkü" row lands on the word),
    // he says yes on the number and the board counts that column up. sade: no reason (60), gercek: "çünkü acelem
    // var" (94), bos: "çünkü fotokopi çekmem lazım" (93), stamped "saçma" and put next to 94 with a ≈
    K6.seventies();
    const kind = V.kind, tk = wordTime(L, V.key, .7), tc = wordTime(L, 'çünkü', .2);
    const ask = prog(lt, 0, .3), add = kind === 'sade' ? 1 : prog(t, tc - .1, tc + .2);
    const yes = prog(t, tk - .1, tk + .25), count = prog(t, tk - .15, tk + .45);
    const ts = kind === 'bos' ? wordTime(L, V.silly, .55) : 99, silly = prog(t, ts - .05, ts + .2);
    const rows = kind === 'sade' ? [['5 sayfam var,', K6INK], ['önce ben çekebilir miyim?', K6INK]]
      : [['Önce ben çekebilir miyim,', K6INK], [kind === 'gercek' ? 'çünkü acelem var?' : 'çünkü fotokopi çekmem lazım?', kind === 'gercek' ? K6GOLD : K6RED, add]];
    cam(540, 1000, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => k6TwoShot(t, {
      denExpr: yes > .4 ? 'warm' : silly > .3 ? 'confused' : { ...EXPR.calm, look: [.4, 0] }, nod: yes > 0 && yes < 1 ? Math.sin(t * 10) * .05 : 0,
      denFront: yes > 0 ? { sh: lerp(.15, .9, eo(yes)), el: lerp(.3, -.2, yes), hand: 'palm' } : null, denGlow: yes,
      arsTalk: t < tk - .3 ? talk(t, L) * .6 : 0, arsExpr: yes > .5 ? 'happy' : 'warm' }));
    k6Ask(rows, ask);
    if (kind === 'bos') k6Stamp('SAÇMA SEBEP', 790, 508, 40, silly, .05);
    k6Answer('Tabii!', true, yes);
    // the board: earlier columns stand, this one counts up
    const col = { sade: 0, bos: 1, gercek: 2 }[kind], val = { sade: 60, gercek: 94, bos: 93 }[kind];
    const done = { sade: [], gercek: [0], bos: [0, 2] }[kind], fin = [60, 93, 94];
    const vals = [0, 1, 2].map(i => i === col ? (count > 0 ? k6Count(0, val, count) : null) : done.includes(i) ? fin[i] : null);
    k6Board(vals, { head: '5 sayfa · evet diyenler', hot: col, t });
    if (kind === 'bos') k6Sign(707, K6B.y + 176, '≈', prog(t, tk + .45, tk + .75));
    flash(t, tk - .05, .25);
  },
  buyuk(V, L, t, lt) {
    // 20 pages: the same empty reason, now on a thick stack. He frowns and says no; the board switches to 20 pages
    // and on "çöktü" the empty-reason column crashes from 93 to 24, the others fall to 24 and 42; "=" on "sebepsiz"
    K6.seventies();
    const tb = wordTime(L, V.big, .2), td = wordTime(L, V.drop, .5), tk = wordTime(L, V.key, .65), ta = wordTime(L, V.same, .8);
    const no = prog(t, td - .2, td + .15), fall = prog(t, td - .05, tk + .2), head = prog(t, tb - .1, tb + .2);
    cam(540, 1000, lerp(1.03, 1.0, eio(prog(lt, 0, 4))), () => k6TwoShot(t, {
      pages: 20, denExpr: no > .4 ? 'displeased' : { ...EXPR.surprised, look: [.5, .3] }, denGlow: no, denGlowCol: 'rgba(210,35,47,1)',
      denFront: no > 0 ? { sh: lerp(.15, .5, eo(no)), el: lerp(.3, 2.0, eo(no)), hand: 'palm' } : null, tilt: no * -.04 + Math.sin(t * 14) * .03 * no * (1 - prog(t, td + .4, td + .8)),
      arsTalk: t < td - .2 ? talk(t, L) * .6 : 0, arsExpr: no > .5 ? { ...EXPR.surprised, curve: -.2 } : 'warm' }));
    // the stack's tag
    plate('20 sayfa', 575, 1084, 30, back(head), C.red);
    k6Ask([['20 sayfam var,', K6INK], ['çünkü fotokopi çekmem lazım.', K6RED]], prog(lt, 0, .3));
    k6Answer('Hayır.', false, no);
    const vals = [k6Count(60, 24, fall), k6Count(93, 24, fall), k6Count(94, 42, fall)];
    k6Board(vals, { head: head > .5 ? '20 sayfa · evet diyenler' : '5 sayfa · evet diyenler', headCol: head > .5 ? C.red2 : C.gold, hot: 1, t,
      cols: ['#cfd6e4', mixColor(C.gold2, C.red2, fall), C.gold2], shake: 1, shakeK: fall > 0 && fall < 1 ? 1 : 0 });
    k6Sign(372, K6B.y + 176, '=', prog(t, ta - .1, ta + .2), C.red2);
    flash(t, td - .05, .3);
  },
  neden(V, L, t, lt) {
    // the why, in close-up on the man from the copier. Small favour: he is on autopilot; the grey reason drifts in and
    // bounces off his head on "dinlemez", only a gold "ÇÜNKÜ" goes in, and he says "Tamam!" at once. On "Büyükte" a
    // cut: twenty pages on the table, he is wide awake, a magnifier over the pile, thinking "Gerçek sebep?"
    K6.seventies();
    const tk = wordTime(L, V.key, .35), te = wordTime(L, V.enough, .5), tb = wordTime(L, V.big, .6), tr = wordTime(L, V.real, .8);
    ctx.fillStyle = '#1a1410'; ctx.fillRect(0, 0, W, H);
    drawSoft(.4, () => cam(540, 900, 1.5, () => k6Library(t)), .55);
    if (t < tb - .08) {
      const ok = prog(t, te + .05, te + .35);
      cam(540, 960, lerp(1.0, 1.03, eio(prog(lt, 0, 3))), () => {
        glow(560, 960, 460, 'rgba(255,214,140,1)', .22 + .2 * ok);
        figure(K6.denek, 600, 2060, 1.2, t, { dir: -1, legs: 'hide', seed: 2, expr: ok > .5 ? { ...K6AUTO, curve: .6 } : K6AUTO,
          nod: ok > 0 ? Math.sin(t * 10) * .04 : 0, front: { sh: .2, el: .4 }, back: { sh: .1, el: .2 } });
      });
      // the reason drifts in from the right, hits the side of his head on "dinlemez" and falls away
      const come = prog(t, L.t0 + .15, tk), off = prog(t, tk, tk + .7);
      if (come > 0 && off < 1) {
        const x = off > 0 ? 820 + 260 * off : lerp(1300, 820, eo(come)), y = off > 0 ? 880 - 160 * off + 620 * off * off : 880;
        ctx.save(); ctx.globalAlpha *= 1 - off; ctx.translate(x, y); ctx.rotate(off * .9);
        k6Bubble(0, 0, [['fotokopi', '#5a5460'], ['çekmem lazım', '#5a5460']], 36, 1, 110, 110, { fill: '#e6e2da' });
        ctx.restore();
        if (off > 0 && off < .35) { const k = off / .35; ctx.save(); ctx.globalAlpha *= 1 - k; ctx.strokeStyle = C.ivory; ctx.lineWidth = 6; ctx.lineCap = 'round';
          [-.6, 0, .6].forEach(a => { ctx.beginPath(); ctx.moveTo(660 + Math.cos(a) * (30 + 40 * k), 880 + Math.sin(a) * (30 + 40 * k)); ctx.lineTo(660 + Math.cos(a) * (60 + 60 * k), 880 + Math.sin(a) * (60 + 60 * k)); ctx.stroke(); });
          ctx.restore(); }
      }
      // only the "çünkü" goes in: it pops up big, then flies into his ear
      const pop = prog(t, te - .75, te - .5), fly = prog(t, te - .35, te + .05);
      if (pop > 0 && fly < 1) {
        const x = lerp(830, 650, eio(fly)), y = lerp(610, 925, eio(fly)), s = back(pop) * lerp(1, .25, ei(fly));
        ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
        glow(0, 0, 160, 'rgba(232,181,74,1)', .55); text('ÇÜNKÜ', 0, 0, 84, C.gold2, SANS, 900, 'center', 12);
        ctx.restore();
      }
      if (fly >= 1) glow(650, 925, 200, 'rgba(255,217,138,1)', .6 * (1 - prog(t, te + .05, te + .6)));
      k6Bubble(260, 680, [['Tamam!', K6GOLD]], 56, ok, 420, 770, { fill: '#fff2c8', border: C.gold });
      plate('Küçük ricada “çünkü” yeter', 540, 1330, 50, back(prog(t, tk + .1, tk + .4)), C.gold);
      return;
    }
    const look = eo(prog(t, tb, tb + .35)), ask = prog(t, tr - .35, tr + .05), sway = Math.sin(t * 2.2) * .05;
    cam(540, 960, lerp(1.04, 1.0, eio(prog(t, tb, tb + 3))), () => {
      glow(600, 980, 480, 'rgba(255,214,140,1)', .2);
      // the twenty pages on a table
      shape(() => rrp(640, 1180, 480, 28, 6), '#8a5a32', { shade: 5, hi: 5, line: 5 });
      const g = ctx.createLinearGradient(0, 1206, 0, 1500); g.addColorStop(0, '#5a3a20'); g.addColorStop(1, '#1e120a');
      ctx.fillStyle = g; ctx.fillRect(650, 1206, 470, 300);
      k6Stack(870, 1182, 170, 20);
      const sh = lerp(.2, .5, look), el = lerp(.3, 2.2, look);
      figure(K6.denek, 330, 2060, 1.2, t, { dir: 1, legs: 'hide', seed: 2, expr: { ...EXPR.displeased, lid: 0, squint: .35, look: [.7, .2] },
        front: { sh: sh + sway * look, el, hand: 'hold', prop: () => {  // a magnifier held up, the lens above the hand
          ctx.save(); ctx.rotate(sh + sway * look + el);
          ctx.fillStyle = '#4a2e1a'; ctx.strokeStyle = INK; ctx.lineWidth = 5; rr(-13, -84, 26, 90, 10); ctx.fill(); ctx.stroke();
          ctx.fillStyle = 'rgba(200,230,255,.28)'; ctx.beginPath(); ctx.arc(0, -150, 70, 0, Math.PI * 2); ctx.fill();
          ctx.strokeStyle = '#2a2a30'; ctx.lineWidth = 14; ctx.stroke(); ctx.strokeStyle = 'rgba(255,255,255,.6)'; ctx.lineWidth = 5;
          ctx.beginPath(); ctx.arc(0, -150, 48, -2.6, -1.8); ctx.stroke();
          ctx.restore();
          hand(K6.denek, 'hold', 1.3);
        } }, back: { sh: .1, el: .3 } });
    });
    plate('20 sayfa', 870, 1250, 32, back(prog(t, tb + .1, tb + .4)), C.red);
    thought(330, 610, 430, 200, 380, 745, ask);
    if (ask > .4) { text('GERÇEK', 330, 580, 52, K6GOLD, SANS, 900); text('SEBEP?', 330, 640, 52, K6INK, SANS, 900); }
    plate('Büyükte gerçek sebep arar', 540, 1340, 50, back(prog(t, tb + .05, tb + .35)), C.gold);
    flash(t, tb - .08, .45);
  }
});
