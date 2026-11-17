// k010 Ortadaki seçenek, the compromise effect (Simonson & Tversky 1992, J. Marketing Research 29(3), 281-295). Business
// and psychology students at three West Coast universities chose between 35 mm Minolta cameras shown with catalog
// pictures and prices. One group (n 106) saw the X-370 at $169.99 and the Maxxum 3000i at $239.99: 50 % / 50 %. Another
// group (n 115) also saw the Maxxum 7000i at $469.99: 22 % / 57 % / 21 %. The why (the authors): extremeness aversion,
// each end has one big drawback (the cheap one's quality, the dear one's price) and the middle one only small ones.
// The hook is a phone shop today: the customer points at the cheaper of two phones; the seller slides a much dearer
// third one in beside them and the customer takes the middle one.
const K10 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  shop() { setLight(-.62, -.72, { rim: '#eef6ff', rimA: .5, warm: '#fff6e6', cool: '#2a3450' }); },
  // today: the seller in the shop's teal blazer, the customer in a red jacket over jeans
  satici: { ...CAST.arastirmaci2, coat: '#1f6a70', trim: '#1f6a70', shirt: '#eef2ee', tie: '#eef2ee', breeches: '#2a2e36', hose: '#2a2e36',
    hair: 'short', hairColor: '#2a1a12', brow: '#2a1a12', skin: '#d9a07c', glasses: false },
  musteri: { ...CAST.uye, outfit: 'suit', coat: '#b8402e', trim: '#b8402e', shirt: '#f2ece0', tie: '#f2ece0', breeches: '#2f4a72', hose: '#2f4a72',
    shoe: '#2a2a30', hair: 'side', hairColor: '#3a2414', brow: '#3a2414', skin: '#efc29e', age: .05, iris: '#5a4030' },
  // 1990: students of the first group (denim jacket, mustard dress) and of the second (green jacket, plum dress)
  ogr1: { ...CAST.uye, outfit: 'suit', coat: '#4a6a9a', trim: '#4a6a9a', shirt: '#f0e8d8', tie: '#f0e8d8', breeches: '#2e3a52', hose: '#2e3a52',
    hair: 'side', hairColor: '#6a3e1e', brow: '#5a3418', skin: '#efc29e', age: .05 },
  ogr2: { ...CAST.evsahibi, coat: '#c8902a', trim: '#f4efe4', sleeve: 'long', hairColor: '#2a1a12', skin: '#e8b890', shoe: '#5a2a1a' },
  ogr3: { ...CAST.uye, outfit: 'suit', coat: '#3f7a4e', trim: '#3f7a4e', shirt: '#f2ece0', tie: '#f2ece0', breeches: '#3a3a44', hose: '#3a3a44',
    hair: 'short', hairColor: '#1e1814', brow: '#1e1814', skin: '#d9a07c', age: .05 },
  ogr4: { ...CAST.evsahibi, coat: '#7a3a6a', trim: '#f4efe4', sleeve: 'long', hairColor: '#b8682a', skin: '#f3c9a8', shoe: '#3a1a2a' }
};
const K10INK = '#1a1117', K10GOLD = '#9a6400', K10RED = '#b81d28';
// the three products: today's phones (TL) and the 1990 cameras (dollars), cheap, middle, dear
const K10P = [
  { tl: '17.000 TL', usd: '170 $', frame: '#8a9098', scr: ['#6a8aa8', '#2a3a58'], s: .9 },
  { tl: '24.000 TL', usd: '240 $', frame: '#2a2e38', scr: ['#2f8a9a', '#f0a868'], s: 1 },
  { tl: '47.000 TL', usd: '470 $', frame: '#c8a050', scr: ['#5a2a9a', '#f06a8a'], s: 1.14 }
];

// ---------- small things ----------
// a speech bubble: rows [[text, colour, alpha]], size px, tail to (tx, ty); k 0..1 pops it in; o.bottom keeps the bottom edge
function k10Bubble(x, y, rows, size, k, tx, ty, o = {}) {
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
    ctx.save(); ctx.globalAlpha *= clamp(a); text(up(str), x, yy + size * .04, size, col || K10INK, SANS, 900); ctx.restore();
    yy += lh * ka;
  });
  ctx.restore();
}
// a rubber stamp, k 0..1 drops it from large onto the frame
function k10Stamp(label, x, y, size, k, rot = -.1, col = C.red) {
  if (k <= 0) return;
  const s = lerp(2.2, 1, eo(clamp(k)));
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s); ctx.globalAlpha *= clamp(k * 3);
  const w = measure(label, size, SANS, 900, size * .08) + size * .9, h = size * 1.5;
  ctx.fillStyle = 'rgba(251,246,232,.95)'; rr(-w / 2, -h / 2, w, h, size * .16); ctx.fill();
  ctx.strokeStyle = col; ctx.lineWidth = size * .1; rr(-w / 2, -h / 2, w, h, size * .16); ctx.stroke();
  ctx.lineWidth = size * .03; rr(-w / 2 + size * .14, -h / 2 + size * .14, w - size * .28, h - size * .28, size * .1); ctx.stroke();
  text(label, 0, size * .05, size, col, SANS, 900, 'center', 0, C.ink, size * .08);
  ctx.restore();
}
// a price card, centre (x, y); k pops it in
function k10Tag(x, y, label, k, o = {}) {
  if (k <= 0) return;
  const size = o.size || 40, w = measure(label, size, SANS, 900) + size * .9, h = size * 1.45, s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.rotate(o.rot || 0); ctx.scale(s, s);
  if (o.glow) glow(0, 0, w * .8, o.glow, .5);
  shape(() => rrp(-w / 2, -h / 2, w, h, 9), o.fill || '#fbf6e8', { shade: 4, hi: 3, line: 4.5, noRim: true });
  if (o.border) { ctx.strokeStyle = o.border; ctx.lineWidth = 4; rr(-w / 2 + 6, -h / 2 + 6, w - 12, h - 12, 6); ctx.stroke(); }
  text(label, 0, size * .05, size, o.col || K10INK, SANS, 900);
  ctx.restore();
}
// the customer's pick: a gold badge with a tick
function k10Check(x, y, k, s = 1) {
  if (k <= 0) return;
  const z = back(clamp(k)) * s;
  ctx.save(); ctx.translate(x, y); ctx.scale(z, z);
  glow(0, 0, 80, 'rgba(232,181,74,1)', .55);
  shape(() => ctx.arc(0, 0, 34, 0, Math.PI * 2), C.gold, { shade: 5, hi: 5, line: 5, noRim: true });
  ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = K10INK; ctx.lineWidth = 9;
  ctx.beginPath(); ctx.moveTo(-15, 1); ctx.lineTo(-4, 13); ctx.lineTo(16, -12); ctx.stroke();
  ctx.restore();
}

// ---------- today: the phone shop ----------
// a phone on a small clear stand, bottom centre (x, y); i 0 cheap, 1 middle, 2 dear. o.crack 0..1, o.lit 0..1
function k10Phone(x, y, i, s, t, o = {}) {
  const P = K10P[i], w = 120, h = 236;
  ctx.save(); ctx.translate(x, y); ctx.scale(s * P.s, s * P.s);
  if (o.lit > 0) glow(0, -h * .55, 230, 'rgba(255,214,120,1)', .55 * o.lit);
  if (o.red > 0) glow(0, -h * .55, 220, 'rgba(230,40,50,1)', .5 * o.red);
  ctx.fillStyle = 'rgba(210,228,240,.6)'; ctx.beginPath(); ctx.moveTo(-46, 0); ctx.lineTo(46, 0); ctx.lineTo(30, -36); ctx.lineTo(-30, -36); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = 'rgba(26,17,23,.55)'; ctx.lineWidth = 3; ctx.stroke();
  ctx.translate(0, -12);
  shape(() => rrp(-w / 2, -h, w, h, 20), P.frame, { shade: 6, hi: 6, line: 5 });
  const sx = -w / 2 + 8, sy = -h + 8, sw = w - 16, sh = h - 16;
  ctx.save(); rr(sx, sy, sw, sh, 13); ctx.clip();
  const g = ctx.createLinearGradient(0, sy, 0, sy + sh); g.addColorStop(0, P.scr[0]); g.addColorStop(1, P.scr[1]);
  ctx.fillStyle = g; ctx.fillRect(sx, sy, sw, sh);
  ctx.fillStyle = 'rgba(255,255,255,.2)'; ctx.beginPath(); ctx.moveTo(sx, sy + sh * .62);
  ctx.quadraticCurveTo(0, sy + sh * .46 + Math.sin(t * 1.6 + i) * 8, sx + sw, sy + sh * .58); ctx.lineTo(sx + sw, sy + sh); ctx.lineTo(sx, sy + sh); ctx.closePath(); ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,.22)'; ctx.beginPath(); ctx.moveTo(sx + sw * .55, sy); ctx.lineTo(sx + sw * .8, sy); ctx.lineTo(sx + sw * .1, sy + sh); ctx.lineTo(sx - sw * .15, sy + sh); ctx.closePath(); ctx.fill();
  text('12:30', 0, sy + 46, 24, 'rgba(255,255,255,.92)', SANS, 800);
  if (o.crack > 0) {  // a cracked screen, the cracks running out from one point
    const c = eo(clamp(o.crack)), cx = -12, cy = sy + sh * .55;
    ctx.strokeStyle = 'rgba(255,255,255,.95)'; ctx.lineWidth = 3.5; ctx.lineCap = 'round';
    [[-1.0, 90], [-.2, 110], [.7, 80], [1.6, 100], [2.5, 90], [3.6, 70], [4.4, 100], [5.4, 80]].forEach(([a, l], j) => {
      ctx.beginPath(); ctx.moveTo(cx, cy);
      const mx = cx + Math.cos(a) * l * .5 * c, my = cy + Math.sin(a) * l * .5 * c;
      ctx.lineTo(mx + (j % 2 ? 6 : -6), my); ctx.lineTo(cx + Math.cos(a + .15) * l * c, cy + Math.sin(a + .15) * l * c); ctx.stroke();
    });
    ctx.strokeStyle = 'rgba(20,20,30,.5)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(cx, cy, 22 * c, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.restore();
  ctx.fillStyle = '#0e0c10'; rr(-15, sy + 8, 30, 10, 5); ctx.fill();
  if (i === 2) {  // the dear one: a gold edge that glints
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = .35 + .3 * Math.sin(t * 3);
    ctx.strokeStyle = '#ffe9b0'; ctx.lineWidth = 4; rr(-w / 2 + 3, -h + 3, w - 6, h - 6, 18); ctx.stroke(); ctx.restore();
  }
  ctx.restore();
}
// the back wall: a lit shelf unit of boxed phones (drawn soft, behind the counter)
function k10ShopBack(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1200); g.addColorStop(0, '#9fb0bc'); g.addColorStop(1, '#d6dee3');
  ctx.fillStyle = g; ctx.fillRect(-300, -300, W + 600, 1600);
  shape(() => rrp(30, 560, 1020, 560, 12), '#eef1f2', { shade: 10, hi: 6, line: 5 });
  ctx.fillStyle = '#dfe9ee'; ctx.fillRect(48, 578, 984, 524);
  glow(540, 820, 520, 'rgba(255,255,255,1)', .35);
  const cols = ['#4a4e58', '#b8a070', '#8a9098', '#5a8a92', '#9a6a60', '#6a5a8a'];
  [690, 830, 970, 1110].forEach((sy, r) => {
    for (let i = 0; i < 12; i++) {
      const x = 74 + i * 80 + (r % 2) * 20, c = cols[(i * 5 + r * 3) % cols.length];
      ctx.fillStyle = '#fbfbf8'; rr(x, sy - 96, 56, 92, 6); ctx.fill();
      ctx.fillStyle = c; rr(x + 10, sy - 84, 36, 62, 5); ctx.fill();
      ctx.strokeStyle = 'rgba(26,17,23,.35)'; ctx.lineWidth = 2.5; rr(x, sy - 96, 56, 92, 6); ctx.stroke();
    }
    shape(() => rrp(40, sy - 4, 1000, 14, 3), '#c9d2d8', { flat: true, line: 3 });
  });
}
// the glass counter in front: a white top at y, the lit case below with cases and earbuds
function k10Counter(t, y = 1160) {
  const g = ctx.createLinearGradient(0, y + 20, 0, y + 300); g.addColorStop(0, '#a8c8d6'); g.addColorStop(1, '#5a7a8a');
  ctx.fillStyle = g; ctx.fillRect(-60, y + 20, W + 120, 300);
  for (let i = 0; i < 10; i++) {  // phone covers on the glass shelf
    const x = 40 + i * 106, c = ['#e86a6a', '#3a6ab8', '#f0c050', '#2a2e38', '#6ab88a'][i % 5];
    shape(() => rrp(x, y + 120, 54, 96, 10), c, { shade: 4, hi: 3, line: 3.5, noRim: true });
  }
  ctx.fillStyle = 'rgba(255,255,255,.55)'; ctx.fillRect(-60, y + 222, W + 120, 8);
  ctx.save(); ctx.globalAlpha = .18; ctx.fillStyle = '#ffffff';
  [[120, 60], [520, 40], [860, 70]].forEach(([x, w]) => { ctx.beginPath(); ctx.moveTo(x, y + 26); ctx.lineTo(x + w, y + 26); ctx.lineTo(x + w - 90, y + 300); ctx.lineTo(x - 90, y + 300); ctx.closePath(); ctx.fill(); });
  ctx.restore();
  ctx.fillStyle = '#3a3a42'; ctx.fillRect(-300, y + 300, W + 600, 1200);
  shape(() => rrp(-40, y - 6, W + 80, 30, 6), '#f4f2ec', { shade: 6, hi: 5, line: 5 });
}
// the shop shot: o.slide brings the dear phone in, o.pick 0 (cheap) .. 1 (middle) moves the customer's finger and
// tick, o.tag3 shows its price, o.ps scales the phones; expressions and arms come in o. Phones stand at K10X.
const K10X = [392, 620, 856];
// the customer's pointing arm: elbow down and forearm out to the cheap phone, the whole arm out to the middle one
const k10Point = p => ({ sh: lerp(0, 1.58, eio(p)), el: lerp(1.91, .01, eio(p)), hand: 'point' });
function k10ShopShot(t, o = {}) {
  ctx.fillStyle = '#3a3a42'; ctx.fillRect(-600, -600, W + 1200, H + 1200);  // nothing of the last frame may show at the edges
  drawSoft(.5, () => k10ShopBack(t), .16);
  const sh3 = o.slide ?? 1, X = K10X, ps = o.ps || 1, py = 1172;
  figure(K10.satici, 930, 1330, .6, t, { dir: -1, legs: 'stand', seed: 7, expr: o.sExpr || 'warm', talk: o.sTalk || 0, nod: o.sNod || 0,
    front: o.sFront || { sh: 1.1, el: .5, hand: 'palm' }, back: { sh: .1, el: .3 }, noShadow: true });
  k10Counter(t);
  const lift = o.lift || 0;
  k10Phone(X[0] + (o.jit || 0), py, 0, ps, t, { lit: (1 - (o.pick || 0)) * (o.pickA ?? 1), red: o.red || 0, crack: o.crack || 0 });
  k10Phone(X[1], py - lift, 1, ps, t, { lit: Math.max((o.pick || 0) * (o.pickA ?? 1), o.lit2 || 0) });
  if (sh3 > 0) {
    const x3 = lerp(1260, X[2], eo(sh3));
    if (sh3 < 1) { ctx.save(); ctx.globalAlpha = .6 * (1 - sh3); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 7; ctx.lineCap = 'round';
      [1000, 1050, 1100].forEach((y, j) => { ctx.beginPath(); ctx.moveTo(x3 + 90, y); ctx.lineTo(x3 + 220 + j * 30, y); ctx.stroke(); }); ctx.restore(); }
    k10Phone(x3 - (o.jit || 0), py, 2, ps, t, { lit: o.lit3 || 0, red: o.red || 0 });
  }
  k10Tag(X[0], 1240, K10P[0].tl, 1, { size: 35 });
  k10Tag(X[1], 1240, K10P[1].tl, 1, { size: 35 });
  k10Tag(X[2], 1240, K10P[2].tl, o.tag3 ?? 1, { size: 35, col: K10RED, glow: (o.tag3 ?? 1) < 1 ? 'rgba(255,90,80,1)' : null });
  // the customer in front of the counter on the left, pointing at what he will take
  const p = o.pick || 0;
  figure(K10.musteri, 140, 1530, .74, t, { dir: 1, legs: 'stand', seed: 3, expr: o.mExpr || { ...EXPR.neutral, look: [.6, .4] }, talk: o.mTalk || 0,
    lean: o.mLean ?? lerp(.04, .15, eio(p)), headTilt: o.mTilt || 0, nod: o.mNod || 0,
    front: o.mFront || k10Point(p), back: { sh: .1, el: .3 } });
  // the tick over the pick, flying in an arc from the cheap phone to the middle one
  if ((o.pickA ?? 1) > 0) {
    const e = eio(p), x = lerp(X[0], X[1], e), y = 850 - Math.sin(Math.PI * e) * 90;
    k10Check(x, y, o.pickA ?? 1, .9);
  }
}

// ---------- 1990: the lecture room and its slide ----------
const K10S = { x: 70, y: 590, w: 800, h: 612 };  // the projection screen
function k10Room(t, o = {}) {
  let g = ctx.createLinearGradient(0, 0, 0, 1260); g.addColorStop(0, '#9a8466'); g.addColorStop(1, '#bfa47c');
  ctx.fillStyle = g; ctx.fillRect(-300, -300, W + 600, 1564);
  ctx.fillStyle = '#7a6448'; ctx.fillRect(-300, 1200, W + 600, 64);
  shape(() => rrp(-40, 1190, W + 80, 14, 2), '#5a4430', { shade: 3, hi: 2, line: 4 });
  g = ctx.createLinearGradient(0, 1262, 0, H); g.addColorStop(0, '#8a6a48'); g.addColorStop(1, '#4a3424');
  ctx.fillStyle = g; ctx.fillRect(-300, 1262, W + 600, H - 1262 + 300);
  ctx.strokeStyle = 'rgba(40,20,10,.3)'; ctx.lineWidth = 3;
  for (let i = -6; i <= 6; i++) { ctx.beginPath(); ctx.moveTo(540 + i * 90, 1264); ctx.lineTo(540 + i * 330, H); ctx.stroke(); }
  // a window with the afternoon sun on the right
  shape(() => rrp(905, 560, 150, 520, 4), '#efe6d2', { shade: 6, hi: 4, line: 5 });
  g = ctx.createLinearGradient(0, 570, 0, 1070); g.addColorStop(0, '#9cc8e8'); g.addColorStop(1, '#e8f0e0');
  ctx.fillStyle = g; ctx.fillRect(917, 572, 126, 496);
  ctx.fillStyle = '#efe6d2'; ctx.fillRect(974, 572, 10, 496); ctx.fillRect(917, 810, 126, 10);
  // the screen on its roller, with the projector's light on it
  const S = K10S;
  shape(() => rrp(S.x - 24, S.y - 30, S.w + 48, 26, 8), '#3a3430', { shade: 4, hi: 3, line: 5 });
  shape(() => rrp(S.x, S.y, S.w, S.h, 3), '#f6f3ea', { shade: 6, hi: 4, line: 5 });
  glow(S.x + S.w / 2, S.y + S.h / 2, 420, 'rgba(255,250,230,1)', .35);
  if (o.content) { ctx.save(); ctx.beginPath(); ctx.rect(S.x, S.y, S.w, S.h); ctx.clip(); o.content(); ctx.restore(); }
  stroke(INK, 4, () => { ctx.moveTo(S.x + S.w / 2, S.y + S.h); ctx.lineTo(S.x + S.w / 2, S.y + S.h + 44); });
  circle(S.x + S.w / 2, S.y + S.h + 50, 8, '#3a3430');
}
// a 35 mm camera seen from the front, centre (x, y); kind 0 silver and plain, 1 black autofocus, 2 the big one
function k10Cam(x, y, s, kind) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  const bw = [200, 214, 250][kind], bh = [118, 126, 146][kind], body = kind ? '#26262c' : '#c9cdd3', top = kind ? '#1c1c22' : '#aeb3ba';
  shape(() => { ctx.moveTo(-44, -bh / 2 + 4); ctx.lineTo(-28, -bh / 2 - 42); ctx.lineTo(28, -bh / 2 - 42); ctx.lineTo(44, -bh / 2 + 4); ctx.closePath(); }, top, { shade: 6, hi: 5, line: 5 });
  shape(() => rrp(-bw / 2, -bh / 2, bw, bh, 16), body, { shade: 10, hi: 6, line: 5,
    after: () => {
      ctx.fillStyle = kind ? '#141418' : '#2a2a30'; ctx.fillRect(-bw / 2, -bh * .12, bw, bh * .62);
      if (kind) { ctx.fillStyle = 'rgba(255,255,255,.12)'; ctx.fillRect(-bw / 2, -bh / 2 + 6, bw, 8); }
    } });
  if (kind) shape(() => rrp(bw / 2 - 46, -bh / 2 + 8, 40, bh - 14, 12), '#18181c', { shade: 4, hi: 3, line: 4 });  // the grip
  shape(() => rrp(bw / 2 - 64, -bh / 2 - 16, 34, 16, 5), top, { shade: 3, hi: 3, line: 4 });  // the shutter dial
  const lr = [46, 52, 66][kind];
  if (kind === 2) shape(() => ctx.arc(0, 10, lr + 16, 0, Math.PI * 2), '#1a1a20', { shade: 6, hi: 5, line: 5 });  // the lens hood
  shape(() => ctx.arc(0, 10, lr, 0, Math.PI * 2), kind ? '#34343c' : '#4a4a52', { shade: 6, hi: 6, line: 5 });
  if (kind === 2) { ctx.strokeStyle = C.red; ctx.lineWidth = 5; ctx.beginPath(); ctx.arc(0, 10, lr - 6, 0, Math.PI * 2); ctx.stroke(); }
  circle(0, 10, lr * .66, '#0e0e14');
  const lg = ctx.createRadialGradient(-lr * .2, -lr * .1, 2, 0, 10, lr * .66); lg.addColorStop(0, 'rgba(120,150,220,.8)'); lg.addColorStop(1, 'rgba(20,20,40,0)');
  ctx.fillStyle = lg; ctx.beginPath(); ctx.arc(0, 10, lr * .66, 0, Math.PI * 2); ctx.fill();
  circle(-lr * .22, -lr * .12, lr * .14, 'rgba(255,255,255,.75)');
  ctx.restore();
}
// a small person, for the tallies
function k10Person(x, y, s, col) {
  circle(x, y - 22 * s, 10 * s, col);
  ctx.fillStyle = col; rr(x - 12 * s, y - 8 * s, 24 * s, 26 * s, 9 * s); ctx.fill();
}
// the slide: o.cams [{kind, x, k (pop), tag (0..1)}], o.tally [{x, k}] (a row of five people and %50), o.head 0..1
function k10Slide(t, o) {
  const S = K10S, hk = o.head ?? 1;
  if (hk > 0) { ctx.save(); ctx.beginPath(); ctx.rect(S.x, S.y, S.w * hk, 120); ctx.clip(); text('HANGİSİNİ ALIRDINIZ?', S.x + S.w / 2, S.y + 58, 46, '#2a2420', SANS, 900); ctx.restore(); }
  (o.cams || []).forEach(c => {
    if (c.k <= 0) return;
    const s = back(clamp(c.k)) * (c.kind === 2 ? 1.06 : .9);
    if (c.glow > 0) glow(c.x, S.y + 205, 200, 'rgba(232,181,74,1)', .5 * c.glow);
    k10Cam(c.x, S.y + 215, s * (c.s || 1), c.kind);
    k10Tag(c.x, S.y + 340, K10P[c.kind].usd, c.tag || 0, { size: 46, col: c.kind === 2 ? K10RED : K10INK, border: c.kind === 2 ? C.red : null });
  });
  (o.tally || []).forEach(g => {
    if (g.k <= 0) return;
    for (let i = 0; i < 5; i++) { const ki = clamp(g.k * 6 - i * .8); if (ki > 0) k10Person(g.x - 96 + i * 48, S.y + 432 + (1 - back(ki)) * 20, 1.3, '#3a6ab8'); }
    const kp = back(prog(g.k, .6, 1));
    if (kp > 0) { ctx.save(); ctx.translate(g.x, S.y + 520); ctx.scale(kp, kp); text('%50', 0, 0, 86, C.red, SANS, 900); ctx.restore(); }
  });
}
// a 1990 school desk seen from the front
function k10Desk(x, y, w) {
  shape(() => { ctx.moveTo(x - w * .42, y + 20); ctx.lineTo(x - w * .4, y + 180); ctx.moveTo(x + w * .42, y + 20); ctx.lineTo(x + w * .4, y + 180); }, '#3a3a44', { line: 9 });
  shape(() => rrp(x - w / 2, y, w, 34, 6), '#c49a6a', { shade: 6, hi: 5, line: 5 });
  shape(() => rrp(x - w * .45, y + 34, w * .9, 60, 4), '#8a5a36', { shade: 6, hi: 3, line: 4.5 });
}
// the two students in front of the screen; o.a and o.b raise a hand, o.wow surprises them
function k10Students(t, A, B, o = {}) {
  const look = { ...EXPR.neutral, look: [.4, -.7] };
  const ex = (k, w) => w > .3 ? { ...EXPR.surprised, look: [.6, -.6] } : k > .4 ? 'happy' : look;
  figure(A, 160, 1800, .62, t, { dir: 1, legs: 'hide', seed: 9, expr: ex(o.a || 0, o.wow || 0), headTilt: -.08,
    front: (o.a || 0) > 0 ? { sh: lerp(.4, 2.0, eo(o.a)), el: lerp(1.2, .4, o.a), hand: 'open' } : { sh: .4, el: 1.2 }, back: { sh: .1, el: .3 } });
  figure(B, 590, 1810, .6, t, { dir: 1, legs: 'hide', seed: 5, expr: ex(o.b || 0, o.wow || 0), headTilt: -.1,
    front: (o.b || 0) > 0 ? { sh: lerp(.4, 2.0, eo(o.b)), el: lerp(1.2, .4, o.b), hand: 'open' } : { sh: .4, el: 1.2 }, back: { sh: .1, el: .3 } });
  k10Desk(180, 1430, 300); k10Desk(600, 1440, 300);
}
// the researcher by the window with a pointer; sh aims it (1.6 at rest, 1.78 the left camera, 2.14 the right one)
function k10Prof(t, L, sh, o = {}) {
  figure('arastirmaci', 975, 1470, .6, t, { dir: -1, legs: 'stand', seed: 6, expr: o.expr || 'warm', talk: o.talk ? talk(t, L) * .5 : 0,
    front: { sh, el: o.el ?? .1, hand: 'hold', prop: () => { ctx.save(); ctx.lineCap = 'round'; ctx.strokeStyle = INK; ctx.lineWidth = 14; ctx.beginPath(); ctx.moveTo(0, 20); ctx.lineTo(0, 330); ctx.stroke(); ctx.strokeStyle = '#c8c8d0'; ctx.lineWidth = 7; ctx.stroke(); ctx.restore(); } },
    back: { sh: .1, el: .3 } });
}
// the pointer swinging smoothly between aims: [[time, sh], ...]
function k10Aim(t, keys) {
  let sh = keys[0][1];
  for (let i = 1; i < keys.length; i++) sh = lerp(sh, keys[i][1], eio(prog(t, keys[i][0] - .25, keys[i][0])));
  return sh;
}

// ---------- the results ----------
const K10C = { base: 1330, px: 7.5, xs: [220, 540, 860] };
// v: [cheap, middle, dear] (null shows a "?"), col: their colours, ghost 0..1 the dashed 50 % line of the two-camera group
function k10Chart(t, v, col, o = {}) {
  const { base, px, xs } = K10C;
  xs.forEach((x, i) => { k10Cam(x, 640, .72, i); k10Tag(x, 760, K10P[i].usd, 1, { size: 36, col: i === 2 ? K10RED : K10INK }); });
  ctx.strokeStyle = rgba(C.ivory, .7); ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(80, base); ctx.lineTo(1000, base); ctx.stroke();
  if (o.ghost > 0) {
    ctx.save(); ctx.globalAlpha *= o.ghost; ctx.setLineDash([16, 12]); ctx.strokeStyle = rgba(C.ivory, .75); ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(110, base - 50 * px); ctx.lineTo(685, base - 50 * px); ctx.stroke(); ctx.setLineDash([]);
    text('İKİ MAKİNEDE %50', 700, base - 50 * px, 26, rgba(C.ivory, .9), SANS, 900, 'left', 5, C.ink, 2);
    ctx.restore();
  }
  xs.forEach((x, i) => {
    const val = v[i];
    if (val == null) { text('?', x, base - 70, 90, rgba(C.ivory, .35), SANS, 900); return; }
    const h = Math.max(0, val * px), top = base - h;
    if (o.hot === i) glow(x, top + h / 2, 240, 'rgba(232,181,74,1)', .35);
    const g = ctx.createLinearGradient(0, top, 0, base); g.addColorStop(0, col[i][0]); g.addColorStop(1, col[i][1]);
    ctx.fillStyle = g; rr(x - 80, top, 160, h, 12); ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 5; rr(x - 80, top, 160, h, 12); ctx.stroke();
    ctx.save(); ctx.globalAlpha *= .3; ctx.fillStyle = '#ffffff'; ctx.fillRect(x - 60, top + 10, 14, Math.max(0, h - 20)); ctx.restore();
    text('%' + Math.round(val), x, top - 50, o.big === i ? 88 : 70, col[i][0], SANS, 900, 'center', 10);
  });
}
const K10COL = { cheap: [C.red2, C.redDim], mid: [C.gold2, C.goldDim], dear: ['#c8ccd8', '#5a5e6e'], dim: ['#8a8578', '#3a3830'] };

Object.assign(SAHNE, {
  kanca(V, L, t, lt) {
    // the hook, from the first frame: a phone shop, two phones on the counter, the customer points at the cheaper one
    // ("Ucuzu alayım."). On "sunma" the red plate; on "yanına" the seller slides a 47.000 TL phone in beside them; on
    // "çok" its price and the gold plate; on "koy" the customer's tick flies to the middle phone: "Ortadakini alayım!"
    K10.shop();
    const tn = wordTime(L, V.no, .3), tb = wordTime(L, V.back, .5), ty = wordTime(L, V.yes, .62), tg = wordTime(L, V.give, .9);
    const slide = prog(t, tb - .32, tb + .08), tag3 = prog(t, ty - .2, ty + .12), pick = prog(t, tg - .45, tg - .05);
    const wow = prog(t, tb - .05, tb + .15) * (1 - prog(t, tg - .5, tg - .3)), happy = prog(t, tg - .1, tg + .2);
    const reach = prog(t, tb - .5, tb - .1);
    cam(540, 940, lerp(1.0, 1.04, eio(prog(lt, 0, 4.5))), () => k10ShopShot(t, {
      slide, tag3, pick,
      sExpr: happy > .3 ? 'sly' : t > tn - .3 && t < tb - .3 ? { ...EXPR.displeased, look: [.5, .3] } : 'warm',
      sTalk: slide > 0 && slide < 1 ? .5 : 0,
      sFront: reach > 0 ? { sh: lerp(1.1, .5, eo(reach)), el: lerp(.5, .35, eo(reach)), hand: 'palm' } : { sh: 1.1, el: .5, hand: 'palm' },
      mExpr: happy > .4 ? 'happy' : wow > .4 ? { ...EXPR.surprised, look: [.8, .2] } : { ...EXPR.calm, look: [.6, .5] },
      mTalk: (t < 1.0 || (happy > 0 && happy < 1)) ? talk(t, L) * .5 + .15 : 0, mNod: happy > 0 && happy < 1 ? Math.sin(t * 10) * .04 : 0,
      lit3: wow * .6 }));
    k10Bubble(330, 0, [['Ucuzu', K10INK], ['alayım.', K10INK]], 44, 1 - prog(t, tg - .45, tg - .3), 210, 790, { bottom: 715 });
    k10Bubble(350, 0, [['Ortadakini', K10GOLD], ['alayım!', K10GOLD]], 44, prog(t, tg - .3, tg), 220, 790, { bottom: 715, fill: '#fff2c8', border: C.gold });
    plate(V.no_label, 540, 510, 54, back(prog(t, tn - .1, tn + .25)) * (1 - prog(t, ty - .3, ty - .1)), C.red);
    if (tag3 > 0) glow(540, 1350, 320, 'rgba(232,181,74,1)', .25 * tag3);
    plate(V.yes_label, 540, 1350, 58, back(prog(t, ty - .15, ty + .2)), C.gold);
  },
  deney(V, L, t, lt) {
    // 1990: a lecture room; the researcher shows a slide, "Which would you buy?", and two cameras pop up on "fotoğraf",
    // their prices on "170" and "240" as his pointer moves from one to the other
    K10.day();
    const tc = wordTime(L, V.cam, .45), ta = wordTime(L, V.a, .75), tb = wordTime(L, V.b, .9);
    cam(500, 900, lerp(1.0, 1.05, eio(prog(lt, 0, 4.5))), () => {
      k10Room(t, { content: () => k10Slide(t, { head: prog(t, L.t0, L.t0 + .6),
        cams: [{ kind: 0, x: 270, s: 1.2, k: prog(t, tc - .2, tc + .15), tag: prog(t, ta - .15, ta + .15) }, { kind: 1, x: 670, s: 1.2, k: prog(t, tc - .05, tc + .3), tag: prog(t, tb - .15, tb + .15) }] }) });
      k10Prof(t, L, k10Aim(t, [[0, 1.6], [ta, 1.78], [tb, 2.14]]), { talk: true });
      k10Students(t, K10.ogr1, K10.ogr2);
    });
    plate(V.year, 540, 500, 44, back(prog(t, L.t0 + .05, L.t0 + .4)));
  },
  iki(V, L, t, lt) {
    // half took the cheap one, half the dear one: a row of five people and "%50" under each camera, the students raise
    // their hands in turn
    K10.day();
    const ta = wordTime(L, V.a, .3), tb = wordTime(L, V.b, .6);
    const ka = prog(t, ta - .15, ta + .4), kb = prog(t, tb - .15, tb + .4);
    cam(470, 900, lerp(1.04, 1.08, eio(prog(lt, 0, 3))), () => {
      k10Room(t, { content: () => k10Slide(t, { cams: [{ kind: 0, x: 270, s: 1.2, k: 1, tag: 1 }, { kind: 1, x: 670, s: 1.2, k: 1, tag: 1 }], tally: [{ x: 270, k: ka }, { x: 670, k: kb }] }) });
      k10Prof(t, L, k10Aim(t, [[0, 2.14], [ta - .2, .12]]), { el: .05 });
      k10Students(t, K10.ogr1, K10.ogr2, { a: prog(t, ta - .1, ta + .25), b: prog(t, tb - .1, tb + .25) });
    });
    plate('İki makine: yarı yarıya', 540, 500, 46, back(prog(t, tb + .1, tb + .45)), C.gold);
  },
  ucuncu(V, L, t, lt) {
    // another group: new students; the two cameras shrink to the left and on "üçüncü" a big third camera lands on the
    // slide, its price, 470 $, on "470"; the students gasp
    K10.day();
    const tk = wordTime(L, V.key, .4), tp = wordTime(L, V.price, .85);
    const move = eio(prog(t, tk - .45, tk - .05)), land = prog(t, tk - .1, tk + .25), tag = prog(t, tp - .15, tp + .15);
    cam(470, 920, lerp(1.04, 1.0, eio(prog(lt, 0, 3.5))), () => {
      k10Room(t, { content: () => k10Slide(t, { cams: [{ kind: 0, x: lerp(270, 210, move), s: lerp(1.2, 1, move), k: 1, tag: 1 }, { kind: 1, x: lerp(670, 470, move), s: lerp(1.2, 1, move), k: 1, tag: 1 },
        { kind: 2, x: 735, k: land, tag, glow: tag }] }) });
      k10Prof(t, L, k10Aim(t, [[0, 1.6], [tk + .1, 2.45]]), { expr: land > .5 ? 'sly' : 'warm' });
      k10Students(t, K10.ogr3, K10.ogr4, { wow: prog(t, tk + .1, tk + .3) });
    });
    if (land > 0 && land < 1) sparks(735, K10S.y + 215, t, 18, 220, land);
    plate('Başka bir grup', 540, 500, 48, back(prog(t, L.t0 + .05, L.t0 + .4)), C.gold);
    flash(t, tk - .1, .4);
  },
  sonuc(V, L, t, lt) {
    // the result of the three-camera group over the dimmed room: the cheap camera's bar starts at the dashed 50 % of
    // the first group and falls to 22 on "%22", a red arrow down beside it
    K10.day();
    const tf = wordTime(L, V.from, .4), tk = wordTime(L, V.key, .65);
    drawSoft(.4, () => cam(470, 900, 1.2, () => k10Room(t)), .3);
    ctx.fillStyle = 'rgba(10,12,24,.78)'; ctx.fillRect(0, 0, W, H);
    const show = clamp(prog(lt, 0, .3)), fall = prog(t, tk - .35, tk + .15);
    ctx.save(); ctx.globalAlpha = show;
    k10Chart(t, [lerp(50, 22, eio(fall)), 50, null], [fall > 0 ? K10COL.cheap : K10COL.dim, K10COL.dim, K10COL.dear], { ghost: prog(t, tf - .2, tf + .1), big: 0 });
    ctx.restore();
    const ar = prog(t, tk - .2, tk + .2);  // the arrow down from 50 to 22
    if (ar > 0) {
      const { base, px } = K10C, x = 352, y0 = base - 50 * px + 10, y1 = lerp(y0, base - 22 * px - 10, eo(ar));
      ctx.save(); ctx.lineCap = 'round'; ctx.strokeStyle = C.red2; ctx.fillStyle = C.red2; ctx.lineWidth = 14; ctx.shadowColor = 'rgba(255,90,80,.7)'; ctx.shadowBlur = 16;
      ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1 - 14); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x, y1 + 12); ctx.lineTo(x - 24, y1 - 22); ctx.lineTo(x + 24, y1 - 22); ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    plate('Üç makine görenler', 540, 500, 46, back(prog(lt, .05, .4)), C.gold);
  },
  birinci(V, L, t, lt) {
    // the middle camera's bar climbs to 57 on "%57" (gold, the biggest), the dear one stops at 21; a "1." medal on
    // "birinci"
    K10.day();
    const tk = wordTime(L, V.key, .45), tw = wordTime(L, V.win, .8);
    drawSoft(.4, () => cam(470, 900, 1.2, () => k10Room(t)), .3);
    ctx.fillStyle = 'rgba(10,12,24,.78)'; ctx.fillRect(0, 0, W, H);
    const rise = prog(t, tk - .4, tk + .15), dear = prog(t, tk - .1, tk + .4);
    k10Chart(t, [22, lerp(50, 57, eio(rise)), dear > 0 ? lerp(0, 21, eio(dear)) : null], [K10COL.cheap, rise > 0 ? K10COL.mid : K10COL.dim, K10COL.dear],
      { ghost: 1 - prog(lt, 0, .5), hot: rise >= 1 ? 1 : -1, big: 1 });
    const m = back(prog(t, tw - .2, tw + .15));
    if (m > 0) {  // the medal
      const x = 540 + 118, y = K10C.base - 57 * K10C.px + 70;
      ctx.save(); ctx.translate(x, y); ctx.scale(m, m); ctx.rotate(.12);
      glow(0, 0, 130, 'rgba(232,181,74,1)', .6);
      shape(() => { ctx.moveTo(-28, 22); ctx.lineTo(-44, 100); ctx.lineTo(-18, 86); ctx.lineTo(-4, 108); ctx.lineTo(0, 32); ctx.closePath(); }, C.red, { shade: 3, hi: 3, line: 4, noRim: true });
      shape(() => { ctx.moveTo(28, 22); ctx.lineTo(44, 100); ctx.lineTo(18, 86); ctx.lineTo(4, 108); ctx.lineTo(0, 32); ctx.closePath(); }, C.red, { shade: 3, hi: 3, line: 4, noRim: true });
      shape(() => ctx.arc(0, 0, 58, 0, Math.PI * 2), C.gold, { shade: 8, hi: 7, line: 5, noRim: true });
      text('1.', 3, 4, 62, K10INK, SANS, 900);
      ctx.restore();
      sparks(540 + 118, K10C.base - 57 * K10C.px + 70, t, 22, 200, prog(t, tw, tw + .8));
    }
    plate('Üç makine görenler', 540, 500, 46, 1, C.gold);
    flash(t, tk - .05, .3);
  },
  neden(V, L, t, lt) {
    // the why, back in the shop: on "uçlardan" the two end phones glow red and shake; on "kalitesiz" the cheap one's
    // screen cracks under a KALİTESİZ? stamp (the customer frowns at it); on "fazla" FAZLA! lands on the dear one (he
    // gasps); on "Ortadaki" the middle one lights up and he points at it, AKILLICA on "akıllıca", the seller smiles
    K10.shop();
    const te = wordTime(L, V.ends, .2), tc = wordTime(L, V.cheap, .4), td = wordTime(L, V.dear, .55), tm = wordTime(L, V.mid, .7), tk = wordTime(L, V.key, .8);
    const ends = prog(t, te - .2, te + .2) * (1 - prog(t, tm - .2, tm + .2)), crack = prog(t, tc - .1, tc + .25), mid = prog(t, tm - .25, tm + .25);
    const jit = Math.sin(t * 40) * 4 * ends * (1 - prog(t, te + .2, te + .6));
    const point = eio(prog(t, tm - .2, tm + .3));
    const mExpr = mid > .3 ? 'happy' : t > td - .1 ? { ...EXPR.surprised, look: [1, .3] } : t > tc - .1 ? { ...EXPR.displeased, look: [.4, .6] }
      : ends > .3 ? { ...EXPR.confused, look: [.8, .2] } : { ...EXPR.neutral, look: [.6, .4] };
    cam(560, 1000, lerp(1.02, 1.06, eio(prog(lt, 0, 5))), () => {
      k10ShopShot(t, { pick: 1, pickA: 0, red: ends, jit, crack, lit2: mid, lift: 26 * eo(mid),
        mExpr, mLean: lerp(.04, .15, point), mFront: { sh: lerp(.3, 1.58, point), el: lerp(2.8, .01, point), hand: point > .2 ? 'point' : 'fist' },
        mTilt: t > tc - .1 && t < td - .1 ? .06 : 0, mNod: mid > 0 && mid < 1 ? Math.sin(t * 10) * .04 : 0,
        sExpr: t > tk - .1 ? 'sly' : 'warm', sFront: mid > 0 ? { sh: lerp(.2, 1.1, eo(mid)), el: lerp(.3, .5, mid), hand: 'palm' } : { sh: .2, el: .3 } });
      if (mid > 0) sparks(K10X[1], 960, t, 18, 220, prog(t, tm, tm + .8));
      k10Stamp('KALİTESİZ?', K10X[0] + 10, 1010, 38, prog(t, tc - .1, tc + .12), -.14);
      k10Stamp('FAZLA!', K10X[2] - 5, 990, 46, prog(t, td - .1, td + .12), .12);
      k10Stamp('AKILLICA', K10X[1], 870, 44, prog(t, tk - .1, tk + .12), -.06, '#b07a10');
    });
    plate('İnsan uçlardan kaçar', 540, 505, 50, back(prog(t, te - .15, te + .2)), C.gold);
    plate('Ortadaki = akıllıca seçim', 540, 1350, 52, back(prog(t, tk - .1, tk + .25)), C.gold);
  }
});
