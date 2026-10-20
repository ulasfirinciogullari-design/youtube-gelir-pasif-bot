// 20th-century sets for the everyday episodes: a 1960s Californian street at golden hour, the house's front door
// and picture window, the signs of the foot-in-the-door study, and small houses for counting results. Frame
// coordinates are 1080x1920; the house front stands on y 1400, the lawn runs to the sidewalk at y 1600.

const DUSK = { skyTop: '#22244e', skyMid: '#a85a58', skyLow: '#f0a865', hill: '#5a3f52', lawn: '#5f7a3a', lawn2: '#45602c',
  walk: '#cdb79a', road: '#3a3540', stucco: '#ecd8b4', roof: '#5a3a30', trim: '#f6efe0' };

// a palm tree: a curved trunk and a crown of drooping fronds, silhouetted against the sky
function palm(x, y, h, t, s = 1, seed = 0) {
  const sway = Math.sin(t * .8 + seed) * 6;
  ctx.save(); ctx.translate(x, y);
  ctx.strokeStyle = '#3a2a38'; ctx.lineCap = 'round';
  ctx.lineWidth = 16 * s; ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(14 * s, -h * .5, sway, -h); ctx.stroke();
  ctx.fillStyle = '#2f2a3a';
  for (let i = 0; i < 8; i++) {
    const a = -Math.PI / 2 + (i - 3.5) * .42 + Math.sin(t * 1.1 + i) * .03, l = (90 + 30 * rnd(i + seed)) * s;
    ctx.save(); ctx.translate(sway, -h); ctx.rotate(a);
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(l * .5, -18 * s, l, 26 * s); ctx.quadraticCurveTo(l * .5, 4 * s, 0, 0); ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}
function duskSky(t, horizon = 1180) {
  const g = ctx.createLinearGradient(0, 0, 0, horizon);
  g.addColorStop(0, DUSK.skyTop); g.addColorStop(.55, DUSK.skyMid); g.addColorStop(1, DUSK.skyLow);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, horizon + 4);
  glow(180, horizon - 120, 420, 'rgba(255,214,150,1)', .55);
  circle(180, horizon - 110, 60, 'rgba(255,236,200,.85)');
  ctx.fillStyle = DUSK.hill; ctx.beginPath(); ctx.moveTo(0, horizon);
  for (let x = 0; x <= W; x += 60) ctx.lineTo(x, horizon - 70 - 40 * Math.sin(x * .006 + 1) - 20 * Math.sin(x * .017));
  ctx.lineTo(W, horizon); ctx.closePath(); ctx.fill();
}
// the house: stucco walls, a low gable roof, a big picture window with curtains, a front door with a porch light
// o: { door: colour, open: 0..1, sticker: 0..1, wall: colour, lit: 0..1 }
function ranchHouse(t, o = {}) {
  const wall = o.wall || DUSK.stucco, lit = o.lit ?? .8;
  // roof
  shape(() => { ctx.moveTo(90, 1120); ctx.lineTo(540, 990); ctx.lineTo(990, 1120); ctx.lineTo(990, 1146); ctx.lineTo(90, 1146); ctx.closePath(); },
    DUSK.roof, { shade: 18, hi: 8, line: 6,
      after: () => { ctx.strokeStyle = 'rgba(0,0,0,.18)'; ctx.lineWidth = 3; for (let y = 1010; y < 1146; y += 22) { ctx.beginPath(); ctx.moveTo(80, y); ctx.lineTo(1000, y); ctx.stroke(); } } });
  // walls with the eave's shadow
  shape(() => rrp(140, 1140, 800, 262, 4), wall, { shade: 24, hi: 10, line: 6,
    after: () => { const g = ctx.createLinearGradient(0, 1140, 0, 1200); g.addColorStop(0, 'rgba(40,20,30,.35)'); g.addColorStop(1, 'rgba(40,20,30,0)'); ctx.fillStyle = g; ctx.fillRect(140, 1140, 800, 60); } });
  pictureWindow(190, 1190, 300, 150, t, { lit, sticker: o.sticker || 0 });
  pictureWindow(770, 1200, 130, 120, t, { lit: lit * .7, small: true });
  // the front door in its frame, opening inward (the dark gap grows)
  const dx = 590, dy = 1182, dw = 110, dh = 218, open = o.open || 0;
  shape(() => rrp(dx - 12, dy - 12, dw + 24, dh + 12, 3), DUSK.trim, { shade: 6, hi: 4, line: 5 });
  ctx.fillStyle = '#1c1418'; ctx.fillRect(dx, dy, dw, dh);
  if (open > 0) glow(dx + dw / 2, dy + dh * .6, 120, 'rgba(255,200,130,1)', .35 * open * lit);
  shape(() => rrp(dx, dy, dw * (1 - .78 * open), dh, 2), o.door || '#b8463e', { shade: 8, hi: 5, line: 5,
    after: () => { ctx.strokeStyle = 'rgba(0,0,0,.25)'; ctx.lineWidth = 3; ctx.strokeRect(dx + 14, dy + 16, dw * (1 - .78 * open) - 28, 80); ctx.strokeRect(dx + 14, dy + 116, dw * (1 - .78 * open) - 28, 84); } });
  if (open < .5) circle(dx + dw * (1 - .78 * open) - 16, dy + 116, 6, '#e8c25a');
  // porch light and step
  glow(dx + dw + 34, dy + 34, 70, 'rgba(255,214,140,1)', .6 * lit);
  shape(() => rrp(dx + dw + 24, dy + 18, 20, 30, 4), '#f8e2a8', { shade: 3, hi: 3, line: 4, noRim: true });
  shape(() => rrp(dx - 30, 1398, dw + 60, 18, 3), '#b8a68e', { shade: 4, hi: 3, line: 4.5 });
  // shrubs along the wall
  [[230, 1400, 70], [330, 1404, 60], [440, 1400, 64], [790, 1402, 70], [890, 1404, 56]].forEach(([x, y, r], i) =>
    shape(() => { ctx.moveTo(x - r, y); ctx.bezierCurveTo(x - r, y - r * 1.1, x + r, y - r * 1.1, x + r, y); ctx.closePath(); }, i % 2 ? '#3f5a2e' : '#4a6634', { shade: 12, hi: 7, line: 5 }));
}
function pictureWindow(x, y, w, h, t, o = {}) {
  shape(() => rrp(x - 10, y - 10, w + 20, h + 20, 3), DUSK.trim, { shade: 6, hi: 4, line: 5 });
  const g = ctx.createLinearGradient(x, y, x, y + h);
  g.addColorStop(0, mixColor('#2a2a44', '#f4c27a', (o.lit ?? .8) * .7)); g.addColorStop(1, mixColor('#1a1a2c', '#c88a4a', (o.lit ?? .8) * .6));
  ctx.fillStyle = g; ctx.fillRect(x, y, w, h);
  // curtains drawn to the sides
  ctx.fillStyle = '#c8784a'; [[x, 1], [x + w, -1]].forEach(([cx, d]) => {
    ctx.beginPath(); ctx.moveTo(cx, y); ctx.lineTo(cx + d * w * .2, y); ctx.quadraticCurveTo(cx + d * w * .12, y + h * .6, cx + d * w * .16, y + h); ctx.lineTo(cx, y + h); ctx.fill();
  });
  // glass sheen and mullions
  ctx.save(); ctx.globalAlpha = .18; ctx.fillStyle = '#ffffff'; ctx.beginPath(); ctx.moveTo(x + w * .3, y); ctx.lineTo(x + w * .45, y); ctx.lineTo(x + w * .2, y + h); ctx.lineTo(x + w * .05, y + h); ctx.fill(); ctx.restore();
  ctx.strokeStyle = DUSK.trim; ctx.lineWidth = 8;
  if (!o.small) { ctx.beginPath(); ctx.moveTo(x + w / 3, y); ctx.lineTo(x + w / 3, y + h); ctx.moveTo(x + w * 2 / 3, y); ctx.lineTo(x + w * 2 / 3, y + h); ctx.stroke(); }
  else { ctx.beginPath(); ctx.moveTo(x + w / 2, y); ctx.lineTo(x + w / 2, y + h); ctx.moveTo(x, y + h / 2); ctx.lineTo(x + w, y + h / 2); ctx.stroke(); }
  if (o.sticker > 0) stickerProp(x + w * .83, y + h * .92, (o.stickerW || 46) * back(clamp(o.sticker)), t);
}
// the street in front: lawn, a path to the door, sidewalk, kerb and road, a mailbox
function yardAndStreet(t) {
  const g = ctx.createLinearGradient(0, 1400, 0, 1600); g.addColorStop(0, DUSK.lawn2); g.addColorStop(1, DUSK.lawn);
  ctx.fillStyle = g; ctx.fillRect(0, 1400, W, 200);
  ctx.fillStyle = DUSK.walk; ctx.beginPath(); ctx.moveTo(610, 1414); ctx.lineTo(680, 1414); ctx.lineTo(720, 1600); ctx.lineTo(570, 1600); ctx.fill();
  ctx.fillStyle = '#d9c6aa'; ctx.fillRect(0, 1600, W, 64);
  ctx.fillStyle = '#9a8a78'; ctx.fillRect(0, 1664, W, 14);
  ctx.fillStyle = DUSK.road; ctx.fillRect(0, 1678, W, 242);
  ctx.strokeStyle = 'rgba(240,220,160,.55)'; ctx.lineWidth = 8; ctx.setLineDash([60, 50]); ctx.beginPath(); ctx.moveTo(0, 1840); ctx.lineTo(W, 1840); ctx.stroke(); ctx.setLineDash([]);
  // long evening shadows across the lawn
  ctx.save(); ctx.globalAlpha = .18; ctx.fillStyle = '#1a1030'; ctx.beginPath(); ctx.moveTo(140, 1402); ctx.lineTo(940, 1402); ctx.lineTo(1080, 1470); ctx.lineTo(300, 1470); ctx.fill(); ctx.restore();
  shape(() => rrp(930, 1500, 14, 100, 2), '#5a4a3e', { shade: 3, hi: 2, line: 4 });
  shape(() => { ctx.moveTo(896, 1500); ctx.lineTo(896, 1470); ctx.quadraticCurveTo(896, 1446, 922, 1446); ctx.lineTo(950, 1446); ctx.quadraticCurveTo(976, 1446, 976, 1470); ctx.lineTo(976, 1500); ctx.closePath(); }, '#4a6a8a', { shade: 6, hi: 5, line: 4.5 });
}
// the whole street shot
function suburb(t, o = {}) {
  duskSky(t);
  // the neighbours' yards behind: darker lawn, a board fence and hedges
  const g = ctx.createLinearGradient(0, 1176, 0, 1404); g.addColorStop(0, '#3e4a34'); g.addColorStop(1, DUSK.lawn2);
  ctx.fillStyle = g; ctx.fillRect(0, 1176, W, 230);
  ctx.fillStyle = '#6a5040'; ctx.fillRect(0, 1286, W, 10);
  for (let x = 0; x < W; x += 34) { ctx.fillStyle = x % 68 ? '#7a5c48' : '#6e5242'; ctx.fillRect(x, 1250, 30, 90); }
  ctx.fillStyle = 'rgba(20,10,30,.25)'; ctx.fillRect(0, 1250, W, 90);
  [[40, 1350, 90], [1050, 1350, 80]].forEach(([x, y, r]) => shape(() => { ctx.moveTo(x - r, y); ctx.bezierCurveTo(x - r, y - r * 1.2, x + r, y - r * 1.2, x + r, y); ctx.closePath(); }, '#3a5230', { shade: 12, hi: 6, line: 5 }));
  palm(100, 1180, 420, t, 1, 1); palm(1000, 1190, 360, t, .85, 2);
  ranchHouse(t, o);
  yardAndStreet(t);
  if (o.sign > 0) uglySign(540, 1560, 760, 470, o.sign, t);
}

// the small first request: a square window card, about three inches across
function stickerProp(x, y, w, t) {
  if (w <= 1) return;
  ctx.save(); ctx.translate(x, y - w / 2); ctx.rotate(-.04);
  shape(() => rrp(-w / 2, -w / 2, w, w, w * .08), '#fbf6e8', { shade: w * .06, hi: w * .04, line: 4 });
  ctx.fillStyle = '#2a5a8a'; ctx.fillRect(-w * .42, -w * .42, w * .84, w * .3);
  text('GÜVENLİ', 0, -w * .27, w * .17, '#fbf6e8', SANS, 900);
  text('SÜR', 0, w * .1, w * .3, '#c43a3a', SANS, 900);
  // a tiny car
  ctx.fillStyle = '#2a5a8a'; rr(-w * .2, w * .22, w * .4, w * .1, w * .04); ctx.fill();
  circle(-w * .12, w * .34, w * .04, '#1a1117'); circle(w * .12, w * .34, w * .04, '#1a1117');
  ctx.restore();
}
// the big second request: a crude hand-lettered board on two posts, k 0..1 rises out of the lawn
function uglySign(x, y, w, h, k, t) {
  if (k <= 0) return;
  const rise = (1 - eo(clamp(k))) * h * .9, wob = Math.sin(t * 2.2) * .008;
  ctx.save(); ctx.translate(x, y + rise); ctx.rotate(-.025 + wob);
  [-w * .32, w * .32].forEach(px => shape(() => rrp(px - 14, -h * .35, 28, h * .5), '#6a4a30', { shade: 6, hi: 3, line: 5 }));
  shape(() => { ctx.moveTo(-w / 2, -h); ctx.lineTo(w / 2, -h + 14); ctx.lineTo(w / 2 - 6, -h * .3); ctx.lineTo(-w / 2 + 4, -h * .3 - 10); ctx.closePath(); },
    '#e9dcc0', { shade: 18, hi: 8, line: 6,
      after: () => {
        ctx.strokeStyle = 'rgba(120,90,60,.25)'; ctx.lineWidth = 3;
        for (let i = 0; i < 6; i++) { ctx.beginPath(); ctx.moveTo(-w / 2, -h + 30 + i * 70); ctx.lineTo(w / 2, -h + 40 + i * 70); ctx.stroke(); }
      } });
  // uneven painted letters with drips
  const words = [['DİKKATLİ', -h * .74, h * .2], ['SÜR', -h * .47, h * .24]];
  words.forEach(([s, yy, size], wi) => {
    const letters = [...s], total = measure(s, size, SANS, 900) * 1.08; let xx = -total / 2;
    letters.forEach((ch, i) => {
      const lw = measure(ch, size, SANS, 900) * 1.08, jy = (rnd(i * 3 + wi * 11) - .5) * size * .22, jr = (rnd(i * 7 + wi) - .5) * .18;
      ctx.save(); ctx.translate(xx + lw / 2, yy + jy); ctx.rotate(jr); text(ch, 0, 0, size * (.92 + .16 * rnd(i + wi * 5)), '#c0302e', SANS, 900); ctx.restore();
      if (rnd(i * 5 + wi * 3) > .55) { ctx.fillStyle = '#c0302e'; const dl = size * (.2 + .3 * rnd(i + 9)); rr(xx + lw * .4, yy + jy + size * .3, 6, dl, 3); ctx.fill(); }
      xx += lw;
    });
  });
  ctx.restore();
}
// a small icon of the big sign for usta's warning
function bigSignProp(x, y, w, t) { ctx.save(); ctx.translate(x, y + w * .3); ctx.scale(w / 190, w / 190); uglySign(0, 0, 300, 190, 1, 0); ctx.restore(); }
function clipboard(x, y, w, t, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  shape(() => rrp(-w / 2, -w * .65, w, w * 1.3, 6), '#8a6a44', { shade: 6, hi: 4, line: 4.5 });
  shape(() => rrp(-w * .4, -w * .55, w * .8, w * 1.1, 3), '#fbf6e8', { shade: 4, hi: 3, line: 3, noRim: true,
    after: () => { ctx.strokeStyle = 'rgba(40,40,60,.4)'; ctx.lineWidth = 3; for (let i = 0; i < 5; i++) { ctx.beginPath(); ctx.moveTo(-w * .3, -w * .35 + i * w * .18); ctx.lineTo(w * .3, -w * .35 + i * w * .18); ctx.stroke(); } } });
  shape(() => rrp(-w * .2, -w * .72, w * .4, w * .16, 4), '#c8ccd4', { shade: 3, hi: 3, line: 3.5 });
  ctx.restore();
}
// one of the houses in the results grid: k > 0 puts the big sign in its yard
function miniHouse(x, y, s, k, t, tone = 0) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  const wall = mixColor(DUSK.stucco, '#b8c8d8', tone);
  ctx.fillStyle = DUSK.lawn; ctx.fillRect(-50, 0, 100, 16);
  shape(() => { ctx.moveTo(-50, -40); ctx.lineTo(0, -70); ctx.lineTo(50, -40); ctx.closePath(); }, DUSK.roof, { shade: 5, hi: 3, line: 4 });
  shape(() => rrp(-42, -42, 84, 44, 2), wall, { shade: 7, hi: 4, line: 4,
    after: () => { ctx.fillStyle = '#f4c27a'; ctx.fillRect(-32, -30, 26, 16); ctx.fillStyle = '#b8463e'; ctx.fillRect(10, -30, 16, 32); } });
  if (k > 0) {
    ctx.save(); ctx.translate(0, 14 + (1 - back(clamp(k))) * 30); ctx.scale(back(clamp(k)), back(clamp(k)));
    shape(() => rrp(-36, -40, 72, 34, 2), '#e9dcc0', { shade: 4, hi: 2, line: 3.5,
      after: () => { ctx.fillStyle = '#c0302e'; ctx.fillRect(-28, -32, 56, 7); ctx.fillRect(-18, -20, 36, 8); } });
    ctx.restore();
  }
  ctx.restore();
}
