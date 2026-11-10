// k007 "Ona özel" (Strohmetz, Rind, Fisher & Lynn 2002, J. Appl. Soc. Psychol. 32(2), 300-309, study 2). In an
// Italian-American restaurant in New Jersey one waitress brought the bill to 80 tables in four ways, chosen by shuffled
// cards: no chocolate (mean tip 18.95 %), one chocolate each from a small basket (19.59 %, +3 %), two each (21.62 %,
// +14 %), and "1+1": one each, then as she was leaving she stopped and offered one more (22.99 %, +21 %). The same two
// chocolates, but the second one felt like a gesture just for them, and a special favour gets paid back (reciprocity).
// The hook is today's café: a waiter brings the bill, walks off, then turns back with one more "just for you".
const K7 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  warm() { setLight(-.6, -.7, { rim: '#ffd9a0', rimA: .45, warm: '#ffe2b8', cool: '#3a1e2a' }); },
  // today: the waiter in black, a couple in teal and navy
  garson: { ...CAST.arastirmaci2, coat: '#25252b', trim: '#25252b', shirt: '#f4f1ea', tie: '#25252b', hairColor: '#1e1814', skin: '#d9a07c',
    breeches: '#1e1e22', hose: '#1e1e22', shoe: '#121214' },
  kadin: { ...CAST.evsahibi, coat: '#2f8a7a', trim: '#f4efe4', sleeve: 'long', hairColor: '#3a2016', skin: '#efc29e', shoe: '#3a1a1a' },
  erkek: { ...CAST.uye, outfit: 'suit', coat: '#3a4a6a', trim: '#3a4a6a', shirt: '#f1ede2', tie: '#c8a040', hair: 'side', hairColor: '#2a1a12',
    skin: '#e3b08e', breeches: '#2a3040', hose: '#2a3040' },
  // 2002: the waitress in a black waistcoat and a wine-red tie; the diners in a rose dress and a navy blazer
  garsonK: { ...CAST.evsahibi, outfit: 'suit', sleeve: 'long', coat: '#1c1a1e', trim: '#1c1a1e', shirt: '#f6f2ea', tie: '#8a1f24',
    breeches: '#1c1a1e', hose: '#1c1a1e', shoe: '#141012', hairColor: '#4a2a16', skin: '#f0c4a2' },
  bayan: { ...CAST.evsahibi, coat: '#c8505a', trim: '#f4efe4', sleeve: 'long', hairColor: '#c08a48', skin: '#f3c9a8', lips: '#b8404a' },
  bay: { ...CAST.arastirmaci, coat: '#2e3a56', trim: '#2e3a56', tie: '#a03a2a', glasses: false, hairColor: '#5a3a20', skin: '#e8b48e',
    breeches: '#2a2e3a', hose: '#2a2e3a' }
};
const K7INK = '#1a1117', K7GOLD = '#9a6400', K7RED = '#b81d28', K7GREEN = '#4f9a5c';
const K7CH = ['#6a3418', '#8a2a2a', '#3a2a5a', '#2a5a3a', '#6a3418'];  // chocolate wrappers
const K7CHIN = { sh: .45, el: 1.25 };  // a diner's chin on the hand

// ---------- small things ----------
// a small wrapped chocolate: a coloured middle with a gold band and twisted gold ends; s 1 is 68 wide
function k7Choc(x, y, s = 1, rot = 0, col = K7CH[0]) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s);
  [-1, 1].forEach(d => shape(() => { ctx.moveTo(d * 18, -6); ctx.lineTo(d * 34, -16); ctx.lineTo(d * 30, 0); ctx.lineTo(d * 34, 16); ctx.lineTo(d * 18, 6); ctx.closePath(); },
    '#e3b445', { shade: 3, hi: 3, line: 3.5, noRim: true }));
  shape(() => rrp(-21, -14, 42, 28, 10), col, { shade: 4, hi: 5, line: 4, noRim: true,
    after: () => { ctx.fillStyle = '#e3b445'; ctx.fillRect(-6, -15, 12, 30); } });
  ctx.restore();
}
// a small wicker basket of chocolates, the top of its handle at (x, y); s 1 is 156 wide
function k7Basket(x, y, s = 1, n = 5) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  ctx.lineCap = 'round';
  ctx.strokeStyle = INK; ctx.lineWidth = 17; ctx.beginPath(); ctx.moveTo(-62, 78); ctx.bezierCurveTo(-60, -8, 60, -8, 62, 78); ctx.stroke();
  ctx.strokeStyle = '#c8945a'; ctx.lineWidth = 9; ctx.stroke();
  [[-38, 74, -.35], [2, 64, .1], [38, 74, .4], [-16, 82, .5], [22, 84, -.25]].slice(0, n).forEach(([cx, cy, r], i) => k7Choc(cx, cy, .8, r, K7CH[i]));
  shape(() => { ctx.moveTo(-78, 78); ctx.lineTo(78, 78); ctx.quadraticCurveTo(72, 146, 50, 152); ctx.lineTo(-50, 152); ctx.quadraticCurveTo(-72, 146, -78, 78); ctx.closePath(); },
    '#c08a4a', { shade: 10, hi: 6, line: 5, noRim: true,
      after: () => {
        ctx.strokeStyle = 'rgba(70,40,15,.55)'; ctx.lineWidth = 3;
        [96, 114, 132].forEach(yy => { ctx.beginPath(); ctx.moveTo(-80, yy); ctx.lineTo(80, yy); ctx.stroke(); });
        for (let r = 0; r < 4; r++) for (let c = -5; c <= 5; c++) { ctx.beginPath(); ctx.moveTo(c * 15 + (r % 2) * 7, 80 + r * 18); ctx.lineTo(c * 15 + (r % 2) * 7, 94 + r * 18); ctx.stroke(); }
      } });
  shape(() => rrp(-82, 72, 164, 14, 7), '#a8743a', { shade: 3, hi: 3, line: 4, noRim: true });
  ctx.restore();
}
// a small white saucer with n chocolates, its centre at (x, y)
function k7Dish(x, y, s = 1, n = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  shape(() => ctx.ellipse(0, 0, 70, 18, 0, 0, Math.PI * 2), '#f6f3ec', { shade: 5, hi: 3, line: 4.5, noRim: true });
  for (let i = 0; i < n; i++) k7Choc((i - (n - 1) / 2) * 44, -14, .8, (i ? .25 : -.15), K7CH[i]);
  ctx.restore();
}
// a banknote, centre (x, y), w wide
function k7Note(x, y, w, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  const h = w * .48;
  shape(() => rrp(-w / 2, -h / 2, w, h, 6), '#8cc48a', { shade: 5, hi: 4, line: 4.5, noRim: true,
    after: () => {
      ctx.strokeStyle = 'rgba(30,80,40,.6)'; ctx.lineWidth = 3; ctx.strokeRect(-w / 2 + 8, -h / 2 + 8, w - 16, h - 16);
      ctx.fillStyle = 'rgba(30,80,40,.45)'; ctx.beginPath(); ctx.arc(0, 0, h * .26, 0, Math.PI * 2); ctx.fill();
    } });
  ctx.restore();
}
// the black bill folder lying on the table, its front edge on y; tip 0..1 pushes a banknote up out of it
function k7Folder(x, y, w = 130, tip = 0) {
  if (tip > 0) k7Note(x + 6, y - 18 - 26 * eo(tip), w * .8, -.08);
  shape(() => { ctx.moveTo(x - w / 2 + 12, y - 20); ctx.lineTo(x + w / 2 - 12, y - 20); ctx.lineTo(x + w / 2, y); ctx.lineTo(x - w / 2, y); ctx.closePath(); },
    '#1e1a1e', { shade: 4, hi: 5, line: 4.5, noRim: true });
  ctx.fillStyle = '#f8f4ea'; ctx.fillRect(x - w * .22, y - 26, w * .36, 7);
}
// a wine glass and a plate on the table top
function k7Glass(x, y, s = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  ctx.strokeStyle = 'rgba(30,20,25,.85)'; ctx.lineWidth = 4;
  ctx.fillStyle = 'rgba(230,240,245,.35)'; ctx.beginPath(); ctx.moveTo(-22, -96); ctx.quadraticCurveTo(-24, -56, 0, -50); ctx.quadraticCurveTo(24, -56, 22, -96); ctx.closePath(); ctx.fill(); ctx.stroke();
  ctx.fillStyle = 'rgba(140,20,40,.85)'; ctx.beginPath(); ctx.moveTo(-19, -76); ctx.quadraticCurveTo(-18, -54, 0, -53); ctx.quadraticCurveTo(18, -54, 19, -76); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(0, -50); ctx.lineTo(0, -6); ctx.stroke();
  ctx.beginPath(); ctx.ellipse(0, -4, 18, 5, 0, 0, Math.PI * 2); ctx.stroke();
  ctx.restore();
}
function k7Plate(x, y, w = 120) { shape(() => ctx.ellipse(x, y, w / 2, w * .12, 0, 0, Math.PI * 2), '#f6f3ec', { shade: 4, hi: 3, line: 4, noRim: true }); }
// a candle in a straw-wrapped bottle, the bottle's foot on y
function k7Candle(x, y, t, s = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  glow(0, -150, 160, 'rgba(255,200,120,1)', .45 + .05 * Math.sin(t * 9));
  shape(() => rrp(-9, -128, 18, 70, 4), '#2f5a32', { shade: 4, hi: 3, line: 4, noRim: true });
  shape(() => ctx.ellipse(0, -36, 40, 38, 0, 0, Math.PI * 2), '#d8b070', { shade: 8, hi: 5, line: 4.5, noRim: true,
    after: () => { ctx.strokeStyle = 'rgba(110,70,20,.5)'; ctx.lineWidth = 3; for (let i = -4; i <= 4; i++) { ctx.beginPath(); ctx.moveTo(i * 12 - 20, -76); ctx.lineTo(i * 12 + 20, 4); ctx.stroke(); } } });
  shape(() => rrp(-10, -176, 20, 52, 5), '#f6efe0', { shade: 4, hi: 3, line: 4, noRim: true });
  const f = 1 + .08 * Math.sin(t * 13);
  ctx.save(); ctx.translate(0, -180); ctx.scale(1, f);
  ctx.fillStyle = '#ffd27a'; ctx.beginPath(); ctx.moveTo(0, -30); ctx.quadraticCurveTo(12, -8, 0, 2); ctx.quadraticCurveTo(-12, -8, 0, -30); ctx.fill();
  ctx.fillStyle = '#fff6dc'; ctx.beginPath(); ctx.ellipse(0, -6, 4, 8, 0, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
  ctx.restore();
}
// a prop held in the front hand, drawn upright whatever the arm does. fn(hx, hy) draws it around the hand, which is at
// (hx, hy) in that upright frame; o.under draws the prop before the hand (a saucer resting on the palm)
const k7Hold = (fn, sh, el, o = {}) => spec => {
  const a = sh + el, hx = 30 * Math.sin(a), hy = 30 * Math.cos(a);
  const draw = () => { ctx.save(); ctx.rotate(a); fn(hx, hy); ctx.restore(); };
  if (o.under) draw();
  hand(spec, o.hand || 'hold', 1.3 * Math.sqrt(spec.build.arm || 1));
  if (!o.under) draw();
};
const k7BasketIn = (sh, el) => k7Hold((hx, hy) => k7Basket(hx, hy - 10, 1), sh, el, { under: true });
const k7ChocIn = (sh, el, n = 1, s = 1.25) => k7Hold((hx, hy) => { for (let i = 0; i < n; i++) k7Choc(hx + 4 + i * 34 * s, hy - 34 * s - i * 22 * s, s, -.5 + i * .5, K7CH[i]); }, sh, el);
const k7DishIn = (sh, el, n) => k7Hold((hx, hy) => k7Dish(hx, hy - 34, 1.05, n), sh, el, { under: true, hand: 'palm' });

// a speech bubble: rows [[text, colour, alpha]], size px, tail to (tx, ty); k 0..1 pops it in
function k7Bubble(x, y, rows, size, k, tx, ty, o = {}) {
  if (k <= 0) return;
  const lh = size * 1.22, w = Math.max(...rows.map(r => measure(up(r[0]), size, SANS, 900))) + size * 1.3;
  const h = rows.length * lh + size * .85, r = Math.min(30, h / 2 - 2);
  const s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.translate(-x, -y);
  shape(() => {
    ctx.moveTo(x - w / 2 + r, y - h / 2); ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, r); ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, r);
    const bx = clamp(tx, x - w / 2 + r + 14, x + w / 2 - r - 30);
    ctx.lineTo(bx + 22, y + h / 2); ctx.lineTo(tx, ty); ctx.lineTo(bx - 8, y + h / 2);
    ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, r); ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, r); ctx.closePath();
  }, o.fill || '#fbf6e8', { shade: 6, hi: 4, noRim: true, line: 6 });
  if (o.border) { ctx.strokeStyle = o.border; ctx.lineWidth = 6; rr(x - w / 2 + 8, y - h / 2 + 8, w - 16, h - 16, Math.max(4, r - 6)); ctx.stroke(); }
  rows.forEach(([str, col], i) => text(up(str), x, y - h / 2 + size * .425 + lh * (i + .5) + size * .04, size, col || K7INK, SANS, 900));
  ctx.restore();
}
// a round gold badge with a short label ("1", "+1")
function k7Badge(x, y, label, k, r = 40) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(back(clamp(k)), back(clamp(k)));
  glow(0, 0, r * 2.2, 'rgba(232,181,74,1)', .4);
  shape(() => ctx.arc(0, 0, r, 0, Math.PI * 2), C.gold, { shade: 4, hi: 4, line: 5, noRim: true });
  text(label, 0, 3, r * 1.15, K7INK, SANS, 900);
  ctx.restore();
}
// a rubber stamp, k 0..1 drops it from large onto the frame
function k7Stamp(label, x, y, size, k, rot = -.1, col = C.red) {
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
// "=" drawn as two bars, so no font glyph is needed
function k7Equal(x, y, k, col = C.gold2) {
  if (k <= 0) return;
  const s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.lineCap = 'round'; ctx.strokeStyle = col; ctx.lineWidth = 11;
  ctx.shadowColor = rgba(col, .7); ctx.shadowBlur = 16;
  [-14, 14].forEach(dy => { ctx.beginPath(); ctx.moveTo(-28, dy); ctx.lineTo(28, dy); ctx.stroke(); });
  ctx.restore();
}
// a curved arrow along a quadratic from (x0, y0) over (cx, cy) to (x1, y1), drawn up to p 0..1; returns the tip
function k7Arc(x0, y0, cx, cy, x1, y1, p, col, w = 14) {
  if (p <= 0) return [x0, y0];
  const pt = u => [(1 - u) * (1 - u) * x0 + 2 * (1 - u) * u * cx + u * u * x1, (1 - u) * (1 - u) * y0 + 2 * (1 - u) * u * cy + u * u * y1];
  const e = eo(clamp(p)), n = 40;
  ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.shadowColor = rgba(col, .7); ctx.shadowBlur = 18;
  ctx.strokeStyle = col; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(x0, y0);
  for (let i = 1; i <= n; i++) { const [x, y] = pt(e * i / n); ctx.lineTo(x, y); }
  ctx.stroke();
  const [ax, ay] = pt(e), [bx, by] = pt(Math.max(0, e - .04)), an = Math.atan2(ay - by, ax - bx);
  ctx.fillStyle = col; ctx.beginPath(); ctx.moveTo(ax + Math.cos(an) * w * 1.6, ay + Math.sin(an) * w * 1.6);
  ctx.lineTo(ax + Math.cos(an + 2.4) * w * 1.8, ay + Math.sin(an + 2.4) * w * 1.8); ctx.lineTo(ax + Math.cos(an - 2.4) * w * 1.8, ay + Math.sin(an - 2.4) * w * 1.8); ctx.closePath(); ctx.fill();
  ctx.restore();
  return [ax, ay];
}

// ---------- the rooms ----------
// today: a café in afternoon light, a big window on the right, a chalk menu on the left
function k7Cafe(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1330); g.addColorStop(0, '#a3b7ad'); g.addColorStop(1, '#7c9188');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1330);
  shape(() => rrp(580, 430, 480, 600, 6), '#2a2e32', { shade: 4, hi: 3, line: 5 });
  g = ctx.createLinearGradient(0, 444, 0, 1016); g.addColorStop(0, '#bfe0f2'); g.addColorStop(1, '#f4efdc');
  ctx.fillStyle = g; ctx.fillRect(594, 444, 452, 572);
  ctx.save(); ctx.beginPath(); ctx.rect(594, 444, 452, 572); ctx.clip();
  ctx.fillStyle = '#d9c9b0'; ctx.fillRect(700, 600, 360, 420);  // the building across the street
  ctx.fillStyle = 'rgba(80,110,140,.45)';
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) ctx.fillRect(724 + c * 84, 630 + r * 96, 46, 60);
  [[640, 800, 120], [930, 860, 110]].forEach(([x, y, rad], i) => {
    ctx.fillStyle = '#5a4030'; ctx.fillRect(x - 10, y, 20, 240);
    [[0, 0, 1], [-.6, .3, .75], [.6, .35, .75], [0, -.5, .8]].forEach(([dx, dy, k], j) => { ctx.fillStyle = j % 2 ? '#7a9a52' : '#6a8a46'; circle(x + dx * rad, y + dy * rad, rad * k, ctx.fillStyle); });
  });
  ctx.restore();
  ctx.fillStyle = '#2a2e32'; ctx.fillRect(816, 444, 10, 572); ctx.fillRect(594, 724, 452, 10);
  // the chalk menu
  shape(() => rrp(40, 470, 250, 320, 8), '#6a4426', { shade: 6, hi: 4, line: 5 });
  ctx.fillStyle = '#26302c'; ctx.fillRect(56, 486, 218, 288);
  text('MENÜ', 165, 528, 40, '#efe9dc', SANS, 900);
  ctx.strokeStyle = 'rgba(239,233,220,.7)'; ctx.lineWidth = 4; ctx.lineCap = 'round';
  for (let i = 0; i < 5; i++) { ctx.beginPath(); ctx.moveTo(76, 590 + i * 38); ctx.lineTo(76 + (i % 2 ? 120 : 150), 590 + i * 38); ctx.moveTo(236, 590 + i * 38); ctx.lineTo(254, 590 + i * 38); ctx.stroke(); }
  // two black pendant lamps
  [[330, 410], [880, 300]].forEach(([x, y]) => {
    ctx.strokeStyle = '#1a1a1e'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, y); ctx.stroke();
    glow(x, y + 80, 220, 'rgba(255,224,170,1)', .35);
    shape(() => { ctx.moveTo(x - 20, y); ctx.lineTo(x + 20, y); ctx.lineTo(x + 70, y + 56); ctx.lineTo(x - 70, y + 56); ctx.closePath(); }, '#24262a', { shade: 6, hi: 4, line: 4.5 });
    circle(x, y + 60, 16, '#fff2cc');
  });
  // the floor
  g = ctx.createLinearGradient(0, 1330, 0, H); g.addColorStop(0, '#7a5a3e'); g.addColorStop(1, '#2e1e12');
  ctx.fillStyle = g; ctx.fillRect(0, 1330, W, H - 1330);
  shape(() => rrp(-10, 1318, W + 20, 16, 2), '#4a3a30', { shade: 3, hi: 2, line: 4 });
}
// a small painting of hills and cypresses in a gold frame
function k7Hills(x, y, w, h) {
  shape(() => rrp(x, y, w, h, 4), '#b88a3a', { shade: 6, hi: 5, line: 5 });
  ctx.save(); ctx.beginPath(); ctx.rect(x + 14, y + 14, w - 28, h - 28); ctx.clip();
  let g = ctx.createLinearGradient(0, y, 0, y + h); g.addColorStop(0, '#f0c890'); g.addColorStop(1, '#d89a5a');
  ctx.fillStyle = g; ctx.fillRect(x, y, w, h);
  ctx.fillStyle = '#8a9a5a'; ctx.beginPath(); ctx.moveTo(x, y + h * .62); ctx.quadraticCurveTo(x + w * .35, y + h * .38, x + w * .7, y + h * .58); ctx.quadraticCurveTo(x + w * .85, y + h * .5, x + w, y + h * .55); ctx.lineTo(x + w, y + h); ctx.lineTo(x, y + h); ctx.fill();
  ctx.fillStyle = '#6a7a3a'; ctx.beginPath(); ctx.moveTo(x, y + h * .8); ctx.quadraticCurveTo(x + w * .5, y + h * .6, x + w, y + h * .78); ctx.lineTo(x + w, y + h); ctx.lineTo(x, y + h); ctx.fill();
  ctx.fillStyle = '#2f4a2a';
  [.3, .38, .72].forEach(f => { const cx = x + w * f, by = y + h * .7; ctx.beginPath(); ctx.moveTo(cx, by - h * .4); ctx.quadraticCurveTo(cx + 12, by - h * .15, cx, by); ctx.quadraticCurveTo(cx - 12, by - h * .15, cx, by - h * .4); ctx.fill(); });
  ctx.restore();
}
// 2002: a warm Italian-American dining room, wine-red walls, framed hills, a bottle shelf, wall lamps
function k7Trattoria(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1330); g.addColorStop(0, '#7a2a24'); g.addColorStop(1, '#561c18');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1330);
  ctx.fillStyle = 'rgba(255,190,140,.05)'; for (let i = 0; i < 14; i++) ctx.fillRect(i * 80 + 20, 0, 34, 1330);
  k7Hills(60, 470, 250, 180);
  k7Hills(790, 480, 230, 200);
  // a shelf of straw-wrapped bottles in the middle
  shape(() => rrp(380, 600, 330, 18, 4), '#4a2a18', { shade: 3, hi: 3, line: 4 });
  [420, 490, 560, 630, 680].forEach((x, i) => {
    shape(() => rrp(x - 8, 500 + (i % 2) * 8, 16, 50, 4), i % 2 ? '#2f5a32' : '#3a4a2a', { shade: 3, hi: 3, line: 3.5, noRim: true });
    shape(() => ctx.ellipse(x, 572, 28, 30, 0, 0, Math.PI * 2), '#d8b070', { shade: 6, hi: 4, line: 4, noRim: true });
  });
  // wall lamps with warm pools of light
  [[340, 760], [740, 760]].forEach(([x, y]) => {
    glow(x, y - 30, 240, 'rgba(255,200,130,1)', .35 + .03 * Math.sin(t * 3 + x));
    shape(() => { ctx.moveTo(x - 30, y - 60); ctx.lineTo(x + 30, y - 60); ctx.lineTo(x + 20, y); ctx.lineTo(x - 20, y); ctx.closePath(); }, '#f0d8a0', { shade: 5, hi: 4, line: 4, noRim: true });
    shape(() => rrp(x - 6, y, 12, 26, 3), '#3a2a1a', { shade: 2, hi: 2, line: 3, noRim: true });
  });
  // dark wood wainscot
  ctx.fillStyle = '#3a2216'; ctx.fillRect(0, 980, W, 350);
  shape(() => rrp(-10, 970, W + 20, 18, 3), '#5a3a22', { shade: 3, hi: 3, line: 4 });
  ctx.strokeStyle = 'rgba(0,0,0,.25)'; ctx.lineWidth = 3;
  for (let i = 0; i < 8; i++) { ctx.beginPath(); ctx.moveTo(60 + i * 140, 1000); ctx.lineTo(60 + i * 140, 1320); ctx.stroke(); }
  // terracotta floor
  g = ctx.createLinearGradient(0, 1330, 0, H); g.addColorStop(0, '#8a4a2a'); g.addColorStop(1, '#2e160c');
  ctx.fillStyle = g; ctx.fillRect(0, 1330, W, H - 1330);
  shape(() => rrp(-10, 1318, W + 20, 16, 2), '#3a2216', { shade: 3, hi: 2, line: 4 });
}
// a restaurant table from the front: the cloth's top surface ends at y, then hangs down `drop`. o.check: red gingham
function k7Table(x0, x1, y, drop, o = {}) {
  const top = 40, base = o.check ? '#f6efe2' : '#f3f1ea';
  const gingham = (x, yy, w, h, cw, ch) => () => {
    if (!o.check) return;
    ctx.fillStyle = 'rgba(196,36,46,.5)';
    for (let i = 0; i * cw < w + cw; i += 2) ctx.fillRect(x + i * cw, yy, cw, h);
    for (let j = 0; j * ch < h + ch; j += 2) ctx.fillRect(x, yy + j * ch, w, ch);
  };
  shape(() => { ctx.moveTo(x0 + 26, y - top); ctx.lineTo(x1 - 26, y - top); ctx.lineTo(x1 + 8, y); ctx.lineTo(x0 - 8, y); ctx.closePath(); }, base,
    { shade: 3, hi: 3, line: 5, noRim: true, after: gingham(x0 - 20, y - top, x1 - x0 + 40, top, 36, 10) });
  shape(() => {
    ctx.moveTo(x0 - 8, y); ctx.lineTo(x1 + 8, y); ctx.lineTo(x1 + 18, y + drop);
    for (let i = 1; i <= 8; i++) { const xx = lerp(x1 + 18, x0 - 18, i / 8); ctx.quadraticCurveTo(xx + (x1 - x0) / 16, y + drop + 12, xx, y + drop); }
    ctx.closePath();
  }, base, { shade: 14, hi: 6, line: 5, noRim: true,
    after: () => {
      gingham(x0 - 20, y, x1 - x0 + 40, drop + 20, 36, 36)();
      const g = ctx.createLinearGradient(0, y, 0, y + 40); g.addColorStop(0, 'rgba(0,0,0,.28)'); g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g; ctx.fillRect(x0 - 30, y, x1 - x0 + 60, 40);
      ctx.strokeStyle = 'rgba(60,30,30,.18)'; ctx.lineWidth = 5;
      [.2, .45, .7, .9].forEach(f => { ctx.beginPath(); ctx.moveTo(lerp(x0, x1, f), y + 20); ctx.quadraticCurveTo(lerp(x0, x1, f) - 10, y + drop * .6, lerp(x0, x1, f) + 6, y + drop); ctx.stroke(); });
    } });
}
// a diner seated behind a table whose top is at `top`: the hips sit a little below it
function k7Diner(spec, x, top, s, t, o = {}) {
  const y = top + (440 + 110) * s * spec.build.height;
  figure(spec, x, y, s, t, { dir: o.dir || 1, legs: 'hide', seed: o.seed || 0, expr: o.expr || 'calm', talk: o.talk || 0, nod: o.nod || 0,
    headTilt: o.tilt || 0, lean: o.lean || 0, front: o.front || { sh: .25, el: .4 }, back: { sh: .2, el: .4 } });
}
// where a figure's head centre is (for bubbles and badges)
const k7Head = (spec, x, y, s, dir = 1) => [x + dir * 46 * s * spec.build.height, y - 886 * s * spec.build.height];

// ---------- the four ways (cards) and the score board ----------
const K7WAYS = [
  { n: 0, label: ['SADECE', 'HESAP'] },
  { n: 1, label: ['1', 'ÇİKOLATA'] },
  { n: 2, label: ['2', 'ÇİKOLATA'] },
  { n: -1, label: ['?'] }
];
// one card: number badge, the bill and its chocolates, a label; i 0..3, k pops it in; o.reveal turns "?" into 1 + 1
function k7Card(i, x, y, k, o = {}) {
  if (k <= 0) return;
  const w = 228, h = 236, s = back(clamp(k)) * (o.s || 1), way = K7WAYS[i];
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  shape(() => rrp(-w / 2, -h / 2, w, h, 16), 'rgba(14,12,18,.92)', { flat: true, line: 5 });
  ctx.strokeStyle = o.hot ? C.gold : rgba(C.gold, .6); ctx.lineWidth = o.hot ? 5 : 3; rr(-w / 2 + 8, -h / 2 + 8, w - 16, h - 16, 10); ctx.stroke();
  shape(() => ctx.arc(-w / 2 + 6, -h / 2 + 6, 26, 0, Math.PI * 2), C.gold, { shade: 3, hi: 3, line: 4, noRim: true });
  text(String(i + 1), -w / 2 + 6, -h / 2 + 8, 32, K7INK, SANS, 900);
  const rv = clamp(o.reveal || 0);
  if (way.n >= 0 || rv > 0) {
    // the bill: a small slip with lines
    shape(() => rrp(-38, -86, 76, 92, 6), '#f8f4ea', { shade: 4, hi: 3, line: 4, noRim: true,
      after: () => { ctx.fillStyle = 'rgba(40,40,60,.4)'; for (let j = 0; j < 4; j++) ctx.fillRect(-26, -72 + j * 18, j === 3 ? 30 : 52, 6); } });
    if (way.n > 0) for (let j = 0; j < way.n; j++) k7Choc(way.n === 1 ? 50 : 40 + j * 16, -20 - j * 30, .9, -.3 + j * .4, K7CH[j]);
    if (way.n < 0) {
      ctx.save(); ctx.globalAlpha *= rv;
      k7Choc(52, -10, .85, -.3, K7CH[0]); k7Choc(-56, -60, .85, .3, K7CH[1]);
      ctx.restore();
    }
  }
  if (way.n < 0) {
    if (rv < 1) { ctx.save(); ctx.globalAlpha *= 1 - rv; text('?', 0, -30, 120, C.gold2, SANS, 900, 'center', 8); ctx.restore(); }
    if (rv > 0) { ctx.save(); ctx.globalAlpha *= rv; text('1 + 1', 0, 66, 46, C.gold2, SANS, 900); ctx.restore(); }
  } else if (way.label.length === 2) {
    text(way.label[0], 0, 46, 30, C.ivory, SANS, 900); text(way.label[1], 0, 82, 30, C.ivory, SANS, 900);
  }
  ctx.restore();
}
// the board under the 2002 two-shot: the tip rise for each way against the tables with no chocolate.
// vals [one, two, one plus one] (null: not shown yet); o.hot the column being counted; o.reveal names the last one
const K7B = { y: 1110, h: 286, xs: [205, 540, 875] };
function k7Board(vals, o = {}) {
  const { y, h, xs } = K7B;
  shape(() => rrp(36, y, 1008, h, 18), 'rgba(14,12,18,.92)', { flat: true, line: 5 });
  ctx.save(); ctx.strokeStyle = rgba(C.gold, .7); ctx.lineWidth = 3; rr(46, y + 10, 988, h - 20, 12); ctx.stroke(); ctx.restore();
  text('BAHŞİŞ ARTIŞI · ÇİKOLATASIZ MASALARA GÖRE', 540, y + 42, 29, C.gold, SANS, 900, 'center', 0, C.ink, 2);
  const labels = ['1 ÇİKOLATA', '2 ÇİKOLATA', o.reveal ? '1 + 1' : '?'];
  xs.forEach((cx, i) => {
    const v = vals[i], hot = o.hot === i;
    ctx.fillStyle = hot ? rgba(C.gold, .16) : 'rgba(255,255,255,.05)'; rr(cx - 156, y + 72, 312, h - 92, 12); ctx.fill();
    ctx.strokeStyle = hot ? C.gold : 'rgba(243,234,216,.22)'; ctx.lineWidth = hot ? 4 : 2; rr(cx - 156, y + 72, 312, h - 92, 12); ctx.stroke();
    text(labels[i], cx, y + 106, 30, C.ivory, SANS, 900, 'center', 0, C.ink, 1);
    if (v == null) text('?', cx, y + 180, 80, 'rgba(243,234,216,.3)', SANS, 900);
    else {
      text('+%' + Math.round(v), cx, y + 182, 92, C.gold2, SANS, 900, 'center', 8);
      ctx.fillStyle = 'rgba(255,255,255,.1)'; rr(cx - 124, y + 238, 248, 18, 9); ctx.fill();
      ctx.fillStyle = C.gold2; rr(cx - 124, y + 238, Math.max(18, 248 * v / 25), 18, 9); ctx.fill();
    }
  });
}
const k7Count = (from, to, k) => lerp(from, to, eo(clamp(k)));

// the 2002 two-shot over the board: the diners on the left, the waitress on the right with the basket
const K7P = { top: 1000, bay: [175, .6], bayan: [410, .6], wait: [800, 1240, .7] };
function k7TwoShot(t, o = {}) {
  k7Trattoria(t);
  chairBack(175, K7P.top + 20, .85, '#4a2a18'); chairBack(410, K7P.top + 20, .85, '#4a2a18');
  k7Diner(K7.bay, K7P.bay[0], K7P.top, K7P.bay[1], t, { seed: 2, expr: o.bayExpr || { ...EXPR.calm, look: [.5, 0] }, front: K7CHIN });
  k7Diner(K7.bayan, K7P.bayan[0], K7P.top, K7P.bayan[1], t, { seed: 5, expr: o.bayanExpr || { ...EXPR.calm, look: [.5, -.1] },
    front: o.bayanFront, talk: o.bayanTalk || 0 });
  const [wx, wy, ws] = K7P.wait;
  if (o.glow > 0) glow(K7P.bayan[0] + 60, 860, 260, 'rgba(232,181,74,1)', .45 * o.glow);
  const sh = o.waitSh ?? 1.0, el = o.waitEl ?? .35;
  figure(K7.garsonK, wx, wy, ws, t, { dir: -1, legs: 'stand', seed: 7, expr: o.waitExpr || 'warm', talk: o.waitTalk || 0,
    front: { sh, el, hand: 'hold', prop: k7BasketIn(sh, el) }, back: { sh: .1, el: .3 } });
  k7Table(30, 640, K7P.top, 150, { check: true });
  k7Plate(150, K7P.top - 18, 130); k7Plate(400, K7P.top - 18, 130);
  k7Glass(250, K7P.top - 16, .9); k7Candle(300, K7P.top - 10, t, .7);
  k7Folder(520, K7P.top - 6, 120, o.tip || 0);
}

Object.assign(SAHNE, {
  kanca(V, L, t, lt) {
    // the hook, from the very first frame: in today's café the waiter puts the bill down ("Hesabınız."). On "herkese"
    // he turns and walks off; by "küçük" he has turned back; on "ona" he holds out one more chocolate, "Bu da size
    // özel!", and the couple light up
    K7.day();
    const tn = wordTime(L, V.no, .3), tb = wordTime(L, V.back, .65), ty = wordTime(L, V.yes, .85);
    const away = prog(t, tn - .05, tn + .8), come = prog(t, tb - .45, tb + .35), yes = prog(t, ty - .15, ty + .2);
    const turned = t > tn - .1 && t < tb - .45;
    const wx = t < tn - .1 ? 800 : turned ? lerp(800, 960, eio(away)) : lerp(960, 790, eio(come));
    const walking = (away > 0 && away < 1) || (come > 0 && come < 1);
    const top = 1130, ws = .74;
    cam(540, 900, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => {
      k7Cafe(t);
      chairBack(190, top + 30, .9, '#3a2a20'); chairBack(440, top + 30, .9, '#3a2a20');
      const happy = yes > .4, meh = t > tn + .2 && !happy;
      k7Diner(K7.kadin, 190, top, .68, t, { seed: 3, expr: happy ? 'happy' : meh ? { ...EXPR.neutral, look: [.6, .1], curve: -.1 } : { ...EXPR.calm, look: [.6, -.1] },
        front: happy ? { sh: lerp(.45, 1.2, yes), el: lerp(1.25, 1.6, yes), hand: 'open' } : null });
      k7Diner(K7.erkek, 440, top, .68, t, { seed: 6, expr: happy ? 'warm' : meh ? { ...EXPR.displeased, look: [.6, 0] } : { ...EXPR.calm, look: [.6, 0] } });
      if (yes > 0) glow(330, 900, 360, 'rgba(232,181,74,1)', .35 * yes);
      const offer = t > tb - .45 ? eo(prog(t, ty - .5, ty)) : 0, served = t > tn - .35;
      const sh = t < tn - .35 ? .95 : turned ? .15 : lerp(.25, .8, offer), el = t < tn - .35 ? .35 : turned ? .3 : lerp(1.4, .5, offer);
      figure(K7.garson, wx, 1490, ws, t, { dir: turned ? 1 : -1, legs: walking ? 'walk' : 'stand', phase: t * 9, seed: 4,
        expr: yes > .3 ? 'happy' : 'warm', talk: (t < tn - .3 || (yes > 0 && yes < 1)) ? .5 : 0,
        front: !served ? { sh, el, hand: 'palm', prop: k7DishIn(sh, el, 1) } : t > tb - .45 ? { sh, el, hand: 'hold', prop: k7Hold((hx, hy) => k7Choc(hx + 4, hy - 34, 1.35, -.4, K7CH[1]), sh, el) } : { sh, el },
        back: { sh: .1, el: .3 } });
      k7Table(40, 640, top, 340);
      k7Plate(170, top - 18, 140); k7Plate(420, top - 18, 140);
      k7Glass(300, top - 16, 1);
      k7Folder(560, top - 6, 130, 0);
      if (t > tn - .35) k7Dish(500, top - 24, .8, 1);
      sparks(330, 820, t, 18, 220, prog(t, ty, ty + .8));
    });
    k7Bubble(700, 600, [['Hesabınız.', K7INK]], 44, 1 - prog(t, tn - .3, tn - .05), 760, 690);
    k7Bubble(600, 650, [['Bu da size özel!', K7GOLD]], 46, yes, 700, 740, { fill: '#fff2c8', border: C.gold });
    plate(V.no_label, 540, 520, 60, back(prog(t, tn - .05, tn + .3)), C.red);
    if (yes > 0) glow(540, 1340, 320, 'rgba(232,181,74,1)', .3 * yes);
    plate(V.yes_label, 540, 1340, 60, back(prog(t, ty - .1, ty + .25)), C.gold);
  },
  restoran(V, L, t, lt) {
    // 2002, a dining room in New Jersey: the waitress walks up to a couple with the bill and a basket of chocolates.
    // On "dört" the four ways pop up as cards over the tablecloth: just the bill, 1, 2, and a "?"
    K7.warm();
    const tk = wordTime(L, V.key, .6), walk = prog(t, L.t0, tk - .3);
    const wx = lerp(1150, 820, eo(walk)), sh = lerp(.3, .75, prog(t, tk - .4, tk)), el = lerp(1.3, .9, prog(t, tk - .4, tk));
    const top = 1130;
    cam(540, 900, lerp(1.0, 1.04, eio(prog(lt, 0, 5))), () => {
      k7Trattoria(t);
      chairBack(180, top + 30, .9, '#4a2a18'); chairBack(430, top + 30, .9, '#4a2a18');
      k7Diner(K7.bay, 180, top, .66, t, { seed: 2, expr: { ...EXPR.calm, look: [.6, 0] }, front: K7CHIN });
      k7Diner(K7.bayan, 430, top, .66, t, { seed: 5, expr: walk > .7 ? 'warm' : { ...EXPR.calm, look: [.6, -.1] } });
      figure(K7.garsonK, wx, 1490, .8, t, { dir: -1, legs: walk > 0 && walk < 1 ? 'walk' : 'stand', phase: walk * 16, seed: 7, expr: 'warm',
        front: { sh, el, hand: 'hold', prop: k7BasketIn(sh, el) }, back: { sh: .15, el: .4 } });
      k7Table(40, 640, top, 340, { check: true });
      k7Plate(170, top - 18, 140); k7Plate(420, top - 18, 140);
      k7Glass(290, top - 16, 1); k7Candle(340, top - 10, t, .8);
      k7Folder(560, top - 6, 130, 0);
    });
    [150, 393, 637, 880].forEach((x, i) => k7Card(i, x, 1270, prog(t, tk + i * .14 - .1, tk + i * .14 + .2)));
    plate(V.year, 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)));
  },
  ikram(V, L, t, lt) {
    // one way of giving: the woman reaches into the basket and holds up one (kind 1) or two (kind 2) chocolates, a
    // gold count badge pops, and on the number the board counts that column up: +3 % or +14 %
    K7.warm();
    const n = V.kind, tc = wordTime(L, 'çikolata', .3), tk = wordTime(L, V.key, .75);
    const reach = prog(t, tc - .35, tc - .05), held = prog(t, tc + .05, tc + .4), count = prog(t, tk - .15, tk + .45);
    const front = held > 0 ? { sh: lerp(1.25, .7, eo(held)), el: lerp(.85, 2.3, eo(held)), hand: 'hold' } : { sh: lerp(.25, 1.25, eo(reach)), el: lerp(.4, .85, eo(reach)), hand: reach > .5 ? 'hold' : 'open' };
    const back = eo(prog(t, tc + .05, tc + .45)), come = eo(prog(lt, 0, .4));
    if (held > 0) front.prop = k7ChocIn(front.sh, front.el, n);
    cam(540, 960, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => k7TwoShot(t, {
      bayanFront: front, bayanExpr: held > .5 ? (n === 2 ? 'happy' : 'warm') : { ...EXPR.calm, look: [.6, -.2] },
      bayExpr: held > .5 && n === 2 ? 'warm' : { ...EXPR.calm, look: [.5, 0] }, glow: n === 2 ? held * .6 : 0,
      waitSh: lerp(lerp(.4, 1.0, come), .12, back), waitEl: lerp(lerp(1.2, .35, come), .2, back), tip: count * (n === 2 ? 1 : .4) }));
    const [hx, hy] = [K7P.bayan[0] + 30, 610];
    k7Badge(hx, hy, String(n), prog(t, tc + .2, tc + .5));
    const vals = n === 1 ? [count > 0 ? k7Count(0, 3, count) : null, null, null] : [3, count > 0 ? k7Count(0, 14, count) : null, null];
    k7Board(vals, { hot: n - 1 });
    flash(t, tk - .05, .25);
  },
  don(V, L, t, lt) {
    // the fourth way, the twist: card 4 still shows "?". She gives one chocolate, starts to walk away, stops, turns
    // back and holds out the basket again: "Size bir tane daha!" On "daha" the card turns into "1 + 1"
    K7.warm();
    const tf = wordTime(L, V.key, .1), t1 = wordTime(L, V.one, .25), tw = wordTime(L, V.walk, .4), ts = wordTime(L, V.stop, .55);
    const tb = wordTime(L, V.back, .65), tm = wordTime(L, V.more, .85);
    const give = prog(t, t1 - .2, t1 + .3), took = prog(t, t1 + .1, t1 + .45);
    const away = prog(t, tw - .1, ts), come = prog(t, tb - .1, tm - .15), again = prog(t, tm - .3, tm + .1), took2 = prog(t, tm, tm + .35);
    const leaving = t > tw - .1 && t < tb - .1;
    const wx = leaving ? lerp(700, 920, eo(away)) : t >= tb - .1 ? lerp(920, 710, eio(come)) : 700;
    const top = 1130;
    cam(540, 900, lerp(1.0, 1.03, eio(prog(lt, 0, 5))), () => {
      k7Trattoria(t);
      chairBack(170, top + 30, .9, '#4a2a18'); chairBack(420, top + 30, .9, '#4a2a18');
      const joy = took2 > .4;
      if (joy) glow(380, 880, 320, 'rgba(232,181,74,1)', .45 * took2);
      k7Diner(K7.bay, 170, top, .66, t, { seed: 2, expr: joy ? 'happy' : { ...EXPR.calm, look: [.6, 0] }, front: K7CHIN });
      const hold1 = took > 0 && !joy, f = joy ? { sh: lerp(.6, .9, took2), el: lerp(2.0, 2.3, took2), hand: 'hold' } : hold1 ? { sh: lerp(1.2, .6, eo(took)), el: lerp(.9, 2.0, eo(took)), hand: 'hold' } : { sh: lerp(.45, 1.2, eo(give)), el: lerp(1.25, .9, eo(give)), hand: 'open' };
      if (hold1 || joy) f.prop = k7ChocIn(f.sh, f.el, joy ? 2 : 1);
      k7Diner(K7.bayan, 420, top, .66, t, { seed: 5, expr: joy ? 'happy' : took > .5 && leaving ? { ...EXPR.calm, look: [.7, 0], curve: .3 } : 'warm', front: f });
      const offer = leaving ? 0 : t >= tb - .1 ? eo(again) : eo(give) * (1 - prog(t, tw - .3, tw - .1));
      const sh = leaving ? .15 : lerp(.35, 1.0, offer), el = leaving ? .3 : lerp(1.3, .35, offer);
      figure(K7.garsonK, wx, 1490, .8, t, { dir: leaving ? 1 : -1, legs: (away > 0 && away < 1) || (come > 0 && come < 1) ? 'walk' : 'stand',
        phase: t * 9, seed: 7, expr: again > .3 ? 'happy' : 'warm', talk: again > 0 && again < 1 ? .6 : 0,
        front: { sh, el, hand: 'hold', prop: k7BasketIn(sh, el) }, back: { sh: .15, el: .4 } });
      k7Table(40, 600, top, 340, { check: true });
      k7Plate(160, top - 18, 140); k7Plate(410, top - 18, 140);
      k7Glass(285, top - 16, 1); k7Candle(330, top - 10, t, .8);
      k7Folder(530, top - 6, 130, 0);
      sparks(420, 800, t, 18, 200, prog(t, tm, tm + .8));
    });
    k7Card(3, 160, 590, prog(t, L.t0, L.t0 + .3), { s: .85 * (1 + .12 * Math.sin(Math.PI * prog(t, tf - .1, tf + .3))), hot: true, reveal: prog(t, tm + .1, tm + .4) });
    k7Badge(330, 650, '1', prog(t, t1 + .2, t1 + .5));
    k7Badge(440, 650, '+1', prog(t, tm + .05, tm + .35));
    k7Bubble(720, 610, [['Size bir tane', K7GOLD], ['daha!', K7GOLD]], 42, again, 770, 720, { fill: '#fff2c8', border: C.gold });
  },
  sonuc(V, L, t, lt) {
    // the result as three big bars over the dimmed dining room: +3 and +14. On "yine" the two chocolates under bars 2
    // and 3 get an "=" between them and a dashed outline of bar 3 rises to the level of bar 2; on the number it fills and
    // climbs on to +21
    K7.warm();
    const ts = wordTime(L, V.same, .2), tk = wordTime(L, V.key, .65);
    cam(540, 900, 1.1, () => k7Trattoria(t));
    ctx.fillStyle = 'rgba(10,8,14,.74)'; ctx.fillRect(0, 0, W, H);
    plate('Bahşiş artışı', 540, 510, 52, back(prog(lt, 0, .3)), C.ivory);
    const bot = 1190, topY = 720, max = 25, same = prog(t, ts - .05, ts + .35), grow = prog(t, tk - .1, tk + .5);
    [[200, 3, '1 ÇİKOLATA'], [540, 14, '2 ÇİKOLATA'], [880, grow > 0 ? k7Count(14, 21, grow) : 0, '1 + 1']].forEach(([cx, v, cap], i) => {
      const hot = i === 2 && grow > 0, y = lerp(bot, topY, v / max);
      shape(() => rrp(cx - 135, topY - 30, 270, bot - topY + 30, 16), 'rgba(20,18,26,.85)', { flat: true, line: 4 });
      if (v > .2) {
        if (hot) glow(cx, y, 260, 'rgba(232,181,74,1)', .45 * grow);
        const g = ctx.createLinearGradient(0, y, 0, bot); g.addColorStop(0, C.gold2); g.addColorStop(1, C.goldDim);
        shape(() => rrp(cx - 112, y, 224, Math.max(10, bot - y - 4), 10), C.gold, { flat: true, line: 4,
          after: () => { ctx.fillStyle = g; ctx.fillRect(cx - 120, y, 240, bot - y); ctx.fillStyle = 'rgba(255,255,255,.25)'; ctx.fillRect(cx - 92, y + 10, 18, Math.max(0, bot - y - 20)); } });
        text('+%' + Math.round(v), cx, y - 70, hot ? 116 : 96, i === 2 ? C.gold2 : C.ivory, SANS, 900, 'center', 12);
      } else if (i === 2 && same > 0) {  // a dashed ghost at the level of bar 2: "the same two chocolates", no number yet
        const gy = lerp(bot, lerp(bot, topY, 14 / max), eo(same));
        ctx.save(); ctx.setLineDash([16, 12]); ctx.strokeStyle = rgba(C.gold2, .8); ctx.lineWidth = 6; rr(cx - 112, gy, 224, bot - gy - 4, 10); ctx.stroke(); ctx.restore();
        text('?', cx, gy - 70, 96, rgba(C.gold2, .8), SANS, 900, 'center', 10);
      } else text('?', cx, bot - 70, 96, 'rgba(243,234,216,.35)', SANS, 900);
      plate(cap, cx, bot + 62, 34, 1, i === 2 ? C.gold : C.bone, 300);
      // the chocolates given: 1, 2, 1 + 1
      const cy = bot + 150;
      if (i === 0) k7Choc(cx, cy, 1.1, -.2, K7CH[0]);
      else if (i === 1) { k7Choc(cx - 40, cy, 1.1, -.2, K7CH[0]); k7Choc(cx + 40, cy, 1.1, .2, K7CH[1]); }
      else { k7Choc(cx - 64, cy, 1.1, -.2, K7CH[0]); text('+', cx, cy + 2, 54, C.gold2, SANS, 900); k7Choc(cx + 64, cy, 1.1, .2, K7CH[1]); }
    });
    k7Equal(710, bot + 150, prog(t, ts - .05, ts + .25));
    flash(t, tk - .05, .4);
  },
  neden(V, L, t, lt) {
    // the why. Close on the woman with her second chocolate: a grey "KURAL" stamp is crossed out, a thought cloud
    // says "BİZE ÖZEL!". Cut on the second "özel": the gift goes one way (a gold arc with a chocolate) and on
    // "karşılıksız" the tip comes back (a green arc with a banknote), the waitress beaming
    K7.warm();
    const tr = wordTime(L, V.rule, .25), to = wordTime(L, V.key, .45);
    const second = L.words.filter(w => norm(w.w).startsWith('özel'))[1];
    const tc = second ? second.t0 : L.t0 + (L.t1 - L.t0) * .65, tp = wordTime(L, V.pay, .8);
    ctx.fillStyle = '#1a1010'; ctx.fillRect(0, 0, W, H);
    if (t < tc - .08) {
      const special = prog(t, to - .1, to + .25);
      drawSoft(.4, () => cam(540, 900, 1.5, () => k7Trattoria(t)), .5);
      cam(540, 960, lerp(1.0, 1.03, eio(prog(lt, 0, 3))), () => {
        glow(560, 960, 480, 'rgba(255,214,140,1)', .2 + .25 * special);
        figure(K7.bayan, 560, 2060, 1.22, t, { dir: -1, legs: 'hide', seed: 5, expr: special > .4 ? 'happy' : { ...EXPR.warm, look: [-.5, -.3] },
          front: { sh: .5, el: 2.25, hand: 'hold', prop: k7ChocIn(.5, 2.25, 1) }, back: { sh: .1, el: .2 } });
      });
      k7Stamp('KURAL', 820, 700, 54, prog(t, tr - .15, tr + .1), .08, '#6a6a72');
      const st = eo(prog(t, tr + .3, tr + .6));
      if (st > 0) { ctx.save(); ctx.translate(820, 700); ctx.rotate(.08); ctx.lineCap = 'round'; ctx.strokeStyle = C.red2; ctx.lineWidth = 12;
        ctx.shadowColor = 'rgba(210,35,47,.8)'; ctx.shadowBlur = 16; ctx.beginPath(); ctx.moveTo(-150, 4); ctx.lineTo(-150 + 300 * st, -4); ctx.stroke(); ctx.restore(); }
      thought(300, 640, 440, 220, 430, 800, special);
      if (special > .4) { text('BİZE', 300, 610, 56, K7INK, SANS, 900); text('ÖZEL!', 300, 676, 60, K7GOLD, SANS, 900); }
      sparks(300, 640, t, 20, 260, prog(t, to, to + .8));
      plate('Kural değil, özel jest', 540, 1330, 54, back(prog(t, to - .05, to + .3)), C.gold);
      return;
    }
    const give = prog(t, tc - .05, tc + .5), pay = prog(t, tp - .25, tp + .35);
    cam(540, 900, lerp(1.0, 1.03, eio(prog(t, tc, tc + 3))), () => {
      k7Trattoria(t);
      glow(540, 900, 520, 'rgba(255,214,140,1)', .2);
      chairBack(270, 1260, 1.1, '#4a2a18');
      k7Diner(K7.bayan, 270, 1230, .9, t, { seed: 5, expr: 'happy', front: { sh: .95, el: 1.85, hand: 'hold', prop: k7ChocIn(.95, 1.85, 2, 1.1) } });
      figure(K7.garsonK, 840, 1560, .95, t, { dir: -1, legs: 'hide', seed: 7, expr: pay > .5 ? 'happy' : 'warm',
        front: { sh: .15, el: .3 }, back: { sh: .1, el: .3 } });
      k7Table(-20, 1100, 1230, 300, { check: true });
    });
    const [gx, gy] = k7Arc(720, 640, 540, 450, 380, 700, give, C.gold2);
    if (give > 0 && give < 1) k7Choc(gx, gy - 40, 1.2, 0, K7CH[1]);
    const [px, py] = k7Arc(420, 1110, 570, 1250, 740, 1110, pay, K7GREEN);
    if (pay > 0) k7Note(px, py - 60, 120, -.1);
    if (give > .3) { ctx.save(); ctx.globalAlpha *= clamp((give - .3) * 3); text('ÖZEL JEST', 550, 490, 44, C.gold2, SANS, 900, 'center', 8); ctx.restore(); }
    if (pay > .3) { ctx.save(); ctx.globalAlpha *= clamp((pay - .3) * 3); text('KARŞILIK', 575, 1210, 44, '#a8e0a8', SANS, 900, 'center', 8); ctx.restore(); }
    plate('Özel iyilik karşılıksız kalmaz', 540, 1340, 50, back(prog(t, tp - .1, tp + .25)), C.gold);
  }
});
