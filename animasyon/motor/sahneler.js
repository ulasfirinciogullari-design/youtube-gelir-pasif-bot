// Zirvenin Kanunu drawings: the hooded narrator "Usta", side-view people in silhouette and the props the
// episodes use. Every figure is a near-black shape lifted off the dark by rim light (gold = calm or ally,
// crimson = hostile), so the frames read like a lit stage instead of flat cartoon.

// ---------- path helpers ----------
// a symmetric figure from its right half: [start, [c1, c2, end], ...] down to the bottom centre
function mirrored(half) {
  const p = new Path2D(), [s, ...segs] = half;
  p.moveTo(s[0], s[1]);
  segs.forEach(([a, b, e]) => p.bezierCurveTo(a[0], a[1], b[0], b[1], e[0], e[1]));
  const pts = [s, ...segs.map(g => g[2])];
  for (let i = segs.length - 1; i >= 0; i--) {
    const [a, b] = segs[i], e = pts[i];
    p.bezierCurveTo(-b[0], b[1], -a[0], a[1], -e[0], e[1]);
  }
  p.closePath();
  return p;
}
function path(cmds) {  // [['M',x,y],['C',x1,y1,x2,y2,x,y],['L',x,y],['Q',...],['Z']]
  const p = new Path2D();
  cmds.forEach(([k, ...a]) => k === 'M' ? p.moveTo(...a) : k === 'L' ? p.lineTo(...a) : k === 'C' ? p.bezierCurveTo(...a)
    : k === 'Q' ? p.quadraticCurveTo(...a) : p.closePath());
  return p;
}
// fill a shape black with a halo of rim light around it and a brighter edge on the side facing the light
function rimFill(p, rim, a = 1, lightX = 0, edge = 1) {
  ctx.save();
  ctx.shadowColor = rgba(rim, .75 * a); ctx.shadowBlur = 34;
  ctx.fillStyle = C.ink; ctx.fill(p);
  ctx.shadowBlur = 12; ctx.shadowColor = rgba(rim, .9 * a); ctx.fill(p);
  ctx.shadowColor = 'transparent';
  if (edge > 0) {
    ctx.clip(p);
    const g = ctx.createLinearGradient(lightX - 420, 0, lightX + 420, 0);
    g.addColorStop(0, rgba(rim, 0)); g.addColorStop(.5, rgba(rim, .95 * a * edge)); g.addColorStop(1, rgba(rim, 0));
    ctx.strokeStyle = g; ctx.lineWidth = 7; ctx.stroke(p);
  }
  ctx.restore();
}

// ---------- Usta: the hooded narrator, front view, origin at the centre of the dark hood opening ----------
const USTA_BODY = mirrored([[0, -238],
  [[72, -235], [142, -188], [156, -104]],
  [[166, -44], [161, 8], [150, 44]],
  [[206, 70], [278, 102], [312, 164]],
  [[348, 236], [358, 424], [378, 624]],
  [[392, 770], [412, 905], [424, 1010]],
  [[300, 1030], [120, 1010], [0, 1022]]]);
const USTA_HOOD = mirrored([[0, -238],
  [[72, -235], [142, -188], [156, -104]],
  [[166, -44], [161, 8], [150, 44]],
  [[110, 100], [60, 150], [0, 168]]]);
const USTA_FACE = mirrored([[0, -162], [[56, -157], [93, -96], [89, -34]], [[85, 26], [42, 82], [0, 108]]]);
function usta(x, y, s, t, o = {}) {
  const talk = o.talk || 0, rim = o.rim || C.gold, eyes = o.eyes === undefined ? 1 : o.eyes;
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s * (1 + .007 * Math.sin(t * 1.7)));
  glow(0, -60, 640, rgba(rim, .55), .55 + .1 * Math.sin(t * .9));
  rimFill(USTA_BODY, rim, 1, 0, .8);
  // hood: a little lighter than the cloak, darkening into the opening
  ctx.save(); ctx.clip(USTA_HOOD);
  const hg = ctx.createRadialGradient(0, -40, 60, 0, -60, 260);
  hg.addColorStop(0, '#000'); hg.addColorStop(.55, '#151319'); hg.addColorStop(1, '#0b0a0e');
  ctx.fillStyle = hg; ctx.fill(USTA_HOOD);
  ctx.restore();
  // cloak folds catching a little light
  ctx.save(); ctx.clip(USTA_BODY); ctx.lineCap = 'round';
  [[-250, 260, -300, 1000], [-140, 220, -150, 1000], [140, 220, 160, 1000], [250, 260, 310, 1000], [0, 300, 10, 1000]].forEach(([x0, y0, x1, y1], i) => {
    const g = ctx.createLinearGradient(0, y0, 0, y1); g.addColorStop(0, 'rgba(60,52,44,.0)'); g.addColorStop(.3, 'rgba(60,52,44,.35)'); g.addColorStop(1, 'rgba(60,52,44,0)');
    ctx.strokeStyle = g; ctx.lineWidth = 10 + 6 * rnd(i);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.quadraticCurveTo((x0 + x1) / 2 + Math.sin(t * .8 + i) * 6, (y0 + y1) / 2, x1, y1); ctx.stroke();
  });
  ctx.strokeStyle = rgba(rim, .22); ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(-150, 46); ctx.quadraticCurveTo(0, 210, 150, 46); ctx.stroke();
  ctx.restore();
  // the opening: pure dark with a faint lit edge
  ctx.fillStyle = '#000'; ctx.fill(USTA_FACE);
  ctx.save(); ctx.clip(USTA_FACE);
  ctx.strokeStyle = rgba(rim, .28); ctx.lineWidth = 10; ctx.stroke(USTA_FACE);
  ctx.restore();
  // eyes: thin gold almonds that flare with the voice
  const bl = (t + .7) % 4.1 < .14 ? 1 - Math.abs(((t + .7) % 4.1) - .07) / .07 : 0;
  const e = eyes * (.72 + .28 * talk), eh = 7.5 * (1 - bl) + .8;
  if (e > .01) {
    ctx.save(); ctx.globalCompositeOperation = 'lighter';
    [-1, 1].forEach(d => {
      glow(d * 37, -38, 70, rgba(C.gold2, 1), .45 * e);
      ctx.shadowColor = C.gold; ctx.shadowBlur = 26 * e;
      ctx.fillStyle = rgba(C.gold2, e);
      ctx.beginPath(); ctx.moveTo(d * 37 - 19, -38); ctx.quadraticCurveTo(d * 37, -38 - eh * 1.6, d * 37 + 19, -38 + d * -2);
      ctx.quadraticCurveTo(d * 37, -38 + eh, d * 37 - 19, -38); ctx.fill();
    });
    ctx.restore();
  }
  // the clasp: a small gold crown, the channel's mark
  ctx.save(); ctx.translate(0, 182);
  ctx.shadowColor = C.gold; ctx.shadowBlur = 16;
  circle(0, 0, 25, '#2a2114'); ctx.strokeStyle = C.gold; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(0, 0, 25, 0, Math.PI * 2); ctx.stroke();
  crown(0, 2, 26, C.gold);
  ctx.restore();
  ctx.restore();
}
function crown(x, y, w, color) {
  ctx.save(); ctx.translate(x, y); ctx.fillStyle = color;
  ctx.beginPath(); ctx.moveTo(-w / 2, w * .22); ctx.lineTo(-w / 2, -w * .2); ctx.lineTo(-w / 4, w * .02); ctx.lineTo(0, -w * .32);
  ctx.lineTo(w / 4, w * .02); ctx.lineTo(w / 2, -w * .2); ctx.lineTo(w / 2, w * .22); ctx.closePath(); ctx.fill();
  ctx.restore();
}

// ---------- people in profile: 18th-century cut-paper silhouettes, facing right, origin under the chin ----------
const FIGURE = path([['M', -62, 30], ['C', -88, -10, -104, -70, -100, -130], ['C', -96, -190, -50, -228, 0, -228],
  ['C', 46, -228, 76, -196, 80, -152], ['C', 81, -142, 84, -138, 80, -130], ['C', 78, -124, 80, -118, 86, -108],
  ['L', 104, -78], ['C', 106, -72, 100, -66, 92, -66], ['L', 84, -64], ['C', 88, -58, 90, -54, 86, -50], ['L', 82, -46],
  ['C', 88, -42, 88, -36, 84, -32], ['C', 84, -22, 82, -10, 72, -4], ['C', 60, 4, 44, 6, 34, 12],
  ['C', 30, 30, 30, 50, 34, 66], ['C', 56, 72, 70, 92, 66, 116], ['C', 84, 150, 96, 220, 94, 300],
  ['C', 92, 380, 84, 440, 88, 520], ['C', 96, 600, 112, 700, 120, 820], ['L', -160, 820],
  ['C', -150, 700, -124, 600, -114, 520], ['C', -108, 440, -118, 360, -114, 280], ['C', -110, 180, -98, 110, -78, 70],
  ['C', -70, 54, -64, 42, -62, 30], ['Z']]);
const HAIR = path([['M', -30, -214], ['C', -86, -200, -116, -130, -114, -50], ['C', -112, 6, -104, 50, -84, 74],
  ['C', -76, 82, -66, 80, -62, 70], ['C', -80, 30, -86, -40, -70, -110], ['Z']]);
const QUEUE = path([['M', -96, -96], ['C', -118, -60, -126, 0, -118, 62], ['L', -102, 62], ['C', -108, 4, -100, -50, -84, -84], ['Z'],
  ['M', -112, 8], ['C', -150, -14, -156, 30, -126, 26], ['C', -150, 52, -112, 60, -108, 20], ['Z']]);
const TRICORN = path([['M', -150, -246], ['Q', -70, -200, 0, -204], ['Q', 80, -202, 150, -250], ['L', 136, -258],
  ['Q', 70, -270, 26, -300], ['Q', -40, -310, -90, -288], ['Q', -126, -272, -150, -246], ['Z']]);
const FIG_BOX = [-180, -330, 560, 1160];  // x, y, w, h in figure units
const FIG_CACHE = {};
function figureCanvas(key, s) {
  const c = FIG_CACHE[key] || (FIG_CACHE[key] = [0, 1].map(() => document.createElement('canvas')));
  const w = Math.ceil(FIG_BOX[2] * s), h = Math.ceil(FIG_BOX[3] * s);
  c.forEach(k => { if (k.width !== w || k.height !== h) { k.width = w; k.height = h; } });
  return c;
}
const POSES = {  // elbow and wrist goals in figure units, facing right
  point: { el: [110, 170], wr: [205, 100] }, shake: { el: [80, 330], wr: [180, 300] },
  give: { el: [100, 270], wr: [212, 246] }, cross: { el: [24, 316], wr: [128, 236] }
};
function armPose(arm, k) {
  const rest = { el: [6, 300], wr: [26, 450] }, goal = POSES[arm] || POSES.shake;
  return { el: rest.el.map((v, i) => lerp(v, goal.el[i], k)), wr: rest.wr.map((v, i) => lerp(v, goal.wr[i], k)) };
}
function hand(g, x, y, ang, kind) {
  g.save(); g.translate(x, y); g.rotate(ang);
  g.beginPath(); g.ellipse(8, 0, 30, 24, 0, 0, Math.PI * 2); g.fill();          // palm
  g.beginPath(); g.ellipse(-2, -20, 16, 10, -.6, 0, Math.PI * 2); g.fill();     // thumb
  if (kind === 'point') { g.beginPath(); g.roundRect(20, -8, 62, 15, 7); g.fill(); }
  else if (kind === 'give') { g.beginPath(); g.roundRect(14, -6, 52, 22, 10); g.fill(); }  // flat, palm up
  else { g.beginPath(); g.roundRect(14, -18, 40, 36, 14); g.fill(); }
  g.restore();
}
// a glowing eye and a lit brow on the dark profile: the only face these figures have, so it carries the mood
// ('angry' slit under a lowered brow, 'calm' almond that blinks, 'happy' closed smiling arc)
function profileEye(x, y, mood, color, t, seed = 0) {
  ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.lineCap = 'round';
  ctx.shadowColor = color; ctx.shadowBlur = 16; ctx.fillStyle = color; ctx.strokeStyle = color;
  const bl = (t + seed) % 3.7 < .13 ? .15 : 1;
  if (mood === 'happy') {
    ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(x - 12, y + 2); ctx.quadraticCurveTo(x - 1, y - 12, x + 10, y + 2); ctx.stroke();
    ctx.lineWidth = 4; ctx.globalAlpha = .75; ctx.beginPath(); ctx.moveTo(x - 16, y - 22); ctx.quadraticCurveTo(x - 2, y - 32, x + 12, y - 25); ctx.stroke();
  } else if (mood === 'angry') {
    ctx.beginPath(); ctx.moveTo(x - 12, y - 4); ctx.quadraticCurveTo(x - 1, y - 8, x + 10, y + 1); ctx.quadraticCurveTo(x - 1, y + 3, x - 12, y - 4); ctx.fill();
    ctx.lineWidth = 5; ctx.globalAlpha = .85; ctx.beginPath(); ctx.moveTo(x - 16, y - 24); ctx.lineTo(x + 13, y - 11); ctx.stroke();
  } else {
    ctx.beginPath(); ctx.moveTo(x - 11, y); ctx.quadraticCurveTo(x, y - 9 * bl, x + 9, y - 1); ctx.quadraticCurveTo(x, y + 6 * bl, x - 11, y); ctx.fill();
    ctx.lineWidth = 4; ctx.globalAlpha = .7; ctx.beginPath(); ctx.moveTo(x - 15, y - 19); ctx.quadraticCurveTo(x - 2, y - 24, x + 11, y - 19); ctx.stroke();
  }
  ctx.restore();
}
// who: 'franklin' (bald crown, long hair, spectacles) or 'rakip' (tricorn hat, tied queue)
// arm: null, 'point', 'shake', 'give' or 'cross', raised by armK (0..1); arm2 + armMix blend into a second pose.
// mood: the eye ('angry', 'calm', 'happy'), eye: its colour (default the rim), lean: radians forward from the feet
function person(who, x, y, s, dir, rim, t, o = {}) {
  const arm = o.arm, k = o.armK === undefined ? 1 : o.armK;
  const [sil, tint] = figureCanvas(who, s);
  const g = sil.getContext('2d');
  g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, sil.width, sil.height);
  g.setTransform(s, 0, 0, s, -FIG_BOX[0] * s, -FIG_BOX[1] * s);
  g.fillStyle = '#000'; g.strokeStyle = '#000'; g.lineCap = 'round'; g.lineJoin = 'round';
  g.fill(FIGURE);
  if (who === 'franklin') g.fill(HAIR); else { g.fill(QUEUE); g.fill(TRICORN); }
  if (arm && k > 0) {
    let { el, wr } = armPose(arm, k); const sh = [-14, 116];
    if (o.arm2 && o.armMix > 0) {
      const b = armPose(o.arm2, 1);
      el = el.map((v, i) => lerp(v, b.el[i], o.armMix)); wr = wr.map((v, i) => lerp(v, b.wr[i], o.armMix));
    }
    g.lineWidth = 62; g.beginPath(); g.moveTo(...sh); g.lineTo(...el); g.stroke();
    g.lineWidth = 48; g.beginPath(); g.moveTo(...el); g.lineTo(...wr); g.stroke();
    const ang = Math.atan2(wr[1] - el[1], wr[0] - el[0]);
    g.save(); g.translate(...wr); g.rotate(ang);  // lace cuff
    g.beginPath(); for (let i = 0; i <= 6; i++) { const a = -Math.PI / 2 + i * Math.PI / 6; g.lineTo(8 + Math.cos(a) * 10, Math.sin(a) * (34 + (i % 2) * 6)); } g.lineTo(-4, 0); g.fill();
    g.restore();
    hand(g, wr[0] + Math.cos(ang) * 30, wr[1] + Math.sin(ang) * 30, ang, o.arm2 && o.armMix > .5 ? o.arm2 : arm);
  }
  // tinted copy for the rim
  const tg = tint.getContext('2d');
  tg.setTransform(1, 0, 0, 1, 0, 0); tg.clearRect(0, 0, tint.width, tint.height);
  tg.globalCompositeOperation = 'source-over'; tg.drawImage(sil, 0, 0);
  tg.globalCompositeOperation = 'source-in'; tg.fillStyle = rim; tg.fillRect(0, 0, tint.width, tint.height);
  tg.globalCompositeOperation = 'source-over';
  ctx.save(); ctx.translate(x, y + Math.sin(t * 1.4 + (dir > 0 ? 0 : 2)) * 3); ctx.scale(dir, 1);
  if (o.lean) { ctx.translate(0, 800 * s); ctx.rotate(o.lean); ctx.translate(0, -800 * s); }
  const ox = FIG_BOX[0] * s, oy = FIG_BOX[1] * s;
  ctx.shadowColor = rgba(rim, .7); ctx.shadowBlur = 40; ctx.drawImage(tint, ox, oy);  // halo
  ctx.shadowColor = 'transparent';
  ctx.drawImage(tint, ox, oy);
  ctx.drawImage(sil, ox - 6 * s, oy + 3 * s);  // leaves a thin lit edge on the face side
  ctx.drawImage(sil, ox - 3 * s, oy + 6 * s);
  ctx.save(); ctx.scale(s, s);  // coat buttons and a pocket flap, just catching the light
  for (let i = 0; i < 6; i++) { const by = 150 + i * 62; circle(lerp(80, 76, i / 5), by, 4.5, rgba(rim, .55)); }
  ctx.strokeStyle = rgba(rim, .3); ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(-60, 470); ctx.quadraticCurveTo(-10, 480, 40, 466); ctx.stroke();
  ctx.restore();
  if (who === 'franklin') {  // spectacles catching the light
    ctx.save(); ctx.scale(s, s); ctx.strokeStyle = rgba(C.gold2, .95); ctx.lineWidth = 3.2; ctx.shadowColor = C.gold; ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.roundRect(70, -134, 30, 18, 7); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(70, -127); ctx.lineTo(14, -122); ctx.stroke();
    ctx.restore();
  }
  if (o.mood || o.talk) {
    ctx.save(); ctx.scale(s, s);
    if (o.mood) { ctx.save(); ctx.translate(58, -122); ctx.scale(1.55, 1.55); profileEye(0, 0, o.mood, o.eye || rim, t, dir > 0 ? 0 : 1.6); ctx.restore(); }
    if (o.talk > .05) {  // the lips part: a sliver of light at the mouth
      ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.shadowColor = o.eye || rim; ctx.shadowBlur = 12;
      ctx.fillStyle = rgba(o.eye || rim, .85); ctx.beginPath(); ctx.ellipse(76, -45, 11, 2 + 6 * o.talk, -.1, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
    ctx.restore();
  }
  ctx.restore();
}

// ---------- props ----------
// a leather book seen from the front, gold tooling; open (0..1) tilts the cover back a little
function book(x, y, w, t, o = {}) {
  const h = w * 1.36, a = o.alpha === undefined ? 1 : o.alpha;
  if (a <= 0 || w < 2) return;
  ctx.save(); ctx.globalAlpha *= a; ctx.translate(x, y); ctx.rotate(o.rot || 0);
  glow(0, 0, w * 1.6, rgba(C.gold, 1), .5 * (o.shine === undefined ? 1 : o.shine));
  ctx.shadowColor = 'rgba(0,0,0,.8)'; ctx.shadowBlur = w * .2; ctx.shadowOffsetY = w * .06;
  const g = ctx.createLinearGradient(-w / 2, -h / 2, w / 2, h / 2); g.addColorStop(0, '#5a2a1c'); g.addColorStop(1, '#2b120c');
  ctx.fillStyle = g; rr(-w / 2, -h / 2, w, h, w * .05); ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.fillStyle = '#1d0b07'; rr(-w / 2, -h / 2, w * .1, h, w * .04); ctx.fill();  // spine edge
  ctx.strokeStyle = C.gold; ctx.lineWidth = Math.max(1.5, w * .018);
  rr(-w * .34, -h * .42, w * .74, h * .84, w * .03); ctx.stroke();
  rr(-w * .3, -h * .38, w * .66, h * .76, w * .02); ctx.stroke();
  ctx.save(); ctx.translate(w * .03, 0); ctx.strokeStyle = C.gold2;
  ctx.beginPath(); ctx.arc(0, 0, w * .15, 0, Math.PI * 2); ctx.stroke();
  crown(0, w * .01, w * .17, C.gold2);
  ctx.restore();
  // a slow glint across the cover
  const gx = ((t * .35) % 1.6 - .3) * w * 1.4 - w * .7;
  ctx.save(); rr(-w / 2, -h / 2, w, h, w * .05); ctx.clip();
  const sg = ctx.createLinearGradient(gx - w * .15, 0, gx + w * .15, 0);
  sg.addColorStop(0, 'rgba(255,230,170,0)'); sg.addColorStop(.5, 'rgba(255,230,170,.22)'); sg.addColorStop(1, 'rgba(255,230,170,0)');
  ctx.fillStyle = sg; ctx.fillRect(-w / 2, -h / 2, w, h);
  ctx.restore();
  ctx.restore();
}
// Usta's dark gloved hands gripping a book of width w at (x, y) from both sides, sleeves running back into the cloak
function ustaHands(x, y, w, rim, a = 1) {
  if (a <= 0) return;
  const h = w * 1.36;
  ctx.save(); ctx.globalAlpha *= a;
  [-1, 1].forEach(d => {
    const ex = x + d * w / 2, ey = y + h * .12;
    ctx.save(); ctx.translate(ex, ey); ctx.scale(d, 1);  // drawn for the right hand, mirrored for the left
    const sleeve = path([['M', 4, -54], ['C', 60, -40, 120, 20, 150, 150], ['L', 70, 190], ['C', 50, 90, 20, 40, -6, 30], ['Z']]);
    rimFill(sleeve, rim, .8, 60, .8);
    const glove = new Path2D();
    glove.roundRect(-26, -46, 50, 86, 22);
    for (let i = 0; i < 4; i++) glove.roundRect(-46, -40 + i * 20, 40, 17, 8);  // fingers over the cover's edge
    rimFill(glove, rim, .9, -30, 1);
    ctx.restore();
  });
  ctx.restore();
}
// a sealed letter: parchment with a crimson wax seal
function letter(x, y, w, rot = 0, a = 1) {
  if (a <= 0) return;
  const h = w * .64;
  ctx.save(); ctx.globalAlpha *= a; ctx.translate(x, y); ctx.rotate(rot);
  ctx.shadowColor = 'rgba(0,0,0,.7)'; ctx.shadowBlur = 24; ctx.shadowOffsetY = 8;
  const g = ctx.createLinearGradient(0, -h / 2, 0, h / 2); g.addColorStop(0, '#e9dcc0'); g.addColorStop(1, '#b9a47f');
  ctx.fillStyle = g; rr(-w / 2, -h / 2, w, h, 6); ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.strokeStyle = 'rgba(90,70,40,.5)'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(-w / 2, -h / 2); ctx.lineTo(0, h * .08); ctx.lineTo(w / 2, -h / 2); ctx.stroke();
  glow(0, h * .08, w * .22, 'rgba(220,40,40,.8)', .4);
  circle(0, h * .08, w * .1, '#9d1520');
  ctx.strokeStyle = '#c62a33'; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(0, h * .08, w * .065, 0, Math.PI * 2); ctx.stroke();
  ctx.restore();
}
// the shelves of a candle-lit private library; `pick` is the glowing book's slot
function shelves(t, pick, lit) {
  const top = 470, rowH = 238, rows = 4, x0 = 90, x1 = 990;
  ctx.save();
  ctx.fillStyle = '#0e0b0a'; ctx.fillRect(x0 - 30, top - 40, x1 - x0 + 60, rowH * rows + 70);
  for (let r = 0; r < rows; r++) {
    const base = top + (r + 1) * rowH;
    let x = x0, i = 0;
    while (x < x1 - 20) {
      const seed = r * 97 + i, bw = 30 + rnd(seed) * 26, bh = rowH * (.62 + rnd(seed + 3) * .3);
      if (x + bw > x1) break;
      const slot = r === pick[0] && i === pick[1];
      const dist = Math.hypot(x - 160, base - 700) / 900;  // the candle stands on the left
      const light = clamp(1.05 - dist) * (.65 + .35 * Math.sin(t * 9 + seed) * .1 + .35);
      const hue = ['#4a2418', '#2e3020', '#232a3a', '#4b2d1c', '#3a1d28', '#3b3226'][Math.floor(rnd(seed + 5) * 6)];
      if (!slot) {
        ctx.fillStyle = mixColor('#050404', hue, light * .9); ctx.fillRect(x, base - bh, bw - 3, bh);
        if (rnd(seed + 8) > .45) { ctx.fillStyle = rgba(C.gold, .22 * light); ctx.fillRect(x + 3, base - bh + 18, bw - 9, 3); ctx.fillRect(x + 3, base - 26, bw - 9, 3); }
      } else if (lit < 1) {
        ctx.save(); ctx.globalAlpha = 1 - lit;
        ctx.fillStyle = '#6a3220'; ctx.fillRect(x, base - bh, bw - 3, bh);
        ctx.restore();
        glow(x + bw / 2, base - bh / 2, 160, rgba(C.gold, 1), .7 * Math.min(1, lit * 3) * (1 - lit));
        ctx.save(); ctx.strokeStyle = rgba(C.gold2, (1 - lit)); ctx.lineWidth = 4; ctx.shadowColor = C.gold; ctx.shadowBlur = 20;
        ctx.strokeRect(x, base - bh, bw - 3, bh); ctx.restore();
        shelves.slot = [x + bw / 2, base - bh / 2];
      }
      x += bw; i++;
    }
    const pg = ctx.createLinearGradient(0, base, 0, base + 24); pg.addColorStop(0, '#2a1e16'); pg.addColorStop(1, '#0a0706');
    ctx.fillStyle = pg; ctx.fillRect(x0 - 30, base, x1 - x0 + 60, 24);
  }
  ctx.restore();
}
function candle(x, y, t, s = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  glow(0, -70, 420, 'rgba(255,170,80,.9)', .42 + .05 * Math.sin(t * 13) + .04 * Math.sin(t * 7.3));
  ctx.fillStyle = '#e8dcc0'; rr(-16, -60, 32, 120, 6); ctx.fill();
  ctx.fillStyle = '#2a2018'; ctx.fillRect(-26, 55, 52, 12);
  const f = 1 + .08 * Math.sin(t * 17);
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  ctx.fillStyle = 'rgba(255,190,90,.95)'; ctx.beginPath(); ctx.ellipse(0, -92, 10, 26 * f, Math.sin(t * 5) * .08, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = 'rgba(255,245,210,.95)'; ctx.beginPath(); ctx.ellipse(0, -86, 5, 12 * f, 0, 0, Math.PI * 2); ctx.fill();
  ctx.restore(); ctx.restore();
}
// an 18th-century assembly hall: columns, tall windows, a chandelier
function hall(t) {
  room(t, '#0d0c12');
  [[300, .5], [780, .5]].forEach(([x]) => {  // windows with cold moonlight
    ctx.save();
    const g = ctx.createLinearGradient(0, 330, 0, 1000); g.addColorStop(0, 'rgba(120,140,190,.22)'); g.addColorStop(1, 'rgba(120,140,190,.02)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(x - 85, 1000); ctx.lineTo(x - 85, 420); ctx.arc(x, 420, 85, Math.PI, 0); ctx.lineTo(x + 85, 1000); ctx.fill();
    ctx.strokeStyle = 'rgba(10,10,14,.9)'; ctx.lineWidth = 8;
    ctx.beginPath(); ctx.moveTo(x, 335); ctx.lineTo(x, 1000); ctx.moveTo(x - 85, 620); ctx.lineTo(x + 85, 620); ctx.stroke();
    ctx.restore();
  });
  beam(300, 420, 1500, 150, 520, '#8fa4d8', .07, t, 18);
  beam(780, 420, 1500, 150, 520, '#8fa4d8', .07, t + 3, 18);
  // columns in front, almost black, with a warm edge from the candles
  [[70, 130], [1010, 130], [540, 0]].forEach(([x, w]) => {
    if (!w) return;
    ctx.fillStyle = '#050407'; ctx.fillRect(x - w / 2, 0, w, H);
    ctx.fillRect(x - w / 2 - 24, 250, w + 48, 46); ctx.fillRect(x - w / 2 - 24, 1290, w + 48, 60);
    const g = ctx.createLinearGradient(x - w / 2, 0, x + w / 2, 0);
    const side = x < 540 ? 1 : 0;
    g.addColorStop(side, 'rgba(255,170,90,.16)'); g.addColorStop(1 - side, 'rgba(255,170,90,0)');
    ctx.fillStyle = g; ctx.fillRect(x - w / 2, 0, w, H);
  });
  // chandelier
  ctx.save(); ctx.translate(540, 300 + Math.sin(t * .8) * 3);
  ctx.strokeStyle = '#08070a'; ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(0, -300); ctx.lineTo(0, 0); ctx.stroke();
  ctx.lineWidth = 8; ctx.beginPath(); ctx.ellipse(0, 20, 150, 26, 0, 0, Math.PI); ctx.stroke();
  for (let i = 0; i < 7; i++) {
    const a = Math.PI * (i / 6), cx = Math.cos(a) * 150, cy = 20 + Math.sin(a) * 26;
    glow(cx, cy - 26, 70, 'rgba(255,180,90,.9)', .5 + .08 * Math.sin(t * 11 + i));
    ctx.fillStyle = 'rgba(255,220,160,.95)'; ctx.beginPath(); ctx.ellipse(cx, cy - 22, 4, 9, 0, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
  floor(1320, C.candle, .2, 900);
}
// any black shape drawn by fn(g) on an offscreen canvas (origin at its centre), lit like the people: a halo and a
// bright edge in the rim colour
const SHAPE_CACHE = {};
function litShape(key, w, h, fn, x, y, rim, edge = [-5, 3]) {
  const c = SHAPE_CACHE[key] || (SHAPE_CACHE[key] = [0, 1].map(() => { const k = document.createElement('canvas'); k.width = w; k.height = h; return k; }));
  const [sil, tint] = c, g = sil.getContext('2d'), tg = tint.getContext('2d');
  if (!sil.drawn) {
    g.translate(w / 2, h / 2); g.fillStyle = '#000'; g.strokeStyle = '#000'; g.lineCap = 'round'; g.lineJoin = 'round';
    fn(g); sil.drawn = true;
  }
  tg.globalCompositeOperation = 'source-over'; tg.clearRect(0, 0, w, h); tg.drawImage(sil, 0, 0);
  tg.globalCompositeOperation = 'source-in'; tg.fillStyle = rim; tg.fillRect(0, 0, w, h);
  ctx.save(); ctx.translate(x - w / 2, y - h / 2);
  ctx.shadowColor = rgba(rim, .7); ctx.shadowBlur = 34; ctx.drawImage(tint, 0, 0);
  ctx.shadowColor = 'transparent'; ctx.drawImage(tint, 0, 0);
  ctx.drawImage(sil, edge[0], edge[1]); ctx.drawImage(sil, edge[0] / 2, edge[1] * 2);
  ctx.restore();
}
// the two things Franklin did not do, drawn like the rest of the stage instead of as line icons
function icon(name, x, y, s, color) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  if (name === 'yalvar') {  // a man on his knees in profile, hands clasped and raised, a coin purse at his knee
    litShape('yalvar', 320, 330, g => {
      g.beginPath(); g.arc(34, -108, 30, 0, Math.PI * 2); g.fill();                       // head, bowed forward
      g.beginPath(); g.moveTo(18, -84); g.bezierCurveTo(-26, -64, -40, -10, -34, 40);      // back
      g.lineTo(28, 46); g.bezierCurveTo(34, 0, 40, -40, 40, -76); g.closePath(); g.fill();  // chest
      g.lineWidth = 34; g.beginPath(); g.moveTo(-20, 40); g.lineTo(52, 66); g.lineTo(58, 118); g.stroke();  // thigh, knee down
      g.beginPath(); g.moveTo(-26, 44); g.lineTo(-30, 112); g.lineTo(-96, 118); g.stroke();  // shin back along the floor
      g.lineWidth = 26; g.beginPath(); g.moveTo(16, -60); g.lineTo(74, -70); g.lineTo(98, -122); g.stroke();  // arms up
      g.beginPath(); g.ellipse(102, -134, 20, 26, .4, 0, Math.PI * 2); g.fill();            // clasped hands
      g.beginPath(); g.moveTo(-150, 132); g.lineTo(150, 132); g.lineWidth = 8; g.stroke();  // floor
    }, 0, 0, color);
  } else if (name === 'hediye') {  // a lacquered gift box tied with a gold ribbon
    const red = color === C.gold ? '#7a1a20' : '#3a2a22', dark = color === C.gold ? '#3a0a0e' : '#1d1612';
    glow(0, 10, 220, rgba(color, 1), .25);
    ctx.shadowColor = 'rgba(0,0,0,.8)'; ctx.shadowBlur = 30; ctx.shadowOffsetY = 14;
    let g = ctx.createLinearGradient(-90, 0, 90, 0); g.addColorStop(0, dark); g.addColorStop(.45, red); g.addColorStop(1, dark);
    ctx.fillStyle = g; ctx.fillRect(-90, -20, 180, 130);                       // box
    ctx.shadowColor = 'transparent';
    g = ctx.createLinearGradient(0, -60, 0, -20); g.addColorStop(0, red); g.addColorStop(1, dark);
    ctx.fillStyle = g; ctx.fillRect(-104, -58, 208, 40);                       // lid
    ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(-90, -18, 180, 12);         // lid shadow
    g = ctx.createLinearGradient(-20, 0, 20, 0); g.addColorStop(0, C.goldDim); g.addColorStop(.5, C.gold2); g.addColorStop(1, C.goldDim);
    ctx.fillStyle = g; ctx.fillRect(-16, -58, 32, 168);                        // ribbon
    ctx.fillStyle = rgba(C.gold2, .85); ctx.fillRect(-104, -44, 208, 12);
    ctx.save(); ctx.shadowColor = color; ctx.shadowBlur = 16; ctx.fillStyle = C.gold;  // bow
    [-1, 1].forEach(d => {
      ctx.beginPath(); ctx.moveTo(0, -60); ctx.bezierCurveTo(d * 30, -118, d * 92, -110, d * 70, -76);
      ctx.bezierCurveTo(d * 56, -60, d * 24, -58, 0, -60); ctx.fill();
      ctx.beginPath(); ctx.moveTo(d * 6, -58); ctx.lineTo(d * 40, -20); ctx.lineTo(d * 22, -18); ctx.closePath(); ctx.fill();
    });
    ctx.fillStyle = '#5a3e12';
    [-1, 1].forEach(d => { ctx.beginPath(); ctx.ellipse(d * 42, -82, 14, 8, d * -.5, 0, Math.PI * 2); ctx.fill(); });
    ctx.fillStyle = C.gold2; ctx.beginPath(); ctx.arc(0, -62, 14, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    ctx.strokeStyle = rgba(color, .5); ctx.lineWidth = 3; ctx.strokeRect(-90, -20, 180, 130); ctx.strokeRect(-104, -58, 208, 40);
  }
  ctx.restore();
}
// a brush-stroke crimson cross stamped over something (k: 0..1 drawing progress)
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
// a brain drawn in gold lines with sparks running along it
function brain(x, y, s, t, k) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  glow(0, 0, 420, rgba(C.gold, 1), .35 * k);
  ctx.globalAlpha = k;
  ctx.strokeStyle = C.gold; ctx.lineWidth = 7; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.shadowColor = C.gold; ctx.shadowBlur = 22;
  const outline = path([['M', -10, -200], ['C', -90, -230, -190, -190, -215, -100], ['C', -270, -60, -260, 50, -215, 90],
    ['C', -205, 170, -110, 210, -40, 180], ['C', -20, 205, 40, 210, 60, 180], ['C', 150, 210, 230, 150, 225, 80],
    ['C', 275, 30, 265, -80, 205, -120], ['C', 180, -200, 80, -230, -10, -200], ['Z']]);
  ctx.stroke(outline);
  const folds = [
    [['M', -10, -200], ['C', 10, -120, -30, -60, 0, 0], ['C', 20, 60, -20, 120, -40, 180]],
    [['M', -180, -120], ['C', -120, -110, -110, -60, -150, -30], ['C', -190, 0, -150, 50, -100, 40]],
    [['M', -215, 90], ['C', -160, 60, -120, 110, -70, 90]],
    [['M', 150, -170], ['C', 110, -120, 160, -80, 120, -40], ['C', 80, 0, 140, 40, 190, 20]],
    [['M', 60, 180], ['C', 70, 120, 140, 130, 170, 90]],
    [['M', -100, -195], ['C', -80, -150, -40, -150, -50, -100]]];
  folds.forEach(f => ctx.stroke(path(f)));
  ctx.shadowColor = 'transparent';
  // sparks: dots travelling along the folds
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 14; i++) {
    const a = t * (1.2 + rnd(i) * .8) + rnd(i + 3) * 6.28, r = 60 + rnd(i + 5) * 150;
    const px = Math.cos(a) * r * 1.05, py = Math.sin(a * 1.3) * r * .7;
    glow(px, py, 30, rgba(C.gold2, 1), .8 * k);
    circle(px, py, 5, rgba('#fff4d6', k));
  }
  ctx.restore();
}
// the assembly in the foreground: heads and shoulders of seated members, out of focus, just rimmed
function crowd(t, y, rim) {
  ctx.save();
  ctx.filter = 'blur(3px)';
  [[60, 1.15, 0], [290, 1.0, 1], [560, 1.2, 0], [820, 1.05, 1], [1040, 1.15, 0]].forEach(([x, s, wig], i) => {
    ctx.save(); ctx.translate(x, y + Math.sin(t * 1.1 + i) * 2 + (i % 2) * 40); ctx.scale(s, s);
    const p = new Path2D();
    p.ellipse(0, -150, 62, 74, 0, 0, Math.PI * 2);                                  // head from behind
    if (wig) { p.ellipse(-48, -120, 30, 48, .2, 0, Math.PI * 2); p.ellipse(48, -120, 30, 48, -.2, 0, Math.PI * 2); }
    p.moveTo(-200, 200); p.bezierCurveTo(-190, 0, -110, -70, 0, -76); p.bezierCurveTo(110, -70, 190, 0, 200, 200); p.closePath();
    ctx.shadowColor = rgba(rim, .35); ctx.shadowBlur = 24; ctx.fillStyle = '#050406'; ctx.fill(p);
    ctx.restore();
  });
  ctx.restore();
}
