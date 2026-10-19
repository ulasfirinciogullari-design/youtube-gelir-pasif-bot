// Zirvenin Kanunu cartoon cast. Every character is built from parts (legs, coat, arms, neck, head) drawn with a
// thick ink outline, a cool shade crescent on the side away from the key light, a warm highlight toward it and a
// thin rim light on the shadow edge: a candlelit graphic-novel look. A character stands with its feet at (x, y);
// at s = 1 it is about 1000 px tall. Everything is drawn facing right and mirrored for dir = -1.

const INK = '#1a1117';
const LIGHT = { x: -.75, y: -.66, warm: '#ffd9a0', cool: '#20203c', rim: '#9fc2ff', rimA: .9 };
function setLight(x, y, o = {}) { const n = Math.hypot(x, y) || 1; Object.assign(LIGHT, { x: x / n, y: y / n }, o); }

// the light direction in the current (rotated, mirrored) local frame, `len` local units long
function lightLocal(len, sign = 1) {
  const m = ctx.getTransform(), det = m.a * m.d - m.b * m.c;
  const lx = LIGHT.x * sign, ly = LIGHT.y * sign;
  const x = (m.d * lx - m.c * ly) / det, y = (-m.b * lx + m.a * ly) / det, n = Math.hypot(x, y) || 1;
  return [x / n * len, y / n * len];
}
const pxScale = () => { const m = ctx.getTransform(); return Math.sqrt(Math.abs(m.a * m.d - m.b * m.c)); };
const shadeOf = (c, k = .5) => mixColor(c, LIGHT.cool, k);
const hiOf = (c, k = .28) => mixColor(c, LIGHT.warm, k);

// the part minus a copy of itself shifted by (ox, oy): a crescent along one edge
function crescent(build, ox, oy, color, a = 1) {
  if (a <= 0) return;
  ctx.save(); ctx.beginPath(); build(); ctx.clip();
  ctx.beginPath(); ctx.rect(-1e5, -1e5, 2e5, 2e5);
  ctx.translate(ox, oy); build(); ctx.translate(-ox, -oy);
  ctx.globalAlpha *= a; ctx.fillStyle = color; ctx.fill('evenodd'); ctx.restore();
}
function rrp(x, y, w, h, r) { ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function inkLine(w = 6.5) { const s = pxScale(); return clamp(w * s, 3.2, 11) / s; }
// one shaded part. build() adds the outline to the current path (no beginPath).
function shape(build, base, o = {}) {
  ctx.save();
  ctx.beginPath(); build(); ctx.fillStyle = base; ctx.fill();
  if (!o.flat) {
    const sw = o.shade ?? 16, hw = o.hi ?? 9, rw = o.rim ?? 3.5;
    const [sx, sy] = lightLocal(sw), [hx, hy] = lightLocal(hw, -1), [rx, ry] = lightLocal(rw);
    crescent(build, sx, sy, o.shadeColor || shadeOf(base), o.shadeA ?? .85);
    crescent(build, hx, hy, o.hiColor || hiOf(base), o.hiA ?? .55);
    if (LIGHT.rimA > 0 && !o.noRim) crescent(build, rx, ry, LIGHT.rim, LIGHT.rimA * (o.rimA ?? .8));
  }
  if (o.after) { ctx.save(); ctx.beginPath(); build(); ctx.clip(); o.after(); ctx.restore(); }
  if (!o.noLine) { ctx.beginPath(); (o.outline || build)(); ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.lineWidth = inkLine(o.line); ctx.strokeStyle = INK; ctx.stroke(); }
  ctx.restore();
}
// a tapered capsule from (0, 0) down to (0, len); capsuleLine is its outline without the top cap, so a limb seems
// to come out of the body instead of being pinned on it
function capsule(len, w0, w1) {
  return () => {
    const a = w0 / 2, b = w1 / 2;
    ctx.moveTo(-a, 0); ctx.arc(0, 0, a, Math.PI, 0); ctx.lineTo(b, len); ctx.arc(0, len, b, 0, Math.PI); ctx.closePath();
  };
}
function capsuleLine(len, w0, w1) {
  return () => { const a = w0 / 2, b = w1 / 2; ctx.moveTo(a, a * .2); ctx.lineTo(b, len); ctx.arc(0, len, b, 0, Math.PI); ctx.lineTo(-a, a * .2); };
}
function stroke(color, w, fn, a = 1) {
  ctx.save(); ctx.globalAlpha *= a; ctx.strokeStyle = color; ctx.lineWidth = inkLine(w); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.beginPath(); fn(); ctx.stroke(); ctx.restore();
}
function blinkAt(t, seed) { const p = (t * .9 + seed * 1.7) % 3.9; return p < .14 ? 1 - Math.abs(p - .07) / .07 : 0; }

// ---------- faces ----------
// expression: brow (inner end up +, down -), raise (whole brow up), lid (upper lid closing 0..1), squint (lower lid),
// curve (smile + / frown -), smirk (one corner up), open (mouth open), happyEyes (closed smiling arcs), look [x, y]
const EXPR = {
  neutral: { brow: 0, raise: 0, lid: .12, squint: 0, curve: .05 },
  angry: { brow: -1, raise: -.3, lid: .32, squint: .3, curve: -.6, look: [.3, .1] },
  shout: { brow: -1, raise: -.2, lid: .2, squint: .2, curve: -.4, open: .2, look: [.4, 0] },
  smug: { brow: .2, raise: .2, lid: .5, squint: .1, curve: .3, smirk: .6, browSplit: .5 },
  haughty: { brow: .1, raise: .3, lid: .55, squint: 0, curve: -.15, look: [.2, -.2] },
  surprised: { brow: 1, raise: 1, lid: 0, squint: 0, curve: 0, open: .55, pupil: .7 },
  confused: { brow: .4, raise: .3, lid: .12, squint: .1, curve: -.2, smirk: -.4, browSplit: 1, look: [-.2, -.6] },
  happy: { brow: .6, raise: .3, lid: 0, squint: 0, curve: 1, happyEyes: 1, open: .25 },
  warm: { brow: .4, raise: .15, lid: .28, squint: .15, curve: .7 },
  sly: { brow: .2, raise: .1, lid: .4, squint: .15, curve: .45, smirk: .7, browSplit: .7, look: [.4, 0] },
  displeased: { brow: -.5, raise: -.1, lid: .3, squint: .1, curve: -.35, look: [.5, 0] },
  calm: { brow: .1, raise: 0, lid: .38, squint: .05, curve: .2 },
  knowing: { brow: .3, raise: .2, lid: .42, squint: .1, curve: .35, smirk: .5, browSplit: .6, look: [0, 0] }
};
function exprMix(a, b, k) {
  const A = typeof a === 'string' ? EXPR[a] : a, B = typeof b === 'string' ? EXPR[b] : b, out = {};
  new Set([...Object.keys(A), ...Object.keys(B)]).forEach(key => {
    const va = A[key] ?? (key === 'look' ? [0, 0] : 0), vb = B[key] ?? (key === 'look' ? [0, 0] : 0);
    out[key] = Array.isArray(va) ? [lerp(va[0], vb[0], k), lerp(va[1], vb[1], k)] : lerp(va, vb, k);
  });
  return out;
}

function eye(x, y, rx, ry, e, blink, iris, inner) {
  const lid = clamp(Math.max(e.lid || 0, blink)), sq = clamp(e.squint || 0), look = e.look || [0, 0];
  if ((e.happyEyes || 0) > .5) {
    stroke(INK, 7.5, () => { ctx.moveTo(x - rx, y + ry * .25); ctx.quadraticCurveTo(x, y - ry * 1.1, x + rx, y + ry * .25); });
    return;
  }
  const build = () => ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.save();
  ctx.beginPath(); build(); ctx.fillStyle = '#fbf5ea'; ctx.fill(); ctx.clip();
  const ir = ry * .64, ix = x + look[0] * rx * .42, iy = y + look[1] * ry * .3 + ry * .06;
  circle(ix, iy, ir, iris); circle(ix, iy, ir * .78, mixColor(iris, '#000000', .25));
  circle(ix, iy, ir * .5 * (e.pupil || 1), '#0b0709');
  circle(ix - ir * .35, iy - ir * .38, ir * .28, 'rgba(255,255,255,.95)');
  circle(ix + ir * .3, iy + ir * .3, ir * .12, 'rgba(255,255,255,.7)');
  // the upper lid shadow on the eyeball, then the lids themselves
  ctx.fillStyle = 'rgba(60,30,40,.12)'; ctx.fillRect(x - rx, y - ry, rx * 2, ry * .5);
  const ly = y - ry + ry * 2 * lid, sy = y + ry - ry * 1.2 * sq;
  ctx.fillStyle = e.lidColor; ctx.beginPath();
  ctx.moveTo(x - rx - 2, y - ry - 4); ctx.lineTo(x + rx + 2, y - ry - 4); ctx.lineTo(x + rx + 2, ly - ry * .15);
  ctx.quadraticCurveTo(x, ly + ry * .35 * (1 - lid), x - rx - 2, ly - ry * .15); ctx.fill();
  if (sq > 0) { ctx.beginPath(); ctx.moveTo(x - rx - 2, y + ry + 4); ctx.lineTo(x + rx + 2, y + ry + 4); ctx.lineTo(x + rx + 2, sy); ctx.quadraticCurveTo(x, sy - ry * .25, x - rx - 2, sy); ctx.fill(); }
  ctx.restore();
  // lash line on the upper lid, a thin line under the eye
  const lidY = ly - ry * .15, mid = ly + ry * .35 * (1 - lid);
  stroke(INK, 7.5, () => { ctx.moveTo(x - rx - 3, lidY + 3); ctx.quadraticCurveTo(x, mid - (lid > .9 ? 0 : 2), x + rx + 4, lidY - 1); });
  if (lid < .9) {
    stroke(INK, 3, () => ctx.ellipse(x, y, rx, ry, 0, .15 * Math.PI, .85 * Math.PI), .55);
    stroke(INK, 7, () => { ctx.moveTo(x + rx + 3, lidY - 1); ctx.lineTo(x + rx + 9, lidY - 7); }, inner ? 0 : 1);
  }
}

// head-local: origin at the head's centre, facing right in three-quarter view; r is the head radius (about 100)
function face(spec, e, t, talkK, seed) {
  const fx = 28, skin = spec.skin, lidColor = shadeOf(skin, .18);
  const E = { ...e, lidColor };
  const blink = blinkAt(t, seed);
  // eyes
  const ey = -4;
  eye(fx + 36, ey, 21, 24, E, blink, spec.iris, false);
  eye(fx - 28, ey, 16, 23, E, blink, spec.iris, true);
  // brows: inner ends are the ones nearest the nose
  const br = (e.brow || 0), rs = (e.raise || 0) * 13, split = (e.browSplit || 0);
  const bw = spec.browW || 9;
  stroke(spec.brow, bw, () => {
    const by = ey - 42 - rs - split * 6;
    ctx.moveTo(fx + 14, by + 4 - br * 12); ctx.quadraticCurveTo(fx + 36, by - 8 - br * 2, fx + 62, by + 6 + br * 4);
  });
  stroke(spec.brow, bw * .9, () => {
    const by = ey - 41 - rs + split * 7;
    ctx.moveTo(fx - 8, by + 4 - br * 12); ctx.quadraticCurveTo(fx - 28, by - 6, fx - 44, by + 6 + br * 3);
  });
  // nose: a single curved line down the near side of the bridge, a rounded tip, a short nostril stroke and a soft
  // shadow on the cheek side; nl makes it longer and prouder
  const nl = spec.nose || 1, tx = fx + 24 + 10 * nl, ty = ey + 38 + 6 * nl;
  ctx.save(); ctx.globalAlpha = .16; ctx.fillStyle = shadeOf(skin, 1); ctx.beginPath();
  ctx.moveTo(fx + 14, ey + 4); ctx.bezierCurveTo(fx + 18, ey + 20, tx + 4, ty - 14, tx + 4, ty); ctx.quadraticCurveTo(tx, ty + 12, tx - 14, ty + 10);
  ctx.quadraticCurveTo(fx + 8, ey + 30, fx + 14, ey + 4); ctx.fill(); ctx.restore();
  stroke(INK, 5, () => {
    ctx.moveTo(fx + 14, ey + 4); ctx.bezierCurveTo(fx + 18, ey + 20, tx + 6 + 4 * nl, ty - 12, tx + 6 + 4 * nl, ty);
    ctx.bezierCurveTo(tx + 6 + 4 * nl, ty + 12, tx - 6, ty + 14, tx - 12, ty + 8);
  });
  stroke(INK, 4, () => { ctx.moveTo(tx - 6, ty + 2); ctx.quadraticCurveTo(tx - 16, ty + 2, tx - 18, ty + 10); }, .55);
  ctx.save(); ctx.globalAlpha = .7; circle(tx + 2 + 2 * nl, ty - 4, 4, '#fff3e6'); ctx.restore();
  // cheeks
  if (spec.blush) { glow(fx + 58, 36, 34, 'rgba(230,92,80,1)', spec.blush); glow(fx - 26, 34, 26, 'rgba(230,92,80,1)', spec.blush * .8); }
  // mouth
  const mx = fx + 14, my = 70, mw = (spec.mouthW || 46) * (1 + .15 * (e.curve || 0)), c = e.curve || 0, sm = e.smirk || 0;
  const open = clamp((e.open || 0) + talkK * .75);
  const yl = my - c * 9 + sm * 6, yr = my - c * 9 - sm * 10;
  if (open < .06) {
    stroke(INK, 6.5, () => { ctx.moveTo(mx - mw / 2, yl); ctx.quadraticCurveTo(mx, my + c * 15 + Math.abs(sm) * 2, mx + mw / 2, yr); });
    if (c > .4) { stroke(INK, 4, () => { ctx.moveTo(mx + mw / 2 - 2, yr - 2); ctx.quadraticCurveTo(mx + mw / 2 + 8, yr + 2, mx + mw / 2 + 6, yr + 10); }, .7); }
  } else {
    const h = 8 + open * 34, w2 = mw * (.9 - open * .2);
    const build = () => {
      ctx.moveTo(mx - w2 / 2, yl); ctx.quadraticCurveTo(mx, my - 6 + c * 6, mx + w2 / 2, yr);
      ctx.quadraticCurveTo(mx + w2 * .1, my + h + c * 10, mx - w2 / 2, yl);
    };
    ctx.save(); ctx.beginPath(); build(); ctx.fillStyle = '#3b0f19'; ctx.fill(); ctx.clip();
    ctx.fillStyle = '#fff6ec'; ctx.fillRect(mx - w2, my - 14 + c * 4, w2 * 2, 9 + open * 4);
    circle(mx + 4, my + h + 4, w2 * .32, '#c2505d');
    ctx.restore();
    ctx.save(); ctx.beginPath(); build(); ctx.lineJoin = 'round'; ctx.lineWidth = inkLine(5.5); ctx.strokeStyle = INK; ctx.stroke(); ctx.restore();
  }
  // age lines
  if (spec.age) {
    const a = spec.age;
    stroke(INK, 3.2, () => { ctx.moveTo(mx + mw / 2 + 10, my - 30); ctx.quadraticCurveTo(mx + mw / 2 + 22, my - 8, mx + mw / 2 + 12, my + 12); }, .45 * a);
    if (a > .6) stroke(INK, 3, () => { ctx.moveTo(fx + 66, ey - 2); ctx.lineTo(fx + 78, ey - 8); ctx.moveTo(fx + 66, ey + 8); ctx.lineTo(fx + 78, ey + 10); }, .4 * a);
    stroke(INK, 3, () => { for (let i = 0; i < 2; i++) { ctx.moveTo(fx - 30, -76 - i * 14); ctx.quadraticCurveTo(fx + 10, -82 - i * 14, fx + 50, -74 - i * 14); } }, .3 * a);
  }
}

// ---------- the cast ----------
const CAST = {
  franklin: {
    skin: '#f1bf98', iris: '#5b7fa8', brow: '#8a7a6c', browW: 8, blush: .35, nose: 1.15, age: 1, mouthW: 48,
    coat: '#6e4a2e', trim: '#8c6440', waist: '#b08850', shirt: '#f4ede0', breeches: '#5d3e27', hose: '#e8e0d0', shoe: '#2a1d18',
    hair: 'franklin', hairColor: '#a99a8c', glasses: true, head: [1.06, 1], jowl: true, build: { chest: 1.12, belly: 1.25, neck: .9, height: .96 }
  },
  rakip: {
    skin: '#eec3a6', iris: '#3d5a46', brow: '#5a463c', browW: 9, blush: .2, nose: 1.45, age: .25, mouthW: 42,
    coat: '#7b1f2e', trim: '#d8a84a', waist: '#e7d7b5', shirt: '#fbf6ee', breeches: '#3a1820', hose: '#efe8dc', shoe: '#1d1416',
    hair: 'wig', hairColor: '#e9e4da', jabot: true, head: [.92, 1.06], build: { chest: .95, belly: .9, neck: 1.1, height: 1.06 }
  },
  usta: {
    skin: '#d9a888', iris: '#e8b54a', brow: '#cfc6b8', browW: 10, blush: 0, nose: 1.3, age: 1.2, mouthW: 44,
    coat: '#24202a', trim: '#e8b54a', waist: '#2e2a36', shirt: '#2e2a36', breeches: '#1d1a22', hose: '#1d1a22', shoe: '#120f14',
    hair: 'hood', hairColor: '#cfc6b8', beard: true, glowEyes: true, build: { chest: 1.1, belly: 1, neck: 1, height: 1.02 }
  },
  uye: {
    skin: '#e3b08e', iris: '#4a3a2c', brow: '#4a3a2c', browW: 8, blush: .2, nose: 1.2, age: .5, mouthW: 44,
    coat: '#3d4a5e', trim: '#a8946a', waist: '#c8b48c', shirt: '#f1ebdf', breeches: '#2c3442', hose: '#e6dfd2', shoe: '#1c1a1e',
    hair: 'short', hairColor: '#5a4030', build: { chest: 1, belly: 1.05, neck: 1, height: 1 }
  }
};

function backHair(spec) {
  const c = spec.hairColor;
  if (spec.hair === 'franklin') {
    shape(() => {
      ctx.moveTo(-60, -70); ctx.bezierCurveTo(-118, -60, -126, 30, -112, 120); ctx.bezierCurveTo(-104, 168, -60, 186, -30, 170);
      ctx.bezierCurveTo(-36, 120, -40, 60, -20, 30); ctx.bezierCurveTo(-10, -10, -20, -50, -60, -70);
    }, c, { shade: 22, hi: 8 });
    stroke(shadeOf(c, .45), 3, () => { for (let i = 0; i < 5; i++) { ctx.moveTo(-90 + i * 9, -20 + i * 6); ctx.quadraticCurveTo(-110 + i * 10, 70, -84 + i * 12, 150 - i * 4); } }, .7);
  } else if (spec.hair === 'wig') {
    // the queue with a black ribbon bow
    shape(() => { ctx.moveTo(-96, 10); ctx.bezierCurveTo(-130, 40, -136, 110, -122, 170); ctx.lineTo(-104, 170); ctx.bezierCurveTo(-110, 110, -100, 50, -76, 26); ctx.closePath(); }, c, { shade: 10 });
    shape(() => { ctx.moveTo(-112, 40); ctx.bezierCurveTo(-160, 10, -166, 70, -118, 58); ctx.bezierCurveTo(-74, 80, -70, 20, -112, 40); }, '#16121a', { shade: 6, hi: 4, line: 5 });
  } else if (spec.hair === 'hood') {
    shape(() => {
      ctx.moveTo(-40, -150); ctx.bezierCurveTo(-150, -140, -170, 20, -150, 150); ctx.bezierCurveTo(-120, 220, 20, 230, 110, 180);
      ctx.bezierCurveTo(150, 120, 160, -40, 120, -110); ctx.bezierCurveTo(80, -170, 10, -170, -40, -150);
    }, spec.coat, { shade: 30, hi: 10 });
  }
}
function frontHair(spec, t) {
  const c = spec.hairColor;
  if (spec.hair === 'franklin') {
    // side tufts over the ear and a bald crown with a shine
    shape(() => {
      ctx.moveTo(-36, -92); ctx.bezierCurveTo(-82, -96, -104, -40, -100, 10); ctx.bezierCurveTo(-98, 50, -82, 80, -64, 92);
      ctx.bezierCurveTo(-62, 50, -52, 10, -40, -16); ctx.bezierCurveTo(-30, -40, -24, -70, -36, -92);
    }, c, { shade: 14, hi: 8 });
    stroke(shadeOf(c, .4), 3, () => { for (let i = 0; i < 4; i++) { ctx.moveTo(-50 - i * 9, -70 + i * 8); ctx.quadraticCurveTo(-84 - i * 4, -10, -66 - i * 4, 70); } }, .6);
    ctx.save(); ctx.globalAlpha = .55; ctx.fillStyle = '#fff3e4';
    ctx.beginPath(); ctx.ellipse(10, -98, 36, 12, -.25, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  } else if (spec.hair === 'wig') {
    shape(() => {
      ctx.moveTo(84, -62); ctx.bezierCurveTo(90, -120, 20, -150, -40, -138); ctx.bezierCurveTo(-100, -126, -122, -70, -112, -10);
      ctx.lineTo(-66, -8); ctx.bezierCurveTo(-62, -40, -50, -64, -20, -74); ctx.bezierCurveTo(14, -84, 50, -80, 84, -62); ctx.closePath();
    }, c, { shade: 18, hi: 9,
      after: () => stroke(shadeOf(c, .45), 3, () => { for (let i = 0; i < 5; i++) { ctx.moveTo(70 - i * 26, -70 - i * 3); ctx.quadraticCurveTo(50 - i * 30, -112 - i * 2, 10 - i * 30, -128 + i * 6); } }, .55) });
    // two rolled curls above the ear
    [[-86, -26], [-90, 18]].forEach(([x, y]) => {
      shape(() => ctx.ellipse(x, y, 42, 20, -.05, 0, Math.PI * 2), c, { shade: 10, hi: 6, line: 5.5 });
      stroke(shadeOf(c, .5), 3.5, () => { ctx.ellipse(x - 8, y, 18, 9, 0, .3, Math.PI * 1.7); }, .8);
    });
  } else if (spec.hair === 'short') {
    shape(() => {
      ctx.moveTo(70, -80); ctx.bezierCurveTo(50, -130, -40, -140, -84, -100); ctx.bezierCurveTo(-110, -70, -108, -20, -96, 10);
      ctx.lineTo(-64, 6); ctx.bezierCurveTo(-60, -40, -30, -76, 10, -84); ctx.closePath();
    }, c, { shade: 14, hi: 8 });
  }
}

function headShape() {
  ctx.moveTo(-92, -30);
  ctx.bezierCurveTo(-112, -118, -40, -132, 12, -128);
  ctx.bezierCurveTo(72, -124, 106, -80, 104, -20);
  ctx.bezierCurveTo(104, 18, 102, 46, 86, 76);
  ctx.bezierCurveTo(70, 104, 42, 120, 14, 118);
  ctx.bezierCurveTo(-28, 116, -66, 92, -80, 52);
  ctx.bezierCurveTo(-88, 30, -90, 0, -92, -30);
  ctx.closePath();
}
function head(spec, o, t) {
  const e = o.expr ? (typeof o.expr === 'string' ? EXPR[o.expr] : o.expr) : EXPR.neutral;
  backHair(spec);
  // ear on the far side
  if (spec.hair !== 'hood') shape(() => ctx.ellipse(-64, 8, 20, 30, -.15, 0, Math.PI * 2), spec.skin, { shade: 8, hi: 4, line: 5,
    after: () => stroke(shadeOf(spec.skin, .5), 3.5, () => { ctx.arc(-62, 8, 11, -1.2, 1.6); }, .9) });
  const hs = spec.head || [1, 1];
  ctx.save(); ctx.scale(hs[0], hs[1]);
  shape(headShape, spec.skin, { shade: 26, hi: 12,
    after: () => {
      // a soft shadow under the hairline / hood and a jaw shadow
      if (spec.hair === 'hood') {
        const g = ctx.createLinearGradient(-40, -140, 10, -30); g.addColorStop(0, 'rgba(12,8,16,.85)'); g.addColorStop(1, 'rgba(12,8,16,0)');
        ctx.fillStyle = g; ctx.fillRect(-140, -150, 280, 160);
        const g2 = ctx.createLinearGradient(-100, 0, -20, 0); g2.addColorStop(0, 'rgba(12,8,16,.7)'); g2.addColorStop(1, 'rgba(12,8,16,0)');
        ctx.fillStyle = g2; ctx.fillRect(-120, -150, 120, 300);
      }
    } });
  if (spec.jowl) stroke(INK, 4, () => { ctx.moveTo(40, 112); ctx.quadraticCurveTo(70, 104, 84, 82); }, .45);
  ctx.restore();
  if (spec.beard) {
    shape(() => {
      ctx.moveTo(-74, 20); ctx.bezierCurveTo(-74, 100, -20, 150, 30, 196); ctx.bezierCurveTo(60, 150, 100, 110, 100, 30);
      ctx.bezierCurveTo(96, 60, 84, 84, 64, 92); ctx.bezierCurveTo(50, 104, 20, 104, 4, 92); ctx.bezierCurveTo(-20, 84, -50, 60, -74, 20);
    }, spec.hairColor, { shade: 18, hi: 8,
      after: () => stroke(shadeOf(spec.hairColor, .5), 3, () => { for (let i = 0; i < 6; i++) { ctx.moveTo(-50 + i * 24, 70 + i * 4); ctx.quadraticCurveTo(-30 + i * 18, 130, 20 + i * 4, 180); } }, .6) });
  }
  frontHair(spec, t);
  face(spec, e, t, o.talk || 0, o.seed || 0);
  if (spec.beard) {  // moustache over the mouth, flowing down into the beard
    shape(() => {
      ctx.moveTo(30, 60); ctx.bezierCurveTo(44, 44, 76, 46, 92, 62); ctx.bezierCurveTo(100, 76, 98, 96, 90, 110);
      ctx.bezierCurveTo(86, 90, 76, 74, 62, 70); ctx.bezierCurveTo(50, 72, 40, 80, 26, 96); ctx.bezierCurveTo(20, 80, 22, 68, 30, 60);
    }, spec.hairColor, { shade: 7, hi: 5, line: 4.5 });
  }
  if (spec.glowEyes && o.glow > 0) { glow(92, -4, 60, rgba(C.gold, 1), .5 * o.glow); glow(28, -4, 46, rgba(C.gold, 1), .4 * o.glow); }
  if (spec.glasses) {
    stroke('#5a4a3a', 4.5, () => { ctx.arc(64, -4, 27, 0, Math.PI * 2); });
    stroke('#5a4a3a', 4, () => { ctx.arc(2, -4, 21, 0, Math.PI * 2); });
    stroke('#5a4a3a', 4, () => { ctx.moveTo(23, -8); ctx.quadraticCurveTo(32, -14, 38, -8); ctx.moveTo(-19, -8); ctx.lineTo(-58, -2); });
    ctx.save(); ctx.globalAlpha = .45; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = inkLine(4); ctx.beginPath(); ctx.arc(64, -4, 19, -2.6, -1.9); ctx.stroke(); ctx.restore();
  }
}

// ---------- hands ----------
function hand(spec, kind, s = 1) {
  const sk = spec.skin, o = { shade: 7, hi: 5, line: 5 };
  ctx.save(); ctx.scale(s, s);
  if (kind === 'fist') {
    shape(() => { rrp(-24, -6, 48, 44, 16); }, sk, o);
    stroke(INK, 3, () => { ctx.moveTo(-12, 8); ctx.lineTo(-12, 22); ctx.moveTo(2, 8); ctx.lineTo(2, 24); ctx.moveTo(14, 8); ctx.lineTo(14, 22); }, .5);
  } else if (kind === 'point') {
    shape(() => { rrp(-22, -4, 44, 40, 15); }, sk, o);
    shape(() => { rrp(4, 26, 14, 50, 7); }, sk, { ...o, shade: 4 });
    shape(() => { ctx.ellipse(-20, 14, 9, 16, .5, 0, Math.PI * 2); }, sk, { ...o, shade: 4 });
  } else if (kind === 'palm') {  // flat open palm, fingers together, thumb out
    shape(() => { ctx.moveTo(-20, -4); ctx.lineTo(20, -4); ctx.lineTo(24, 40); ctx.quadraticCurveTo(26, 78, 4, 80); ctx.quadraticCurveTo(-20, 80, -22, 44); ctx.closePath(); }, sk, o);
    shape(() => { ctx.ellipse(-24, 22, 9, 20, .6, 0, Math.PI * 2); }, sk, { ...o, shade: 4 });
    stroke(INK, 2.5, () => { ctx.moveTo(-6, 44); ctx.lineTo(-7, 74); ctx.moveTo(6, 44); ctx.lineTo(7, 76); }, .45);
  } else if (kind === 'hold') {  // fingers curled around something held across the palm
    shape(() => { rrp(-22, -4, 44, 46, 16); }, sk, o);
    stroke(INK, 3, () => { ctx.moveTo(-16, 30); ctx.lineTo(16, 30); }, .5);
  } else {  // relaxed open hand
    shape(() => { ctx.moveTo(-20, -4); ctx.lineTo(20, -4); ctx.quadraticCurveTo(28, 40, 14, 66); ctx.quadraticCurveTo(0, 74, -14, 64); ctx.quadraticCurveTo(-26, 40, -20, -4); }, sk, o);
    shape(() => { ctx.ellipse(-22, 20, 8, 18, .5, 0, Math.PI * 2); }, sk, { ...o, shade: 4 });
  }
  ctx.restore();
}

// ---------- the body ----------
// pose: { dir, lean, headTilt, nod, front: {sh, el, hand}, back: {sh, el, hand}, legs: 'stand' | 'walk' | 'hide',
//         phase, expr, talk, glow, prop: fn(spec) drawn in the front hand }
function arm(spec, a, isFront, t) {
  const sh = a.sh ?? .06, el = a.el ?? .3, up = 180, fo = 168;
  const sleeve = isFront ? spec.coat : shadeOf(spec.coat, .3);
  ctx.save(); ctx.rotate(-sh);
  shape(capsule(up, 88, 68), sleeve, { shade: 18, hi: 9, outline: capsuleLine(up, 88, 68) });
  ctx.translate(0, up - 4); ctx.rotate(-el);
  shape(capsule(fo, 68, 58), sleeve, { shade: 15, hi: 8, outline: capsuleLine(fo, 68, 58) });
  // the big turned-back cuff and the lace ruffle
  ctx.save(); ctx.translate(0, fo - 30);
  shape(() => { ctx.moveTo(-38, 4); ctx.quadraticCurveTo(0, -6, 38, 4); ctx.lineTo(42, 46); ctx.quadraticCurveTo(0, 54, -42, 46); ctx.closePath(); },
    isFront ? spec.trim : shadeOf(spec.trim, .3), { shade: 9, hi: 6, line: 5.5 });
  if (spec.hair !== 'hood') shape(() => { ctx.moveTo(-28, 44); for (let i = 0; i <= 4; i++) ctx.quadraticCurveTo(-28 + i * 14 - 7, 68, -28 + i * 14, 46); ctx.lineTo(28, 44); ctx.closePath(); }, spec.shirt, { shade: 5, hi: 3, line: 4 });
  ctx.translate(0, 56);
  hand(spec, a.hand || 'open', 1.3);
  if (a.prop && isFront) a.prop(spec, t);
  ctx.restore();
  ctx.restore();
}
function legs(spec, p, t) {
  if (p.legs === 'hide') return;
  const walk = p.legs === 'walk' ? Math.sin(p.phase || 0) : 0;
  [[-1, -walk], [1, walk]].forEach(([side, sw]) => {
    const isFront = side > 0;
    ctx.save(); ctx.translate(side * 26 + 4, -440); ctx.rotate(sw * .38);
    shape(capsule(220, 86, 70), isFront ? spec.breeches : shadeOf(spec.breeches, .25), { shade: 14 });
    ctx.translate(0, 210); ctx.rotate(Math.max(0, -sw) * .5);
    shape(capsule(215, 62, 48), isFront ? spec.hose : shadeOf(spec.hose, .3), { shade: 12 });
    ctx.translate(0, 215); ctx.rotate(-sw * .2);
    shape(() => { ctx.moveTo(-26, -12); ctx.lineTo(18, -12); ctx.quadraticCurveTo(66, -6, 70, 12); ctx.lineTo(-30, 14); ctx.closePath(); }, isFront ? spec.shoe : shadeOf(spec.shoe, .3), { shade: 6, hi: 6, line: 5 });
    shape(() => rrp(4, -14, 22, 12, 3), '#d9c27a', { shade: 3, hi: 3, line: 3, noRim: true });
    ctx.restore();
  });
}
// the coat in two pieces: the skirt hangs from the hips and stays upright, the upper body leans and bows
function coatSkirt(spec) {
  const be = spec.build.belly;
  const skirt = () => {
    ctx.moveTo(-86 * be, -40); ctx.lineTo(92 * be, -40);
    ctx.bezierCurveTo(112, 40, 132, 130, 138, 204); ctx.quadraticCurveTo(14, 226, -122, 206);
    ctx.bezierCurveTo(-118, 120, -104, 30, -86 * be, -40); ctx.closePath();
  };
  const edge = () => { ctx.moveTo(30, -40); ctx.bezierCurveTo(32, 100, 40, 160, 46, 214); };
  shape(skirt, spec.coat, { shade: 36, hi: 14,
    after: () => {
      // the waistcoat's own short skirt between the coat fronts
      ctx.fillStyle = spec.waist; ctx.beginPath(); ctx.moveTo(30, -40); ctx.lineTo(92 * be, -40); ctx.quadraticCurveTo(70, 60, 40, 74); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = rgba(INK, .5); ctx.lineWidth = inkLine(3); ctx.beginPath(); ctx.moveTo(92 * be, 10); ctx.quadraticCurveTo(70, 60, 40, 74); ctx.stroke();
      ctx.strokeStyle = 'rgba(10,6,12,.4)'; ctx.lineWidth = inkLine(3.5);
      [[-60, 10, -70, 206], [-10, 40, -18, 214], [96, 40, 110, 206]].forEach(([x0, y0, x1, y1]) => {
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.quadraticCurveTo(x0 - 6, (y0 + y1) / 2, x1, y1); ctx.stroke();
      });
      ctx.fillStyle = shadeOf(spec.coat, .25); ctx.beginPath(); ctx.moveTo(56, 36); ctx.quadraticCurveTo(92, 26, 128, 40); ctx.lineTo(124, 66); ctx.quadraticCurveTo(90, 56, 58, 64); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = rgba(spec.trim, .85); ctx.lineWidth = inkLine(3); ctx.stroke();
      ctx.strokeStyle = rgba(spec.trim, .95); ctx.lineWidth = inkLine(7);
      ctx.beginPath(); ctx.moveTo(-122, 198); ctx.quadraticCurveTo(14, 218, 138, 196); ctx.stroke();
      ctx.beginPath(); edge(); ctx.stroke();
    } });
  ctx.beginPath(); edge(); ctx.lineWidth = inkLine(4.5); ctx.strokeStyle = INK; ctx.stroke();
}
function coatUpper(spec) {
  const b = spec.build, ch = b.chest, be = b.belly;
  const side = () => {
    ctx.moveTo(-84 * be, 10); ctx.quadraticCurveTo(-90 * be, 0, -86 * be, -30);
    ctx.bezierCurveTo(-98 * ch, -140, -104 * ch, -250, -92 * ch, -300);
    ctx.bezierCurveTo(-86 * ch, -326, -72 * ch, -334, -58 * ch, -338);
    ctx.bezierCurveTo(-20, -356, 36, -356, 66 * ch, -336);
    ctx.bezierCurveTo(98 * ch, -318, 104 * ch, -270, 100 * ch, -220);
    ctx.bezierCurveTo(98 * be, -150, 108 * be, -90, 92 * be, -30);
    ctx.quadraticCurveTo(96 * be, 0, 90 * be, 10);
  };
  const upper = () => { side(); ctx.closePath(); };
  const edge = () => { ctx.moveTo(18, -338); ctx.bezierCurveTo(24, -220, 30, -80, 30, 10); };
  shape(upper, spec.coat, { shade: 36, hi: 16, outline: side,
    after: () => {
      ctx.fillStyle = spec.waist; ctx.beginPath();
      ctx.moveTo(18, -338); ctx.lineTo(70 * ch, -338); ctx.bezierCurveTo(100 * ch, -260, 108 * be, -120, 94 * be, 20); ctx.lineTo(30, 20);
      ctx.bezierCurveTo(30, -60, 24, -220, 18, -338); ctx.fill();
      ctx.fillStyle = shadeOf(spec.waist, .4); ctx.beginPath(); ctx.moveTo(18, -338); ctx.lineTo(34, -338);
      ctx.bezierCurveTo(40, -220, 46, -80, 50, 20); ctx.lineTo(30, 20); ctx.bezierCurveTo(30, -60, 24, -220, 18, -338); ctx.fill();
      for (let i = 0; i < 6; i++) {
        const yy = -300 + i * 52, xx = lerp(70 * ch, 86 * be, i / 5) - 14;
        circle(xx, yy, 6.5, '#3a2a1c'); circle(xx - 1, yy - 1.5, 3.2, mixColor(spec.trim, '#ffffff', .2));
      }
      ctx.strokeStyle = rgba(spec.trim, .95); ctx.lineWidth = inkLine(7); ctx.beginPath(); edge(); ctx.stroke();
      // a soft shadow where the upper body overlaps the skirt
      const g = ctx.createLinearGradient(0, -40, 0, 12); g.addColorStop(0, 'rgba(10,6,12,0)'); g.addColorStop(1, 'rgba(10,6,12,.25)');
      ctx.fillStyle = g; ctx.fillRect(-150, -40, 300, 52);
    } });
  ctx.beginPath(); edge(); ctx.lineWidth = inkLine(4.5); ctx.strokeStyle = INK; ctx.stroke();
  shape(() => { ctx.moveTo(-50, -346); ctx.bezierCurveTo(-30, -380, 30, -384, 60, -350); ctx.bezierCurveTo(30, -360, -20, -356, -50, -346); }, shadeOf(spec.coat, .15), { shade: 6, hi: 6, line: 5 });
}
function neckwear(spec) {
  if (spec.jabot) {  // a lace jabot cascading down the chest
    shape(() => {
      ctx.moveTo(0, -350); ctx.bezierCurveTo(30, -340, 60, -330, 56, -300);
      for (let i = 0; i < 4; i++) { const y = -300 + i * 30; ctx.quadraticCurveTo(70, y + 15, 52 - i * 2, y + 30); }
      ctx.bezierCurveTo(30, -170, 8, -200, 4, -240); ctx.bezierCurveTo(-4, -280, -8, -320, 0, -350);
    }, spec.shirt, { shade: 8, hi: 4, line: 4.5 });
    stroke(shadeOf(spec.shirt, .4), 2.5, () => { for (let i = 0; i < 4; i++) { ctx.moveTo(20, -300 + i * 30); ctx.quadraticCurveTo(40, -292 + i * 30, 54, -296 + i * 30); } }, .7);
  } else if (spec.hair !== 'hood') {  // a plain white neckcloth with a small bow
    shape(() => { rrp(-40, -372, 92, 40, 16); }, spec.shirt, { shade: 8, hi: 4, line: 5 });
    shape(() => { ctx.moveTo(24, -340); ctx.lineTo(56, -318); ctx.lineTo(44, -276); ctx.lineTo(22, -300); ctx.closePath(); }, spec.shirt, { shade: 6, hi: 3, line: 4.5 });
  } else {  // the cloak's clasp
    shape(() => ctx.arc(24, -330, 16, 0, Math.PI * 2), spec.trim, { shade: 5, hi: 6, line: 4 });
  }
}
function figure(who, x, y, s, t, p = {}) {
  const spec = typeof who === 'string' ? CAST[who] : who, b = spec.build, dir = p.dir || 1;
  ctx.save(); ctx.translate(x, y); ctx.scale(s * dir * b.height, s * b.height);
  if (p.legs !== 'hide' && !p.noShadow) { ctx.save(); ctx.scale(1, .18); glow(10, 0, 210, 'rgba(0,0,0,1)', .55); ctx.restore(); }
  legs(spec, p, t);
  const breathe = Math.sin(t * 1.6 + (p.seed || 0)) * .006, hood = spec.hair === 'hood';
  ctx.translate(0, -440 + (p.bob || 0));
  const torso = () => { ctx.rotate(p.lean || 0); ctx.scale(1, 1 + breathe); };
  // the arm behind the body, then the skirt, then the upper body that leans
  if (!p.hideArms) { ctx.save(); torso(); ctx.translate(-66 * b.chest, -296); arm(spec, p.back || {}, false, t); ctx.restore(); }
  if (!hood) coatSkirt(spec);
  ctx.save(); torso();
  if (hood) {  // the cloak instead of a coat
    shape(() => {
      ctx.moveTo(-90, -340); ctx.bezierCurveTo(-30, -370, 60, -370, 100, -330); ctx.bezierCurveTo(140, -200, 150, 40, 160, 220);
      ctx.lineTo(-150, 226); ctx.bezierCurveTo(-140, 40, -130, -220, -90, -340); ctx.closePath();
    }, spec.coat, { shade: 40, hi: 14,
      after: () => { ctx.strokeStyle = rgba(C.gold, .8); ctx.lineWidth = inkLine(5); ctx.beginPath(); ctx.moveTo(24, -330); ctx.bezierCurveTo(30, -200, 40, 0, 50, 226); ctx.stroke(); } });
  } else coatUpper(spec);
  neckwear(spec);
  ctx.save(); ctx.translate(14, -350); ctx.rotate((p.headTilt || 0) + (p.nod || 0));
  if (!hood) shape(capsule(70, 66 * b.neck, 70 * b.neck), spec.skin, { shade: 18, hi: 4, after: () => { ctx.fillStyle = 'rgba(40,20,30,.35)'; ctx.fillRect(-60, -50, 120, 70); } });
  ctx.translate(18, -96); ctx.scale(1.02, 1.02);
  head(spec, p, t);
  ctx.restore();
  if (!p.hideArms) { ctx.save(); ctx.translate(66 * b.chest, -296); arm(spec, p.front || {}, true, t); ctx.restore(); }
  ctx.restore();
  ctx.restore();
}
