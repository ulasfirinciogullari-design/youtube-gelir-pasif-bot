// k005 Uyma (Asch): Solomon Asch (1951, in Guetzkow's "Groups, Leadership and Men"; 1955 Scientific American).
// A student sits at a table with seven others who were told to give the same wrong answer on an easy line-matching
// task. A quarter never went along, so three in four did at least once; 32% of the answers followed the majority.
// With one member told to answer right all along, those errors fell to 5.5%. The why: alone against everyone you
// doubt your own eyes; one supporter breaks that. Hook and close are today's office meeting (Elif and her ally Can),
// so the very first frame is already a person alone against a table.
const K5 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  // 1951: the student who is tested, in a mustard jacket so he is the one you find in every shot
  ogr: { ...CAST.uye, outfit: 'suit', coat: '#d99a2b', trim: '#d99a2b', shirt: '#f4efe2', tie: '#6a2a1a', breeches: '#4a4a52',
    hose: '#4a4a52', shoe: '#2a1d18', hair: 'side', hairColor: '#5a3a1e', skin: '#efc29e', age: .05, iris: '#4a6a8a' },
  // the seven others: 1950s jackets in greys and browns; index 3 sits in seat 4, the one told to answer right in line 6
  OY: [['#5a6270', '#24345a', 'short', '#2a1a12', '#e8b896'], ['#6e5640', '#8a2a2a', 'side', '#1e1814', '#e2b08c'],
    ['#4a5a4a', '#2a2a2a', 'side', '#7a5030', '#f0c4a2'], ['#3d4a5e', '#a07040', 'short', '#3a2a20', '#d9a07c'],
    ['#7a6a5a', '#24345a', 'side', '#a07850', '#f1c8a6'], ['#4e4e58', '#6a2a2a', 'short', '#1e1814', '#e3b08e'],
    ['#5e4a3a', '#2f4a3c', 'side', '#4a2e1a', '#eabd98']]
    .map(([coat, tie, hair, hairColor, skin]) => ({ ...CAST.uye, outfit: 'suit', coat, trim: coat, tie, hair, hairColor, skin,
      shirt: '#f1ede2', breeches: '#3a3a44', hose: '#3a3a44', shoe: '#1c1a1e', age: .1 })),
  // today's office: Elif in a coral dress, Can in a teal jacket, three colleagues across the table
  elif: { ...CAST.evsahibi, coat: '#d4574a', trim: '#f4efe4', hairColor: '#2e1c12', skin: '#ecbf9a', shoe: '#3a1a1a', lips: '#b83a44' },
  can: { ...CAST.arastirmaci2, coat: '#2f7a74', trim: '#2f7a74', shirt: '#f4f1ea', tie: '#1c3a52', hairColor: '#2a1a12', skin: '#e0aa84', glasses: false },
  ES: [
    { ...CAST.arastirmaci2, coat: '#2a3450', trim: '#2a3450', tie: '#8a2a2a', hairColor: '#a49c92', age: .8, glasses: true, glassesColor: '#1e1816' },
    { ...CAST.evsahibi, coat: '#c8962a', trim: '#f4efe4', hairColor: '#1e1410', skin: '#d9a07c', sleeve: 'long' },
    { ...CAST.uye, outfit: 'suit', coat: '#7a2a34', trim: '#7a2a34', shirt: '#f1ede2', tie: '#7a2a34', hair: 'short', hairColor: '#2a1a12', skin: '#efc29e' }
  ],
  // the row in 1951: eight seats behind a long table; the student is seat 7 (index 6), as in Asch's set-up
  SEAT: i => 98 + i * 126, SUB: 6, PART: 3, S: .46,
  ROWY: 1468,  // a seated figure's "feet" (hidden by the table): heads land near y 1060
  FLOOR: 1335, // a standing figure's feet behind the table
  TOP: 1250,   // the table top
  // the office: colleagues seated on the left, Elif and Can standing on the right
  MEET: 1300   // the meeting table top
};
const K5I = i => i < K5.SUB ? i : i - 1;  // seat -> index in K5.OY

// ---------- small things ----------
// a comic speech bubble with a tail to (tx, ty)
function k5Bubble(x, y, w, h, tx, ty, k, fill = '#fbf6e8', line) {
  if (k <= 0) return;
  const s = back(clamp(k)), r = Math.min(30, h / 2 - 2);
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.translate(-x, -y);
  shape(() => {
    ctx.moveTo(x - w / 2 + r, y - h / 2); ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, r); ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, r);
    const bx = clamp(tx, x - w / 2 + r + 14, x + w / 2 - r - 14);
    ctx.lineTo(bx + 16, y + h / 2); ctx.lineTo(tx, ty); ctx.lineTo(bx - 12, y + h / 2);
    ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, r); ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, r); ctx.closePath();
  }, fill, { shade: 6, hi: 4, noRim: true, line: 6 });
  if (line) { ctx.strokeStyle = line; ctx.lineWidth = 6; rr(x - w / 2 + 7, y - h / 2 + 7, w - 14, h - 14, r - 6); ctx.stroke(); }
  ctx.restore();
}
// a bubble saying one answer letter: red for the wrong A, gold for the right B
function k5Say(x, y, letter, k, tx, ty, size = 1) {
  if (k <= 0) return;
  const good = letter !== 'A', w = 112 * size, h = 100 * size;
  k5Bubble(x, y, w, h, tx ?? x - 6, ty ?? y + h / 2 + 34 * size, k, good ? '#fff2c8' : '#fbf6e8', good ? C.gold : C.red);
  ctx.save(); ctx.translate(x, y); ctx.scale(back(clamp(k)), back(clamp(k)));
  text(letter, 0, 4 * size, 74 * size, good ? '#8a5a0a' : C.red, SANS, 900);
  ctx.restore();
}
// a red rubber stamp, k 0..1 drops it from large onto the frame
function k5Stamp(label, x, y, size, k, rot = -.1) {
  if (k <= 0) return;
  const s = lerp(2.2, 1, eo(clamp(k)));
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s); ctx.globalAlpha = clamp(k * 3);
  const w = measure(label, size, SANS, 900, size * .08) + size * .9, h = size * 1.5;
  ctx.fillStyle = 'rgba(251,246,232,.92)'; rr(-w / 2, -h / 2, w, h, size * .16); ctx.fill();
  ctx.strokeStyle = C.red; ctx.lineWidth = size * .1; rr(-w / 2, -h / 2, w, h, size * .16); ctx.stroke();
  ctx.lineWidth = size * .03; rr(-w / 2 + size * .14, -h / 2 + size * .14, w - size * .28, h - size * .28, size * .1); ctx.stroke();
  text(label, 0, size * .05, size, C.red, SANS, 900, 'center', 0, C.ink, size * .08);
  ctx.restore();
}
// a round gold badge with a number
function k5Badge(x, y, n, k) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(back(clamp(k)), back(clamp(k)));
  shape(() => ctx.arc(0, 0, 34, 0, Math.PI * 2), C.gold, { shade: 4, hi: 4, line: 5, noRim: true });
  text(String(n), 0, 3, 42, '#1a1117', SANS, 900);
  ctx.restore();
}

// ---------- the two cards: a line on the left, three lines A B C on the right ----------
// drawn around (x, y) at scale s; o.abc (0..1 grow), o.glow (the standard), o.match (B lit, a guide to it)
const K5L = { std: 360, A: 250, B: 360, C: 480 };
function k5Cards(x, y, s, o = {}) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  const base = 220, card = (cx, w) => shape(() => rrp(cx - w / 2, -330, w, 660, 14), '#fbf8f0', { shade: 10, hi: 6, line: 6, noRim: true });
  card(-310, 300); card(175, 570);
  const line = (lx, len, k, col = '#15131a') => {
    if (k <= 0) return;
    const l = len * eo(clamp(k));
    ctx.lineCap = 'butt'; ctx.strokeStyle = col; ctx.lineWidth = 24; ctx.beginPath(); ctx.moveTo(lx, base); ctx.lineTo(lx, base - l); ctx.stroke();
  };
  if (o.glow > 0) glow(-310, base - K5L.std / 2, 220, 'rgba(232,181,74,1)', .6 * o.glow);
  line(-310, K5L.std, 1);
  const abc = o.abc ?? 1, xs = { A: 30, B: 175, C: 320 };
  if (o.match > 0) glow(xs.B, base - K5L.B / 2, 200, 'rgba(232,181,74,1)', .6 * o.match);
  ['A', 'B', 'C'].forEach((n, i) => {
    const k = clamp(abc * 3 - i), lit = o.match > .5 && n === 'B';
    if (k < 1) { ctx.save(); ctx.globalAlpha *= .12; line(xs[n], K5L[n], 1); ctx.restore(); }  // a faint ghost before it is drawn
    line(xs[n], K5L[n], k, lit ? '#7a4a00' : '#15131a');
    if (k > 0) text(n, xs[n], base + 64, 84, lit ? '#b07800' : '#15131a', SANS, 900);
  });
  if (o.match > 0) {  // a dashed guide from the top of the standard to the top of B
    ctx.save(); ctx.globalAlpha = clamp(o.match * 2); ctx.setLineDash([22, 14]); ctx.strokeStyle = C.gold; ctx.lineWidth = 10;
    ctx.beginPath(); ctx.moveTo(-310, base - K5L.std - 14); ctx.lineTo(xs.B, base - K5L.B - 14); ctx.stroke(); ctx.restore();
  }
  ctx.restore();
}

// ---------- 1951: the seminar room and the row ----------
function k5Class(t) {
  let g = ctx.createLinearGradient(0, 0, 0, 1300); g.addColorStop(0, '#d9c9a0'); g.addColorStop(1, '#b49e74');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1310);
  // the blackboard with the two cards pinned on it
  shape(() => rrp(214, 462, 652, 360, 6), '#6a4426', { shade: 8, hi: 5, line: 6 });
  g = ctx.createLinearGradient(230, 478, 850, 806); g.addColorStop(0, '#2f4a3c'); g.addColorStop(1, '#22382e');
  ctx.fillStyle = g; ctx.fillRect(230, 478, 620, 328);
  ctx.save(); ctx.globalAlpha = .1; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 16;
  [[.25, .3], [.7, .7]].forEach(([fx, fy]) => { ctx.beginPath(); ctx.ellipse(230 + 620 * fx, 478 + 328 * fy, 90, 26, .2, 0, Math.PI * 2); ctx.stroke(); }); ctx.restore();
  k5Cards(540, 650, .32);
  // a clock and a tall window
  shape(() => ctx.arc(960, 560, 46, 0, Math.PI * 2), '#f6f0e2', { shade: 5, hi: 4, line: 5 });
  ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(960, 560); ctx.lineTo(960 + Math.sin(t * .05 + 1) * 24, 560 - Math.cos(t * .05 + 1) * 24); ctx.moveTo(960, 560); ctx.lineTo(960 + Math.sin(t * .6) * 34, 560 - Math.cos(t * .6) * 34); ctx.stroke();
  shape(() => rrp(26, 470, 150, 420, 4), '#efe6d2', { shade: 6, hi: 4, line: 5 });
  g = ctx.createLinearGradient(0, 480, 0, 880); g.addColorStop(0, '#a8cce6'); g.addColorStop(1, '#eef2e2');
  ctx.fillStyle = g; ctx.fillRect(38, 482, 126, 396); ctx.fillStyle = '#efe6d2'; ctx.fillRect(96, 482, 8, 396); ctx.fillRect(38, 674, 126, 8);
  ctx.fillStyle = '#8c7754'; ctx.fillRect(0, 1110, W, 200);  // wainscot
  shape(() => rrp(-10, 1100, W + 20, 14, 2), '#6a4426', { shade: 3, hi: 2, line: 4 });
}
// the chair backs, so the empty seat reads as empty
function k5Chairs() {
  for (let i = 0; i < 8; i++) {
    const x = K5.SEAT(i);
    shape(() => { rrp(x - 52, 1090, 104, 170, 10); }, '#7a4e2c', { shade: 6, hi: 4, line: 5, noRim: true,
      after: () => { ctx.fillStyle = 'rgba(40,20,10,.25)'; ctx.fillRect(x - 34, 1112, 68, 14); } });
  }
}
// the long table in front of the row: a top and a front panel down past the subtitles
function k5Table(y = K5.TOP) {
  shape(() => rrp(-20, y, W + 40, 34, 6), '#a0683c', { shade: 6, hi: 6, line: 5 });
  const g = ctx.createLinearGradient(0, y + 30, 0, H); g.addColorStop(0, '#6e4426'); g.addColorStop(1, '#2a1a10');
  ctx.fillStyle = g; ctx.fillRect(-20, y + 32, W + 40, H - y);
  ctx.strokeStyle = 'rgba(20,10,4,.35)'; ctx.lineWidth = 3;
  for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.moveTo(0, y + 90 + i * 70); ctx.lineTo(W, y + 90 + i * 70); ctx.stroke(); }
  [[200, 0], [690, 1]].forEach(([x, i]) => {  // papers on the table
    ctx.save(); ctx.translate(x + 60 * i, y + 10); ctx.rotate(-.05 + i * .08);
    ctx.fillStyle = '#f6f0e2'; ctx.fillRect(-50, -8, 100, 14); ctx.restore();
  });
}
const K5ARM = { sh: .18, el: .5 };  // seated, hands resting low behind the table
// one of the seven, seated; o: expr, talk, nod
function k5Member(i, t, o = {}) {
  const spec = K5.OY[K5I(i)];
  figure(spec, K5.SEAT(i), K5.ROWY, K5.S, t, { dir: i < 4 ? 1 : -1, expr: o.expr || { ...EXPR.neutral, look: [.3, .1] }, legs: 'hide',
    front: o.front || K5ARM, back: { sh: .1, el: .3 }, seed: i * 3 + 1, nod: o.nod || 0, headTilt: o.tilt || 0, talk: o.talk || 0 });
}
// the student; y is his "feet" (K5.ROWY seated, K5.FLOOR standing)
function k5Student(x, y, t, o = {}) {
  figure(K5.ogr, x, y, K5.S, t, { dir: o.dir || -1, expr: o.expr || 'calm', legs: o.legs || 'hide', phase: o.phase || 0,
    front: o.front || K5ARM, back: { sh: .1, el: .3 }, seed: 2, headTilt: o.tilt || 0, talk: o.talk || 0, lean: o.lean || 0 });
}
// above a seated head: where its bubble or badge goes
const k5Above = i => [clamp(K5.SEAT(i), 78, 990), K5.ROWY - 1030 * K5.S - 70];

// ---------- today: the meeting room ----------
function k5Office(t, o = {}) {
  let g = ctx.createLinearGradient(0, 0, 0, 1320); g.addColorStop(0, '#3e5468'); g.addColorStop(1, '#2c3e50');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 1320);
  // a wide window on the right: a bright city afternoon
  shape(() => rrp(590, 450, 470, 600, 4), '#d8dde2', { shade: 6, hi: 4, line: 5 });
  g = ctx.createLinearGradient(0, 462, 0, 1040); g.addColorStop(0, '#8ec2ea'); g.addColorStop(1, '#e6f0f4');
  ctx.fillStyle = g; ctx.fillRect(602, 462, 446, 576);
  ctx.save(); ctx.beginPath(); ctx.rect(602, 462, 446, 576); ctx.clip();
  [[620, 780, 70], [700, 720, 60], [770, 840, 90], [870, 680, 70], [950, 760, 80], [1020, 820, 60]].forEach(([x, y, w], i) => {
    ctx.fillStyle = i % 2 ? '#9fb2c4' : '#b4c4d2'; ctx.fillRect(x, y, w, 320);
    ctx.fillStyle = 'rgba(255,255,255,.55)';
    for (let r = 0; r < 8; r++) for (let c = 0; c < 3; c++) if (rnd(i * 31 + r * 7 + c) > .35) ctx.fillRect(x + 10 + c * (w - 20) / 3, y + 16 + r * 34, (w - 40) / 3, 14);
  });
  ctx.restore();
  ctx.fillStyle = '#d8dde2'; ctx.fillRect(820, 462, 10, 576); ctx.fillRect(602, 740, 446, 10);
  // the screen on the left: a chart in the hook, the two options at the close
  shape(() => rrp(40, 450, 500, 300, 8), '#1c2430', { shade: 6, hi: 4, line: 6 });
  ctx.fillStyle = '#f4f6f8'; ctx.fillRect(56, 466, 468, 268);
  if (o.options) {
    text('HANGİ PLAN?', 290, 506, 32, '#2c3e50', SANS, 900);
    [['A', 170, C.red], ['B', 410, '#2f7a74']].forEach(([n, x, col]) => {
      shape(() => rrp(x - 90, 540, 180, 170, 10), n === 'A' ? '#fbe2de' : '#dff0ee', { shade: 3, hi: 3, line: 4, noRim: true });
      text(n, x, 628, 120, col, SANS, 900);
    });
  } else {
    [[110, 120, '#5a8ab0'], [200, 170, '#5a8ab0'], [290, 90, '#d4574a'], [380, 200, '#5a8ab0']].forEach(([x, h, c]) => {
      ctx.fillStyle = c; ctx.fillRect(x - 30, 708 - h, 60, h);
    });
    ctx.strokeStyle = '#2c3e50'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(70, 710); ctx.lineTo(500, 710); ctx.stroke();
  }
  ctx.fillStyle = '#26323e'; ctx.fillRect(0, 1110, W, 210);
}
// the meeting table: white top, dark front, a laptop and mugs
function k5MeetTable(y = K5.MEET) {
  shape(() => rrp(-20, y, W + 40, 30, 6), '#e8e4dc', { shade: 5, hi: 5, line: 5 });
  const g = ctx.createLinearGradient(0, y + 28, 0, H); g.addColorStop(0, '#3a4450'); g.addColorStop(1, '#141a20');
  ctx.fillStyle = g; ctx.fillRect(-20, y + 28, W + 40, H - y);
  shape(() => { ctx.moveTo(140, y + 4); ctx.lineTo(260, y + 4); ctx.lineTo(250, y - 76); ctx.lineTo(150, y - 76); ctx.closePath(); }, '#9aa3ad', { shade: 4, hi: 4, line: 4, noRim: true });
  [[430, '#f4efe4'], [640, '#c43a3a']].forEach(([x, c]) => shape(() => rrp(x - 22, y - 48, 44, 50, 6), c, { shade: 4, hi: 4, line: 4, noRim: true }));
}
// the three colleagues seated on the left, turned toward Elif; mode 'point' | 'vote'
const K5E = [[120, 'displeased'], [310, 'haughty'], [500, 'angry']];
function k5Colleagues(t, mode, k = 1) {
  K5E.forEach(([x, e], i) => {
    const jab = Math.sin(t * 7 + i * 1.7) * .06;
    const front = mode === 'point' ? { sh: lerp(.7, 1.5, k) + jab * k, el: lerp(1.1, .08, k), hand: k > .4 ? 'point' : 'open' }
      : { sh: lerp(.2, 2.75, eo(k)) + jab * .5 * k, el: lerp(.5, .15, k), hand: 'open' };
    figure(K5.ES[i], x, 1590, .66, t, { expr: e, legs: 'hide', front, back: { sh: .1, el: .3 }, seed: i * 5 + 2, headTilt: mode === 'point' ? -.04 : 0 });
  });
}
// Elif and Can standing at the right end of the table
const K5P = { elif: [770, 1530, .74], can: [965, 1550, .74] };

// ---------- the results ----------
// a big bar from the floor line up to v (0..35 % fills the height), the value on top, a caption below
const K5B = { top: 700, bot: 1230, max: 35 };
function k5Bar(cx, v, col, col2, caption, a = 1) {
  const y = lerp(K5B.bot, K5B.top, v / K5B.max);
  ctx.save(); ctx.globalAlpha *= a;
  shape(() => rrp(cx - 130, K5B.top - 20, 260, K5B.bot - K5B.top + 20, 16), 'rgba(20,18,26,.85)', { flat: true, line: 4 });
  const g = ctx.createLinearGradient(0, y, 0, K5B.bot); g.addColorStop(0, col2); g.addColorStop(1, col);
  shape(() => rrp(cx - 112, y, 224, Math.max(8, K5B.bot - y - 4), 10), col, { flat: true, line: 4, after: () => { ctx.fillStyle = g; ctx.fillRect(cx - 120, y, 240, K5B.bot - y);
    ctx.fillStyle = 'rgba(255,255,255,.25)'; ctx.fillRect(cx - 92, y + 10, 20, K5B.bot - y - 20); } });
  const label = '%' + (Math.round(v * 10) / 10).toString().replace('.', ',');
  text(label, cx, y - 76, 120, col2, SANS, 900, 'center', 12);
  plate(caption, cx, K5B.bot + 74, 36, 1, col === C.red ? C.red : C.gold, 440);
  ctx.restore();
}

Object.assign(SAHNE, {
  kanca(V, L, t, lt) {
    // the hook, from the very first frame: Elif stands alone at the end of the table, three colleagues point at her.
    // On "yanına" Can steps in beside her and raises his hand; she lights up
    K5.day();
    const tn = wordTime(L, V.no, .3), ty = wordTime(L, V.yes, .8);
    const come = eo(prog(t, ty - .35, ty + .25)), joy = prog(t, ty, ty + .3);
    const [ex, ey, es] = K5P.elif, [cx, cy, cs] = K5P.can;
    cam(540, 980, lerp(1.04, 1.08, eio(prog(lt, 0, 4))), () => {
      k5Office(t);
      glow(ex, 950, 420, 'rgba(255,240,200,1)', .3);  // a pool of light on her
      k5Colleagues(t, 'point', 1);
      if (come > 0) figure(K5.can, lerp(1300, cx, come), cy, cs, t, { dir: -1, expr: 'warm', legs: 'hide', seed: 8,
        front: { sh: lerp(.6, 2.8, eo(joy)), el: lerp(1, .15, joy), hand: 'open' }, back: { sh: .1, el: .3 }, lean: -.04 * (1 - come) });
      figure(K5.elif, ex, ey, es, t, { dir: -1, expr: joy > .5 ? 'happy' : 'surprised', legs: 'hide', seed: 3,
        front: joy > .5 ? { sh: lerp(.4, 2.6, eo(joy)), el: lerp(2.6, 1.0, joy), hand: 'fist' } : { sh: .4 + Math.sin(t * 6) * .03, el: 2.6, hand: 'open' },
        back: { sh: .1, el: .3 }, headTilt: joy > .5 ? 0 : .05 });
      k5MeetTable();
    });
    plate(V.no_label, 540, 540, 60, back(prog(t, tn - .05, tn + .3)), C.red);
    if (joy > 0) glow(540, 1340, 320, 'rgba(232,181,74,1)', .3 * joy);
    plate(V.yes_label, 540, 1340, 56, back(prog(t, ty - .1, ty + .25)), C.gold);
  },
  masa(V, L, t, lt) {
    // the 1951 seminar room: seven seated at a long table, one empty chair; the psychologist shows the student in.
    // On "yedi" the seven are counted 1..7; on "oturttu" he sits down in seat 7
    K5.day();
    const tc = wordTime(L, V.count, .5), tk = wordTime(L, V.key, .85);
    const walk = prog(t, L.t0 + .1, tk - .1), sit = eio(prog(t, tk - .1, tk + .3));
    cam(540, 1000, lerp(1.0, 1.04, eio(prog(lt, 0, 5))), () => {
      k5Class(t);
      k5Chairs();
      figure('arastirmaci', 1010, K5.FLOOR, .5, t, { dir: -1, expr: 'warm', legs: 'hide', seed: 6, talk: talk(t, L) * .3,
        front: { sh: lerp(.3, 1.3, eo(prog(t, tk - .7, tk - .2))), el: .3, hand: 'palm' }, back: { sh: .1, el: .3 } });
      for (let i = 0; i < 8; i++) if (i !== K5.SUB) k5Member(i, t, { expr: { ...EXPR.calm, look: [.5, 0] } });
      const sx = lerp(1200, K5.SEAT(K5.SUB), eo(walk)), sy = lerp(K5.FLOOR, K5.ROWY, sit);
      k5Student(sx, sy, t, { legs: walk > 0 && walk < 1 ? 'walk' : 'hide', phase: walk * 16, expr: 'warm' });
      k5Table();
      let n = 0;
      for (let i = 0; i < 8; i++) {
        if (i === K5.SUB) continue;
        const [bx, by] = k5Above(i);
        k5Badge(bx, by + 10, ++n, prog(t, tc + n * .1 - .1, tc + n * .1 + .15));
      }
    });
    plate(V.year, 540, 470, 46, back(prog(t, L.t0 + .05, L.t0 + .4)));
  },
  soru(V, L, t, lt) {
    // the task, close up: the line on the left card, then A, B and C; on "aynı" a gold guide shows B is the match
    K5.day();
    const te = wordTime(L, V.easy, .3), tl = wordTime(L, V.line, .5), t3 = wordTime(L, V.three, .6), ta = wordTime(L, V.key, .8);
    ctx.fillStyle = '#22382e'; ctx.fillRect(0, 0, W, H);
    cam(540, 960, lerp(1.0, 1.05, eio(prog(lt, 0, 4))), () => {
      drawSoft(.4, () => cam(540, 900, 1.6, () => k5Class(t)), .35);
      k5Cards(540, 900, 1.02, { glow: prog(t, tl - .1, tl + .3) * (1 - prog(t, ta, ta + .3)), abc: prog(t, t3 - .1, t3 + .6), match: prog(t, ta, ta + .45) });
    });
    plate('Çok kolay', 540, 476, 46, back(prog(t, te - .1, te + .25)), C.gold);
  },
  hile(V, L, t, lt) {
    // "they were in on it": a red stamp, a sly look from the seven; then one by one each says "A" (the wrong one),
    // while the student's face falls; his own thought asks "B?"
    K5.day();
    const tk = wordTime(L, V.key, .4), th = wordTime(L, V.all, .6);
    const sly = prog(t, tk, tk + .2) * (1 - prog(t, th + .1, th + .4));
    const order = [0, 1, 2, 3, 4, 5, 7];
    const said = i => prog(t, th + order.indexOf(i) * .2, th + order.indexOf(i) * .2 + .22);
    const shock = prog(t, th + .5, th + .9), last = prog(t, th + 1.2, th + 1.5);
    cam(lerp(540, 700, eio(last)), 1000, lerp(1.0, 1.14, eio(last)), () => {
      k5Class(t);
      k5Chairs();
      for (let i = 0; i < 8; i++) {
        if (i === K5.SUB) { k5Student(K5.SEAT(i), K5.ROWY, t, { expr: last > .5 ? 'confused' : shock > .5 ? 'surprised' : 'calm', tilt: shock * .04 }); continue; }
        const s = said(i);
        k5Member(i, t, { expr: sly > .3 ? { ...EXPR.sly, look: [.2, .3] } : s > 0 ? { ...EXPR.calm, curve: .1, look: [.4, 0] } : { ...EXPR.neutral, look: [.4, 0] },
          talk: s > 0 && s < 1 ? .6 : 0 });
      }
      k5Table();
      for (let i = 0; i < 8; i++) {
        if (i === K5.SUB) continue;
        const [bx, by] = k5Above(i);
        k5Say(bx, by - 20, 'A', said(i), bx + (i < 4 ? 16 : -16), by + 50);
      }
      const [sx, sy] = k5Above(K5.SUB);
      if (last > 0) { thought(sx, sy - 40, 150, 116, sx - 10, sy + 50, last); if (last > .4) text('B?', sx, sy - 36, 60, '#8a5a0a', SANS, 900); }
    });
    k5Stamp('ANLAŞMALI', 540, 560, 66, prog(t, tk - .05, tk + .2), -.08);
  },
  sonuc(V, L, t, lt) {
    // four students in a row: the big "3 in 4" from the start, a pulse on "dörtte üçü"; on "kalabalığa" three of them
    // say A, the fourth B
    K5.day();
    const tk = wordTime(L, V.key, .3), tw = wordTime(L, V.went, .7);
    cam(540, 1000, 1.1, () => k5Class(t));
    ctx.fillStyle = 'rgba(10,12,24,.7)'; ctx.fillRect(0, 0, W, H);
    const big = back(prog(lt, .02, .32)), pulse = 1 + .08 * Math.sin(Math.PI * prog(t, tk - .05, tk + .3));
    const cols = [['#d99a2b', '#5a3a1e', '#efc29e'], ['#3d6a8a', '#2a1a12', '#e2b08c'], ['#5a7a4a', '#7a5030', '#f0c4a2'], ['#8a5a3a', '#1e1814', '#d9a07c']];
    cols.forEach(([coat, hairColor, skin], i) => {
      const x = 150 + i * 260, said = prog(t, tw + i * .16, tw + i * .16 + .22), wrong = i < 3;
      const spec = { ...K5.ogr, coat, trim: coat, hairColor, skin, hair: i % 2 ? 'short' : 'side' };
      if (said > 0) glow(x, 1080, 230, wrong ? 'rgba(210,35,47,1)' : 'rgba(232,181,74,1)', .4 * said);
      figure(spec, x, 1570, .62, t, { dir: i < 2 ? 1 : -1, legs: 'hide', seed: i * 4 + 1, front: { sh: .06, el: .12 }, back: { sh: .04, el: .1 },
        expr: said > .5 ? (wrong ? { ...EXPR.displeased, look: [.2, .5] } : 'knowing') : { ...EXPR.calm, look: [.3, -.2] },
        headTilt: said > .5 && wrong ? .1 : 0, talk: said > 0 && said < 1 ? .6 : 0 });
      k5Say(x, 840, wrong ? 'A' : 'B', said, x + (i < 2 ? 18 : -18), 920, 1.2);
    });
    shape(() => rrp(-20, 1240, W + 40, 30, 6), '#a0683c', { shade: 5, hi: 5, line: 5 });
    const g = ctx.createLinearGradient(0, 1270, 0, H); g.addColorStop(0, '#5a3820'); g.addColorStop(1, '#1a100a');
    ctx.fillStyle = g; ctx.fillRect(-20, 1268, W + 40, H);
    if (big > 0) {
      ctx.save(); ctx.translate(540, 580); ctx.scale(big * pulse, big * pulse);
      const s = '4 KİŞİDEN 3\'Ü', size = Math.min(130, Math.floor(130 * 900 / measure(s, 130, SANS, 900)));
      text(s, 0, 0, size, C.red2, SANS, 900, 'center', 14);
      ctx.restore();
    }
    plate('En az bir kez kalabalığa uydu', 540, 704, 46, back(prog(t, tw - .2, tw + .15)), C.red);
  },
  destek(V, L, t, lt) {
    // the partner: in seat 4 one man says B; the student's face clears and he says B too. Then, on "yanlış", the
    // two bars: wrong answers 32 % alone against all, 5.5 % with one on his side
    K5.day();
    const tk = wordTime(L, V.key, .3), tt = wordTime(L, 'tek', .5), tc = wordTime(L, V.cut, .6);
    if (t < tc - .08) {
      const gb = prog(t, tk - .1, tk + .2), sb = prog(t, tt, tt + .25);
      cam(540, 1000, lerp(1.0, 1.04, eio(prog(lt, 0, 2.5))), () => {
        k5Class(t);
        k5Chairs();
        if (gb > 0) glow(K5.SEAT(K5.PART), 1080, 240, 'rgba(232,181,74,1)', .6 * gb);
        for (let i = 0; i < 8; i++) {
          if (i === K5.SUB) { k5Student(K5.SEAT(i), K5.ROWY, t, { expr: sb > .5 ? 'happy' : gb > .5 ? { ...EXPR.surprised, look: [.6, 0] } : 'confused', talk: sb > 0 && sb < 1 ? .6 : 0 }); continue; }
          k5Member(i, t, i === K5.PART ? { expr: 'warm', talk: gb > 0 && gb < 1 ? .6 : 0 } : { expr: { ...EXPR.neutral, look: [.4, 0] } });
        }
        k5Table();
        for (let i = 0; i < 8; i++) {
          if (i === K5.SUB) continue;
          const [bx, by] = k5Above(i);
          if (i === K5.PART) k5Say(bx, by - 20, 'B', gb, bx + 16, by + 50);
          else { ctx.save(); ctx.globalAlpha = .5; k5Say(bx, by - 20, 'A', 1, bx + (i < 4 ? 16 : -16), by + 50); ctx.restore(); }
        }
        const [sx, sy] = k5Above(K5.SUB);
        k5Say(sx, sy - 20, 'B', sb, sx - 16, sy + 50);
      });
      plate('1 kişi doğruyu söyledi', 540, 520, 48, back(prog(t, tk - .05, tk + .3)), C.gold);
      return;
    }
    cam(540, 1000, 1.1, () => k5Class(t));
    ctx.fillStyle = 'rgba(10,12,24,.76)'; ctx.fillRect(0, 0, W, H);
    const show = clamp(prog(t, tc - .08, tc + .25)), down = eio(prog(t, tc + .3, L.t1 - .3));
    plate('Yanlış cevaplar', 540, 510, 52, back(show), C.ivory);
    k5Bar(285, V.from, C.red, C.red2, 'Herkes karşıyken', show);
    k5Bar(795, lerp(V.from, V.to, down), mixColor(C.red, C.goldDim, down), mixColor(C.red2, C.gold2, down), '1 destekçiyle', clamp(prog(t, tc + .1, tc + .4)));
    flash(t, tc - .08, .4);
  },
  neden(V, L, t, lt) {
    // the why. Alone in a ring of red A's he rubs his eyes; a thought cloud asks whether A and B are the same.
    // On the second "tek" the partner steps in with a gold B and the ring blows away
    K5.day();
    const tg = wordTime(L, 'gözünden', .4), ts = wordTime(L, V.key, .45);
    const ta = (L.words.filter(w => norm(w.w).startsWith('tek'))[1] || {}).t0 || wordTime(L, V.ally, .6) - .3;
    const tb = wordTime(L, V.breaks, .9);
    const come = eo(prog(t, ta - .15, ta + .35)), blow = eio(prog(t, ta + .2, tb + .2)), doubt = prog(t, tg - .2, tg + .3) * (1 - prog(t, ta, ta + .3));
    ctx.fillStyle = '#1a1612'; ctx.fillRect(0, 0, W, H);
    cam(540, 960, lerp(1.0, 1.04, eio(prog(lt, 0, 5))), () => {
      drawSoft(.4, () => cam(540, 900, 1.5, () => k5Class(t)), .5);
      glow(600, 940, 540, come > 0 ? 'rgba(255,220,150,1)' : 'rgba(210,60,60,1)', .25 + .2 * come);
      // the ring of A's, closing in until the partner comes, then blown outward
      const close = .14 * eio(prog(t, L.t0, ta));
      [[150, 640], [120, 900], [190, 1160], [400, 560], [960, 860], [930, 1130]].forEach(([x0, y0], i) => {
        const dx = x0 - 600, dy = y0 - 920, x = 600 + dx * (1 - close + 1.4 * blow), y = 920 + dy * (1 - close + 1.4 * blow);
        ctx.save(); ctx.globalAlpha *= 1 - blow; k5Say(x, y, 'A', prog(t, L.t0 + i * .07, L.t0 + i * .07 + .25), x - dx * .15, y + 70, 1.15); ctx.restore();
      });
      if (come > 0) {
        const px = lerp(-220, 220, come);
        glow(px, 1000, 280, 'rgba(232,181,74,1)', .5 * come);
        figure(K5.OY[K5I(K5.PART)], px, 1840, .84, t, { expr: 'warm', legs: 'hide', seed: 10,
          front: { sh: lerp(.6, 2.75, eo(prog(t, ta + .1, ta + .45))), el: .15, hand: 'open' }, back: { sh: .1, el: .3 } });
        k5Say(150, 640, 'B', prog(t, ta + .2, ta + .5), 190, 740, 1.35);
      }
      figure(K5.ogr, 650, 1880, .96, t, { dir: -1, legs: 'hide', seed: 2,
        expr: come > .5 ? 'happy' : doubt > .3 ? { ...EXPR.confused, lid: .3, squint: .3 } : { ...EXPR.surprised, look: [-.4, 0] },
        front: doubt > .2 ? { sh: .5 + Math.sin(t * 8) * .05, el: 2.8, hand: 'open' } : K5ARM,
        back: { sh: .1, el: .3 }, headTilt: doubt * .08 });
      // his doubt: A next to B with "=?" between, wobbling
      if (doubt > 0) {
        thought(790, 610, 420, 270, 720, 800, doubt);
        if (doubt > .4) {
          ctx.save(); ctx.globalAlpha *= clamp((doubt - .4) * 3); ctx.translate(790, 590); ctx.rotate(Math.sin(t * 5) * .06);
          ctx.strokeStyle = '#15131a'; ctx.lineWidth = 16; ctx.lineCap = 'butt';
          ctx.beginPath(); ctx.moveTo(-100, 50); ctx.lineTo(-100, -20); ctx.moveTo(100, 50); ctx.lineTo(100, -50); ctx.stroke();
          text('A', -100, 84, 40, C.red, SANS, 900); text('B', 100, 84, 40, '#15131a', SANS, 900);
          text('=?', 0, 12, 76, '#15131a', SANS, 900);
          ctx.restore();
        }
      }
    });
    const k1 = back(prog(t, tg - .1, tg + .25)) * (1 - prog(t, ta - .1, ta + .1));
    plate('Kendi gözünden şüphe', 540, 1330, 50, k1, C.red);
    plate('Tek destekçi şüpheyi kırar', 540, 1330, 54, back(prog(t, ta + .1, ta + .4)), C.gold);
  },
  toplanti(V, L, t, lt) {
    // back in today's meeting, Can already beside Elif. The colleagues vote A; on "ses" Can's hand and his B go up;
    // on "konuşmak" Elif speaks up for B too
    K5.day();
    const ts = wordTime(L, V.key, .3), tn = wordTime(L, V.no, .5), ty = wordTime(L, V.yes, .7);
    const can = prog(t, ts - .15, ts + .2), elif = prog(t, ty - .1, ty + .25);
    const [ex, ey, es] = K5P.elif, [cx, cy, cs] = K5P.can;
    cam(540, 980, lerp(1.08, 1.04, eio(prog(lt, 0, 4))), () => {
      k5Office(t, { options: true });
      k5Colleagues(t, 'vote', eo(prog(lt, 0, .5)));
      figure(K5.can, cx, cy, cs, t, { dir: -1, expr: 'warm', legs: 'hide', seed: 8, talk: can > 0 && can < 1 ? .6 : 0,
        front: { sh: lerp(.3, 2.8, eo(can)), el: lerp(.6, .15, can), hand: 'open' }, back: { sh: .1, el: .3 } });
      figure(K5.elif, ex, ey, es, t, { dir: -1, expr: elif > .5 ? 'happy' : { ...EXPR.displeased, look: [.6, .1] }, legs: 'hide', seed: 3,
        talk: elif > 0 ? talk(t, L) * .6 : 0,
        front: elif > 0 ? { sh: lerp(.3, 2.7, eo(elif)), el: lerp(.4, .15, elif), hand: 'open' } : { sh: .3, el: .4 }, back: { sh: .1, el: .3 } });
      k5MeetTable();
      K5E.forEach(([x], i) => k5Say(x - 22, 830, 'A', prog(t, L.t0 + .2 + i * .12, L.t0 + .45 + i * .12), x, 910, .9));
      k5Say(960, 640, 'B', can, 960, 730, 1.15);
      k5Bubble(620, 690, 300, 110, 700, 790, elif, '#fff2c8', C.gold);
      if (elif > .3) text('BEN DE B!', 620, 694, 50, '#8a5a0a', SANS, 900);
    });
    plate('Susmak', 270, 1340, 50, back(prog(t, tn - .1, tn + .2)), C.red);
    const st = eo(prog(t, ty - .3, ty));
    if (st > 0) { ctx.save(); ctx.lineCap = 'round'; ctx.strokeStyle = C.red2; ctx.lineWidth = 10; ctx.shadowColor = 'rgba(210,35,47,.8)'; ctx.shadowBlur = 16;
      ctx.beginPath(); ctx.moveTo(150, 1344); ctx.lineTo(150 + 240 * st, 1336); ctx.stroke(); ctx.restore(); }
    plate('Konuşmak', 790, 1340, 50, back(prog(t, ty - .1, ty + .2)), C.gold);
  }
});
