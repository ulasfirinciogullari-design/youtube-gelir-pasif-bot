// Zirvenin Kanunu sets and props, drawn in the cast's style (ink outlines, cool shade, warm candle highlights):
// the Pennsylvania assembly room at night, Franklin's study, the rival's library, and the small things the story
// needs (letter, quill, the rare book, a gift box, a gilt frame for real paintings, thought bubbles).

const WOOD = '#5a3a26', WOOD2 = '#3a2418', WALL = '#34413f', WALL2 = '#2a3433', BAIZE = '#2c5a3c', GLASS = '#1d2c52';

function fillRect(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); }
function vlin(x0, y0, x1, y1, c0, c1) { const g = ctx.createLinearGradient(x0, y0, x1, y1); g.addColorStop(0, c0); g.addColorStop(1, c1); return g; }

// a flickering candle on a brass holder; s = 1 is about 120 px tall
function candleProp(x, y, t, s = 1, light = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  if (light > 0) {
    ctx.save(); ctx.globalCompositeOperation = 'lighter';
    glow(0, -96, 520, 'rgba(255,160,70,1)', .20 * light * (1 + .08 * Math.sin(t * 13) + .05 * Math.sin(t * 7.1)));
    glow(0, -96, 140, 'rgba(255,200,120,1)', .45 * light);
    ctx.restore();
  }
  shape(() => { ctx.ellipse(0, 4, 40, 12, 0, 0, Math.PI * 2); }, '#b8862e', { shade: 6, hi: 4, noRim: true, line: 4 });
  shape(() => { rrp(-14, -70, 28, 72, 5); }, '#efe3c8', { shade: 8, hi: 4, noRim: true, line: 4 });
  shape(() => { ctx.moveTo(-14, -66); ctx.quadraticCurveTo(-6, -50, -8, -40); ctx.quadraticCurveTo(-12, -48, -14, -50); ctx.closePath(); }, '#e2d2b0', { flat: true, line: 3 });
  const f = 1 + .1 * Math.sin(t * 17) + .05 * Math.sin(t * 29), sway = Math.sin(t * 5.3) * .08;
  ctx.save(); ctx.translate(0, -72); ctx.rotate(sway);
  ctx.fillStyle = '#ffb347'; ctx.beginPath(); ctx.moveTo(0, -46 * f); ctx.bezierCurveTo(14, -24, 12, 0, 0, 2); ctx.bezierCurveTo(-12, 0, -14, -24, 0, -46 * f); ctx.fill();
  ctx.fillStyle = '#fff4cc'; ctx.beginPath(); ctx.moveTo(0, -30 * f); ctx.bezierCurveTo(7, -16, 6, -2, 0, 0); ctx.bezierCurveTo(-6, -2, -7, -16, 0, -30 * f); ctx.fill();
  ctx.restore();
  ctx.restore();
}

// ---------- the assembly room ----------
// tall arched windows with moonlight, panelled walls, a brass chandelier, plank floor; `warm` 0..1 lifts the light
function assemblyRoom(t, warm = 0, horizon = 1180) {
  // back wall
  ctx.fillStyle = vlin(0, 0, 0, horizon, mixColor(WALL2, '#4a3a30', warm * .4), mixColor(WALL, '#5a4434', warm * .45)); ctx.fillRect(0, 0, W, horizon);
  // pilasters and panels
  for (let i = 0; i < 6; i++) {
    const x = -60 + i * 240;
    shape(() => rrp(x - 26, 120, 52, horizon - 120, 4), mixColor('#3e4c49', '#6a5440', warm * .4), { shade: 10, hi: 6, noRim: true, line: 4 });
  }
  // windows between the pilasters
  for (let i = 0; i < 5; i++) {
    const x = 60 + i * 240, y = 250, w = 140, h = 520;
    const win = () => { ctx.moveTo(x, y + h); ctx.lineTo(x, y + w / 2); ctx.arc(x + w / 2, y + w / 2, w / 2, Math.PI, 0); ctx.lineTo(x + w, y + h); ctx.closePath(); };
    shape(win, GLASS, { flat: true, line: 6,
      after: () => {
        ctx.fillStyle = vlin(0, y, 0, y + h, '#2f4a86', '#16224a'); ctx.fillRect(x, y, w, h);
        glow(x + w * .7, y + 60, 160, 'rgba(160,190,255,1)', .25);
        ctx.strokeStyle = '#1a1420'; ctx.lineWidth = 6;
        ctx.beginPath(); ctx.moveTo(x + w / 2, y); ctx.lineTo(x + w / 2, y + h);
        for (let k = 1; k < 5; k++) { ctx.moveTo(x, y + w / 2 + k * (h - w / 2) / 5); ctx.lineTo(x + w, y + w / 2 + k * (h - w / 2) / 5); }
        ctx.stroke();
      } });
    // moonlight falling in
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = .06 * (1 - warm * .5);
    ctx.fillStyle = '#9fb8ff'; ctx.beginPath(); ctx.moveTo(x, y + 80); ctx.lineTo(x + w, y + 80); ctx.lineTo(x + w + 260, horizon + 300); ctx.lineTo(x + 120, horizon + 300); ctx.closePath(); ctx.fill(); ctx.restore();
  }
  // dado rail and wainscot
  fillRect(0, horizon - 230, W, 230, mixColor('#2b2a2a', '#4a3424', warm * .5));
  fillRect(0, horizon - 238, W, 12, '#1a1418');
  for (let i = 0; i < 9; i++) { ctx.strokeStyle = 'rgba(0,0,0,.35)'; ctx.lineWidth = 4; ctx.strokeRect(20 + i * 124, horizon - 200, 96, 160); }
  // floor planks in perspective
  ctx.fillStyle = vlin(0, horizon, 0, H, mixColor('#3a2619', '#5a3a22', warm * .4), '#140c09'); ctx.fillRect(0, horizon, W, H - horizon);
  ctx.strokeStyle = 'rgba(0,0,0,.4)'; ctx.lineWidth = 3;
  for (let i = -10; i <= 10; i++) { ctx.beginPath(); ctx.moveTo(540 + i * 40, horizon); ctx.lineTo(540 + i * 260, H); ctx.stroke(); }
  for (let k = 1; k < 8; k++) { const yy = horizon + Math.pow(k / 8, 1.8) * (H - horizon); ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(W, yy); ctx.stroke(); }
  ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(0, horizon, W, 10);
}
function chandelier(x, y, t, s = 1, light = 1) {
  ctx.save(); ctx.translate(x, y + Math.sin(t * .6) * 2); ctx.scale(s, s);
  stroke(INK, 5, () => { ctx.moveTo(0, -400); ctx.lineTo(0, -40); });
  ctx.save(); ctx.globalCompositeOperation = 'lighter'; glow(0, 0, 520, 'rgba(255,170,80,1)', .22 * light); ctx.restore();
  shape(() => ctx.ellipse(0, 10, 150, 26, 0, 0, Math.PI * 2), '#a8792c', { shade: 8, hi: 5, noRim: true, line: 5 });
  shape(() => { ctx.moveTo(-20, -50); ctx.lineTo(20, -50); ctx.lineTo(30, 30); ctx.quadraticCurveTo(0, 70, -30, 30); ctx.closePath(); }, '#b8862e', { shade: 8, hi: 5, noRim: true, line: 5 });
  [-130, -70, 70, 130, -100, 100].forEach((cx, i) => candleProp(cx, i > 3 ? -6 : 8, t + i, .55, light * .6));
  ctx.restore();
}
// a table covered with green baize, seen from the front; papers, an inkwell and a candle on it
function baizeTable(x, y, w, t, o = {}) {
  const h = o.h || 300;
  ctx.save(); ctx.translate(x, y);
  shape(() => { ctx.moveTo(-w / 2, 0); ctx.lineTo(w / 2, 0); ctx.lineTo(w / 2 + 16, h); ctx.lineTo(-w / 2 - 16, h); ctx.closePath(); }, BAIZE, { shade: 22, hi: 10,
    after: () => {
      ctx.fillStyle = 'rgba(0,0,0,.25)'; for (let i = 1; i < 5; i++) ctx.fillRect(-w / 2 + i * w / 5 - 2, 20, 4, h);
      ctx.fillStyle = mixColor(BAIZE, '#000000', .35); ctx.fillRect(-w / 2 - 20, 0, w + 40, 22);
    } });
  shape(() => rrp(-w / 2 - 22, -18, w + 44, 24, 6), mixColor(BAIZE, '#5a8a5a', .2), { shade: 6, hi: 6, line: 5 });
  if (o.papers !== false) {
    ctx.save(); ctx.translate(-w * .18, -20); ctx.rotate(-.06);
    shape(() => rrp(-60, -10, 120, 18, 3), '#efe4cc', { shade: 4, hi: 3, noRim: true, line: 3.5 }); ctx.restore();
    shape(() => { ctx.ellipse(w * .28, -24, 18, 8, 0, 0, Math.PI * 2); }, '#1c1820', { shade: 3, hi: 3, noRim: true, line: 3.5 });
    stroke('#efe4cc', 4, () => { ctx.moveTo(w * .28, -26); ctx.quadraticCurveTo(w * .28 + 20, -60, w * .28 + 44, -86); });
  }
  if (o.candle) candleProp(o.candle * w / 2, -14, t, .9, o.light ?? 1);
  ctx.restore();
}
// a Windsor chair back peeking above a table
function chairBack(x, y, s = 1, c = WOOD) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  shape(() => { ctx.moveTo(-70, 0); ctx.quadraticCurveTo(-80, -150, 0, -170); ctx.quadraticCurveTo(80, -150, 70, 0); ctx.lineTo(56, 0); ctx.quadraticCurveTo(64, -130, 0, -146); ctx.quadraticCurveTo(-64, -130, -56, 0); ctx.closePath(); }, c, { shade: 8, hi: 5, line: 5 });
  for (let i = -2; i <= 2; i++) stroke(c, 7, () => { ctx.moveTo(i * 20, -140 + Math.abs(i) * 6); ctx.lineTo(i * 22, 0); });
  ctx.restore();
}

// ---------- the study and the library ----------
function bookshelf(x, y, w, h, t, seed = 0, o = {}) {
  ctx.save(); ctx.translate(x, y);
  shape(() => rrp(0, 0, w, h, 6), WOOD2, { shade: 18, hi: 6, line: 6,
    after: () => {
      const rows = Math.floor(h / 170);
      for (let r = 0; r < rows; r++) {
        const sy = 20 + r * (h - 20) / rows, sh = (h - 20) / rows - 24;
        fillRect(14, sy, w - 28, sh, '#140c0a');
        let bx = 18, k = 0;
        while (bx < w - 30) {
          const bw = 22 + rnd(seed + r * 31 + k) * 26, bh = sh * (.7 + rnd(seed + r * 17 + k * 3) * .28);
          const hue = ['#6b1d22', '#2a4a3a', '#7a5a2a', '#2c3a5c', '#5a2a4a', '#8a6a3a', '#3a2a1c'][Math.floor(rnd(seed + k * 7 + r) * 7)];
          const lean = rnd(seed + k * 11 + r * 5) > .9 ? .12 : 0;
          ctx.save(); ctx.translate(bx, sy + sh); ctx.rotate(lean);
          ctx.fillStyle = hue; ctx.fillRect(0, -bh, bw - 3, bh);
          ctx.fillStyle = 'rgba(255,220,150,.35)'; ctx.fillRect(3, -bh * .8, bw - 9, 3); ctx.fillRect(3, -bh * .25, bw - 9, 3);
          ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(bw - 9, -bh, 6, bh);
          ctx.restore();
          bx += bw + (lean ? 6 : 0); k++;
        }
        fillRect(8, sy + sh, w - 16, 18, mixColor(WOOD, '#000000', .2));
      }
    } });
  ctx.restore();
}
function window2(x, y, w, h, t) {
  shape(() => rrp(x, y, w, h, 8), '#1a2448', { flat: true, line: 7,
    after: () => {
      ctx.fillStyle = vlin(0, y, 0, y + h, '#2a3f7a', '#121a3a'); ctx.fillRect(x, y, w, h);
      for (let i = 0; i < 26; i++) circle(x + rnd(i + 3) * w, y + rnd(i + 9) * h * .7, 1.2 + rnd(i) * 1.6, `rgba(255,255,255,${.3 + .5 * rnd(i + 1)})`);
      circle(x + w * .72, y + h * .22, 34, '#f0ecd8'); glow(x + w * .72, y + h * .22, 160, 'rgba(200,215,255,1)', .3);
      ctx.strokeStyle = '#1a1420'; ctx.lineWidth = 8; ctx.beginPath(); ctx.moveTo(x + w / 2, y); ctx.lineTo(x + w / 2, y + h); ctx.moveTo(x, y + h / 2); ctx.lineTo(x + w, y + h / 2); ctx.stroke();
    } });
}
// a writing desk seen from the front at table height y
function desk(x, y, w, t, o = {}) {
  ctx.save(); ctx.translate(x, y);
  shape(() => { rrp(-w / 2, 0, w, 420, 8); }, WOOD, { shade: 26, hi: 10, line: 6,
    after: () => {
      ctx.strokeStyle = 'rgba(0,0,0,.35)'; ctx.lineWidth = 5;
      ctx.strokeRect(-w / 2 + 30, 60, w / 2 - 50, 120); ctx.strokeRect(20, 60, w / 2 - 50, 120);
      circle(-w / 4 + 6, 120, 9, '#c9a24a'); circle(w / 4 - 6, 120, 9, '#c9a24a');
    } });
  shape(() => rrp(-w / 2 - 26, -20, w + 52, 30, 8), mixColor(WOOD, '#8a5a36', .3), { shade: 6, hi: 6, line: 6 });
  ctx.restore();
}
function quill(x, y, rot, s = 1) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s);
  shape(() => { ctx.moveTo(0, 0); ctx.bezierCurveTo(30, -60, 60, -150, 40, -230); ctx.bezierCurveTo(10, -170, -10, -80, 0, 0); }, '#f2ebdc', { shade: 8, hi: 4, line: 4,
    after: () => { ctx.strokeStyle = 'rgba(90,70,50,.6)'; ctx.lineWidth = 2; for (let i = 0; i < 9; i++) { ctx.beginPath(); ctx.moveTo(12 + i * 2, -40 - i * 20); ctx.lineTo(34 + i * 1.5, -54 - i * 20); ctx.stroke(); } } });
  stroke(INK, 4, () => { ctx.moveTo(0, 0); ctx.lineTo(30, -150); }, .6);
  ctx.restore();
}
// a sheet of paper with handwriting that writes itself (k 0..1); text in Cinzel as a stand-in for a period hand
function letterSheet(x, y, w, rot, k, lines, t) {
  const h = w * 1.25;
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  ctx.shadowColor = 'rgba(0,0,0,.5)'; ctx.shadowBlur = 20; ctx.shadowOffsetY = 8;
  shape(() => rrp(-w / 2, -h / 2, w, h, 6), '#f1e6cc', { shade: 10, hi: 4, noRim: true, line: 4.5 });
  ctx.shadowColor = 'transparent';
  ctx.save(); ctx.beginPath(); rrp(-w / 2, -h / 2, w, h, 6); ctx.clip();
  glow(-w * .2, -h * .2, w, 'rgba(255,200,120,1)', .18);
  const total = lines.join('').length; let shown = Math.floor(total * clamp(k));
  lines.forEach((ln, i) => {
    const part = ln.slice(0, Math.max(0, shown)); shown -= ln.length;
    text(part, -w / 2 + w * .1, -h / 2 + h * .2 + i * w * .13, w * .075, '#2a1c14', SERIF, 700, 'left');
  });
  ctx.restore();
  ctx.restore();
}
// the rare book: red leather, gilt tooling, a glint running across
function rareBook(x, y, w, t, o = {}) {
  const h = w * 1.3, a = o.alpha ?? 1;
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha *= a; ctx.translate(x, y); ctx.rotate(o.rot || 0);
  if (o.shine !== 0) { ctx.save(); ctx.globalCompositeOperation = 'lighter'; glow(0, 0, w * 1.5, 'rgba(255,190,90,1)', .35 * (o.shine ?? 1)); ctx.restore(); }
  shape(() => rrp(-w / 2 + w * .06, -h / 2 + w * .04, w, h, w * .05), '#efe2c0', { shade: 4, hi: 2, noRim: true, line: 4 });
  shape(() => rrp(-w / 2, -h / 2, w, h, w * .05), '#8a1f24', { shade: w * .12, hi: w * .06, line: 5,
    after: () => {
      ctx.strokeStyle = '#e8b54a'; ctx.lineWidth = Math.max(2, w * .025);
      ctx.strokeRect(-w * .36, -h * .42, w * .76, h * .84); ctx.strokeRect(-w * .31, -h * .37, w * .66, h * .74);
      ctx.beginPath(); ctx.arc(w * .03, 0, w * .16, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = '#e8b54a'; ctx.beginPath(); for (let i = 0; i < 8; i++) { const an = i / 8 * Math.PI * 2, r = i % 2 ? w * .05 : w * .11; ctx.lineTo(w * .03 + Math.cos(an) * r, Math.sin(an) * r); } ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#5a1216'; ctx.fillRect(-w / 2, -h / 2, w * .1, h);
      const gx = ((t * .4) % 1.8 - .4) * w * 1.4 - w * .7;
      const sg = ctx.createLinearGradient(gx - w * .15, 0, gx + w * .15, 0);
      sg.addColorStop(0, 'rgba(255,230,170,0)'); sg.addColorStop(.5, 'rgba(255,230,170,.3)'); sg.addColorStop(1, 'rgba(255,230,170,0)');
      ctx.fillStyle = sg; ctx.fillRect(-w / 2, -h / 2, w, h);
    } });
  ctx.restore();
}
function giftBox(x, y, w, t, rot = 0) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  shape(() => rrp(-w / 2, -w * .7, w, w * .7, 6), '#2a5a8a', { shade: 10, hi: 6, line: 5,
    after: () => { ctx.fillStyle = '#e8b54a'; ctx.fillRect(-w * .08, -w * .7, w * .16, w * .7); } });
  shape(() => rrp(-w / 2 - 8, -w * .82, w + 16, w * .18, 5), '#3470a8', { shade: 6, hi: 6, line: 5,
    after: () => { ctx.fillStyle = '#e8b54a'; ctx.fillRect(-w * .08, -w * .82, w * .16, w * .18); } });
  shape(() => { ctx.moveTo(0, -w * .82); ctx.bezierCurveTo(-w * .4, -w * 1.15, -w * .5, -w * .8, 0, -w * .84); ctx.bezierCurveTo(w * .5, -w * .8, w * .4, -w * 1.15, 0, -w * .82); }, '#e8b54a', { shade: 4, hi: 4, line: 4.5 });
  ctx.restore();
}
// an ornate gilt frame around a real painting; img may still be loading, then only the frame is drawn
function gildedFrame(img, x, y, w, h, focus = [.5, .4], zoom = 1, k = 1, oval = false) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
  ctx.shadowColor = 'rgba(0,0,0,.8)'; ctx.shadowBlur = 50; ctx.shadowOffsetY = 20;
  const fw = w * .085, outer = () => rrp(-w / 2 - fw, -h / 2 - fw, w + fw * 2, h + fw * 2, 10);
  ctx.fillStyle = '#7a5418'; ctx.beginPath(); outer(); ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.save(); ctx.beginPath(); outer(); ctx.clip();
  ctx.fillStyle = vlin(-w / 2 - fw, -h / 2 - fw, w / 2 + fw, h / 2 + fw, '#f2cf6e', '#8a5a14'); ctx.fillRect(-w, -h, w * 2, h * 2);
  for (let i = 0; i < 3; i++) { ctx.strokeStyle = i % 2 ? 'rgba(90,50,10,.6)' : 'rgba(255,240,180,.7)'; ctx.lineWidth = 3; ctx.beginPath(); rrp(-w / 2 - fw + 10 + i * 12, -h / 2 - fw + 10 + i * 12, w + fw * 2 - 20 - i * 24, h + fw * 2 - 20 - i * 24, 6); ctx.stroke(); }
  ctx.restore();
  ctx.lineWidth = 6; ctx.strokeStyle = INK; ctx.beginPath(); outer(); ctx.stroke();
  // the canvas itself
  const inner = () => oval ? ctx.ellipse(0, 0, w / 2, h / 2, 0, 0, Math.PI * 2) : rrp(-w / 2, -h / 2, w, h, 4);
  ctx.save(); ctx.beginPath(); inner(); ctx.clip();
  ctx.fillStyle = '#1a1410'; ctx.fillRect(-w / 2, -h / 2, w, h);
  if (img && img.complete && img.naturalWidth) {
    const sc = Math.max(w / img.naturalWidth, h / img.naturalHeight) * zoom;
    const iw = img.naturalWidth * sc, ih = img.naturalHeight * sc;
    const ix = clamp(-iw * focus[0] + 0, -iw + w / 2, -w / 2), iy = clamp(-ih * focus[1] + 0, -ih + h / 2, -h / 2);
    ctx.drawImage(img, ix, iy, iw, ih);
  }
  // varnish sheen and a dark inner edge
  const g = ctx.createRadialGradient(-w * .2, -h * .3, 10, 0, 0, Math.max(w, h) * .8); g.addColorStop(0, 'rgba(255,230,180,.12)'); g.addColorStop(1, 'rgba(0,0,0,.45)');
  ctx.fillStyle = g; ctx.fillRect(-w / 2, -h / 2, w, h);
  ctx.restore();
  ctx.lineWidth = 5; ctx.strokeStyle = 'rgba(40,20,5,.9)'; ctx.beginPath(); inner(); ctx.stroke();
  ctx.restore();
}
// a comic thought bubble with its trail of small puffs pointing at (tx, ty)
function thought(x, y, w, h, tx, ty, k, fill = '#f6efe0') {
  if (k <= 0) return;
  ctx.save(); ctx.globalAlpha *= clamp(k * 1.5);
  const puffs = 3;
  for (let i = 0; i < puffs; i++) {
    const f = (i + 1) / (puffs + 1), r = 10 + i * 8;
    if (k > f * .5) shape(() => ctx.arc(lerp(tx, x, f), lerp(ty, y + h * .4, f), r, 0, Math.PI * 2), fill, { shade: 4, hi: 3, noRim: true, line: 4.5 });
  }
  ctx.translate(x, y); ctx.scale(back(clamp(k)), back(clamp(k)));
  const cloud = () => {
    const n = 11;
    for (let i = 0; i <= n; i++) {
      const an = i / n * Math.PI * 2, px = Math.cos(an) * w / 2, py = Math.sin(an) * h / 2;
      if (i === 0) ctx.moveTo(px, py);
      else { const am = (i - .5) / n * Math.PI * 2; ctx.quadraticCurveTo(Math.cos(am) * w * .66, Math.sin(am) * h * .66, px, py); }
    }
    ctx.closePath();
  };
  shape(cloud, fill, { shade: 10, hi: 6, noRim: true, line: 6 });
  ctx.restore();
}
function sparks(x, y, t, n, spread, k, color = '#ffd98a') {
  if (k <= 0) return;
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < n; i++) {
    const an = rnd(i + 3) * Math.PI * 2, d = spread * (.3 + .7 * rnd(i + 7)) * eo(clamp(k));
    const px = x + Math.cos(an) * d, py = y + Math.sin(an) * d * .8 - 30 * k;
    ctx.globalAlpha = (1 - clamp(k)) * .9 + .1;
    circle(px, py, 2 + rnd(i + 1) * 4, color);
  }
  ctx.restore();
}
// the other members at the back tables, small and softened, so the room is never empty
function backMembers(t, warm = 0) {
  ctx.save(); ctx.filter = `blur(2px) brightness(${.62 + .2 * warm})`;
  const wig = { ...CAST.uye, coat: '#4a3a2a', hairColor: '#e6e0d6', hair: 'wig' }, blue = { ...CAST.uye, coat: '#2c3a52' };
  [[150, 1, CAST.uye, 'neutral'], [420, 1, wig, 'calm'], [700, -1, blue, 'neutral'], [960, -1, wig, 'displeased']].forEach(([x, d, who, e], i) => {
    figure(who, x, 1330, .36, t + i, { dir: d, expr: e, legs: 'hide', hideArms: true, seed: i + 4, noShadow: true });
  });
  [[280, 380], [830, 380]].forEach(([x, w]) => baizeTable(x, 1100, w, t, { h: 120, papers: false }));
  ctx.restore();
}
// a painted red X drawn in two strokes, k 0..1
function cross(x, y, size, k) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.lineCap = 'round';
  ctx.shadowColor = 'rgba(210,35,47,.8)'; ctx.shadowBlur = 24;
  [[-1, -1, 1, 1, 0], [1, -1, -1, 1, .5]].forEach(([ax, ay, bx, by, st]) => {
    const p = clamp((k - st) / .5);
    if (p <= 0) return;
    for (let j = 0; j < 3; j++) {  // three overlapping strokes for a painted edge
      ctx.strokeStyle = j ? 'rgba(210,35,47,.55)' : C.red; ctx.lineWidth = size * (.17 - j * .04);
      const o = (j - 1) * size * .03;
      ctx.beginPath(); ctx.moveTo(ax * size / 2 + o, ay * size / 2 - o);
      ctx.lineTo(lerp(ax, bx, eo(p)) * size / 2 + o, lerp(ay, by, eo(p)) * size / 2 - o); ctx.stroke();
    }
  });
  ctx.restore();
}
