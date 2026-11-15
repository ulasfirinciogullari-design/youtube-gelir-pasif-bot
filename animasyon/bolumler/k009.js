// k009 "Bir kuruş bile yeter" (Cialdini & Schroeder 1976, J. Pers. Soc. Psychol. 34(4), 599-604, experiment 1). An
// American Cancer Society door-to-door drive in a middle-income suburb: college-age solicitors in mixed pairs asked 84
// adults "Would you be willing to help by giving a donation?"; for half of them they added "Even a penny will help."
// 12 of 42 gave (28.6 %) without it, 21 of 42 (50 %) with it. The gift per donor barely moved ($1.44 vs $1.54), so the
// total rose from $18.55 to $30.34 (+64 %). The why (the authors): the phrase leaves no excuse for refusing, and a no
// now threatens one's image as a helpful person. The hook is today's apartment landing: a volunteer with a donation
// box at a neighbour's door, the door shut on the plain ask, opened again with "Bir kuruş bile yeter!".
const K9 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  dusk() { setLight(-.75, -.45, { rim: '#ffd9a0', rimA: .55, warm: '#ffd9a0', cool: '#3a3260' }); },
  // today: the volunteer in a green dress with the box, the neighbour in a brown cardigan and red slippers
  gonullu: { ...CAST.evsahibi, coat: '#2f8a5a', trim: '#f4efe4', sleeve: 'long', hairColor: '#2a1a12', skin: '#ebbd98', shoe: '#3a2a20',
    lips: '#b8404a', breeches: '#ebbd98', hose: '#ebbd98' },
  komsu: { ...CAST.uye, outfit: 'suit', coat: '#8a6440', trim: '#8a6440', shirt: '#e9e3d4', tie: '#e9e3d4', breeches: '#4a4a52', hose: '#4a4a52',
    shoe: '#9a2a2a', hair: 'short', hairColor: '#6e6862', brow: '#4a4440', skin: '#e0aa84', age: .9 },
  // 1970s: the solicitor in a rust dress with the collection can, her partner in a corduroy jacket, the resident in blue
  ars: { ...CAST.evsahibi, coat: '#c0602a', trim: '#f2e2c4', sleeve: 'long', hairColor: '#6a3a1e', skin: '#f0c4a2', shoe: '#4a2a1a' },
  ars2: { ...CAST.uye, outfit: 'suit', coat: '#a0763e', trim: '#a0763e', shirt: '#efe6d2', tie: '#efe6d2', breeches: '#3d5a86',
    hose: '#3d5a86', shoe: '#5a3a22', hair: 'side', hairColor: '#6a3e1e', skin: '#efc29e', age: .05, iris: '#5a4030' },
  ev: { ...CAST.evsahibi, coat: '#4a6aa8', trim: '#f4efe4', hairColor: '#c08a48', skin: '#f3c9a8', age: .5, shoe: '#3a2a2a' },
  order: [...Array(42).keys()].sort((a, b) => rnd(a * 7 + 11) - rnd(b * 7 + 11)),  // the order the doors light up in
  pt: {}  // screen points recorded while drawing (the box slot, a hand), for things that fly between them
};
const K9INK = '#1a1117', K9GOLD = '#9a6400', K9RED = '#b81d28', K9GREY = '#cfd6e4';
const k9Mark = (name, x, y) => { const p = ctx.getTransform().transformPoint(new DOMPoint(x, y)); K9.pt[name] = [p.x, p.y]; };

// ---------- small things ----------
// a prop held in the front hand, drawn upright whatever the arm does; o.under draws it before the hand
const k9Hold = (fn, sh, el, o = {}) => spec => {
  const a = sh + el, hx = 30 * Math.sin(a), hy = 30 * Math.cos(a);
  const draw = () => { ctx.save(); ctx.rotate(a); fn(hx, hy); ctx.restore(); };
  if (o.under) draw();
  hand(spec, o.hand || 'hold', 1.3 * Math.sqrt(spec.build.arm || 1));
  if (!o.under) draw();
};
// a banknote, centre (x, y), w wide
function k9Note(x, y, w, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  const h = w * .5;
  shape(() => rrp(-w / 2, -h / 2, w, h, 5), '#8cc48a', { shade: 5, hi: 4, line: 4, noRim: true,
    after: () => {
      ctx.strokeStyle = 'rgba(30,80,40,.6)'; ctx.lineWidth = 3; ctx.strokeRect(-w / 2 + 7, -h / 2 + 7, w - 14, h - 14);
      ctx.fillStyle = 'rgba(30,80,40,.45)'; ctx.beginPath(); ctx.arc(0, 0, h * .24, 0, Math.PI * 2); ctx.fill();
    } });
  ctx.restore();
}
function k9Coin(x, y, r, label) {
  shape(() => ctx.arc(x, y, r, 0, Math.PI * 2), '#e3b445', { shade: r * .2, hi: r * .15, line: Math.max(3, r * .12), noRim: true });
  ctx.save(); ctx.strokeStyle = 'rgba(120,80,10,.55)'; ctx.lineWidth = Math.max(2, r * .07); ctx.beginPath(); ctx.arc(x, y, r * .74, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
  if (label) { text(label[0], x, y - r * .12, r * .8, '#5a3a08', SANS, 900); if (label[1]) text(label[1], x, y + r * .5, r * .26, '#5a3a08', SANS, 900); }
}
// today's donation box: clear acrylic, a green lid with a slot, a white label; bottom centre (x, y), s 1 is 150 wide.
// fill 0..1 is the heap of notes inside; the slot is marked as 'slot'
function k9Box(x, y, s = 1, fill = .15) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  ctx.fillStyle = 'rgba(200,232,240,.55)'; rr(-75, -150, 150, 150, 10); ctx.fill();
  const hh = 22 + 96 * clamp(fill);
  ctx.save(); rr(-72, -147, 144, 144, 8); ctx.clip();
  shape(() => { ctx.moveTo(-76, 2); ctx.lineTo(-76, -hh * .7); ctx.quadraticCurveTo(-30, -hh * 1.12, 10, -hh); ctx.quadraticCurveTo(50, -hh * .86, 76, -hh * .72); ctx.lineTo(76, 2); ctx.closePath(); },
    '#8cc48a', { shade: 6, hi: 4, line: 3.5, noRim: true,
      after: () => { ctx.strokeStyle = 'rgba(30,80,40,.5)'; ctx.lineWidth = 3; for (let i = 0; i < 6; i++) { ctx.beginPath(); ctx.moveTo(-70 + i * 27, -4); ctx.lineTo(-58 + i * 27, -hh * .75); ctx.stroke(); } } });
  [[-42, -hh * .5], [24, -hh * .66], [50, -hh * .3]].forEach(([cx, cy]) => k9Coin(cx, cy, 12));
  ctx.restore();
  ctx.strokeStyle = INK; ctx.lineWidth = 5; rr(-75, -150, 150, 150, 10); ctx.stroke();
  ctx.fillStyle = 'rgba(255,255,255,.4)'; ctx.beginPath(); ctx.moveTo(-50, -146); ctx.lineTo(-36, -146); ctx.lineTo(-56, -6); ctx.lineTo(-68, -6); ctx.closePath(); ctx.fill();
  shape(() => rrp(-58, -122, 116, 50, 8), '#fbf6e8', { shade: 3, hi: 2, line: 4, noRim: true });
  text('BAĞIŞ', 0, -96, 29, '#c8362c', SANS, 900);
  shape(() => rrp(-84, -170, 168, 24, 8), '#2f8a5a', { shade: 4, hi: 4, line: 4.5, noRim: true });
  ctx.fillStyle = '#121014'; rr(-34, -163, 68, 9, 4); ctx.fill();
  k9Mark('slot', 0, -168);
  ctx.restore();
}
// a 1970s collection can: a red tin with a white band and a slot in the lid; bottom centre (x, y), s 1 is 110 wide
function k9Can(x, y, s = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  shape(() => { ctx.moveTo(-55, -140); ctx.lineTo(55, -140); ctx.lineTo(55, -8); ctx.quadraticCurveTo(0, 8, -55, -8); ctx.closePath(); }, '#c8362c',
    { shade: 8, hi: 6, line: 5, noRim: true, after: () => { ctx.fillStyle = '#f6efe0'; ctx.fillRect(-60, -106, 120, 56); } });
  text('BAĞIŞ', 0, -77, 27, '#c8362c', SANS, 900);
  shape(() => ctx.ellipse(0, -140, 55, 13, 0, 0, Math.PI * 2), '#c9c4ba', { shade: 3, hi: 3, line: 4.5, noRim: true });
  ctx.fillStyle = '#1a1416'; rr(-22, -144, 44, 7, 3); ctx.fill();
  k9Mark('can', 0, -144);
  ctx.restore();
}
// a speech bubble: rows [[text, colour, alpha]], size px, tail to (tx, ty); k 0..1 pops it in. The width is fixed by
// all rows, the height grows as rows fade in; o.bottom keeps the bottom edge in place
function k9Bubble(x, y, rows, size, k, tx, ty, o = {}) {
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
    ctx.save(); ctx.globalAlpha *= clamp(a); text(up(str), x, yy + size * .04, size, col || K9INK, SANS, 900); ctx.restore();
    yy += lh * ka;
  });
  ctx.restore();
}
// a rubber stamp, k 0..1 drops it from large onto the frame
function k9Stamp(label, x, y, size, k, rot = -.1, col = C.red) {
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
// "≈" drawn as two waves, so no font glyph is needed
function k9Approx(x, y, k, col = C.gold2) {
  if (k <= 0) return;
  const s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.lineCap = 'round'; ctx.strokeStyle = col; ctx.lineWidth = 10;
  ctx.shadowColor = rgba(col, .7); ctx.shadowBlur = 16;
  [-13, 13].forEach(dy => { ctx.beginPath(); ctx.moveTo(-30, dy); ctx.bezierCurveTo(-13, dy - 15, -2, dy - 15, 0, dy); ctx.bezierCurveTo(2, dy + 15, 13, dy + 15, 30, dy); ctx.stroke(); });
  ctx.restore();
}
// a thick arrow from x0 to x1 at y, drawn up to k 0..1
function k9Arrow(x0, x1, y, k, col = C.gold2, w = 14) {
  if (k <= 0) return;
  const x = lerp(x0, x1, eo(clamp(k)));
  ctx.save(); ctx.lineCap = 'round'; ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = w; ctx.shadowColor = rgba(col, .7); ctx.shadowBlur = 16;
  ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x - w, y); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x + w * .8, y); ctx.lineTo(x - w * 1.4, y - w * 1.6); ctx.lineTo(x - w * 1.4, y + w * 1.6); ctx.closePath(); ctx.fill();
  ctx.restore();
}
// a banknote flying along an arc from the point named a to the point named b, p 0..1 (screen coordinates)
function k9Fly(a, b, p, w = 70, lift = 120) {
  const A = K9.pt[a], B = K9.pt[b];
  if (!A || !B || p <= 0 || p >= 1) return;
  const e = eio(p), x = lerp(A[0], B[0], e), y = lerp(A[1], B[1], e) - Math.sin(Math.PI * e) * lift;
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
  k9Note(x, y - 10 * (1 - e), w * lerp(1, .7, e), lerp(-.3, 1.4, e));
  ctx.restore();
}
const k9Count = (from, to, k) => lerp(from, to, eo(clamp(k)));

// ---------- today: the apartment landing ----------
const K9D = { x: 610, y: 600, w: 300, h: 800 };  // the neighbour's door opening
function k9Landing(t) {
  const D = K9D;
  // cream wall above, oil-painted green below a dark band, the way stairwells are painted
  let g = ctx.createLinearGradient(0, 0, 0, 1040); g.addColorStop(0, '#cdbf9e'); g.addColorStop(1, '#e8ddc4');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1040);
  g = ctx.createLinearGradient(0, 1040, 0, 1400); g.addColorStop(0, '#5f8a74'); g.addColorStop(1, '#46695a');
  ctx.fillStyle = g; ctx.fillRect(0, 1040, W, 360);
  ctx.fillStyle = '#34503f'; ctx.fillRect(0, 1030, W, 14);
  // the stair rail going down on the far left
  ctx.save(); ctx.strokeStyle = '#3a2a20'; ctx.lineCap = 'round'; ctx.lineWidth = 16;
  ctx.beginPath(); ctx.moveTo(-20, 1010); ctx.lineTo(150, 1120); ctx.stroke();
  ctx.lineWidth = 7; for (let i = 0; i < 5; i++) { const x = -10 + i * 38, y = 1004 + i * 25; ctx.beginPath(); ctx.moveTo(x, y + 14); ctx.lineTo(x, y + 330); ctx.stroke(); }
  ctx.restore();
  // a wall lamp left of the door, its warm pool on the wall
  glow(470, 640, 300, 'rgba(255,226,170,1)', .5);
  shape(() => rrp(458, 600, 24, 22, 4), '#3a3a34', { shade: 3, hi: 2, line: 4, noRim: true });
  shape(() => { ctx.moveTo(430, 620); ctx.lineTo(510, 620); ctx.quadraticCurveTo(512, 690, 470, 698); ctx.quadraticCurveTo(428, 690, 430, 620); ctx.closePath(); }, '#fbeec4',
    { shade: 6, hi: 4, line: 4.5, noRim: true });
  // the floor: terrazzo with speckles and a skirting board
  g = ctx.createLinearGradient(0, 1400, 0, H); g.addColorStop(0, '#bba98c'); g.addColorStop(1, '#4e4032');
  ctx.fillStyle = g; ctx.fillRect(0, 1400, W, H - 1400);
  for (let i = 0; i < 90; i++) { ctx.fillStyle = ['rgba(90,70,50,.35)', 'rgba(240,230,210,.35)', 'rgba(140,60,40,.25)'][i % 3]; ctx.fillRect(rnd(i) * W, 1410 + rnd(i + 40) * 500, 4 + rnd(i + 7) * 5, 3 + rnd(i + 9) * 3); }
  shape(() => rrp(-10, 1388, W + 20, 16, 2), '#2e3e34', { shade: 3, hi: 2, line: 4 });
  // the door frame and the hall behind it: a warm light, a coat on a hook, a runner
  shape(() => rrp(D.x - 24, D.y - 24, D.w + 48, D.h + 24, 4), '#efe7d4', { shade: 8, hi: 4, line: 5 });
  g = ctx.createLinearGradient(0, D.y, 0, D.y + D.h); g.addColorStop(0, '#3a2a22'); g.addColorStop(1, '#1e1612');
  ctx.fillStyle = g; ctx.fillRect(D.x, D.y, D.w, D.h);
  glow(D.x + D.w * .35, D.y + 260, 300, 'rgba(255,200,130,1)', .55);
  shape(() => { ctx.moveTo(D.x + 40, D.y + 210); ctx.quadraticCurveTo(D.x + 10, D.y + 420, D.x + 30, D.y + 520); ctx.lineTo(D.x + 110, D.y + 520); ctx.quadraticCurveTo(D.x + 120, D.y + 400, D.x + 80, D.y + 210); ctx.closePath(); },
    '#6a3a2a', { shade: 8, hi: 4, line: 4, noRim: true });
  ctx.fillStyle = '#7a2a2a'; ctx.fillRect(D.x, D.y + D.h - 30, D.w, 30);
  // the doormat
  shape(() => { ctx.moveTo(D.x - 6, 1402); ctx.lineTo(D.x + D.w + 6, 1402); ctx.lineTo(D.x + D.w + 46, 1452); ctx.lineTo(D.x - 46, 1452); ctx.closePath(); }, '#9a5a2e',
    { shade: 4, hi: 3, line: 4.5, noRim: true, after: () => { ctx.strokeStyle = 'rgba(60,30,10,.35)'; ctx.lineWidth = 3; for (let i = 0; i < 9; i++) { ctx.beginPath(); ctx.moveTo(D.x - 30 + i * 44, 1404); ctx.lineTo(D.x - 40 + i * 46, 1450); ctx.stroke(); } } });
  // the shoe rack right of the door with slippers and a pair of shoes on it
  shape(() => rrp(938, 1220, 150, 180, 6), '#a07448', { shade: 8, hi: 5, line: 5, after: () => { ctx.fillStyle = 'rgba(0,0,0,.2)'; ctx.fillRect(938, 1300, 150, 6); } });
  [[968, 1222, '#2f6aa8'], [1030, 1222, '#2f6aa8']].forEach(([x, y, c]) => shape(() => { ctx.moveTo(x - 26, y); ctx.lineTo(x + 26, y); ctx.quadraticCurveTo(x + 30, y - 22, x + 4, y - 24); ctx.lineTo(x - 26, y - 18); ctx.closePath(); }, c, { shade: 3, hi: 3, line: 4, noRim: true }));
  [[975, 1296], [1040, 1296]].forEach(([x, y]) => shape(() => { ctx.moveTo(x - 30, y); ctx.lineTo(x + 28, y); ctx.quadraticCurveTo(x + 30, y - 26, x, y - 30); ctx.lineTo(x - 30, y - 28); ctx.closePath(); }, '#3a2418', { shade: 3, hi: 3, line: 4, noRim: true }));
  // a potted plant in the corner
  shape(() => { ctx.moveTo(196, 1400); ctx.lineTo(176, 1300); ctx.lineTo(296, 1300); ctx.lineTo(276, 1400); ctx.closePath(); }, '#b8562e', { shade: 6, hi: 4, line: 5 });
  [[-.7, 150], [-.3, 190], [.1, 200], [.5, 170], [.9, 140]].forEach(([a, l], i) => {
    ctx.save(); ctx.translate(236, 1300); ctx.rotate(a + Math.sin(t * 1.2 + i) * .03);
    shape(() => { ctx.moveTo(0, 0); ctx.quadraticCurveTo(-26, -l * .5, 0, -l); ctx.quadraticCurveTo(26, -l * .5, 0, 0); }, i % 2 ? '#3f7a3e' : '#4f8a46', { shade: 5, hi: 3, line: 4, noRim: true });
    ctx.restore();
  });
}
// the door leaf, hinged on the right and opening inward: open 0 covers the doorway, 1 leaves a narrow strip
function k9AptDoor(open) {
  const D = K9D, pw = D.w * (1 - .8 * clamp(open)), x = D.x + D.w - pw;
  shape(() => rrp(x, D.y, pw, D.h, 3), '#7a4a2e', { shade: 10, hi: 6, line: 5,
    after: () => { ctx.strokeStyle = 'rgba(0,0,0,.3)'; ctx.lineWidth = 4; ctx.strokeRect(x + pw * .13, D.y + 70, pw * .74, 290); ctx.strokeRect(x + pw * .13, D.y + 430, pw * .74, 300); } });
  const k = clamp(1 - open * 2.5);
  if (k > 0) {
    ctx.save(); ctx.globalAlpha *= k;
    shape(() => rrp(x + pw / 2 - 34, D.y + 112, 68, 56, 6), '#d8b04a', { shade: 3, hi: 3, line: 4, noRim: true });
    text('7', x + pw / 2, D.y + 142, 40, '#3a2410', SANS, 900);
    circle(x + pw / 2, D.y + 222, 9, '#141014');
    shape(() => rrp(x + 16, D.y + 410, 56, 16, 7), '#d8b04a', { shade: 3, hi: 3, line: 4, noRim: true });
    ctx.restore();
  }
}
// the landing scene: the neighbour in his doorway, the volunteer with the box on the left. o: open, kExpr, kTalk,
// kFront (his arm), gExpr, gTalk, fill (the box), shake (the slam)
function k9DoorShot(t, o = {}) {
  const open = o.open ?? 1;
  k9Landing(t);
  if (open > .25) figure(K9.komsu, 735, 1408, .7, t, { dir: -1, legs: 'stand', seed: 4, expr: o.kExpr || { ...EXPR.neutral, look: [.5, .1] }, talk: o.kTalk || 0,
    nod: o.kNod || 0, headTilt: o.kTilt || 0, front: o.kFront || { sh: .15, el: .35 }, back: { sh: .1, el: .3 }, noShadow: true });
  k9AptDoor(open);
  const sh = .78, el = .55;
  figure(K9.gonullu, 330, 1500, .74, t, { dir: 1, legs: 'stand', seed: 3, expr: o.gExpr || 'warm', talk: o.gTalk || 0, lean: o.gLean || 0,
    front: { sh, el, hand: 'hold', prop: k9Hold((hx, hy) => k9Box(hx + 8, hy + 14, 1.5, o.fill ?? .15), sh, el) }, back: { sh: .1, el: .3 } });
}

// ---------- 1970s: a front porch at golden hour ----------
const K9P = { x: 720, y: 560, w: 250, h: 545 };  // the front door in the porch shot
function k9Porch(t) {
  const D = K9P;
  let g = ctx.createLinearGradient(0, 0, 0, 1105); g.addColorStop(0, '#c08a5c'); g.addColorStop(1, '#ecd0a2');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1105);
  ctx.strokeStyle = 'rgba(110,70,40,.14)'; ctx.lineWidth = 3;
  for (let y = 430; y < 1100; y += 34) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  // low sun falling across the wall
  ctx.save(); ctx.globalAlpha = .16; ctx.fillStyle = '#fff0c8'; ctx.beginPath(); ctx.moveTo(0, 420); ctx.lineTo(420, 420); ctx.lineTo(160, 1105); ctx.lineTo(0, 1105); ctx.fill(); ctx.restore();
  pictureWindow(70, 590, 340, 250, t, { lit: .85 });
  // the porch light
  glow(1020, 640, 150, 'rgba(255,214,140,1)', .6);
  shape(() => rrp(1004, 600, 32, 54, 6), '#f8e2a8', { shade: 3, hi: 3, line: 4, noRim: true });
  // the door frame and the hall
  shape(() => rrp(D.x - 18, D.y - 18, D.w + 36, D.h + 18, 3), DUSK.trim, { shade: 6, hi: 4, line: 5 });
  g = ctx.createLinearGradient(0, D.y, 0, D.y + D.h); g.addColorStop(0, '#3a2620'); g.addColorStop(1, '#1c1210');
  ctx.fillStyle = g; ctx.fillRect(D.x, D.y, D.w, D.h);
  glow(D.x + D.w * .4, D.y + 200, 220, 'rgba(255,200,130,1)', .5);
  // shrubs under the window
  [[110, 1105, 90], [300, 1105, 80]].forEach(([x, y, r], i) =>
    shape(() => { ctx.moveTo(x - r, y); ctx.bezierCurveTo(x - r, y - r * 1.1, x + r, y - r * 1.1, x + r, y); ctx.closePath(); }, i % 2 ? '#3f5a2e' : '#4a6634', { shade: 12, hi: 7, line: 5 }));
  g = ctx.createLinearGradient(0, 1105, 0, H); g.addColorStop(0, '#a89480'); g.addColorStop(1, '#3a3028');
  ctx.fillStyle = g; ctx.fillRect(0, 1105, W, H - 1105);
  shape(() => rrp(-10, 1096, W + 20, 14, 2), '#8a7a66', { shade: 3, hi: 2, line: 4 });
}
function k9PorchDoor(open) {
  const D = K9P, pw = D.w * (1 - .8 * clamp(open)), x = D.x + D.w - pw;
  shape(() => rrp(x, D.y, pw, D.h, 2), '#b8463e', { shade: 8, hi: 5, line: 5,
    after: () => { ctx.strokeStyle = 'rgba(0,0,0,.25)'; ctx.lineWidth = 3; ctx.strokeRect(x + pw * .14, D.y + 40, pw * .72, 200); ctx.strokeRect(x + pw * .14, D.y + 290, pw * .72, 210); } });
  if (open < .4) circle(x + 22, D.y + 290, 8, '#e8c25a');
}
// the porch two-shot over the board: the resident in her doorway, the solicitor with the can, her partner behind.
// o: open, evExpr, evFront, evTalk, nod, tilt, arsExpr, arsTalk
function k9TwoShot(t, o = {}) {
  k9Porch(t);
  figure(K9.ev, 838, 1112, .54, t, { dir: -1, legs: 'stand', seed: 5, expr: o.evExpr || { ...EXPR.neutral, look: [.5, 0] }, talk: o.evTalk || 0,
    nod: o.nod || 0, headTilt: o.tilt || 0, front: o.evFront || { sh: .2, el: .4 }, back: { sh: .1, el: .3 }, noShadow: true });
  k9PorchDoor(o.open ?? 1);
  figure(K9.ars2, 70, 1180, .52, t, { dir: 1, legs: 'stand', seed: 8, expr: o.ars2Expr || { ...EXPR.calm, look: [.6, 0] },
    front: { sh: .45, el: 1.3, hand: 'hold', prop: () => clipboard(0, 50, 80, t, .2) }, back: { sh: .1, el: .3 } });
  const sh = .8, el = .55;
  figure(K9.ars, 300, 1170, .57, t, { dir: 1, legs: 'stand', seed: 3, expr: o.arsExpr || 'warm', talk: o.arsTalk || 0,
    front: { sh, el, hand: 'hold', prop: k9Hold((hx, hy) => k9Can(hx + 6, hy + 12, 1.45), sh, el) }, back: { sh: .1, el: .3 } });
}
// her question, between the two heads; extra rows fade in under it
function k9Ask(rows, k, o = {}) { k9Bubble(520, 0, rows, 38, k, 400, 720, { bottom: 690, ...o }); }
function k9Reply(word, good, k) { k9Bubble(905, 548, [[word, good ? K9GOLD : K9RED]], 44, k, 860, 626, { fill: good ? '#fff2c8' : '#fbf6e8', border: good ? C.gold : C.red }); }

// ---------- the board: of the 42 people who opened the door, how many gave ----------
const K9B = { y: 1096, h: 318 };
function k9DoorIcon(x, by, s, col, on) {
  ctx.save(); ctx.translate(x, by); ctx.scale(s, s);
  ctx.fillStyle = col; rr(-10, -32, 20, 32, 3); ctx.fill();
  if (on) circle(5, -15, 2.6, '#6a4a10');
  ctx.restore();
}
// rows: [{label, lit (0..n, may be fractional while counting) or null, n, col, hot, a}]
function k9Board(rows) {
  const { y, h } = K9B;
  shape(() => rrp(36, y, 1008, h, 18), 'rgba(14,12,18,.93)', { flat: true, line: 5 });
  ctx.save(); ctx.strokeStyle = rgba(C.gold, .7); ctx.lineWidth = 3; rr(46, y + 10, 988, h - 20, 12); ctx.stroke(); ctx.restore();
  text('KAPIYI AÇAN 42 KİŞİDEN KAÇI PARA VERDİ?', 540, y + 40, 28, C.gold, SANS, 900, 'center', 0, C.ink, 2);
  rows.forEach((r, j) => {
    if (!r) return;
    const ry = y + 86 + j * 118;
    ctx.save(); ctx.globalAlpha *= r.a ?? 1;
    if (r.hot) { ctx.fillStyle = rgba(C.gold, .12); rr(56, ry - 24, 968, 112, 12); ctx.fill(); }
    text(up(r.label), 72, ry, 25, C.ivory, SANS, 900, 'left', 0, C.ink, 1);
    for (let i = 0; i < 42; i++) {
      const slot = K9.order.indexOf(i), k = r.lit == null ? 0 : clamp(r.lit - slot), on = k > .5;
      k9DoorIcon(88 + (i % 21) * 30, ry + 52 + Math.floor(i / 21) * 40, 1 + .4 * Math.sin(Math.PI * k), on ? r.col : 'rgba(243,234,216,.2)', on);
    }
    if (r.lit == null) text('?', 884, ry + 42, 72, 'rgba(243,234,216,.3)', SANS, 900);
    else {
      const n = Math.min(r.n, Math.floor(r.lit + .5));
      text('%' + Math.round(100 * n / 42), 884, ry + 30, 64, r.lit > .2 ? r.col : 'rgba(243,234,216,.35)', SANS, 900, 'center', 8);
      text(n + ' / 42', 884, ry + 80, 28, C.ivory, SANS, 900, 'center', 5);
    }
    ctx.restore();
  });
}
const K9ROW1 = 'Sadece “Bağış yapar mısınız?”', K9ROW2 = '+ “Bir kuruş bile yeter”';

// ---------- 1970s: the street ----------
// a small house, base at gy, centre x, w wide; open 0..1 swings its door in and shows someone in it
function k9House(x, gy, w, t, o = {}) {
  const h = 250, open = o.open || 0;
  shape(() => { ctx.moveTo(x - w / 2 - 34, gy - h + 6); ctx.lineTo(x, gy - h - 120); ctx.lineTo(x + w / 2 + 34, gy - h + 6); ctx.closePath(); }, o.roof || DUSK.roof,
    { shade: 14, hi: 7, line: 5, after: () => { ctx.strokeStyle = 'rgba(0,0,0,.16)'; ctx.lineWidth = 3; for (let y = gy - h - 100; y < gy - h + 6; y += 20) { ctx.beginPath(); ctx.moveTo(x - w, y); ctx.lineTo(x + w, y); ctx.stroke(); } } });
  shape(() => rrp(x - w / 2, gy - h, w, h, 3), o.wall || DUSK.stucco, { shade: 18, hi: 8, line: 5 });
  pictureWindow(x - w / 2 + 24, gy - h + 54, w * .4, 104, t, { lit: .8, small: true });
  const dw = 86, dh = 176, dx = x + w * .14, dy = gy - dh;
  shape(() => rrp(dx - 10, dy - 10, dw + 20, dh + 10, 3), DUSK.trim, { shade: 5, hi: 3, line: 4.5 });
  ctx.fillStyle = '#1c1418'; ctx.fillRect(dx, dy, dw, dh);
  if (open > 0) {
    glow(dx + dw / 2, dy + dh * .5, 110, 'rgba(255,200,130,1)', .55 * open);
    if (open > .5) figure(o.who || K9.ev, dx + dw * .36, gy + 2, .2, t, { dir: -1, legs: 'stand', seed: o.seed || 1, expr: 'neutral', front: { sh: .2, el: .4 }, back: { sh: .1, el: .3 }, noShadow: true });
  }
  shape(() => rrp(dx + dw * .8 * open, dy, dw * (1 - .8 * open), dh, 2), o.door || '#b8463e', { shade: 6, hi: 4, line: 4.5 });
  glow(dx + dw + 26, dy + 26, 56, 'rgba(255,214,140,1)', .6);
  shape(() => rrp(dx + dw + 18, dy + 14, 14, 22, 3), '#f8e2a8', { shade: 2, hi: 2, line: 3, noRim: true });
  [[x - w / 2 + 50, 60], [x - w / 2 + 160, 50]].forEach(([sx, r], i) =>
    shape(() => { ctx.moveTo(sx - r, gy); ctx.bezierCurveTo(sx - r, gy - r * 1.1, sx + r, gy - r * 1.1, sx + r, gy); ctx.closePath(); }, i % 2 ? '#3f5a2e' : '#4a6634', { shade: 10, hi: 6, line: 4.5 }));
  return [dx + dw / 2, dy];
}
const K9S = { gy: 1190, houses: [[200, '#ecd8b4', '#b8463e'], [620, '#d8c0a0', '#2f5a7a'], [1040, '#e6cfae', '#3f6a3a'], [1460, '#ecd8b4', '#8a4a2a']] };
// duskSky only paints x 0..W; the street shot pans right, so the sky and the hills here run wider (else the
// uncovered strip keeps the previous frames)
function k9Sky(t, horizon) {
  const g = ctx.createLinearGradient(0, 0, 0, horizon);
  g.addColorStop(0, DUSK.skyTop); g.addColorStop(.55, DUSK.skyMid); g.addColorStop(1, DUSK.skyLow);
  ctx.fillStyle = g; ctx.fillRect(-600, -300, 3000, horizon + 304);
  glow(180, horizon - 120, 420, 'rgba(255,214,150,1)', .55);
  circle(180, horizon - 110, 60, 'rgba(255,236,200,.85)');
  ctx.fillStyle = DUSK.hill; ctx.beginPath(); ctx.moveTo(-600, horizon);
  for (let x = -600; x <= 2400; x += 60) ctx.lineTo(x, horizon - 70 - 40 * Math.sin(x * .006 + 1) - 20 * Math.sin(x * .017));
  ctx.lineTo(2400, horizon); ctx.closePath(); ctx.fill();
}
function k9Street(t, opens) {
  k9Sky(t, 1000);
  let g = ctx.createLinearGradient(0, 1000, 0, K9S.gy); g.addColorStop(0, '#3e4a34'); g.addColorStop(1, DUSK.lawn2);
  ctx.fillStyle = g; ctx.fillRect(-600, 1000, 3000, K9S.gy - 1000);
  palm(-60, 1010, 380, t, .9, 3); palm(830, 1000, 420, t, 1, 1); palm(1700, 1010, 380, t, .9, 2);
  const doors = K9S.houses.map(([x, wall, door], i) => k9House(x, K9S.gy, 340, t, { wall, door, open: opens[i] || 0, seed: i + 2 }));
  g = ctx.createLinearGradient(0, K9S.gy, 0, 1300); g.addColorStop(0, DUSK.lawn2); g.addColorStop(1, DUSK.lawn);
  ctx.fillStyle = g; ctx.fillRect(-600, K9S.gy, 3000, 110);
  ctx.fillStyle = '#d9c6aa'; ctx.fillRect(-600, 1300, 3000, 90);
  ctx.fillStyle = '#9a8a78'; ctx.fillRect(-600, 1390, 3000, 14);
  ctx.fillStyle = DUSK.road; ctx.fillRect(-600, 1404, 3000, 600);
  ctx.save(); ctx.strokeStyle = 'rgba(240,220,160,.55)'; ctx.lineWidth = 8; ctx.setLineDash([60, 50]); ctx.beginPath(); ctx.moveTo(-600, 1700); ctx.lineTo(2400, 1700); ctx.stroke(); ctx.restore();
  return doors;
}

Object.assign(SAHNE, {
  kanca(V, L, t, lt) {
    // the hook, from the very first frame: a volunteer with a donation box at a neighbour's door, "Yardım eder
    // misiniz?". On "deme" he says "Şimdi olmaz." and shuts the door on her (the red plate). On "Sonuna" it rewinds and
    // the door opens again; on "kuruş" her bubble gains "Bir kuruş bile yeter!", he smiles, and on "ekle" he drops a
    // banknote, not a penny, into the box
    K9.day();
    const tn = wordTime(L, V.no, .4), tb = wordTime(L, V.back, .55), ty = wordTime(L, V.yes, .72), tg = wordTime(L, V.give, .9);
    const slam = prog(t, tn - .18, tn + .06), again = t >= tb - .12, reopen = prog(t, tb - .12, tb + .2);
    const open = again ? eo(reopen) : 1 - ei(slam);
    const add = prog(t, ty - .25, ty + .1), warm = prog(t, ty + .05, ty + .4);
    const reach = prog(t, ty + .15, tg - .25), give = prog(t, tg - .3, tg), drop = prog(t, tg - .05, tg + .45);
    const shake = slam > 0 && slam < 1 ? 0 : (t > tn + .06 && t < tn + .3 ? Math.sin(t * 90) * 6 * (1 - prog(t, tn + .06, tn + .3)) : 0);
    const shut = !again && slam >= 1;
    const kFront = !again ? { sh: lerp(.15, .5, eo(prog(t, tn - .5, tn - .2))), el: lerp(.35, 1.6, eo(prog(t, tn - .5, tn - .2))), hand: 'palm' }
      : give > 0 ? { sh: lerp(.3, 1.05, eo(give)), el: lerp(1.5, .35, eo(give)), hand: 'hold', prop: drop > 0 ? null : k9Hold((hx, hy) => { k9Note(hx + 14, hy - 8, 88, -.25); k9Mark('hand', hx + 14, hy - 8); }, lerp(.3, 1.05, eo(give)), lerp(1.5, .35, eo(give))) }
      : reach > 0 ? { sh: lerp(.15, .3, eo(reach)), el: lerp(.35, 1.5, eo(reach)), hand: 'hold' } : { sh: .15, el: .35 };
    cam(540 + shake, 900, lerp(1.0, 1.04, eio(prog(lt, 0, 5))), () => k9DoorShot(t, {
      open, fill: .15 + .3 * eo(prog(t, tg + .3, tg + .6)),
      kExpr: again ? (warm > .4 ? (drop > .6 ? 'happy' : 'warm') : { ...EXPR.neutral, look: [.5, .1] }) : t > tn - .7 ? { ...EXPR.displeased, look: [.5, 0] } : { ...EXPR.neutral, look: [.5, .1] },
      kTalk: (!again && t > tn - .75 && t < tn - .1) || (warm > 0 && warm < 1) ? .55 : 0, kNod: warm > 0 && warm < 1 ? Math.sin(t * 10) * .05 : 0, kFront,
      gExpr: shut ? { ...EXPR.confused, curve: -.35 } : drop > .5 ? 'happy' : 'warm', gTalk: t < .9 || (add > 0 && add < 1) ? talk(t, L) * .5 + .15 : 0 }));
    // the slam: dust lines out of the door gap
    if (shut && t < tn + .45) {
      const k = prog(t, tn + .02, tn + .45);
      ctx.save(); ctx.globalAlpha *= 1 - k; ctx.strokeStyle = C.ivory; ctx.lineWidth = 7; ctx.lineCap = 'round';
      [[600, 760, -1], [600, 1000, -1], [600, 1240, -1], [920, 800, 1], [920, 1180, 1]].forEach(([x, y, d]) => { ctx.beginPath(); ctx.moveTo(x + d * (16 + 30 * k), y); ctx.lineTo(x + d * (56 + 50 * k), y - 10); ctx.stroke(); });
      ctx.restore();
    }
    k9Fly('hand', 'slot', drop, 76);
    if (drop >= 1) sparks(K9.pt.slot ? K9.pt.slot[0] : 470, K9.pt.slot ? K9.pt.slot[1] : 1100, t, 18, 160, prog(t, tg + .4, tg + 1.1));
    k9Bubble(300, 0, [['Yardım eder', K9INK], ['misiniz?', K9INK], ['Bir kuruş bile', K9GOLD, add], ['yeter!', K9GOLD, add]], 42, 1, 330, 860,
      { bottom: 790, fill: add > .5 ? '#fff2c8' : null, border: add > .5 ? C.gold : null });
    k9Bubble(820, 660, [['Şimdi olmaz.', K9RED]], 42, prog(t, tn - .75, tn - .5) * (1 - prog(t, tn + .15, tn + .4)), 760, 740);
    k9Bubble(800, 660, [['Tabii, buyrun!', K9GOLD]], 42, warm, 740, 740, { fill: '#fff2c8', border: C.gold });
    plate(V.no_label, 540, 520, 60, back(prog(t, tn - .05, tn + .3)) * (1 - prog(t, ty - .35, ty - .15)), C.red);
    if (add > 0) glow(540, 1340, 320, 'rgba(232,181,74,1)', .3 * add);
    plate(V.yes_label, 540, 1340, 60, back(prog(t, ty - .1, ty + .25)), C.gold);
    flash(t, tb - .12, .6);
  },
  deney(V, L, t, lt) {
    // the 1970s: a street of houses at golden hour; the two solicitors walk past them, a can and a clipboard in hand,
    // and the doors open one after another ("kapı kapı"); on "istedi" she holds the can out at the third door
    K9.dusk();
    const td = wordTime(L, V.door, .55), tk = wordTime(L, V.key, .9), dur = L.t1 - L.t0;
    const walk = prog(t, L.t0, tk - .2), px = lerp(170, 860, eio(walk)), stop = prog(t, tk - .25, tk + .1);
    const opens = [prog(t, td - .7, td - .4) * (1 - prog(t, td - .1, td + .2)), prog(t, td - .05, td + .25) * (1 - prog(t, td + .55, td + .85)), prog(t, td + .4, td + .7), 0];
    const cx = lerp(540, 820, eio(prog(t, L.t0, L.t1)));
    cam(cx, 960, 1.0, () => {
      const doors = k9Street(t, opens);
      // "TOK TOK" over each door as it is knocked
      [[0, td - .9], [1, td - .25], [2, td + .2]].forEach(([i, at]) => {
        const k = prog(t, at, at + .25) * (1 - prog(t, at + .55, at + .8));
        if (k > 0) { ctx.save(); ctx.translate(doors[i][0], doors[i][1] - 60); ctx.scale(back(k), back(k)); text('TOK TOK', 0, 0, 34, C.ivory, SANS, 900, 'center', 8); ctx.restore(); }
      });
      const walking = walk > 0 && walk < 1;
      figure(K9.ars2, px - 150, 1392, .5, t, { dir: 1, legs: walking ? 'walk' : 'stand', phase: t * 9 + 1, seed: 8, expr: 'warm',
        front: { sh: .45, el: 1.3, hand: 'hold', prop: () => clipboard(0, 50, 80, t, .2) }, back: { sh: walking ? .3 * Math.sin(t * 9) : .1, el: .3 } });
      const sh = lerp(.4, .95, eo(stop)), el = lerp(.9, .45, eo(stop));
      figure(K9.ars, px, 1398, .54, t, { dir: 1, legs: walking ? 'walk' : 'stand', phase: t * 9, seed: 3, expr: stop > .5 ? 'happy' : 'warm', talk: stop > 0 ? .5 : 0,
        front: { sh, el, hand: 'hold', prop: k9Hold((hx, hy) => k9Can(hx + 6, hy + 12, 1.45), sh, el) }, back: { sh: walking ? -.3 * Math.sin(t * 9) : .1, el: .3 } });
    });
    plate(V.year, 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)));
    plate('Kapı kapı bağış', 540, 1330, 50, back(prog(t, td - .1, td + .25)), C.gold);
  },
  sade(V, L, t, lt) {
    // the plain ask at a 1970s front door: "Bağış yapar mısınız?". On "dediklerinde" she says "Hayır." with a wave and
    // starts to close the door; on "%29" the first row of the board lights 12 of the 42 doors
    K9.dusk();
    const tn = wordTime(L, V.no, .45), tk = wordTime(L, V.key, .75);
    const no = prog(t, tn - .1, tn + .25), shut = prog(t, tn + .35, tn + 1.0), count = prog(t, tk - .2, tk + .5);
    cam(540, 900, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => k9TwoShot(t, {
      open: 1 - .5 * eio(shut),
      evExpr: no > .4 ? { ...EXPR.displeased, look: [.5, 0] } : { ...EXPR.neutral, look: [.5, 0] }, evTalk: no > 0 && no < 1 ? .5 : 0,
      tilt: no > 0 ? Math.sin(t * 14) * .04 * no * (1 - prog(t, tn + .4, tn + .8)) : 0,
      evFront: no > 0 ? { sh: lerp(.2, .5, eo(no)), el: lerp(.4, 2.0, eo(no)), hand: 'palm' } : null,
      arsTalk: t < tn ? talk(t, L) * .6 : 0, arsExpr: no > .5 ? { ...EXPR.surprised, curve: -.2 } : 'warm' }));
    k9Ask([['Bağış yapar', K9INK], ['mısınız?', K9INK]], prog(lt, 0, .3));
    k9Reply('Hayır.', false, no);
    k9Board([{ label: K9ROW1, lit: count > 0 ? count * 12 : null, n: 12, col: K9GREY, hot: true }, { label: K9ROW2, lit: null, a: .45 }]);
    flash(t, tk - .05, .25);
  },
  kurus(V, L, t, lt) {
    // the same ask with one more sentence: on "kuruş" the bubble gains "Bir kuruş bile yeter!", the resident's face
    // opens, "Tabii!", and on "yeter" she takes a banknote out; the board's second row is the one waiting
    K9.dusk();
    const ty = wordTime(L, V.yes, .72), tok = wordTime(L, V.ok, .9);
    const add = prog(t, ty - .3, ty + .05), warm = prog(t, ty + .05, ty + .4), give = prog(t, tok - .1, tok + .35);
    const f = give > 0 ? { sh: lerp(.2, .75, eo(give)), el: lerp(1.6, .6, eo(give)), hand: 'hold' } : warm > 0 ? { sh: .2, el: lerp(.4, 1.6, eo(warm)), hand: 'hold' } : { sh: .2, el: .4 };
    if (give > 0) f.prop = k9Hold((hx, hy) => k9Note(hx + 12, hy - 8, 80, -.2), f.sh, f.el);
    cam(540, 900, lerp(1.03, 1.0, eio(prog(lt, 0, 4))), () => k9TwoShot(t, {
      evExpr: warm > .4 ? 'warm' : add > .3 ? { ...EXPR.surprised, curve: .2 } : { ...EXPR.neutral, look: [.5, 0] },
      nod: warm > 0 && warm < 1 ? Math.sin(t * 10) * .05 : 0, evTalk: warm > 0 && warm < 1 ? .5 : 0, evFront: f,
      arsTalk: t < ty + .5 ? talk(t, L) * .6 : 0, arsExpr: warm > .5 ? 'happy' : 'warm' }));
    k9Ask([['Bağış yapar', K9INK], ['mısınız?', K9INK], ['Bir kuruş bile', K9GOLD, add], ['yeter!', K9GOLD, add]], 1,
      { fill: add > .5 ? '#fff2c8' : null, border: add > .5 ? C.gold : null });
    if (add > 0) sparks(520, 620, t, 16, 220, prog(t, ty, ty + .8));
    k9Reply('Tabii!', true, warm);
    k9Board([{ label: K9ROW1, lit: 12, n: 12, col: K9GREY, a: .6 }, { label: K9ROW2, lit: null, hot: true, a: .45 + .55 * add }]);
  },
  sonuc(V, L, t, lt) {
    // the result: the banknote drops into the can, the shot dims and the big numbers come up: %29 on "%29", an
    // arrow and the gold %50 counting up from 29 on "%50"; the board's second row lights 21 of 42
    K9.dusk();
    const tf = wordTime(L, V.from, .45), tk = wordTime(L, V.key, .65);
    const give = prog(t, L.t0 - .1, L.t0 + .25), drop = prog(t, L.t0 + .2, L.t0 + .65), count = prog(t, tk - .2, tk + .5), dim = prog(t, tf - .3, tf);
    const f = { sh: .75, el: .6, hand: 'hold' };
    if (drop <= 0) f.prop = k9Hold((hx, hy) => { k9Note(hx + 12, hy - 8, 80, -.2); k9Mark('evhand', hx + 12, hy - 8); }, f.sh, f.el);
    cam(540, 900, 1.0, () => k9TwoShot(t, { evExpr: drop >= 1 ? 'happy' : 'warm', evFront: give < 1 ? { ...f, sh: lerp(.5, .75, give) } : f, arsExpr: drop >= 1 ? 'happy' : 'warm' }));
    k9Fly('evhand', 'can', drop, 64, 80);
    if (drop >= 1 && K9.pt.can) sparks(K9.pt.can[0], K9.pt.can[1], t, 16, 140, prog(t, L.t0 + .6, L.t0 + 1.3));
    if (dim > 0) { ctx.fillStyle = `rgba(10,8,14,${.72 * dim})`; ctx.fillRect(0, 0, W, K9B.y); }
    const p29 = back(prog(t, tf - .1, tf + .2)), p50 = prog(t, tk - .15, tk + .2);
    if (p29 > 0) {
      ctx.save(); ctx.translate(232, 790); ctx.scale(p29, p29);
      text('%29', 0, 0, 150, K9GREY, SANS, 900, 'center', 12);
      text('SADECE SORUNCA', 0, 112, 30, C.ivory, SANS, 900, 'center', 6, C.ink, 2);
      ctx.restore();
    }
    k9Arrow(420, 566, 790, prog(t, tk - .35, tk - .05));
    if (p50 > 0) {
      ctx.save(); ctx.translate(790, 790); ctx.scale(back(p50), back(p50));
      glow(0, 0, 300, 'rgba(232,181,74,1)', .4 * count);
      text('%' + Math.round(k9Count(29, 50, count)), 0, 0, 190, C.gold2, SANS, 900, 'center', 14);
      text('BİR KURUŞ BİLE YETER', 0, 126, 30, C.gold2, SANS, 900, 'center', 6, C.ink, 2);
      ctx.restore();
      sparks(790, 790, t, 22, 280, prog(t, tk + .1, tk + .9));
    }
    k9Board([{ label: K9ROW1, lit: 12, n: 12, col: K9GREY, a: .7 }, { label: K9ROW2, lit: count > 0 ? count * 21 : null, n: 21, col: C.gold2, hot: true }]);
    flash(t, tk - .05, .4);
  },
  para(V, L, t, lt) {
    // the money: two cans over the dimmed porch. On "küçülmedi" the plate; on "aynı" the gift per donor, 1,44 $ ≈ 1,54 $;
    // on "toplanan" the totals count up and the gold pile grows; on "%64" a gold +%64 lands on the second can
    K9.dusk();
    const ts = wordTime(L, V.small, .2), ta = wordTime(L, V.same, .45), tt = wordTime(L, V.total, .7), tk = wordTime(L, V.key, .85);
    drawSoft(.4, () => cam(540, 900, 1.3, () => k9Porch(t)), .2);
    ctx.fillStyle = 'rgba(10,8,14,.62)'; ctx.fillRect(0, 0, W, H);
    plate('Bağışlar küçülmedi', 540, 500, 52, back(prog(t, ts - .1, ts + .25)), C.gold);
    const per = prog(t, ta - .35, ta), tot = prog(t, tt - .1, tk), big = prog(t, tk - .1, tk + .25);
    [[270, K9GREY, 'SADECE SORUNCA', 1.44, 18.55, .55], [810, C.gold2, 'BİR KURUŞ BİLE YETER', 1.54, 30.34, 1]].forEach(([x, col, cap, p, sum, heap], i) => {
      plate(cap, x, 618, 28, 1, i ? C.gold : C.bone, 470);
      // the pile of notes in front of the can grows with the total
      const hk = .25 + .75 * eo(tot) * heap;
      k9Can(x, 960, 2.1);
      for (let j = 0; j < Math.round(2 + 10 * hk); j++) k9Note(x + (rnd(j + i * 20) - .5) * 250, 1012 - j * 6 - rnd(j + 5) * 8, 110, (rnd(j + 30) - .5) * .6);
      if (i === 1 && tot > 0) glow(x, 900, 260, 'rgba(232,181,74,1)', .3 * tot);
      if (per > 0) { ctx.save(); ctx.globalAlpha *= clamp(per * 2); text(p.toFixed(2).replace('.', ',') + ' $', x, 1112, 58, col, SANS, 900, 'center', 8); ctx.restore(); }
      if (tot > 0) text(k9Count(0, sum, tot).toFixed(2).replace('.', ',') + ' $', x, 1290, i ? 84 : 72, col, SANS, 900, 'center', 10);
    });
    if (per > 0) { ctx.save(); ctx.globalAlpha *= clamp(per * 2); text('KİŞİ BAŞI', 540, 1056, 28, C.ivory, SANS, 900, 'center', 5, C.ink, 3); ctx.restore(); }
    k9Approx(540, 1112, prog(t, ta - .1, ta + .2));
    if (tot > 0) { text('TOPLAM', 540, 1222, 30, C.ivory, SANS, 900, 'center', 5, C.ink, 3); k9Arrow(440, 610, 1292, tot, C.gold2, 12); }
    if (big > 0) {
      ctx.save(); ctx.translate(810, 790); ctx.rotate(-.08); ctx.scale(back(big), back(big));
      glow(0, 0, 260, 'rgba(232,181,74,1)', .5);
      text('+%64', 0, 0, 132, C.gold2, SANS, 900, 'center', 14);
      ctx.restore();
      sparks(810, 790, t, 22, 260, prog(t, tk + .1, tk + .9));
    }
    flash(t, tk - .05, .35);
  },
  neden(V, L, t, lt) {
    // the why. Close on the neighbour under the gold phrase: a thought cloud "PARAM YOK..." on "param"; on "siler" the
    // phrase turns into a gold one-kuruş coin that knocks it out (struck through, gone). Cut on "Artık" back to his door: he starts "Hayır..." and a red "CİMRİ?"
    // stamp lands on "cimrilik"; he winces and, on "gelir", drops a banknote into her box
    K9.day();
    const te = wordTime(L, V.excuse, .2), ts = wordTime(L, V.erase, .45), tc = wordTime(L, V.cut, .6), tn = wordTime(L, V.no, .65);
    const tk = wordTime(L, V.key, .8), tp = wordTime(L, V.pay, .95);
    if (t < tc - .08) {
      const cloud = prog(t, te - .25, te + .05), hit = prog(t, ts - .45, ts), gone = prog(t, ts + .05, ts + .5);
      ctx.fillStyle = '#2a2420'; ctx.fillRect(0, 0, W, H);
      drawSoft(.4, () => cam(700, 1000, 1.5, () => k9Landing(t)), .35);
      cam(540, 960, lerp(1.0, 1.03, eio(prog(lt, 0, 3))), () => {
        glow(600, 980, 480, 'rgba(255,214,140,1)', .22 + .2 * gone);
        figure(K9.komsu, 650, 2090, 1.25, t, { dir: -1, legs: 'hide', seed: 4, expr: gone > .4 ? { ...EXPR.surprised, curve: .1, look: [-.4, -.3] } : { ...EXPR.displeased, look: [-.5, -.4] },
          front: gone > .4 ? { sh: .3, el: 1.2, hand: 'open' } : { sh: .15, el: 2.2, hand: 'palm' }, hideBack: true });
      });
      // the excuse in a thought cloud, struck through and blown away by the coin
      if (gone < 1) {
        ctx.save(); ctx.translate(330, 640); const s = 1 - .6 * ei(gone); ctx.scale(s, s); ctx.rotate(-.3 * gone); ctx.globalAlpha *= 1 - gone; ctx.translate(-330, -640);
        thought(330, 640, 460, 230, 470, 820, cloud);
        if (cloud > .4) { text('PARAM', 330, 610, 60, '#5a5460', SANS, 900); text('YOK...', 330, 676, 60, '#5a5460', SANS, 900); }
        const st = eo(prog(t, ts - .05, ts + .2));
        if (st > 0) { ctx.save(); ctx.translate(330, 644); ctx.rotate(-.06); ctx.lineCap = 'round'; ctx.strokeStyle = C.red2; ctx.lineWidth = 14;
          ctx.shadowColor = 'rgba(210,35,47,.8)'; ctx.shadowBlur = 16; ctx.beginPath(); ctx.moveTo(-160, 4); ctx.lineTo(-160 + 320 * st, -4); ctx.stroke(); ctx.restore(); }
        ctx.restore();
      }
      if (hit > 0) {
        const x = lerp(540, 470, eio(hit)) + (gone > 0 ? 300 * gone : 0), y = lerp(500, 640, eio(hit)) - 200 * gone + 260 * gone * gone, r = 70;
        ctx.save(); ctx.translate(x, y); ctx.rotate(-hit * 6 + gone * 3); ctx.translate(-x, -y);
        glow(x, y, 160, 'rgba(232,181,74,1)', .5);
        k9Coin(x, y, r, ['1', 'KURUŞ']);
        ctx.restore();
      }
      if (gone > 0) sparks(400, 640, t, 20, 260, gone);
      plate(K9ROW2, 540, 488, 46, back(prog(lt, 0, .3)) * (1 - prog(t, ts - .45, ts - .3)), C.gold);  // the phrase turns into the coin
      plate('“Param yok” bahanesi silinir', 540, 1330, 50, back(prog(t, te + .15, te + .5)), C.gold);
      return;
    }
    // back at the door: "Hayır..." -> CİMRİ? -> the note goes in
    const no = prog(t, tn - .15, tn + .1), stamp = prog(t, tk - .12, tk + .12), give = prog(t, tk + .25, tp - .05), drop = prog(t, tp - .1, tp + .4);
    const f = give > 0 ? { sh: lerp(.3, 1.05, eo(give)), el: lerp(1.5, .35, eo(give)), hand: 'hold' } : { sh: lerp(.15, .5, eo(no)), el: lerp(.35, 1.6, eo(no)), hand: 'palm' };
    if (give > 0 && drop <= 0) f.prop = k9Hold((hx, hy) => { k9Note(hx + 14, hy - 8, 88, -.25); k9Mark('hand', hx + 14, hy - 8); }, f.sh, f.el);
    cam(560, 980, lerp(1.06, 1.1, eio(prog(t, tc, tc + 3))), () => k9DoorShot(t, {
      open: 1, fill: .45 + .3 * eo(prog(t, tp + .25, tp + .55)), kFront: f, kTalk: no > 0 && stamp <= 0 ? .5 : 0,
      kExpr: drop > .5 ? 'warm' : stamp > .3 ? { ...EXPR.surprised, curve: -.3, look: [-.3, -.2] } : { ...EXPR.displeased, look: [.5, 0] },
      kTilt: stamp > 0 ? -.06 * eo(stamp) : 0, gExpr: drop > .5 ? 'happy' : stamp > .3 ? { ...EXPR.surprised, curve: .2 } : 'warm' }));
    k9Fly('hand', 'slot', drop, 76);
    if (drop >= 1 && K9.pt.slot) sparks(K9.pt.slot[0], K9.pt.slot[1], t, 18, 160, prog(t, tp + .3, tp + 1));
    k9Bubble(860, 650, [['Hayır...', K9RED]], 46, no * (1 - prog(t, tk - .15, tk)), 790, 740);
    if (stamp > 0) glow(860, 650, 220, 'rgba(210,35,47,1)', .3 * stamp * (1 - give));
    k9Stamp('CİMRİ?', 860, 650, 64, stamp, .1);
    plate('Hayır demek cimrilik gibi gelir', 540, 1340, 50, back(prog(t, tk - .05, tk + .3)), C.gold);
    flash(t, tc - .08, .45);
  }
});
