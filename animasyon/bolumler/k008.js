// k008 "Önce söz verdir" (Moriarty 1975, J. Pers. Soc. Psychol. 31(2), 370-376, study 1; ERIC ED076923). Jones Beach,
// New York, summer 1972: a researcher spread a blanket within five feet of a sunbather, played a portable radio, then
// spoke to the sunbather and walked off toward the boardwalk. No commitment: he only asked for a light ("I have no
// matches, do you have a light?"). Commitment: "Would you watch my things?" (everyone said yes). Two or three minutes
// later a second researcher took the still-playing radio and walked away. 4 of 20 reacted without the promise, 19 of 20
// with it (called out, chased, or demanded an explanation). The "yes" turns a bystander into someone with a promise.
// The hook is a beach today: a thief creeps up on a bag next to a man on his phone.
const K8 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  sun() { setLight(-.66, -.7, { rim: '#ffe2b0', rimA: .5, warm: '#ffe6bf', cool: '#3a3050' }); },
  // today: the man on his phone in a red tee, the thief in charcoal
  komsu: { ...CAST.arastirmaci2, outfit: 'tee', sleeve: 'short', coat: '#c8363a', breeches: '#2a4a6a', hose: '#e2b08c', shoe: '#3a2a20' },
  hirsiz: { ...CAST.uye, outfit: 'tee', sleeve: 'short', coat: '#34363e', breeches: '#4a4a52', hose: '#d9a07c', skin: '#d9a07c', shoe: '#1c1a1e',
    hair: 'short', hairColor: '#1a1410', brow: '#1a1410' },
  // 1972: the researcher who leaves the radio (mustard striped tee), the sunbather (coral sundress), the "thief" (green)
  sahip: { ...CAST.arastirmaci, outfit: 'tee', sleeve: 'short', coat: '#e0a83a', stripe: '#a8522a', breeches: '#3a4a6a', hose: '#f0c4a2',
    shoe: '#5a3a20', glasses: false, hairColor: '#6a3a1a' },
  kadin: { ...CAST.evsahibi, coat: '#e0604a', trim: '#f6efe0', hairColor: '#b8682a', shoe: '#7a3a22' },
  rol: { ...CAST.uye, outfit: 'tee', sleeve: 'short', coat: '#3f6a46', breeches: '#2e3440', hose: '#e3b08e', shoe: '#2a2018',
    hair: 'side', hairColor: '#2a1a12' }
};
const K8INK = '#1a1117', K8GOLD = '#9a6400';

// beach clothes: outfit 'tee' is a T-shirt over shorts (bare forearms come from sleeve: 'short'). The engine draws an
// outfit it does not know with the coat functions, so those draw the tee and the shorts here; noSkirt hides a sitting
// woman's skirt (her lap is drawn with the legs)
(() => {
  const wrap = (name, fn) => { const orig = window[name]; window[name] = (spec, ...a) => fn(spec, orig, ...a); };
  wrap('coatUpper', (spec, orig) => spec.outfit === 'tee' ? k8Tee(spec) : orig(spec));
  wrap('coatSkirt', (spec, orig) => spec.outfit === 'tee' ? k8Shorts(spec) : orig(spec));
  wrap('neckwear', (spec, orig) => spec.outfit === 'tee' ? k8Collar(spec) : orig(spec));
  wrap('dressSkirt', (spec, orig) => spec.noSkirt ? null : orig(spec));
})();
function k8Tee(spec) {
  const side = torsoSide(spec, .97);
  shape(() => { side(); ctx.closePath(); }, spec.coat, { shade: 30, hi: 15, outline: side,
    after: () => {
      if (spec.stripe) { ctx.fillStyle = spec.stripe; [-262, -206, -150].forEach(y => ctx.fillRect(-170, y, 340, 24)); }
      ctx.fillStyle = shadeOf(spec.coat, .3); ctx.fillRect(-170, -8, 340, 20);
    } });
}
function k8Shorts(spec) {
  const be = spec.build.belly;
  shape(() => rrp(-86 * be, -30, 180 * be, 96, 24), spec.breeches, { shade: 22, hi: 10 });
}
function k8Collar(spec) {
  stroke(shadeOf(spec.coat, .45), 9, () => { ctx.moveTo(-26, -356); ctx.quadraticCurveTo(22, -318, 70, -356); });
}

// ---------- small things ----------
const k8Hold = (fn, sh, el, o = {}) => spec => {
  const a = sh + el, hx = 30 * Math.sin(a), hy = 30 * Math.cos(a);
  const draw = () => { ctx.save(); ctx.rotate(a); fn(hx, hy); ctx.restore(); };
  if (o.under) draw();
  hand(spec, o.hand || 'hold', 1.3 * Math.sqrt(spec.build.arm || 1));
  if (!o.under) draw();
};
// a 1970s portable radio, bottom centre at (x, y); s 1 is 170 wide. on 0..1 lets music notes float out of it
function k8Radio(x, y, s, t, on = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  stroke('#d8d2c2', 5, () => { ctx.moveTo(56, -118); ctx.lineTo(112, -236); });
  circle(112, -236, 6, '#d8d2c2');
  stroke(INK, 16, () => { ctx.moveTo(-52, -116); ctx.quadraticCurveTo(-4, -176, 44, -116); });
  stroke('#3a2a24', 8, () => { ctx.moveTo(-52, -116); ctx.quadraticCurveTo(-4, -176, 44, -116); });
  shape(() => rrp(-85, -122, 170, 122, 18), '#c8362c', { shade: 9, hi: 6, line: 5, noRim: true });
  shape(() => rrp(-72, -106, 84, 90, 10), '#efe4cc', { shade: 4, hi: 3, line: 4, noRim: true,
    after: () => { ctx.fillStyle = 'rgba(60,40,30,.55)'; for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) circle(-58 + c * 14, -90 + r * 15, 3.6, ctx.fillStyle); } });
  shape(() => rrp(22, -106, 50, 36, 6), '#2a2a2e', { shade: 3, hi: 3, line: 4, noRim: true,
    after: () => { ctx.fillStyle = '#f6efe0'; ctx.fillRect(28, -94, 38, 3); ctx.fillStyle = C.red2; ctx.fillRect(36 + 18 * (.5 + .5 * Math.sin(t * .7)), -100, 3, 24); } });
  [34, 60].forEach(cx => shape(() => ctx.arc(cx, -40, 11, 0, Math.PI * 2), '#e8e0cc', { shade: 3, hi: 3, line: 4, noRim: true }));
  ctx.restore();
  if (on > 0) k8Notes(x - 10 * s, y - 140 * s, t, on, s);
}
function k8NoteGlyph(x, y, r, col, rot) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.fillStyle = col; ctx.strokeStyle = col; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.ellipse(0, 0, r * 1.25, r * .9, -.4, 0, Math.PI * 2); ctx.fill();
  ctx.lineWidth = r * .38; ctx.beginPath(); ctx.moveTo(r * 1.05, -r * .2); ctx.lineTo(r * 1.05, -r * 3.6);
  ctx.quadraticCurveTo(r * 2.3, -r * 2.7, r * 2.1, -r * 1.5); ctx.stroke();
  ctx.restore();
}
function k8Notes(x, y, t, k, s = 1) {
  for (let i = 0; i < 4; i++) {
    const life = (t * .5 + i / 4) % 1;
    const nx = x + Math.sin(life * 5 + i * 2) * 34 * s + (i - 1.5) * 34 * s, ny = y - life * 230 * s;
    ctx.save(); ctx.globalAlpha *= clamp(k) * Math.sin(life * Math.PI);
    k8NoteGlyph(nx, ny, 12 * s, i % 2 ? '#2a1e2a' : '#fbf6e8', Math.sin(t * 2 + i) * .25);
    ctx.restore();
  }
}
// a straw beach bag, bottom centre at (x, y); a phone peeks out
function k8Bag(x, y, s = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  shape(() => rrp(-40, -176, 44, 76, 8), '#22252c', { shade: 3, hi: 3, line: 4, noRim: true,
    after: () => { ctx.fillStyle = '#7ab8e8'; ctx.fillRect(-34, -168, 32, 50); } });
  shape(() => { ctx.moveTo(-92, -128); ctx.lineTo(92, -128); ctx.lineTo(76, 0); ctx.lineTo(-76, 0); ctx.closePath(); }, '#d9b070', { shade: 10, hi: 6, line: 5, noRim: true,
    after: () => { ctx.strokeStyle = 'rgba(120,80,30,.35)'; ctx.lineWidth = 3; for (let i = 0; i < 6; i++) { ctx.beginPath(); ctx.moveTo(-92, -110 + i * 20); ctx.lineTo(92, -110 + i * 20); ctx.stroke(); }
      ctx.fillStyle = '#c8362c'; ctx.fillRect(-92, -96, 184, 16); } });
  stroke(INK, 14, () => { ctx.moveTo(-56, -126); ctx.quadraticCurveTo(0, -226, 56, -126); });
  stroke('#a8743a', 7, () => { ctx.moveTo(-56, -126); ctx.quadraticCurveTo(0, -226, 56, -126); });
  ctx.restore();
}
function k8Phone(x, y, s = 1, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s);
  shape(() => rrp(-20, -38, 40, 76, 8), '#1e2028', { shade: 3, hi: 3, line: 4, noRim: true,
    after: () => { ctx.fillStyle = '#8fd0ff'; ctx.fillRect(-15, -32, 30, 62); } });
  ctx.restore();
}
function k8Book(x, y, s = 1, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s);
  shape(() => rrp(-58, -40, 116, 74, 6), '#2f6a8a', { shade: 4, hi: 3, line: 4.5, noRim: true });
  shape(() => { ctx.moveTo(-52, -36); ctx.lineTo(0, -28); ctx.lineTo(52, -36); ctx.lineTo(52, 28); ctx.lineTo(0, 34); ctx.lineTo(-52, 28); ctx.closePath(); }, '#f6efe0',
    { shade: 3, hi: 2, line: 3.5, noRim: true, after: () => { ctx.fillStyle = 'rgba(40,40,60,.3)'; for (let i = 0; i < 4; i++) { ctx.fillRect(-44, -18 + i * 12, 36, 3); ctx.fillRect(8, -18 + i * 12, 36, 3); } } });
  ctx.restore();
}
const k8RadioIn = (sh, el, t, s = 1.25) => k8Hold((hx, hy) => k8Radio(hx + 4 * s, hy + 142 * s, s, t, 1), sh, el, { under: true });
const k8BagIn = (sh, el, s = 1.25) => k8Hold((hx, hy) => k8Bag(hx, hy + 172 * s, s), sh, el, { under: true });
const k8PhoneIn = (sh, el) => k8Hold((hx, hy) => k8Phone(hx + 6, hy - 30, 1.15, -.15), sh, el);
const k8BookIn = (sh, el) => k8Hold((hx, hy) => k8Book(hx + 14, hy - 22, 1, -.1), sh, el, { under: true });

// a beach towel lying on the sand, seen from the front
function k8Towel(cx, cy, w, h, base, stripe) {
  const quad = () => { ctx.moveTo(cx - w / 2 + h * .35, cy - h / 2); ctx.lineTo(cx + w / 2 - h * .35, cy - h / 2); ctx.lineTo(cx + w / 2, cy + h / 2); ctx.lineTo(cx - w / 2, cy + h / 2); ctx.closePath(); };
  shape(quad, base, { shade: 6, hi: 4, line: 5, noRim: true, after: () => {
    ctx.fillStyle = stripe;
    [.2, .5, .8].forEach(f => { const x = cx - w / 2 + w * f, lean = (f - .5) * h * .7;
      ctx.beginPath(); ctx.moveTo(x - 14 - lean, cy - h / 2); ctx.lineTo(x + 14 - lean, cy - h / 2); ctx.lineTo(x + 18, cy + h / 2); ctx.lineTo(x - 18, cy + h / 2); ctx.closePath(); ctx.fill(); });
  } });
}
function k8Umbrella(x, y, s, c1, c2, tilt = -.1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.rotate(tilt);
  shape(() => rrp(-6, -330, 12, 330, 5), '#e8e0cc', { shade: 3, hi: 2, line: 4, noRim: true });
  const R = 190, top = -330;
  const dome = () => {
    ctx.moveTo(-R, top); ctx.bezierCurveTo(-R * .8, top - 130, R * .8, top - 130, R, top);
    for (let i = 1; i <= 6; i++) { const xx = R - i * (2 * R / 6); ctx.quadraticCurveTo(xx + R / 6, top + 24, xx, top); }
    ctx.closePath();
  };
  shape(dome, c1, { shade: 12, hi: 6, line: 5, noRim: true, after: () => {
    ctx.fillStyle = c2;
    for (let i = 0; i < 6; i += 2) { const x0 = -R + i * (2 * R / 6), x1 = x0 + 2 * R / 6; ctx.beginPath(); ctx.moveTo(0, top - 98); ctx.lineTo(x0, top + 30); ctx.lineTo(x1, top + 30); ctx.closePath(); ctx.fill(); }
  } });
  circle(0, top - 98, 9, '#e8e0cc');
  ctx.restore();
}
// the beach looking out to sea: sky, sun, clouds, gulls, the sea with moving waves, foam, sand. o.retro: 1972 colours
const K8P = {
  retro: { sky0: '#7fb2c4', sky1: '#f1d9ae', sea0: '#3c7c8e', sea1: '#79b4ac', sand0: '#e4c088', sand1: '#a87a44', sun: '#fff0c8' },
  today: { sky0: '#4f9fdc', sky1: '#d4ecf6', sea0: '#1c72a4', sea1: '#48aec6', sand0: '#f0d39c', sand1: '#bf9158', sun: '#fffbe8' }
};
function k8Beach(t, o = {}) {
  const P = o.retro ? K8P.retro : K8P.today, hz = o.horizon ?? 880, sh = hz + (o.sea ?? 150);
  let g = ctx.createLinearGradient(0, 0, 0, hz);
  g.addColorStop(0, P.sky0); g.addColorStop(1, P.sky1);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, hz + 2);
  glow(190, hz - 320, 460, rgba(P.sun, 1), .6);
  circle(190, hz - 320, 58, P.sun);
  [[0, 1], [1, .75]].forEach(([i, k]) => {
    const cx = ((t * 9 + i * 560 + 380) % 1500) - 200, cy = hz - 380 + i * 150;
    ctx.fillStyle = 'rgba(255,255,255,.78)';
    [[0, 0, 90, 34], [70, -18, 70, 38], [140, 0, 80, 30], [-70, 6, 60, 24]].forEach(([dx, dy, rx, ry]) => { ctx.beginPath(); ctx.ellipse(cx + dx * k, cy + dy * k, rx * k, ry * k, 0, 0, Math.PI * 2); ctx.fill(); });
  });
  ctx.strokeStyle = 'rgba(40,40,50,.7)'; ctx.lineWidth = 4; ctx.lineCap = 'round';
  for (let i = 0; i < 3; i++) {
    const gx = ((t * (40 + i * 12) + i * 400 + 300) % 1300) - 100, gy = hz - 250 - i * 60 + Math.sin(t * 2 + i) * 10, f = Math.sin(t * 6 + i * 2) * 8;
    ctx.beginPath(); ctx.moveTo(gx - 22, gy - 6 + f); ctx.quadraticCurveTo(gx - 10, gy - 14, gx, gy); ctx.quadraticCurveTo(gx + 10, gy - 14, gx + 22, gy - 6 + f); ctx.stroke();
  }
  g = ctx.createLinearGradient(0, hz, 0, sh); g.addColorStop(0, P.sea0); g.addColorStop(1, P.sea1);
  ctx.fillStyle = g; ctx.fillRect(0, hz, W, sh - hz + 4);
  ctx.strokeStyle = 'rgba(255,255,255,.4)'; ctx.lineWidth = 3;
  for (let i = 0; i < 5; i++) {
    const yy = hz + 16 + i * (sh - hz - 30) / 5;
    ctx.beginPath();
    for (let x = 0; x <= W; x += 24) { const y = yy + Math.sin(x * .018 + t * 1.4 + i * 1.7) * (2 + i); if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
    ctx.stroke();
  }
  ctx.fillStyle = 'rgba(255,255,255,.2)'; ctx.fillRect(0, hz, W, 4);
  g = ctx.createLinearGradient(0, sh, 0, H); g.addColorStop(0, P.sand0); g.addColorStop(1, P.sand1);
  ctx.fillStyle = g; ctx.fillRect(0, sh, W, H - sh);
  ctx.fillStyle = mixColor(P.sand0, P.sea1, .4); ctx.fillRect(0, sh, W, 28);
  const fy = sh + 4 + Math.sin(t * .9) * 6;
  ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.moveTo(0, fy);
  for (let x = 0; x <= W; x += 30) ctx.lineTo(x, fy + Math.sin(x * .03 + t * 2) * 5);
  ctx.lineTo(W, fy + 12); ctx.lineTo(0, fy + 12); ctx.closePath(); ctx.fill();
  ctx.fillStyle = 'rgba(120,80,40,.18)';
  for (let i = 0; i < 70; i++) ctx.fillRect(rnd(i) * W, sh + 40 + rnd(i + 50) * (H - sh - 40), 3 + rnd(i + 9) * 3, 2);
  if (o.far !== false) [[90, .3, '#d84a3a'], [470, .26, '#3a8ac8'], [990, .32, '#e8b54a']].forEach(([x, s, c]) => k8Umbrella(x, sh + 70, s, c, '#f6efe0'));
}

// a person sitting on the sand with the knees up, hips at (x, gy), facing dir. The legs come first, then the body.
function k8Sit(spec, x, gy, s, t, o = {}) {
  const dir = o.dir || 1, k = s * spec.build.height, dress = spec.outfit === 'dress';
  ctx.save(); ctx.translate(x + dir * 60 * k, gy + 70 * k); ctx.scale(1, .2); glow(0, 0, 300 * k, 'rgba(60,30,10,1)', .35); ctx.restore();
  ctx.save(); ctx.translate(x, gy); ctx.scale(k * dir, k);
  [[-1, .3], [1, 0]].forEach(([side, dark]) => {
    const tone = c => dark ? shadeOf(c, dark) : c, knee = (o.knee || 0) + side * .05;
    ctx.save(); ctx.translate(18 + side * 10, -6);
    ctx.rotate(-2.09 - knee);
    shape(capsule(200, 84, 70), tone(dress ? spec.coat : spec.skin), { shade: 12, outline: capsuleLine(200, 84, 70) });
    if (!dress) shape(capsule(92, 94, 86), tone(spec.breeches), { shade: 12, outline: capsuleLine(92, 94, 86) });
    ctx.translate(0, 196); ctx.rotate(1.14 + knee * 1.6);
    shape(capsule(186, 64, 48), tone(spec.skin), { shade: 10, outline: capsuleLine(186, 64, 48) });
    ctx.translate(0, 186); ctx.rotate(.95 - knee * .6);
    shape(() => { ctx.moveTo(-24, -14); ctx.lineTo(16, -14); ctx.quadraticCurveTo(60, -8, 64, 10); ctx.lineTo(-28, 12); ctx.closePath(); }, tone(spec.skin), { shade: 5, hi: 5, line: 5 });
    stroke(tone(spec.shoe), 7, () => { ctx.moveTo(10, -13); ctx.lineTo(18, 11); });
    ctx.restore();
  });
  ctx.restore();
  figure(dress ? { ...spec, noSkirt: true } : spec, x, gy + 440 * k, s, t, { legs: 'hide', dir, lean: -.05, ...o.pose });
}
// where a figure's head centre is (for bubbles and badges); sitting figures use the hips' y + 440 * s * height
const k8Head = (spec, x, y, s, dir = 1) => [x + dir * 46 * s * spec.build.height, y - 886 * s * spec.build.height];

// a speech bubble: rows [[text, colour]], tail to (tx, ty); k 0..1 pops it in
function k8Bubble(x, y, rows, size, k, tx, ty, o = {}) {
  if (k <= 0) return;
  const lh = size * 1.22, w = Math.max(...rows.map(r => measure(up(r[0]), size, SANS, 900))) + size * 1.3;
  const h = rows.length * lh + size * .85, r = Math.min(30, h / 2 - 2), s = back(clamp(k));
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.translate(-x, -y);
  shape(() => {
    ctx.moveTo(x - w / 2 + r, y - h / 2); ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, r); ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, r);
    const bx = clamp(tx, x - w / 2 + r + 14, x + w / 2 - r - 30);
    ctx.lineTo(bx + 22, y + h / 2); ctx.lineTo(tx, ty); ctx.lineTo(bx - 8, y + h / 2);
    ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, r); ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, r); ctx.closePath();
  }, o.fill || '#fbf6e8', { shade: 6, hi: 4, noRim: true, line: 6 });
  if (o.border) { ctx.strokeStyle = o.border; ctx.lineWidth = 6; rr(x - w / 2 + 8, y - h / 2 + 8, w - 16, h - 16, Math.max(4, r - 6)); ctx.stroke(); }
  rows.forEach(([str, col], i) => text(up(str), x, y - h / 2 + size * .425 + lh * (i + .5) + size * .04, size, col || K8INK, SANS, 900));
  ctx.restore();
}
function k8Badge(x, y, label, k, r = 46) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(back(clamp(k)), back(clamp(k)));
  glow(0, 0, r * 2.4, 'rgba(232,181,74,1)', .45);
  shape(() => ctx.arc(0, 0, r, 0, Math.PI * 2), C.gold, { shade: 4, hi: 4, line: 5, noRim: true });
  text(label, 0, 3, r * .72, K8INK, SANS, 900);
  ctx.restore();
}
function k8Stamp(label, x, y, size, k, rot = -.1, col = C.red) {
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
// motion lines behind a runner
function k8Speed(x, y, dir, k, n = 4, len = 90) {
  if (k <= 0) return;
  ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineCap = 'round'; ctx.lineWidth = 6; ctx.globalAlpha *= clamp(k);
  for (let i = 0; i < n; i++) { const yy = y - i * 46, l = len * (.6 + .4 * rnd(i + 3)); ctx.beginPath(); ctx.moveTo(x - dir * 30, yy); ctx.lineTo(x - dir * (30 + l), yy); ctx.stroke(); }
  ctx.restore();
}

// ---------- the score board: 20 people, how many reacted ----------
const K8B = { y: 1088, h: 316 };
function k8Person(x, by, s, col) {
  ctx.save(); ctx.translate(x, by); ctx.scale(s, s);
  ctx.fillStyle = col; ctx.beginPath(); ctx.arc(0, -46, 10, 0, Math.PI * 2); ctx.fill();
  rr(-13, -33, 26, 33, 10); ctx.fill();
  ctx.restore();
}
// rows: [{label, lit (0..20, may be fractional while counting), col, a}]; null rows are not shown yet
function k8Board(rows) {
  const { y, h } = K8B;
  shape(() => rrp(36, y, 1008, h, 18), 'rgba(14,12,18,.92)', { flat: true, line: 5 });
  ctx.save(); ctx.strokeStyle = rgba(C.gold, .7); ctx.lineWidth = 3; rr(46, y + 10, 988, h - 20, 12); ctx.stroke(); ctx.restore();
  text('20 KİŞİDEN KAÇI HIRSIZA MÜDAHALE ETTİ?', 540, y + 42, 28, C.gold, SANS, 900, 'center', 0, C.ink, 2);
  rows.forEach((r, j) => {
    if (!r) return;
    const ry = y + 92 + j * 118;
    ctx.save(); ctx.globalAlpha *= r.a ?? 1;
    if (r.hot) { ctx.fillStyle = rgba(C.gold, .12); rr(56, ry - 26, 968, 110, 12); ctx.fill(); }
    text(r.label, 72, ry, 26, C.ivory, SANS, 900, 'left', 0, C.ink, 1);
    for (let i = 0; i < 20; i++) {
      const k = clamp(r.lit - i), on = k > .5;
      k8Person(86 + i * 34, ry + 76, 1 + .35 * Math.sin(Math.PI * k), on ? r.col : 'rgba(243,234,216,.2)');
    }
    const n = Math.min(20, Math.floor(r.lit + .5));
    text(n + '/20', 890, ry + 30, 62, r.lit > .2 ? r.col : 'rgba(243,234,216,.35)', SANS, 900, 'center', 8);
    if (r.pct) text('%' + r.pct, 890, ry + 80, 32, C.ivory, SANS, 900, 'center', 6);
    ctx.restore();
  });
}

// the 1972 wide shot: the sunbather on her towel at the left, the researcher's towel and radio at the right
const K8W = { her: [250, 1296, .64], herTowel: [300, 1336, 440, 112], hisTowel: [800, 1388, 400, 104], radio: [820, 1380, .9] };
function k8Wide(t, o = {}) {
  k8Beach(t, { retro: true });
  k8Umbrella(70, 1250, .62, '#2f8a8a', '#f6efe0', -.08);
  k8Towel(...K8W.herTowel, '#f2e6c8', '#2f8a8a');
  k8Towel(...K8W.hisTowel, '#e8b54a', '#c8362c');
  if (o.radio !== false) k8Radio(...K8W.radio, t, 1);
  const [hx, hy, hs] = K8W.her;
  k8Sit(K8.kadin, hx, hy, hs, t, { pose: { seed: 5, expr: o.herExpr || { ...EXPR.calm, look: [.4, .5] }, headTilt: o.herTilt ?? .08,
    front: o.herFront || { sh: .95, el: 1.3, hand: 'hold', prop: k8BookIn(.95, 1.3) }, back: { sh: -.5, el: -.05, hand: 'palm' } } });
}

Object.assign(SAHNE, {
  kanca(V, L, t, lt) {
    // the hook, from the very first frame: today, a thief creeps up on a beach bag next to a man on his phone. On
    // "yanında" he takes it and tiptoes off while the man only looks; "güvenme" brings the red plate. On "önce" it rewinds and a gold "SÖZ" badge marks the
    // man; on "söz" he jumps up, points and shouts, and the thief freezes with his hands up
    K8.day();
    const tg = wordTime(L, V.grab, .35), tn = wordTime(L, V.no, .45), tb = wordTime(L, V.back, .7), ty = wordTime(L, V.yes, .85);
    const before = t < tg - .05, gone = t >= tg - .05 && t < tb - .15, again = t >= tb - .15;
    const creep = eio(prog(t, 0, tg - .05)), away = prog(t, tg + .1, tb - .2), stand = prog(t, ty - .2, ty + .1);
    cam(540, 900, lerp(1.0, 1.04, eio(prog(lt, 0, 5))), () => {
      k8Beach(t, {});
      k8Towel(300, 1346, 470, 112, '#3a8ac8', '#f6efe0');
      k8Towel(780, 1396, 380, 104, '#f2e6c8', '#e0604a');
      const bagHere = !gone;
      if (bagHere) k8Bag(720, 1392, .9);
      // the man: on his phone, then looking, then (after the rewind) up on his feet pointing
      if (!again || stand <= 0) {
        const look = gone ? { ...EXPR.confused, look: [.8, 0] } : again ? { ...EXPR.calm, look: [.6, 0] } : { ...EXPR.calm, look: [.25, .55] };
        k8Sit(K8.komsu, 250, 1306, .66, t, { pose: { seed: 3, expr: look, headTilt: gone || again ? 0 : .12,
          front: gone ? { sh: .5, el: .6, hand: 'open' } : { sh: .55, el: 2.0, hand: 'hold', prop: k8PhoneIn(.55, 2.0) },
          back: { sh: -.5, el: -.05, hand: 'palm' } } });
      } else {
        figure(K8.komsu, 330, 1500, .7, t, { dir: 1, legs: 'stand', seed: 3, expr: 'shout', talk: .7, lean: .06,
          front: { sh: lerp(.3, 1.55, eo(stand)), el: .05, hand: 'point' }, back: { sh: .2, el: .5, hand: 'fist' } });
      }
      // the thief
      if (before) {
        figure(K8.hirsiz, lerp(1010, 950, creep), 1512, .7, t, { dir: -1, legs: 'stand', seed: 8, expr: 'sly', lean: lerp(.25, .5, creep),
          front: { sh: lerp(.6, 1.1, creep), el: .2, hand: 'open' }, back: { sh: .4, el: .6, hand: 'open' } });
      } else if (gone) {
        figure(K8.hirsiz, lerp(960, 1160, eio(away)), 1512, .7, t, { dir: 1, legs: 'walk', phase: t * 8, seed: 8, expr: 'sly', lean: .12,
          front: { sh: .35, el: .5, hand: 'hold', prop: k8BagIn(.35, .5) }, back: { sh: .5, el: .4 } });
      } else {
        const up2 = stand;
        figure(K8.hirsiz, 950, 1512, .7, t, { dir: -1, legs: 'stand', seed: 8, expr: up2 > .3 ? 'surprised' : 'sly', lean: lerp(.5, -.05, eo(up2)),
          front: { sh: lerp(1.1, 1.5, eo(up2)), el: lerp(.2, 1.5, eo(up2)), hand: 'open' }, back: { sh: lerp(.4, 1.3, eo(up2)), el: lerp(.6, 1.6, eo(up2)), hand: 'open' } });
      }
      if (again) sparks(560, 900, t, 18, 220, prog(t, ty, ty + .8));
    });
    if (gone) k8Speed(lerp(960, 1160, eio(away)) - 60, 1200, 1, away * (1 - away) * 4);
    plate(V.no_label, 540, 520, 60, back(prog(t, tn - .05, tn + .3)) * (again ? 1 - prog(t, tb - .15, tb + .1) : 1), C.red);
    k8Badge(150, 880, 'SÖZ', prog(t, tb - .05, tb + .3) * (1 - prog(t, ty - .1, ty + .1)), 50);
    k8Bubble(480, 640, [['Hey! Bırak onu!', K8INK]], 46, stand, 420, 760);
    if (stand > 0) glow(540, 1340, 320, 'rgba(232,181,74,1)', .3 * stand);
    plate(V.yes_label, 540, 1340, 60, back(prog(t, ty - .1, ty + .25)), C.gold);
    flash(t, tb - .15, .6);
    flash(t, ty - .2, .35);
  },
  plaj(V, L, t, lt) {
    // 1972, Jones Beach: a sunbather reads on her towel. Next to her a young researcher stands by his towel, his radio
    // playing. On "radyosunu" a gold ring pulses around the radio; on "bırakıp" he turns and walks off to the left
    K8.sun();
    const tr = wordTime(L, V.radio, .45), tl = wordTime(L, V.leave, .75), walk = prog(t, tl - .1, L.t1 + .2);
    cam(540, 900, lerp(1.0, 1.04, eio(prog(lt, 0, 6))), () => {
      k8Wide(t, { herExpr: walk > 0 ? { ...EXPR.calm, look: [-.2, .2] } : { ...EXPR.calm, look: [.4, .5] } });
      const ring = prog(t, tr - .1, tr + .7);
      if (ring > 0 && ring < 1) { ctx.save(); ctx.strokeStyle = rgba(C.gold2, 1 - ring); ctx.lineWidth = 8; ctx.beginPath(); ctx.ellipse(820, 1320, 120 + 80 * ring, 90 + 50 * ring, 0, 0, Math.PI * 2); ctx.stroke(); ctx.restore(); }
      const x = walk > 0 ? lerp(680, -260, eio(walk)) : 680, sw = walk > 0 ? .35 * Math.sin(t * 8) : 0;
      figure(K8.sahip, x, 1520, .7, t, { dir: -1, legs: walk > 0 && walk < 1 ? 'walk' : 'stand', phase: t * 8, seed: 2,
        expr: walk > 0 ? 'calm' : { ...EXPR.warm, look: [.3, .3] }, front: { sh: sw - .15, el: .3 }, back: { sh: -sw - .3, el: .2 } });
    });
    plate(V.year, 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)));
  },
  hirsiz(V, L, t, lt) {
    // a few minutes later: the towel and the radio are alone. A second researcher strolls in from the right, a red tag
    // "SAHTE HIRSIZ" over him; on "kapıp" he bends and the radio jumps into his hand, on "kaçtı" he runs off right
    K8.sun();
    const tr = wordTime(L, V.role, .5), tg = wordTime(L, V.grab, .75), tk = wordTime(L, V.run, .88);
    const come = prog(t, L.t0, tg - .35), bend = prog(t, tg - .35, tg), run = prog(t, tk - .1, L.t1 + .25);
    const took = t >= tg;
    cam(540, 900, lerp(1.0, 1.04, eio(prog(lt, 0, 6))), () => {
      k8Wide(t, { radio: !took, herExpr: took ? { ...EXPR.surprised, look: [.8, 0] } : { ...EXPR.calm, look: [.4, .5] }, herTilt: took ? 0 : .08 });
      if (!took) {
        const x = lerp(1240, 960, eo(come));
        figure(K8.rol, x, 1520, .7, t, { dir: -1, legs: come > 0 && come < 1 ? 'walk' : 'stand', phase: t * 8, seed: 6, expr: 'sly', lean: lerp(0, .5, eo(bend)),
          front: { sh: lerp(.2, 1.15, eo(bend)), el: .2, hand: 'open' }, back: { sh: .3, el: .5 } });
      } else {
        const x = run > 0 ? lerp(960, 1300, ei(run)) : 960;
        figure(K8.rol, x, 1520, .7, t, { dir: run > 0 ? 1 : -1, legs: run > 0 ? 'walk' : 'stand', phase: t * 14, seed: 6, expr: 'sly', lean: run > 0 ? .18 : .2,
          front: { sh: .4, el: 1.3, hand: 'hold', prop: k8RadioIn(.4, 1.3, t) }, back: { sh: run > 0 ? .6 * Math.sin(t * 14) : .3, el: .6 } });
        if (run > 0) k8Speed(x - 70, 1200, 1, Math.min(1, run * 4));
      }
    });
    plate('2-3 dakika sonra', 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)));
    const tagX = took ? (run > 0 ? lerp(960, 1300, ei(run)) : 960) : lerp(1240, 960, eo(come));
    plate('Sahte hırsız', clamp(tagX - 20, 220, 860), 700, 40, back(prog(t, tr - .1, tr + .25)) * (1 - run), C.red);
    flash(t, tg - .05, .35);
  },
  ates(V, L, t, lt) {
    // the first way: before leaving he only asks for a light ("Ateşin var mı?"). On "yirmi" cut to the wide shot over
    // the board: the thief runs off with the radio while she stays on her towel, and the first row counts up to 4 of 20
    K8.sun();
    const ta = wordTime(L, V.ask, .3), tk = wordTime(L, V.key, .55), tn = wordTime(L, V.n, .78);
    if (t < tk - .08) {
      cam(540, 900, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => {
        k8Beach(t, { retro: true, horizon: 820, far: false });
        k8Towel(330, 1370, 520, 120, '#f2e6c8', '#2f8a8a');
        const ans = prog(t, ta + .3, ta + .8);
        k8Sit(K8.kadin, 270, 1330, .8, t, { pose: { seed: 5, expr: ans > 0 ? { ...EXPR.neutral, look: [.6, -.1] } : { ...EXPR.calm, look: [.6, -.2] }, headTilt: -.05,
          front: ans > 0 ? { sh: lerp(.9, 1.3, eo(ans)), el: lerp(1.3, .3, eo(ans)), hand: 'palm' } : { sh: .95, el: 1.3, hand: 'hold', prop: k8BookIn(.95, 1.3) },
          back: { sh: -.5, el: -.05, hand: 'palm' } } });
        figure(K8.sahip, 800, 1600, .9, t, { dir: -1, legs: 'stand', seed: 2, expr: 'warm', talk: talk(t, L) * (t < ta + .5 ? 1 : 0), lean: .1,
          front: { sh: .7, el: 1.1, hand: 'open' }, back: { sh: -.3, el: .2 } });
      });
      k8Bubble(700, 640, [['Ateşin var mı?', K8INK]], 50, prog(t, ta - .25, ta + .05), 760, 760);
      plate('Sadece ateş istedi', 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)), C.bone);
      return;
    }
    const run = prog(t, tk - .08, L.t1 + .2), count = prog(t, tn - .25, tn + .45);
    cam(540, 900, 1.0, () => {
      k8Beach(t, { retro: true, horizon: 600, sea: 110, far: false });
      k8Towel(240, 990, 360, 76, '#f2e6c8', '#2f8a8a');
      k8Sit(K8.kadin, 200, 966, .5, t, { pose: { seed: 5, expr: { ...EXPR.neutral, look: [.8, 0] }, front: { sh: .95, el: 1.3, hand: 'hold', prop: k8BookIn(.95, 1.3) },
        back: { sh: -.5, el: -.05, hand: 'palm' } } });
      const x = lerp(540, 860, eio(run));
      figure(K8.rol, x, 1050, .5, t, { dir: 1, legs: 'walk', phase: t * 14, seed: 6, expr: 'sly', lean: .18,
        front: { sh: .4, el: 1.3, hand: 'hold', prop: k8RadioIn(.4, 1.3, t) }, back: { sh: .6 * Math.sin(t * 14) - .2, el: .6 } });
      k8Speed(x - 60, 880, 1, 1, 3, 80);
    });
    k8Board([{ label: 'SADECE ATEŞ İSTEDİ', lit: count * 4, col: C.ivory, pct: count >= 1 ? 20 : 0, hot: true }]);
    flash(t, tk - .08, .35);
  },
  soz(V, L, t, lt) {
    // the second way: "Eşyalarıma göz kulak olur musun?" and she nods "Tabii!" On "yirmi" cut to the wide shot over the
    // board: she jumps up and runs after the thief, "Hey!", and the second row counts up to 19 of 20 under the first (4)
    K8.sun();
    const ta = wordTime(L, V.ask, .2), ty = wordTime(L, V.yes, .45), tk = wordTime(L, V.key, .6), tn = wordTime(L, V.n, .8), tx = wordTime(L, V.act, .95);
    if (t < tk - .08) {
      cam(540, 900, lerp(1.0, 1.03, eio(prog(lt, 0, 4))), () => {
        k8Beach(t, { retro: true, horizon: 820, far: false });
        k8Towel(330, 1370, 520, 120, '#f2e6c8', '#2f8a8a');
        const nod = prog(t, ty - .1, ty + .5);
        k8Sit(K8.kadin, 270, 1330, .8, t, { pose: { seed: 5, expr: nod > 0 ? 'warm' : { ...EXPR.calm, look: [.6, -.2] }, headTilt: -.05, nod: Math.sin(nod * Math.PI * 2) * .08,
          front: { sh: .95, el: 1.3, hand: 'hold', prop: k8BookIn(.95, 1.3) }, back: { sh: -.5, el: -.05, hand: 'palm' } } });
        figure(K8.sahip, 800, 1600, .9, t, { dir: -1, legs: 'stand', seed: 2, expr: nod > .3 ? 'happy' : 'warm', talk: talk(t, L) * (t < ty ? 1 : 0), lean: .1,
          front: { sh: .5, el: 1.6, hand: 'point' }, back: { sh: -.3, el: .2 } });
      });
      k8Bubble(660, 620, [['Eşyalarıma göz kulak', K8GOLD], ['olur musun?', K8GOLD]], 46, prog(t, ta - .25, ta + .05), 760, 760, { fill: '#fff2c8', border: C.gold });
      k8Bubble(300, 800, [['Tabii!', K8INK]], 50, prog(t, ty - .05, ty + .25), 330, 900);
      plate('Göz kulak olur musun?', 540, 488, 46, back(prog(t, L.t0 + .05, L.t0 + .4)), C.gold);
      return;
    }
    const chase = prog(t, tk - .08, L.t1 + .2), count = prog(t, tn - .45, tn + .45), grab = prog(t, tx - .2, tx + .2);
    cam(540, 900, 1.0, () => {
      k8Beach(t, { retro: true, horizon: 600, sea: 110, far: false });
      k8Towel(200, 990, 330, 76, '#f2e6c8', '#2f8a8a');
      const tx2 = lerp(620, 790, eio(chase)), hx = lerp(320, tx2 - 230, eio(chase));
      figure(K8.rol, tx2, 1050, .5, t, { dir: 1, legs: 'walk', phase: t * 14, seed: 6, expr: grab > .3 ? 'surprised' : { ...EXPR.surprised, look: [-.6, 0] }, lean: .1, headTilt: -.15,
        front: { sh: .4, el: 1.3, hand: 'hold', prop: k8RadioIn(.4, 1.3, t) }, back: { sh: .6 * Math.sin(t * 14), el: .6 } });
      figure(K8.kadin, hx, 1050, .52, t, { dir: 1, legs: 'walk', phase: t * 15, seed: 5, expr: 'shout', talk: .6, lean: .16,
        front: { sh: lerp(1.2, 1.55, grab), el: .05, hand: grab > .5 ? 'fist' : 'open' }, back: { sh: -.4, el: .8 } });
      k8Speed(hx - 50, 880, 1, 1, 3, 80);
    });
    const bx = lerp(320, lerp(620, 790, eio(chase)) - 230, eio(chase));
    k8Bubble(clamp(bx + 40, 200, 880), 500, [['Hey! Dur!', K8INK]], 46, prog(t, tk + .1, tk + .4), bx + 30, 590);
    k8Board([{ label: 'SADECE ATEŞ İSTEDİ', lit: 4, col: C.ivory, pct: 20, a: .6 },
      { label: 'GÖZ KULAK OLUR MUSUN?', lit: count * 19, col: C.gold2, pct: count >= 1 ? 95 : 0, hot: true }]);
    if (count > 0) glow(890, K8B.y + 240, 200, 'rgba(232,181,74,1)', .3 * count);
    flash(t, tk - .08, .35);
  },
  neden(V, L, t, lt) {
    // the why. Close on her: "Tamam!", a grey "SEYİRCİ" stamp is struck through, a gold "SÖZ" badge lands on "söz".
    // Cut on "İnsan": she stands with the radio back in her hands, the researcher returns smiling, the music plays on
    K8.sun();
    const to = wordTime(L, V.ok, .15), tw = wordTime(L, V.watcher, .35), ts = wordTime(L, V.key, .5), tc = wordTime(L, V.cut, .7), tp = wordTime(L, V.keep, .88);
    if (t < tc - .08) {
      const promise = prog(t, ts - .1, ts + .3);
      drawSoft(.4, () => cam(540, 900, 1.4, () => k8Beach(t, { retro: true, horizon: 900 })), .25);
      cam(540, 960, lerp(1.0, 1.03, eio(prog(lt, 0, 3))), () => {
        glow(500, 960, 480, 'rgba(255,214,140,1)', .2 + .25 * promise);
        figure(K8.kadin, 470, 2070, 1.25, t, { dir: 1, legs: 'hide', seed: 5, expr: promise > .4 ? { ...EXPR.warm, look: [.3, 0] } : to < t ? 'warm' : 'calm',
          talk: t > to - .1 && t < to + .4 ? .6 : 0, front: { sh: .12, el: 2.3, hand: 'palm' }, back: { sh: .1, el: .2 } });
      });
      k8Bubble(300, 620, [['Tamam!', K8INK]], 54, prog(t, to - .2, to + .1) * (1 - prog(t, tw - .2, tw)), 380, 740);
      k8Stamp('SEYİRCİ', 790, 660, 54, prog(t, tw - .15, tw + .1), .08, '#6a6a72');
      const st = eo(prog(t, tw + .3, tw + .6));
      if (st > 0) { ctx.save(); ctx.translate(790, 660); ctx.rotate(.08); ctx.lineCap = 'round'; ctx.strokeStyle = C.red2; ctx.lineWidth = 12;
        ctx.shadowColor = 'rgba(210,35,47,.8)'; ctx.shadowBlur = 16; ctx.beginPath(); ctx.moveTo(-170, 4); ctx.lineTo(-170 + 340 * st, -4); ctx.stroke(); ctx.restore(); }
      k8Badge(800, 880, 'SÖZ', promise, 70);
      sparks(800, 880, t, 20, 240, prog(t, ts, ts + .8));
      plate('Seyirci değil, söz veren', 540, 1330, 52, back(prog(t, ts - .05, ts + .3)), C.gold);
      return;
    }
    const back2 = prog(t, tc - .08, tc + 1.2), keep = prog(t, tp - .15, tp + .3);
    cam(540, 900, lerp(1.0, 1.03, eio(prog(t, tc, tc + 3))), () => {
      k8Beach(t, { retro: true, horizon: 860 });
      k8Towel(560, 1400, 520, 120, '#f2e6c8', '#2f8a8a');
      glow(640, 1000, 420, 'rgba(255,214,140,1)', .25 * keep);
      figure(K8.sahip, lerp(-120, 300, eo(back2)), 1560, .8, t, { dir: 1, legs: back2 < 1 ? 'walk' : 'stand', phase: t * 8, seed: 2, expr: keep > .3 ? 'happy' : 'surprised',
        front: { sh: lerp(.6, 1.2, keep), el: lerp(.8, .4, keep), hand: 'open' }, back: { sh: -.3, el: .2 } });
      figure(K8.kadin, 700, 1560, .8, t, { dir: -1, legs: 'stand', seed: 5, expr: keep > .3 ? 'happy' : 'warm',
        front: { sh: lerp(.45, .7, keep), el: lerp(.9, 1.2, keep), hand: 'hold', prop: k8RadioIn(lerp(.45, .7, keep), lerp(.9, 1.2, keep), t) }, back: { sh: -.3, el: .2 } });
      sparks(560, 900, t, 18, 220, prog(t, tp, tp + .8));
    });
    plate('İnsan verdiği sözü tutar', 540, 1340, 52, back(prog(t, tp - .1, tp + .25)), C.gold);
  }
});
