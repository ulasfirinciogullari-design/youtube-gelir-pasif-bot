// k004 Çapa etkisi: Tversky & Kahneman (Science, 1974). A wheel of fortune spun in front of the subjects, rigged to stop
// only at 10 or 65; then "what percent of the UN's members are African?". Median guesses 25 and 45. The why is the
// anchor the mind drops on the first number it hears (two boats tied to anchors on a number sea); a bazaar stall
// brings it home. A sunny 1970s classroom.
const K4 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  WEDGE: ['#d2232f', '#f3ead8', '#2a6a8a', '#e8b54a'],
  // 1970s students: a young man in a tan corduroy jacket over jeans, a young woman in a rust dress
  ali: { ...CAST.uye, outfit: 'suit', coat: '#a0763e', trim: '#a0763e', shirt: '#efe6d2', tie: '#efe6d2', breeches: '#3d5a86',
    hose: '#3d5a86', shoe: '#5a3a22', hair: 'side', hairColor: '#6a3e1e', skin: '#efc29e', age: .05, iris: '#5a4030' },
  ayse: { ...CAST.evsahibi, coat: '#b8542a', trim: '#f2e2c4', hairColor: '#3a2416', shoe: '#5a2a1a', skin: '#ecbf9a' },
  // today's bazaar: a moustached rug seller in a waistcoat, a buyer in a blue coat
  seller: { ...CAST.arastirmaci2, coat: '#6a2a24', trim: '#6a2a24', shirt: '#f3ead8', tie: '#f3ead8', hairColor: '#2a1a12', beard: true,
    skin: '#d9a07c', glasses: false },
  buyer: { ...CAST.evsahibi, coat: '#2f5f8f', trim: '#e8eef4', hairColor: '#4a2a18', skin: '#f0c4a2', shoe: '#2a2a3a' },
  cards: {},  // where each student's card is on screen this frame (device pixels), filled by the card prop
  // the spin: it starts on "çevirdi" (line 2), stops on 10 at "onda" and, spun again, on 65 at "altmış beşte" (line 3)
  spin(t) {
    const step = Math.PI / 10, L2 = TL[1], L3 = TL[2];
    const t0 = wordTime(L2, EP.lines[1].v.key, .8) + .12, t1 = wordTime(L3, EP.lines[2].v.a, .5) - .02;
    const t2 = t1 + .3, t3 = Math.max(t2 + .6, wordTime(L3, EP.lines[2].v.b, .7) + .3);
    const a1 = Math.PI * 8 - 2 * step, a2 = Math.PI * 12 - 13 * step;
    let ang = Math.sin(t * 1.3) * .03, speed = 0;
    if (t >= t0 && t < t1) { const p = prog(t, t0, t1); ang = a1 * eo(p); speed = Math.pow(1 - p, 2); }
    else if (t >= t1 && t < t2) ang = a1;
    else if (t >= t2 && t < t3) { const p = prog(t, t2, t3); ang = lerp(a1, a2, eo(p)); speed = Math.pow(1 - p, 2); }
    else if (t >= t3) ang = a2;
    return { ang, speed, t0, t1, t2, t3 };
  }
};
const k4X = v => 70 + v * 9.4;  // the number sea: 0 at x 70, 100 at x 1010

// ---------- the classroom ----------
function k4Clock(x, y, r, t) {
  shape(() => ctx.arc(x, y, r, 0, Math.PI * 2), '#f6f0e2', { shade: r * .12, hi: r * .08, line: 5 });
  ctx.save(); ctx.translate(x, y); ctx.strokeStyle = INK; ctx.lineCap = 'round';
  for (let i = 0; i < 12; i++) { const a = i / 12 * Math.PI * 2; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(Math.cos(a) * r * .78, Math.sin(a) * r * .78); ctx.lineTo(Math.cos(a) * r * .9, Math.sin(a) * r * .9); ctx.stroke(); }
  [[t * .02 + 1.2, .5, 6], [t * .3 + 3, .74, 4]].forEach(([a, l, w]) => { ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(Math.sin(a) * r * l, -Math.cos(a) * r * l); ctx.stroke(); });
  ctx.strokeStyle = C.red; ctx.lineWidth = 2; const sa = Math.floor(t) / 60 * Math.PI * 2 * 6;
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(Math.sin(sa) * r * .82, -Math.cos(sa) * r * .82); ctx.stroke();
  ctx.restore();
}
// the chalkboard in its wooden frame; o.content() draws on it in frame coordinates
function k4Board(x, y, w, h, t, content) {
  shape(() => rrp(x - 16, y - 16, w + 32, h + 32, 6), '#7a5232', { shade: 10, hi: 6, line: 6 });
  const g = ctx.createLinearGradient(x, y, x + w, y + h); g.addColorStop(0, '#2f4a3c'); g.addColorStop(1, '#22382e');
  ctx.fillStyle = g; ctx.fillRect(x, y, w, h);
  ctx.save(); ctx.globalAlpha = .12; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 18;  // old chalk smudges
  [[.2, .3], [.6, .7], [.8, .25]].forEach(([fx, fy], i) => { ctx.beginPath(); ctx.ellipse(x + w * fx, y + h * fy, w * .14, h * .08, .2 * i, 0, Math.PI * 2); ctx.stroke(); });
  ctx.restore();
  ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip(); if (content) content(); ctx.restore();
  shape(() => rrp(x - 6, y + h + 10, w + 12, 16, 4), '#8a6040', { shade: 4, hi: 3, line: 4 });  // the chalk ledge
  shape(() => rrp(x + w * .7, y + h + 2, 44, 10, 4), '#f6f0e2', { shade: 2, hi: 2, line: 3, noRim: true });
}
function k4Room(t, o = {}) {
  let g = ctx.createLinearGradient(0, 0, 0, 1240); g.addColorStop(0, '#6f8c78'); g.addColorStop(1, '#58725f');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1244);
  // a tall window on the right with the afternoon sun coming through
  shape(() => rrp(800, 600, 230, 470, 4), '#efe6d2', { shade: 8, hi: 5, line: 5 });
  g = ctx.createLinearGradient(0, 610, 0, 1060); g.addColorStop(0, '#9cc8e8'); g.addColorStop(1, '#e8f0e0');
  ctx.fillStyle = g; ctx.fillRect(812, 612, 206, 446);
  ctx.fillStyle = '#efe6d2'; ctx.fillRect(910, 612, 10, 446); ctx.fillRect(812, 830, 206, 10);
  ctx.save(); ctx.globalAlpha = .13; ctx.fillStyle = '#fff4d0';
  ctx.beginPath(); ctx.moveTo(812, 612); ctx.lineTo(1018, 612); ctx.lineTo(760, 1900); ctx.lineTo(300, 1900); ctx.closePath(); ctx.fill(); ctx.restore();
  // wainscot and floor
  ctx.fillStyle = '#465a4c'; ctx.fillRect(0, 1120, W, 124);
  shape(() => rrp(-10, 1108, W + 20, 16, 2), '#7a5232', { shade: 3, hi: 2, line: 4 });
  g = ctx.createLinearGradient(0, 1244, 0, H); g.addColorStop(0, '#8a5c38'); g.addColorStop(1, '#4a2e1c');
  ctx.fillStyle = g; ctx.fillRect(0, 1240, W, H - 1240);
  ctx.strokeStyle = 'rgba(40,20,10,.35)'; ctx.lineWidth = 3;
  for (let i = -6; i <= 6; i++) { ctx.beginPath(); ctx.moveTo(540 + i * 90, 1244); ctx.lineTo(540 + i * 330, H); ctx.stroke(); }
  if (!o.noBoard) k4Board(60, 640, 470, 320, t, o.board);
  if (!o.noClock) k4Clock(o.clockX || 680, o.clockY || 690, 46, t);
}
// a school desk seen from the front: a wooden top on a steel frame
function k4Desk(x, y, w) {
  shape(() => { ctx.moveTo(x - w * .42, y + 20); ctx.lineTo(x - w * .4, y + 180); ctx.moveTo(x + w * .42, y + 20); ctx.lineTo(x + w * .4, y + 180); }, '#3a3a44', { line: 9 });
  shape(() => rrp(x - w / 2, y, w, 34, 6), '#b07a4a', { shade: 6, hi: 5, line: 5 });
  shape(() => rrp(x - w * .45, y + 34, w * .9, 60, 4), '#8a5a36', { shade: 6, hi: 3, line: 4.5 });
}
// a rough chalk outline of Africa (with Madagascar), about 300 px tall at s 1; fill 0..1 floods it gold
function k4Africa(x, y, s, fill = 0, line = '#f3ead8', a = 1) {
  const P = [[18, 8], [30, 2], [45, 4], [58, 7], [68, 8], [73, 15], [80, 30], [88, 40], [100, 41], [93, 56], [85, 70], [81, 82], [74, 93],
    [66, 104], [56, 108], [50, 100], [48, 86], [44, 70], [42, 58], [40, 51], [30, 48], [18, 46], [8, 40], [1, 30], [4, 20], [10, 12]];
  ctx.save(); ctx.translate(x, y); ctx.scale(s * 2.8, s * 2.8); ctx.translate(-50, -55); ctx.globalAlpha *= a;
  const path = () => { ctx.beginPath(); P.forEach(([px, py], i) => i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)); ctx.closePath(); };
  if (fill > 0) { path(); ctx.globalAlpha *= 1; ctx.fillStyle = rgba(C.gold, .85 * fill); ctx.fill(); }
  path(); ctx.strokeStyle = line; ctx.lineWidth = 2.2; ctx.lineJoin = 'round'; ctx.stroke();
  ctx.beginPath(); ctx.ellipse(94, 84, 3.5, 9, .35, 0, Math.PI * 2);
  if (fill > 0) { ctx.fillStyle = rgba(C.gold, .85 * fill); ctx.fill(); }
  ctx.stroke();
  ctx.restore();
}

// ---------- the wheel of fortune ----------
// 20 wedges 0, 5, ... 95; wedge i sits under the pointer when the disc is turned by -i * 18 degrees.
// o: { speed 0..1 (motion blur), stand: floor y, hit: 0..1 glow on the pointer }
function k4Wheel(x, y, r, ang, t, o = {}) {
  const n = 20, step = Math.PI * 2 / n, sp = clamp(o.speed || 0);
  ctx.save(); ctx.translate(x, y);
  if (o.stand) {
    const fy = o.stand - y;
    [-1, 1].forEach(d => shape(() => { ctx.moveTo(d * r * .08, 0); ctx.lineTo(d * r * .5, fy); ctx.lineTo(d * r * .5 - d * 26, fy); ctx.lineTo(-d * r * .02, 10); ctx.closePath(); }, '#6a4428', { shade: 8, hi: 4, line: 5 }));
    shape(() => rrp(-r * .62, fy - 8, r * 1.24, 22, 6), '#5a3a22', { shade: 4, hi: 3, line: 5 });
  }
  shape(() => ctx.arc(0, 0, r * 1.1, 0, Math.PI * 2), '#7a4a2a', { shade: r * .06, hi: r * .05, line: 6 });
  for (let i = 0; i < n; i++) {  // bulbs around the rim, chasing
    const a = i * step + step / 2, on = (Math.floor(t * 8) + i) % 3 === 0;
    if (on) glow(Math.cos(a) * r * 1.05, Math.sin(a) * r * 1.05, r * .09, 'rgba(255,236,170,1)', .8);
    circle(Math.cos(a) * r * 1.05, Math.sin(a) * r * 1.05, r * .028, on ? '#fff6d0' : '#b89058');
  }
  ctx.save(); ctx.rotate(ang);
  for (let i = 0; i < n; i++) {
    const a0 = -Math.PI / 2 + i * step - step / 2;
    ctx.fillStyle = K4.WEDGE[i % 4]; ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, r, a0, a0 + step); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.stroke();
  }
  ctx.save(); ctx.globalAlpha = 1 - sp * .85;
  for (let i = 0; i < n; i++) {
    ctx.save(); ctx.rotate(i * step);
    const c = K4.WEDGE[i % 4], dark = c === '#f3ead8' || c === '#e8b54a';
    text(String(i * 5), 0, -r * .8, r * .13, dark ? '#1a1117' : '#fbf6e8', SANS, 900);
    ctx.restore();
  }
  ctx.restore();
  ctx.restore();
  if (sp > .05) {  // motion blur: a wash of the wedge colours and light streaks
    ctx.save(); ctx.globalAlpha = sp * .6; ctx.fillStyle = '#c8806a'; ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = sp * .7; ctx.strokeStyle = '#fff4dc'; ctx.lineCap = 'round';
    for (let i = 0; i < 7; i++) { const rr2 = r * (.3 + i * .1), a = ang * .9 + i * 1.7; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(0, 0, rr2, a, a + .9 + sp); ctx.stroke(); }
    ctx.restore();
  }
  shape(() => ctx.arc(0, 0, r * .13, 0, Math.PI * 2), '#d8b060', { shade: r * .03, hi: r * .03, line: 5 });
  if (o.hit > 0) { glow(0, -r * .8, r * .4, 'rgba(255,230,150,1)', .7 * o.hit); }
  shape(() => { ctx.moveTo(-r * .1, -r * 1.2); ctx.lineTo(r * .1, -r * 1.2); ctx.lineTo(0, -r * .9); ctx.closePath(); }, C.red, { shade: 3, hi: 3, line: 5 });
  ctx.restore();
}

// ---------- props ----------
// draw fn upright at the current origin (a hand), whatever the arm's rotation or the figure's mirroring
function k4Upright(fn) {
  const m = ctx.getTransform(), s = Math.sqrt(Math.abs(m.a * m.d - m.b * m.c));
  ctx.save(); ctx.setTransform(s, 0, 0, s, m.e, m.f); fn(); ctx.restore();
}
// a white card held up; num shows when k > 0, popping in; key names it in K4.cards
function k4Card(key, num, k) {
  return () => k4Upright(() => {
    const m = ctx.getTransform(); K4.cards[key] = [m.e + 0 * m.a, m.f - 90 * m.d];
    ctx.save(); ctx.translate(0, -90); ctx.rotate(-.04);
    shape(() => rrp(-80, -58, 160, 116, 8), '#fbf6e8', { shade: 6, hi: 4, line: 5 });
    if (k > 0) { ctx.save(); ctx.scale(back(clamp(k)), back(clamp(k))); text(String(num), 0, 6, 78, '#1a1117', SANS, 900); ctx.restore(); }
    ctx.restore();
  });
}
function k4Stick(t) {
  return () => { ctx.save(); ctx.lineCap = 'round'; ctx.strokeStyle = INK; ctx.lineWidth = 16; ctx.beginPath(); ctx.moveTo(0, 20); ctx.lineTo(0, 360); ctx.stroke();
    ctx.strokeStyle = '#a07040'; ctx.lineWidth = 9; ctx.stroke(); ctx.restore(); };
}
// a classic anchor, about 110 px tall at s 1
function k4AnchorIcon(x, y, s, color = C.gold, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.rotate(rot); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  const path = () => {
    ctx.beginPath(); ctx.arc(0, -46, 10, 0, Math.PI * 2);
    ctx.moveTo(-26, -30); ctx.lineTo(26, -30); ctx.moveTo(0, -36); ctx.lineTo(0, 46);
    ctx.moveTo(-38, 14); ctx.quadraticCurveTo(-30, 46, 0, 48); ctx.quadraticCurveTo(30, 46, 38, 14);
    ctx.moveTo(-46, 22); ctx.lineTo(-38, 10); ctx.lineTo(-28, 22); ctx.moveTo(46, 22); ctx.lineTo(38, 10); ctx.lineTo(28, 22);
  };
  path(); ctx.strokeStyle = INK; ctx.lineWidth = 17; ctx.stroke();
  path(); ctx.strokeStyle = color; ctx.lineWidth = 9; ctx.stroke();
  ctx.restore();
}
// a price tag on a string
function k4Tag(x, y, w, label, t, color = '#f2dca8') {
  if (w <= 1) return;
  const h = w * .62;
  ctx.save(); ctx.translate(x, y); ctx.rotate(-.12 + Math.sin(t * 2) * .04);
  ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(-w / 2 + h * .25, 0); ctx.quadraticCurveTo(-w * .7, -h * .8, -w * .62, -h * 1.1); ctx.stroke();
  shape(() => { ctx.moveTo(-w / 2, 0); ctx.lineTo(-w / 2 + h * .4, -h / 2); ctx.lineTo(w / 2, -h / 2); ctx.lineTo(w / 2, h / 2); ctx.lineTo(-w / 2 + h * .4, h / 2); ctx.closePath(); },
    color, { shade: w * .05, hi: w * .04, line: 4.5, after: () => { circle(-w / 2 + h * .25, 0, h * .09, '#3a2a20'); } });
  text(label, w * .1, h * .04, h * .62, '#7a1a1a', SANS, 900);
  ctx.restore();
}
function k4TagProp(x, y, w, t) { k4Tag(x - 10, y, w * 1.8, '?', t); }
function k4AnchorProp(x, y, w, t) { if (w > 8) { glow(x + 190, y - 120, w * 1.1, 'rgba(232,181,74,1)', .35); k4AnchorIcon(x + 190, y - 120, w / 80, C.gold, Math.sin(t * 2) * .08); } }
// a red rubber stamp, k 0..1 drops it from large onto the frame
function k4Stamp(label, x, y, size, k, rot = -.12) {
  if (k <= 0) return;
  const s = lerp(2.2, 1, eo(clamp(k)));
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s); ctx.globalAlpha = clamp(k * 3);
  const w = measure(label, size, SANS, 900, size * .08) + size * .9, h = size * 1.5;
  ctx.fillStyle = 'rgba(251,246,232,.9)'; rr(-w / 2, -h / 2, w, h, size * .16); ctx.fill();
  ctx.strokeStyle = C.red; ctx.lineWidth = size * .1; rr(-w / 2, -h / 2, w, h, size * .16); ctx.stroke();
  ctx.lineWidth = size * .03; rr(-w / 2 + size * .14, -h / 2 + size * .14, w - size * .28, h - size * .28, size * .1); ctx.stroke();
  text(label, 0, size * .05, size, C.red, SANS, 900, 'center', 0, C.ink, size * .08);
  ctx.restore();
}
// a comic speech bubble with a tail to (tx, ty)
function k4Bubble(x, y, w, h, tx, ty, k, fill = '#fbf6e8') {
  if (k <= 0) return;
  const s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.translate(-x, -y);
  shape(() => {
    ctx.moveTo(x - w / 2 + 30, y - h / 2); ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, 30); ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, 30);
    const bx = clamp(tx, x - w / 2 + 50, x + w / 2 - 50);
    ctx.lineTo(bx + 30, y + h / 2); ctx.lineTo(tx, ty); ctx.lineTo(bx - 20, y + h / 2);
    ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, 30); ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, 30); ctx.closePath();
  }, fill, { shade: 8, hi: 5, noRim: true, line: 6 });
  ctx.restore();
}

// ---------- the results: a gauge 0..100 ----------
// the guess starts at the anchor and slides toward its final value as k runs 0..1
const K4G = { top: 680, bot: 1210 };
const k4Y = v => lerp(K4G.bot, K4G.top, v / 100);
function k4Gauge(cx, anchorV, guessV, k, accent, t) {
  const g = lerp(anchorV, guessV, eio(clamp(k))), y = k4Y(g);
  shape(() => rrp(cx - 46, K4G.top - 26, 92, K4G.bot - K4G.top + 52, 46), '#15131a', { shade: 6, hi: 4, line: 5 });
  // the fill up to the guess
  ctx.save(); ctx.beginPath(); rr(cx - 34, K4G.top - 14, 68, K4G.bot - K4G.top + 28, 34); ctx.clip();
  const fg = ctx.createLinearGradient(0, y, 0, K4G.bot + 14); fg.addColorStop(0, accent === C.red ? C.red2 : C.gold2); fg.addColorStop(1, accent === C.red ? C.redDim : C.goldDim);
  ctx.fillStyle = fg; ctx.fillRect(cx - 34, y, 68, K4G.bot + 14 - y);
  ctx.globalAlpha *= .35; ctx.fillStyle = '#ffffff'; ctx.fillRect(cx - 22, y + 6, 10, K4G.bot - y);
  ctx.restore();
  for (let v = 0; v <= 100; v += 10) { ctx.strokeStyle = rgba(C.ivory, v % 50 ? .35 : .7); ctx.lineWidth = v % 50 ? 2 : 3; ctx.beginPath(); ctx.moveTo(cx + 18, k4Y(v)); ctx.lineTo(cx + 34, k4Y(v)); ctx.stroke(); }
  text('100', cx, K4G.top - 52, 24, rgba(C.ivory, .75), SANS, 800);
  text('0', cx, K4G.bot + 52, 24, rgba(C.ivory, .75), SANS, 800);
  // the anchor: a dashed red line and its tag on the left
  const ay = k4Y(anchorV);
  ctx.save(); ctx.setLineDash([10, 8]); ctx.strokeStyle = C.red2; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(cx - 60, ay); ctx.lineTo(cx + 46, ay); ctx.stroke(); ctx.restore();
  ctx.save(); ctx.translate(cx - 140, ay);
  ctx.fillStyle = 'rgba(8,7,10,.88)'; rr(-78, -34, 156, 68, 10); ctx.fill(); ctx.strokeStyle = C.red2; ctx.lineWidth = 3; rr(-78, -34, 156, 68, 10); ctx.stroke();
  k4AnchorIcon(-46, 2, .42, C.red2);
  text(String(anchorV), 22, 3, 44, C.ivory, SANS, 900);
  ctx.restore();
  // the guess: a marker on the right and the big percentage
  ctx.save(); ctx.translate(cx + 52, y);
  ctx.fillStyle = C.gold; ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(30, -20); ctx.lineTo(30, 20); ctx.closePath(); ctx.fill(); ctx.stroke();
  text('%' + Math.round(g), 40, 4, 78, C.gold, SANS, 900, 'left', 9);
  text('TAHMİN', 44, 58, 24, C.ivory, SANS, 900, 'left', 5, C.ink, 4);
  ctx.restore();
}

// ---------- the number sea ----------
function k4Sea(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 900); g.addColorStop(0, '#2a3a6a'); g.addColorStop(.6, '#d88a6a'); g.addColorStop(1, '#f6d29a');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 904);
  glow(850, 860, 320, 'rgba(255,226,160,1)', .7); circle(850, 880, 64, 'rgba(255,244,214,.95)');
  g = ctx.createLinearGradient(0, 900, 0, H); g.addColorStop(0, '#3f8aa6'); g.addColorStop(.3, '#1f5674'); g.addColorStop(1, '#081a2c');
  ctx.fillStyle = g; ctx.fillRect(0, 900, W, H - 900);
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 46; i++) {  // glitter and wave dashes drifting
    const y = 910 + Math.pow(rnd(i), 1.4) * 620, x = ((rnd(i + 3) * 1300 + t * (20 + 30 * rnd(i + 5))) % 1300) - 110, l = 20 + 50 * rnd(i + 7);
    ctx.globalAlpha = .12 + .25 * (1 - (y - 900) / 700) * (.5 + .5 * Math.sin(t * 3 + i));
    ctx.strokeStyle = '#fff0d0'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + l, y); ctx.stroke();
  }
  ctx.restore();
}
// a row of buoys every 10 along a surface at y, the scale the boats sail on
function k4Buoys(y, s, t, a = 1) {
  ctx.save(); ctx.globalAlpha *= a;
  for (let v = 0; v <= 100; v += 10) {
    const x = k4X(v), by = y + Math.sin(t * 2.4 + v * .2) * 4;
    shape(() => ctx.arc(x, by, 9 * s, Math.PI, 0), v % 50 ? '#e8e0d0' : C.red, { shade: 2, hi: 2, line: 3, noRim: true });
    if (v % 50 === 0) text(String(v), x, by + 34 * s, 26 * s, rgba(C.ivory, .9), SANS, 900, 'center', 5);
  }
  ctx.restore();
}
// a little sailing boat; the sail carries the guess; who sits in it
function k4Boat(x, y, s, label, t, who, seed, rowing) {
  const bob = Math.sin(t * 2.2 + seed) * 5, rock = Math.sin(t * 1.7 + seed) * .03;
  ctx.save(); ctx.translate(x, y + bob); ctx.rotate(rock); ctx.scale(s, s);
  shape(() => rrp(-5, -230, 10, 230, 3), '#5a3a22', { shade: 2, hi: 2, line: 4 });
  shape(() => { ctx.moveTo(8, -222); ctx.lineTo(8, -40); ctx.lineTo(118, -40); ctx.closePath(); }, '#f6efe0', { shade: 10, hi: 6, line: 4.5 });
  figure(who, -40, 60, .2, t, { legs: 'hide', expr: 'calm', seed, noShadow: true, hideBack: true,
    front: rowing ? { sh: .9 + Math.sin(t * 6 + seed) * .4, el: .6, hand: 'fist' } : { sh: .3, el: .5 } });
  shape(() => { ctx.moveTo(-110, -30); ctx.lineTo(110, -30); ctx.quadraticCurveTo(96, 30, 60, 34); ctx.lineTo(-64, 34); ctx.quadraticCurveTo(-100, 30, -110, -30); ctx.closePath(); },
    '#8a4a2a', { shade: 10, hi: 6, line: 5, after: () => { ctx.fillStyle = '#e8b54a'; ctx.fillRect(-104, -22, 208, 7); } });
  ctx.restore();
  text(label, x, y + bob - 272 * s, 58 * s, C.gold2, SANS, 900, 'center', 9);
}

// ---------- the bazaar ----------
function k4Rug(x, y, w, h, cols, t, seed) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(Math.sin(t * 1.1 + seed) * .012);
  shape(() => rrp(-w / 2, 0, w, h, 4), cols[0], { shade: 12, hi: 6, line: 5,
    after: () => {
      ctx.strokeStyle = cols[1]; ctx.lineWidth = 10; ctx.strokeRect(-w / 2 + 16, 16, w - 32, h - 32);
      ctx.fillStyle = cols[2]; ctx.beginPath(); ctx.moveTo(0, h * .2); ctx.lineTo(w * .3, h / 2); ctx.lineTo(0, h * .8); ctx.lineTo(-w * .3, h / 2); ctx.closePath(); ctx.fill();
      ctx.fillStyle = cols[1]; ctx.beginPath(); ctx.moveTo(0, h * .34); ctx.lineTo(w * .15, h / 2); ctx.lineTo(0, h * .66); ctx.lineTo(-w * .15, h / 2); ctx.closePath(); ctx.fill();
      for (let i = 0; i < 4; i++) { ctx.fillStyle = cols[2]; ctx.fillRect(-w / 2 + 30 + i * (w - 60) / 3 - 6, h - 46, 12, 12); }
    } });
  ctx.strokeStyle = cols[1]; ctx.lineWidth = 3;  // fringe
  for (let i = 0; i < 12; i++) { const fx = -w / 2 + 8 + i * (w - 16) / 11; ctx.beginPath(); ctx.moveTo(fx, h); ctx.lineTo(fx + Math.sin(t * 2 + i) * 2, h + 18); ctx.stroke(); }
  ctx.restore();
}
function k4Bazaar(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1320); g.addColorStop(0, '#a85a3a'); g.addColorStop(1, '#6e3424');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1324);
  // the awning: red and cream stripes with a scalloped edge
  for (let i = 0; i < 12; i++) { ctx.fillStyle = i % 2 ? '#f3ead8' : '#c43a3a'; ctx.fillRect(i * 90, 480, 90, 150); }
  ctx.save(); for (let i = 0; i < 24; i++) { ctx.fillStyle = Math.floor(i / 2) % 2 ? '#f3ead8' : '#c43a3a'; ctx.beginPath(); ctx.arc(i * 45 + 22.5, 630, 22.5, 0, Math.PI); ctx.fill(); } ctx.restore();
  ctx.fillStyle = 'rgba(40,10,10,.25)'; ctx.fillRect(0, 652, W, 40);
  [[200, 680, 240, 420, ['#8a1e24', '#e8b54a', '#24345a'], 1], [480, 690, 230, 400, ['#24345a', '#e8d6b0', '#a83a2a'], 2], [770, 680, 250, 430, ['#2a5a4a', '#e8b54a', '#8a1e24'], 3]]
    .forEach(([x, y, w, h, c, s]) => k4Rug(x, y, w, h, c, t, s));
  [[340, 700], [640, 706], [930, 700]].forEach(([x, y], i) => {  // brass lanterns
    const sw = Math.sin(t * 1.4 + i) * 4;
    glow(x + sw, y + 70, 110, 'rgba(255,200,120,1)', .45);
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x, y - 30); ctx.lineTo(x + sw, y + 40); ctx.stroke();
    shape(() => { ctx.moveTo(x + sw - 20, y + 40); ctx.lineTo(x + sw + 20, y + 40); ctx.lineTo(x + sw + 14, y + 100); ctx.lineTo(x + sw - 14, y + 100); ctx.closePath(); }, '#d8a040', { shade: 4, hi: 4, line: 4 });
  });
  g = ctx.createLinearGradient(0, 1320, 0, H); g.addColorStop(0, '#8a7a68'); g.addColorStop(1, '#3a3028');
  ctx.fillStyle = g; ctx.fillRect(0, 1320, W, H - 1320);
}
function k4Counter(t) {
  shape(() => rrp(560, 1270, 560, 200, 6), '#7a4a2a', { shade: 14, hi: 6, line: 6,
    after: () => { ctx.strokeStyle = 'rgba(30,10,0,.3)'; ctx.lineWidth = 3; for (let i = 0; i < 4; i++) { ctx.beginPath(); ctx.moveTo(560, 1310 + i * 40); ctx.lineTo(1120, 1310 + i * 40); ctx.stroke(); } } });
  shape(() => rrp(540, 1250, 600, 30, 6), '#9a643a', { shade: 5, hi: 5, line: 5 });
  [[640, 1250, 44], [730, 1250, 32], [1010, 1250, 40]].forEach(([x, y, r]) => {  // copper pots
    shape(() => { ctx.moveTo(x - r, y); ctx.bezierCurveTo(x - r * 1.3, y - r * 1.6, x + r * 1.3, y - r * 1.6, x + r, y); ctx.closePath(); }, '#c8743a', { shade: 8, hi: 8, line: 5 });
    shape(() => rrp(x - r * .45, y - r * 1.45, r * .9, r * .3, 4), '#a85a2a', { shade: 3, hi: 3, line: 4 });
  });
}

Object.assign(SAHNE, {
  cark(V, L, t, lt) {
    // the classroom: the researcher walks to a wheel of fortune and gives it a hard spin; two students watch
    K4.day();
    const S = K4.spin(t), tw = wordTime(L, V.wheel, .6), walk = prog(t, L.t0 + .1, tw), grab = prog(t, S.t0 - .45, S.t0 - .05), swing = prog(t, S.t0 - .05, S.t0 + .2);
    cam(560, 940, lerp(1.04, 1.14, eio(prog(lt, 0, 4.5))), () => {
      k4Room(t, { noClock: true });
      const wx = 680, wy = 960, wr = 200;
      k4Wheel(wx, wy, wr, S.ang, t, { speed: S.speed, stand: 1330 });
      const rx = lerp(110, 360, eio(walk));
      const arm = swing > 0 ? { sh: lerp(1.3, 2.6, eo(swing)), el: lerp(.4, .1, swing), hand: 'open' }
        : grab > 0 ? { sh: lerp(.5, 1.3, eio(grab)), el: lerp(1.0, .4, grab), hand: 'open' }
          : { sh: .5, el: 1.1, hand: 'hold', prop: () => clipboard(0, 50, 80, t, .2) };
      figure('arastirmaci', rx, 1340, .52, t, { expr: S.speed > .2 ? 'sly' : 'warm', legs: walk > 0 && walk < 1 ? 'walk' : 'stand', phase: walk * 14,
        talk: t > tw ? talk(t, L) * .4 : 0, front: arm, back: { sh: .1, el: .3 }, lean: .05 * swing, seed: 6 });
      // the students at their desks in front, leaning in once it spins
      const lean = eo(prog(t, S.t0, S.t0 + .6));
      figure(K4.ali, 150, 1790, .62, t, { expr: lean > .5 ? 'surprised' : { ...EXPR.calm, look: [.4, -.2] }, lean: .06 * lean, legs: 'hide', front: { sh: .4, el: 1.2 }, back: { sh: .1, el: .3 }, seed: 9 });
      figure(K4.ayse, 960, 1790, .62, t, { dir: -1, expr: lean > .5 ? 'happy' : { ...EXPR.calm, look: [.4, -.2] }, lean: .06 * lean, legs: 'hide', front: { sh: .4, el: 1.2 }, back: { sh: .1, el: .3 }, seed: 5 });
      k4Desk(170, 1420, 300); k4Desk(930, 1420, 300);
    });
    plate(V.year, 540, 520, 50, back(prog(t, L.t0 + .1, L.t0 + .45)));
    flash(t, S.t0 - .05, .35);
  },
  hile(V, L, t, lt) {
    // close on the wheel: "rigged" is stamped on it; it stops on 10, the number flies to Ali's card; a second
    // spin stops on 65 for Ayşe
    K4.day();
    const S = K4.spin(t), tk = wordTime(L, V.key, .3);
    const f1 = prog(t, S.t1, S.t1 + .45), f2 = prog(t, S.t3, S.t3 + .45);
    cam(540, 980, lerp(1.02, 1.08, prog(lt, 0, 4)), () => {
      drawSoft(.4, () => cam(560, 1000, 1.5, () => k4Room(t)), .2);
      ctx.fillStyle = 'rgba(20,24,40,.25)'; ctx.fillRect(0, 0, W, H);
      const hit = t > S.t1 && t < S.t2 + .1 ? 1 - prog(t, S.t1 + .3, S.t2 + .1) : t > S.t3 ? 1 - prog(t, S.t3 + .8, S.t3 + 1.6) * .6 : 0;
      k4Wheel(540, 860, 250, S.ang, t, { speed: S.speed, stand: 1700, hit });
      figure(K4.ali, 110, 2050, .95, t, { expr: f1 > .5 ? 'warm' : { ...EXPR.neutral, look: [.5, -.5] }, headTilt: -.06, legs: 'hide',
        front: { sh: lerp(.4, 1.1, eo(prog(t, S.t1 - .4, S.t1))), el: lerp(1.2, .9, f1), hand: 'hold', prop: k4Card('ali', 10, f1 >= 1 ? 1 : 0) }, back: { sh: .1, el: .3 }, seed: 9 });
      figure(K4.ayse, 970, 2050, .95, t, { dir: -1, expr: f2 > .5 ? 'warm' : { ...EXPR.neutral, look: [.5, -.5] }, headTilt: -.06, legs: 'hide',
        front: { sh: lerp(.4, 1.1, eo(prog(t, S.t3 - .4, S.t3))), el: lerp(1.2, .9, f2), hand: 'hold', prop: k4Card('ayse', 65, f2 >= 1 ? 1 : 0) }, back: { sh: .1, el: .3 }, seed: 5 });
    });
    // the winning number flies from the pointer to the card
    [[f1, 10, 'ali', C.gold2], [f2, 65, 'ayse', C.red2]].forEach(([f, num, who, col]) => {
      if (f <= 0 || f >= 1 || !K4.cards[who]) return;
      const [cx, cy] = K4.cards[who], m = ctx.getTransform();
      const x = lerp(540, cx, eio(f)), y = lerp(700, cy, eio(f)) - Math.sin(f * Math.PI) * 160;
      ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); text(String(num), x, y, lerp(110, 70, f), col, SANS, 900, 'center', 10); ctx.restore();
    });
    k4Stamp('HİLELİ', 800, 610, 64, prog(t, tk - .05, tk + .2), .14);
  },
  soru(V, L, t, lt) {
    // the question on the board: the UN's members, Africa outlined, "% ?"; the researcher taps Africa with a stick
    K4.day();
    const tu = wordTime(L, V.un, .3), tp = wordTime(L, V.pct, .55), ta = wordTime(L, V.key, .8);
    const write = prog(t, L.t0, tu + .5), pop = back(prog(t, tp - .05, tp + .3)), fill = prog(t, ta - .1, ta + .4);
    const tap = t > ta - .1 && t < ta + .5 ? Math.abs(Math.sin((t - ta) * 14)) * .12 : 0;
    cam(540, 980, lerp(1.0, 1.07, eio(prog(lt, 0, 4))), () => {
      k4Room(t, { noBoard: true, clockX: 980, clockY: 560 });
      k4Board(60, 600, 820, 470, t, () => {
        ctx.save(); ctx.beginPath(); ctx.rect(60, 600, 820 * write, 120); ctx.clip();
        text('BM ÜLKELERİNİN', 470, 665, 52, '#f3ead8', SANS, 800, 'center', 0, C.ink, 4); ctx.restore();
        k4Africa(300, 880, .95, fill, '#f3ead8', clamp(write * 2));
        text('AFRİKA', 300, 1040, 30, rgba('#f3ead8', clamp(fill * 2)), SANS, 800);
        if (pop > 0) { ctx.save(); ctx.translate(650, 880); ctx.scale(pop, pop); text('% ?', 0, 0, 170, '#f3ead8', SANS, 900); ctx.restore(); }
      });
      figure('arastirmaci', 960, 1600, .66, t, { dir: -1, expr: 'warm', talk: talk(t, L), legs: 'stand',
        front: { sh: 1.45 + tap, el: .15, hand: 'hold', prop: k4Stick(t) }, back: { sh: .1, el: .3 }, seed: 6 });
      const puzzled = prog(t, tp, tp + .4);
      figure(K4.ali, 150, 2120, .92, t, { expr: puzzled > .5 ? 'confused' : { ...EXPR.neutral, look: [.3, -.7] }, headTilt: -.08, legs: 'hide',
        front: puzzled > .3 ? { sh: .4 + Math.sin(t * 7) * .04, el: 2.75, hand: 'open' } : { sh: .3, el: .5 }, back: { sh: .1, el: .3 }, seed: 9 });
      figure(K4.ayse, 560, 2140, .88, t, { dir: -1, expr: puzzled > .5 ? 'confused' : { ...EXPR.neutral, look: [.3, -.7] }, headTilt: -.1, legs: 'hide',
        front: { sh: .4, el: 2.75, hand: 'open' }, back: { sh: .1, el: .3 }, seed: 5 });
      thought(330, 1170, 120, 100, 220, 1250, puzzled);
      if (puzzled > .4) text('?', 330, 1174, 70, '#1a1117', SANS, 900);
    });
  },
  sonuc(V, L, t, lt) {
    // those who saw 10: Ali holds his card; the gauge starts at the anchor and creeps up to 25
    K4.day();
    cam(540, 1000, 1.1, () => k4Room(t));
    ctx.fillStyle = 'rgba(10,12,24,.66)'; ctx.fillRect(0, 0, W, H);
    const k = prog(t, L.t0 + .35, wordTime(L, V.key, .8) + .2);
    const show = clamp(prog(lt, 0, .3));
    ctx.save(); ctx.globalAlpha = show;
    plate(V.label, 640, 560, 40, 1, C.gold);
    k4Gauge(690, V.anchor, V.pct, k, C.gold, t);
    ctx.restore();
    figure(K4.ali, lerp(-60, 130, eo(prog(lt, 0, .5))), 2000, .92, t, { expr: k >= 1 ? 'happy' : { ...EXPR.calm, look: [.5, -.3] }, headTilt: k > 0 && k < 1 ? -.06 : 0,
      front: { sh: 2.0, el: .25, hand: 'hold', prop: k4Card('ali', V.anchor, 1) }, back: { sh: .1, el: .3 }, legs: 'hide', seed: 9, nod: k >= 1 ? Math.sin(t * 8) * .03 : 0 });
  },
  kontrol(V, L, t, lt) {
    // the first gauge slides left; those who saw 65 start at 65 and come down only to 45
    K4.day();
    cam(540, 1000, 1.1, () => k4Room(t));
    ctx.fillStyle = 'rgba(10,12,24,.66)'; ctx.fillRect(0, 0, W, H);
    const A = EP.lines[4].v, slide = eio(prog(lt, 0, .55)), k = prog(t, L.t0 + .45, wordTime(L, V.key, .85) + .2);
    plate(A.label, lerp(640, 290, slide), 560, lerp(40, 30, slide), 1, C.gold, 400);
    k4Gauge(lerp(690, 300, slide), A.anchor, A.pct, 1, C.gold, t);
    ctx.save(); ctx.globalAlpha = clamp(prog(lt, .3, .6));
    plate(V.label, 770, 560, 30, 1, C.red, 400);
    k4Gauge(750, V.anchor, V.pct, k, C.red, t);
    ctx.restore();
    const out = eio(prog(lt, 0, .35));
    if (out < 1) figure(K4.ali, lerp(130, -320, out), 2000, .92, t, { expr: 'happy', legs: 'hide', front: { sh: .3, el: .3 }, back: { sh: .1, el: .3 }, seed: 9 });
    figure(K4.ayse, lerp(1250, 990, eo(prog(lt, .2, .8))), 1930, .8, t, { dir: -1, expr: k >= 1 ? 'happy' : { ...EXPR.calm, look: [.5, -.4] },
      front: { sh: 1.2, el: .9, hand: 'hold', prop: k4Card('ayse', V.anchor, 1) }, back: { sh: .1, el: .3 }, legs: 'hide', seed: 5, nod: k >= 1 ? Math.sin(t * 8) * .03 : 0 });
  },
  neden(V, L, t, lt) {
    // "the wheel had nothing to do with the question": wheel ≠ Africa. Then the why: on the number sea each boat
    // drops anchor at the number it saw and sails off, but the rope stops it at 25 and 45
    K4.day();
    const tc = wordTime(L, V.cut, .35);
    if (t < tc - .05) {
      cam(540, 1000, 1.1, () => k4Room(t));
      ctx.fillStyle = 'rgba(10,12,24,.6)'; ctx.fillRect(0, 0, W, H);
      const th = wordTime(L, 'hiçbir', .2), neq = back(prog(t, th - .1, th + .25));
      k4Wheel(280, 900, 170, t * .8, t, {});
      k4Africa(800, 900, .9, 1, '#f3ead8');
      if (neq > 0) {
        ctx.save(); ctx.translate(540, 900); ctx.scale(neq, neq); ctx.lineCap = 'round';
        [0, 1].forEach(i => { ctx.strokeStyle = INK; ctx.lineWidth = 34; ctx.beginPath(); ctx.moveTo(-60, -26 + i * 52); ctx.lineTo(60, -26 + i * 52); ctx.stroke(); ctx.strokeStyle = C.red2; ctx.lineWidth = 20; ctx.stroke(); });
        ctx.strokeStyle = INK; ctx.lineWidth = 34; ctx.beginPath(); ctx.moveTo(34, -80); ctx.lineTo(-34, 80); ctx.stroke(); ctx.strokeStyle = C.red2; ctx.lineWidth = 20; ctx.stroke();
        ctx.restore();
      }
      plate('Hiçbir ilgisi yok', 540, 560, 50, back(prog(t, th, th + .3)), C.red);
      return;
    }
    const ta = wordTime(L, V.key, .6), tf = wordTime(L, V.far, .85);
    const drop = [prog(t, ta - .05, ta + .45), prog(t, ta + .15, ta + .65)];
    const sail = prog(t, tf - 1.0, tf + .2), jerk = prog(t, tf + .2, tf + .9);
    const recoil = Math.sin(jerk * Math.PI * 2.5) * (1 - jerk) * 14;
    cam(540, 920, lerp(1.06, 1.0, eio(prog(t, tc, tc + 1))), () => {
      k4Sea(t);
      // two rows: the far one (saw 10) and the near one (saw 65)
      [[0, 1000, 1.0, 10, 25, K4.ali, 1, 1170, C.gold], [1, 1250, 1.2, 65, 45, K4.ayse, -1, 1360, C.red2]].forEach(([i, sy, s, from, to, who, dir, ay, col]) => {
        k4Buoys(sy + 26 * s, s, t, .8);
        const v = lerp(from, to, eo(sail)), bx = k4X(v) + (sail >= 1 ? recoil * dir : 0), ax = k4X(from), d = drop[i];
        const ancY = lerp(sy, ay, eo(d));
        if (d > 0) {
          // the rope from the stern to the anchor: slack, then straight
          const sx = bx - dir * 100 * s, sy2 = sy + 10 * s, mx = (sx + ax) / 2, my = (sy2 + ancY) / 2 + 60 * (1 - sail);
          ctx.strokeStyle = INK; ctx.lineWidth = 7; ctx.beginPath(); ctx.moveTo(sx, sy2); ctx.quadraticCurveTo(mx, my, ax, ancY - 50 * s * .9); ctx.stroke();
          ctx.strokeStyle = '#d8c8a0'; ctx.lineWidth = 3.5; ctx.stroke();
          ctx.save(); ctx.globalAlpha = d > 0 && d < 1 ? .6 : 0; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 4;
          ctx.beginPath(); ctx.ellipse(ax, sy + 20, 30 + 60 * d, 8 + 10 * d, 0, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
          k4AnchorIcon(ax, ancY, .8 * s, '#b8c0cc');
          ctx.save(); ctx.translate(ax - dir * 70 * s, ancY + 6);
          ctx.fillStyle = 'rgba(8,7,10,.85)'; rr(-46 * s, -28 * s, 92 * s, 56 * s, 8); ctx.fill();
          ctx.strokeStyle = C.red2; ctx.lineWidth = 3; rr(-46 * s, -28 * s, 92 * s, 56 * s, 8); ctx.stroke();
          text(String(from), 0, 2, 38 * s, C.ivory, SANS, 900);
          ctx.restore();
        }
        k4Boat(bx, sy, s, '%' + Math.round(v), t, who, i * 3 + 1, sail > 0 && sail < 1);
      });
    });
    plate('Çapa etkisi', 540, 560, 54, back(prog(t, ta + .1, ta + .45)), C.gold);
    flash(t, tc - .05, .4);
  },
  pazar(V, L, t, lt) {
    // a rug stall: the buyer means to offer 300; the seller says 1.000 first; the buyer ends up asking "800?"
    K4.day();
    const ti = wordTime(L, V.key, .3), ts = wordTime(L, V.said, .45), tb = wordTime(L, 'bütün', .6), ta = wordTime(L, V.anchor, .8);
    const sell = prog(t, ti - .05, ti + .3), cross1 = prog(t, ts, ts + .35), say = prog(t, tb - .05, tb + .3), anc = prog(t, ta - .4, ta + .05);
    cam(540, 930, lerp(1.0, 1.05, eio(prog(lt, 0, 3.5))), () => {
      k4Bazaar(t);
      figure(K4.seller, 820, 1720, .72, t, { dir: -1, expr: sell > .5 ? 'sly' : 'warm', talk: sell > 0 && say < .5 ? talk(t, L) : 0, legs: 'hide',
        front: sell > 0 ? { sh: lerp(.6, 1.7, eo(sell)) + Math.sin(t * 5) * .05 * sell, el: lerp(1.0, .2, sell), hand: 'palm' } : { sh: .4, el: 1.0 },
        back: { sh: .3, el: .9 }, seed: 7 });
      k4Counter(t);
      figure(K4.buyer, 250, 1800, .76, t, { expr: cross1 > .5 && say < .5 ? 'surprised' : say > .5 ? { ...EXPR.neutral, curve: -.05, look: [.5, 0] } : 'calm', talk: say > 0 ? talk(t, L) * .6 : 0,
        front: say > 0 ? { sh: lerp(.4, 1.4, eo(say)), el: .5, hand: 'point' } : { sh: .4 + Math.sin(t * 6) * .03, el: 2.75, hand: 'open' }, back: { sh: .1, el: .3 }, legs: 'hide', seed: 5 });
      // her plan: 300 in a thought, crossed out once she hears his number
      const th = (1 - prog(t, tb - .1, tb + .1));
      if (th > 0) {
        ctx.save(); ctx.globalAlpha = th; thought(280, 860, 300, 170, 270, 1020, prog(t, L.t0 - .1, L.t0 + .3));
        text('300 TL', 280, 864, 64, '#1a1117', SANS, 900); cross(280, 864, 170, cross1); ctx.restore();
      }
      k4Bubble(770, 850, 380, 170, 790, 990, sell);
      if (sell > .3) text('1.000 TL', 770, 854, 76, C.red, SANS, 900);
      k4Bubble(310, 880, 300, 150, 290, 1030, say);
      if (say > .3) text('800 TL?', 310, 884, 64, '#1a1117', SANS, 900);
      if (anc > 0) {  // the gold anchor lands on his number, a chain to hers
        const ay = lerp(600, 720, eo(anc));
        glow(770, ay, 120, 'rgba(232,181,74,1)', .4 * anc);
        if (anc >= 1) { ctx.save(); ctx.setLineDash([16, 10]); ctx.strokeStyle = C.gold; ctx.lineWidth = 8; ctx.beginPath(); ctx.moveTo(700, 940); ctx.quadraticCurveTo(570, 1040, 440, 940); ctx.stroke(); ctx.restore(); }
        k4AnchorIcon(770, ay, .9, C.gold, Math.sin(t * 3) * .06);
      }
    });
    plate('İlk fiyat = çapa', 540, 560, 52, back(prog(t, ta - .4, ta - .05)), C.gold);
  }
});
