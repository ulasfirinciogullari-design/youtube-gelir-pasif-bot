// k003 Kapıyı yüzüne çarptırma: Cialdini et al. 1975, a US university campus. A huge request first (two years of
// weekly counselling), refused by everyone; then the small one (a two-hour zoo trip). Results as two groups of 24
// students; the why is a concession answered by a concession. A sunny 1970s campus.
const K3 = {
  day() { setLight(-.7, -.62, { rim: '#fff1cf', rimA: .45, warm: '#fff0d2', cool: '#2e3a5c' }); },
  order: [...Array(24).keys()].sort((a, b) => rnd(a * 17 + 3) - rnd(b * 17 + 3)),  // the order the icons light up in
  // 1970s students: a young man in a tan corduroy jacket over jeans (no tie: it takes the shirt's colour), a young
  // woman in a rust dress
  ali: { ...CAST.uye, outfit: 'suit', coat: '#a0763e', trim: '#a0763e', shirt: '#efe6d2', tie: '#efe6d2', breeches: '#3d5a86',
    hose: '#3d5a86', shoe: '#5a3a22', hair: 'side', hairColor: '#6a3e1e', skin: '#efc29e', age: .05, iris: '#5a4030' },
  ayse: { ...CAST.evsahibi, coat: '#b8542a', trim: '#f2e2c4', hairColor: '#3a2416', shoe: '#5a2a1a', skin: '#ecbf9a' }
};
K3.crowd = [
  K3.ali, K3.ayse, { ...K3.ali, coat: '#5a6a4a', trim: '#5a6a4a', hairColor: '#2a1a10', hair: 'short', skin: '#d9a07c' },
  { ...K3.ayse, coat: '#3a6aa0', hairColor: '#c89a4a' }, { ...K3.ali, coat: '#7a3a3a', trim: '#7a3a3a', hairColor: '#1e1612', breeches: '#4a4a52', hose: '#4a4a52' }
];

// the campus: blue sky, a red-brick hall with white columns, two big trees, lawn and a concrete walk toward us
function campus(t, o = {}) {
  const hz = 1180;
  let g = ctx.createLinearGradient(0, 0, 0, hz); g.addColorStop(0, '#5f97cf'); g.addColorStop(.7, '#a9cfe8'); g.addColorStop(1, '#dcecf2');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, hz + 4);
  glow(860, 260, 380, 'rgba(255,248,220,1)', .55);
  [[220, 560, 1], [760, 470, .8], [520, 620, .6]].forEach(([x, y, s], i) => {  // slow clouds
    const cx = x + ((t * 6 * (i + 1)) % 200) - 100;
    ctx.save(); ctx.globalAlpha = .75; ctx.fillStyle = '#ffffff';
    [[-70, 0, 60], [0, -24, 74], [74, 0, 56], [20, 14, 60]].forEach(([dx, dy, r]) => { ctx.beginPath(); ctx.arc(cx + dx * s, y + dy * s, r * s, 0, Math.PI * 2); ctx.fill(); });
    ctx.restore();
  });
  // the hall: brick walls, a cream cornice, tall windows in two rows, a portico of four columns over steps
  shape(() => rrp(70, 720, 940, 470, 2), '#9c4a34', { shade: 20, hi: 8, line: 6,
    after: () => {
      ctx.strokeStyle = 'rgba(60,20,14,.22)'; ctx.lineWidth = 2;
      for (let y = 740; y < 1190; y += 18) { ctx.beginPath(); ctx.moveTo(70, y); ctx.lineTo(1010, y); ctx.stroke(); }
      [[120, 780], [240, 780], [780, 780], [900, 780], [120, 960], [240, 960], [780, 960], [900, 960]].forEach(([x, y]) => {
        ctx.fillStyle = '#efe6d2'; ctx.fillRect(x - 8, y - 8, 76, 136);
        const wg = ctx.createLinearGradient(x, y, x, y + 120); wg.addColorStop(0, '#9cc0dc'); wg.addColorStop(1, '#3a5274');
        ctx.fillStyle = wg; ctx.fillRect(x, y, 60, 120);
        ctx.fillStyle = '#efe6d2'; ctx.fillRect(x + 27, y, 6, 120); ctx.fillRect(x, y + 56, 60, 6);
      });
    } });
  shape(() => { ctx.moveTo(50, 724); ctx.lineTo(1030, 724); ctx.lineTo(1010, 690); ctx.lineTo(70, 690); ctx.closePath(); }, '#efe6d2', { shade: 6, hi: 4, line: 5 });
  shape(() => { ctx.moveTo(360, 700); ctx.lineTo(540, 610); ctx.lineTo(720, 700); ctx.closePath(); }, '#efe6d2', { shade: 8, hi: 5, line: 5 });
  ctx.fillStyle = '#4a2a22'; ctx.fillRect(400, 760, 280, 420);
  [400, 480, 580, 660].forEach(x => shape(() => rrp(x - 6, 730, 32, 440, 4), '#f4ecdc', { shade: 8, hi: 5, line: 4.5 }));
  shape(() => rrp(380, 704, 320, 30, 2), '#efe6d2', { shade: 5, hi: 4, line: 4.5 });
  [0, 1, 2].forEach(i => shape(() => rrp(370 - i * 16, 1168 + i * 10, 340 + i * 32, 12, 2), '#d8d0c0', { shade: 3, hi: 2, line: 3.5 }));
  // lawn and the walk, wide at the bottom
  g = ctx.createLinearGradient(0, hz, 0, H); g.addColorStop(0, '#5f8a3c'); g.addColorStop(1, '#3f6a2a');
  ctx.fillStyle = g; ctx.fillRect(0, hz, W, H - hz);
  ctx.fillStyle = '#d6cdbb'; ctx.beginPath(); ctx.moveTo(470, hz + 20); ctx.lineTo(610, hz + 20); ctx.lineTo(1080, 1700); ctx.lineTo(1080, 1920); ctx.lineTo(0, 1920); ctx.lineTo(0, 1700); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = 'rgba(90,80,60,.25)'; ctx.lineWidth = 3;
  for (let i = 1; i < 6; i++) { const y = hz + 20 + Math.pow(i / 6, 1.6) * 700; const hw = lerp(70, 760, (y - hz) / 700); ctx.beginPath(); ctx.moveTo(540 - hw, y); ctx.lineTo(540 + hw, y); ctx.stroke(); }
  // two trees framing the hall
  [[30, 1240, 1.1, 1], [1050, 1250, 1, 2]].forEach(([x, y, s, seed]) => {
    const sway = Math.sin(t * .9 + seed) * 4;
    shape(() => rrp(x - 22 * s, y - 330 * s, 44 * s, 340 * s, 10), '#5a3e2a', { shade: 8, hi: 4, line: 5 });
    [[-90, -420, 150], [80, -440, 140], [0, -560, 170], [-140, -560, 110], [140, -580, 110]].forEach(([dx, dy, r], i) =>
      shape(() => ctx.arc(x + dx * s + sway, y + dy * s, r * s, 0, Math.PI * 2), i % 2 ? '#4f7f36' : '#5d8f3e', { shade: 22, hi: 12, line: 5 }));
  });
  // people far away on the paths, small and soft, so the campus is alive
  if (!o.empty) drawSoft(.5, () => {
    [[(t * 40) % 1300 - 110, 1215, 1, K3.crowd[2]], [1190 - (t * 34) % 1300, 1225, -1, K3.crowd[3]]].forEach(([x, y, d, who], i) =>
      figure(who, x, y, .13, t, { dir: d, legs: 'walk', phase: t * 7 + i, hideBack: true, front: { sh: .3, el: .4 }, seed: 20 + i, noShadow: true }));
  }, .1);
}
// a stack of books held against the chest
function books(x, y, w, t) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(-.1);
  [['#7a2a2a', 0], ['#2a4a6a', 1], ['#c89a3a', 2]].forEach(([c, i]) => shape(() => rrp(-w / 2 + i * 4, -i * w * .2, w, w * .2, 3), c, { shade: 4, hi: 3, line: 3.5 }));
  ctx.restore();
}
// a desk calendar on two rings: a red header, the week number counting up, "2 YIL" under it; flip 0..1 turns a page
function calendar(x, y, w, num, flip, t, foot = '2 YIL') {
  const h = w * 1.12;
  ctx.save(); ctx.translate(x, y);
  shape(() => rrp(-w / 2, -h / 2, w, h, 14), '#fbf6e8', { shade: 10, hi: 6, line: 6,
    after: () => {
      ctx.fillStyle = C.red; ctx.fillRect(-w / 2, -h / 2, w, h * .24);
      text('HAFTA', 0, -h / 2 + h * .125, w * .13, '#fbf6e8', SANS, 900, 'center', 0, C.ink, w * .02);
      text(String(num), 0, h * .06, w * .42, '#1a1117', SANS, 900);
      text(foot, 0, h * .36, w * .12, C.red, SANS, 900, 'center', 0, C.ink, w * .01);
    } });
  if (flip > 0 && flip < 1) {  // the page lifting away over the top
    ctx.save(); ctx.translate(0, -h / 2 + h * .24); ctx.scale(1, Math.cos(flip * Math.PI));
    ctx.fillStyle = flip < .5 ? '#f2ead6' : '#d8ceb8'; ctx.globalAlpha = 1 - flip * .5; rr(-w / 2, 0, w, h * .76, 10); ctx.fill();
    ctx.restore();
  }
  [-w * .25, w * .25].forEach(rx => shape(() => rrp(rx - 9, -h / 2 - 22, 18, 46, 8), '#c8ccd4', { shade: 4, hi: 3, line: 4 }));
  ctx.restore();
}
// usta's two props: the calendar floats up right, beside his face and clear of both plates; the ticket is larger
function bigCalendarProp(x, y, w, t) { if (w > 24) calendar(x + 200, y - 260, w * 1.0, 104, 0, t); }
function zooTicketProp(x, y, w, t) { zooTicket(x, y + 20, w * 1.6, t); }
// a giraffe's head and neck, looking over something; s 1 is about 300 px tall
function giraffe(x, y, s, t) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.rotate(Math.sin(t * 1.4) * .04);
  shape(() => { ctx.moveTo(-26, 0); ctx.lineTo(-18, -230); ctx.lineTo(24, -236); ctx.lineTo(30, 0); ctx.closePath(); }, '#e8a840', { shade: 8, hi: 5, line: 5,
    after: () => { ctx.fillStyle = '#9a5a26'; [[-6, -40, 14], [10, -100, 12], [-4, -160, 13], [12, -200, 9], [6, -20, 10]].forEach(([sx, sy, r]) => { ctx.beginPath(); ctx.ellipse(sx, sy, r, r * .8, .3, 0, Math.PI * 2); ctx.fill(); }); } });
  shape(() => ctx.ellipse(16, -262, 62, 34, -.25, 0, Math.PI * 2), '#e8a840', { shade: 7, hi: 5, line: 5 });
  [[-8, -300], [14, -306]].forEach(([ox, oy]) => { shape(() => rrp(ox - 5, oy - 26, 10, 30, 4), '#9a5a26', { shade: 2, hi: 2, line: 3.5 }); circle(ox, oy - 28, 9, '#6a3a1a'); });
  circle(30, -272, 7, INK); circle(32, -274, 2.5, '#ffffff');
  circle(66, -250, 4, '#6a3a1a');
  ctx.restore();
}
// the zoo trip in a thought: a gate, a giraffe looking over it, three kids and "2 SAAT"
function zooThought(x, y, k, t) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(back(clamp(k)) * .9, back(clamp(k)) * .9);
  giraffe(250, 110, .8, t);  // beside the gate, its head above the arch
  [-150, 150].forEach(px => shape(() => rrp(px - 22, -60, 44, 160, 4), '#8a6a44', { shade: 5, hi: 3, line: 4.5 }));
  shape(() => { ctx.moveTo(-172, -50); ctx.quadraticCurveTo(0, -150, 172, -50); ctx.lineTo(172, -20); ctx.quadraticCurveTo(0, -120, -172, -20); ctx.closePath(); }, '#3f7a3a', { shade: 5, hi: 4, line: 4.5 });
  text('HAYVANAT BAHÇESİ', 0, -98, 24, '#fbf6e8', SANS, 900, 'center', 5, '#1a1117', 1);
  [[-70, 70, '#3a2416', '#c43a3a'], [0, 80, '#c89a4a', '#3a6aa0'], [70, 70, '#1e1612', '#e8b54a']].forEach(([kx, ky, hc, shirt]) => {
    shape(() => ctx.arc(kx, ky, 26, 0, Math.PI * 2), '#efc29e', { shade: 4, hi: 3, line: 4 });
    shape(() => { ctx.arc(kx, ky - 4, 27, Math.PI * 1.05, Math.PI * 1.95); ctx.closePath(); }, hc, { shade: 2, hi: 2, line: 3.5 });
    shape(() => rrp(kx - 26, ky + 24, 52, 30, 10), shirt, { shade: 3, hi: 2, line: 4 });
  });
  ctx.restore();
}
// the small request: a zoo ticket with a giraffe and "2 SAAT"
function zooTicket(x, y, w, t) {
  if (w <= 1) return;
  const h = w * .56;
  ctx.save(); ctx.translate(x, y - h / 2); ctx.rotate(-.05);
  shape(() => {
    ctx.moveTo(-w / 2, -h / 2); ctx.lineTo(w / 2, -h / 2); ctx.lineTo(w / 2, -h * .12); ctx.arc(w / 2, 0, h * .12, -Math.PI / 2, Math.PI / 2, true);
    ctx.lineTo(w / 2, h / 2); ctx.lineTo(-w / 2, h / 2); ctx.lineTo(-w / 2, h * .12); ctx.arc(-w / 2, 0, h * .12, Math.PI / 2, -Math.PI / 2, true); ctx.closePath();
  }, '#f6e2a0', { shade: w * .05, hi: w * .04, line: 4,
    after: () => {
      ctx.strokeStyle = 'rgba(120,80,30,.6)'; ctx.lineWidth = 2.5; ctx.setLineDash([6, 6]);
      ctx.beginPath(); ctx.moveTo(-w * .18, -h / 2); ctx.lineTo(-w * .18, h / 2); ctx.stroke(); ctx.setLineDash([]);
      text('2 SAAT', w * .15, h * .04, w * .13, '#7a2a1a', SANS, 900);
    } });
  giraffe(-w * .34, h * .42, w / 620, t);
  ctx.restore();
}
// one student in the results: head and shoulders; on 0..1 turns it gold with a tick
function miniStudent(x, y, s, on, t, tone = 0) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  const k = clamp(on), off = mixColor('#6c7488', '#525868', tone), col = mixColor(off, C.gold, k);
  if (k > 0) glow(0, -20, 80, 'rgba(255,214,120,1)', .45 * k);
  shape(() => { ctx.moveTo(-40, 40); ctx.quadraticCurveTo(-40, 0, 0, -2); ctx.quadraticCurveTo(40, 0, 40, 40); ctx.closePath(); }, col, { shade: 5, hi: 3, line: 4 });
  shape(() => ctx.arc(0, -30, 24, 0, Math.PI * 2), col, { shade: 5, hi: 3, line: 4 });
  if (k > 0) {
    ctx.save(); ctx.translate(30, -58); ctx.scale(back(k), back(k));
    circle(0, 0, 17, '#2f8a4a'); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(-8, 0); ctx.lineTo(-2, 7); ctx.lineTo(9, -7); ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
}
// one group of 24 as a 6x4 grid centred on cx: label, the icons (n of them light up as k runs 0..1), the percentage
function k3Group(cx, sc, label, accent, n, k, pct, t, tone) {
  plate(label, cx, 610, 28 + 16 * (sc - .62) / .28, 1, accent, sc > .7 ? 900 : 470);
  const step = 112 * sc;
  for (let i = 0; i < 24; i++) {
    const slot = K3.order.indexOf(i), on = slot < n ? clamp(k * n - slot) : 0;
    miniStudent(cx + ((i % 6) - 2.5) * step, 760 + Math.floor(i / 6) * step * 1.18, sc * 1.05, on, t, tone);
  }
  const bottom = 760 + 3 * step * 1.18 + 60 * sc, size = 60 + 80 * sc;
  text('%' + Math.round(pct * k), cx, bottom + size * .62, size, accent === C.red ? C.red2 : C.gold, SANS, 900, 'center', 8);
  text('EVET DEDİ', cx, bottom + size * 1.3, 32, C.ivory, SANS, 900, 'center', 5, C.ink, 5);
}
// a red rubber stamp, k 0..1 drops it from large onto the frame
function stamp(label, x, y, size, k) {
  if (k <= 0) return;
  const s = lerp(2.2, 1, eo(clamp(k)));
  ctx.save(); ctx.translate(x, y); ctx.rotate(-.12); ctx.scale(s, s); ctx.globalAlpha = clamp(k * 3);
  const w = measure(label, size, SANS, 900, size * .08) + size * .9, h = size * 1.5;
  ctx.fillStyle = 'rgba(251,246,232,.88)'; rr(-w / 2, -h / 2, w, h, size * .16); ctx.fill();
  ctx.strokeStyle = C.red; ctx.lineWidth = size * .1; rr(-w / 2, -h / 2, w, h, size * .16); ctx.stroke();
  ctx.lineWidth = size * .03; rr(-w / 2 + size * .14, -h / 2 + size * .14, w - size * .28, h - size * .28, size * .1); ctx.stroke();
  text(label, 0, size * .05, size, C.red, SANS, 900, 'center', 0, C.ink, size * .08);
  ctx.restore();
}
// a gold arrow on the ground under a step, dir -1 points left
function stepArrow(x, y, dir, k) {
  if (k <= 0) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(dir * eo(clamp(k)), 1); ctx.globalAlpha = clamp(k * 2);
  ctx.shadowColor = 'rgba(232,181,74,.8)'; ctx.shadowBlur = 20;
  ctx.fillStyle = C.gold; ctx.strokeStyle = INK; ctx.lineWidth = 5;
  ctx.beginPath(); ctx.moveTo(-70, -12); ctx.lineTo(30, -12); ctx.lineTo(30, -34); ctx.lineTo(80, 0); ctx.lineTo(30, 34); ctx.lineTo(30, 12); ctx.lineTo(-70, 12); ctx.closePath();
  ctx.fill(); ctx.shadowColor = 'transparent'; ctx.stroke();
  ctx.restore();
}
// the researcher and the student face to face, close: both from the waist up, the campus soft behind them
function k3Close(t, L, o) {
  drawSoft(.35, () => cam(540, 1060, 1.7, () => campus(t, { empty: true })), .12);
  ctx.fillStyle = 'rgba(20,24,40,.18)'; ctx.fillRect(0, 0, W, H);
  figure('arastirmaci', 230, 2040, 1.0, t, { expr: o.rExpr || 'warm', talk: talk(t, L), front: o.rFront || { sh: .5, el: 1.3, hand: 'hold', prop: () => clipboard(0, 60, 90, t, .2) },
    back: { sh: .1, el: .3 }, legs: 'hide', seed: 6 });
  figure(K3.ali, 870, 2040, 1.0, t, { dir: -1, expr: o.sExpr || 'neutral', headTilt: o.sTilt || 0, front: { sh: .45, el: 1.6, hand: 'hold', prop: () => books(0, 40, 120, t) },
    back: { sh: .1, el: .3 }, legs: 'hide', seed: 9 });
  // the cut-off bodies fade out into the dark under the subtitles
  const g = ctx.createLinearGradient(0, 1560, 0, 1720); g.addColorStop(0, 'rgba(8,8,14,0)'); g.addColorStop(1, 'rgba(8,8,14,.96)');
  ctx.fillStyle = g; ctx.fillRect(0, 1560, W, 360);
}
Object.assign(SAHNE, {
  kampus(V, L, t, lt) {
    // a student walks along the campus walk; the researcher steps in with a raised palm and he stops
    K3.day();
    const tk = wordTime(L, V.key, .8), walk = prog(t, L.t0, tk - .15), stop = prog(t, tk - .35, tk + .1);
    cam(540, 1250, lerp(1.08, 1.2, prog(lt, 0, 5)), () => {
      campus(t);
      const sx = lerp(1000, 740, eio(walk));
      figure(K3.ali, sx, 1500, .52, t, { dir: -1, legs: walk < 1 ? 'walk' : 'stand', phase: walk * 16, expr: stop > .5 ? 'surprised' : 'calm',
        front: { sh: .45, el: 1.6, hand: 'hold', prop: () => books(0, 40, 120, t) }, back: { sh: .1, el: .3 }, seed: 9 });
      figure('arastirmaci', 360, 1480, .5, t, { expr: stop > .5 ? 'warm' : 'neutral', talk: t > tk ? talk(t, L) : 0,
        front: stop > 0 ? { sh: lerp(.5, .95, eo(stop)), el: lerp(1.2, 1.7, eo(stop)), hand: 'palm' } : { sh: .5, el: 1.2, hand: 'hold', prop: () => clipboard(0, 60, 80, t, .2) },
        back: { sh: .15, el: .4 }, seed: 6 });
    });
    plate(V.year, 540, 520, 50, back(prog(t, L.t0 + .1, L.t0 + .45)));
    flash(t, tk - .1, .4);
  },
  dev(V, L, t, lt) {
    // close on the two: the big request, a calendar counting the weeks of two years up to 104
    K3.day();
    const td = wordTime(L, V.key, .2), ty = wordTime(L, V.years, .35), tw = wordTime(L, V.week, .5);
    const count = eio(prog(t, tw, tw + 1.6)), num = Math.max(1, Math.round(count * 104));
    const shock = prog(t, tw + .2, tw + .6);
    k3Close(t, L, { sExpr: shock > .5 ? 'surprised' : 'neutral', sTilt: -.05 * shock,
      rFront: { sh: 1.1, el: .9, hand: 'point' } });
    const pop = back(prog(t, ty - .1, ty + .3));
    if (pop > 0) {
      ctx.save(); ctx.translate(540, 790); ctx.scale(pop, pop);
      glow(0, 0, 300, 'rgba(255,90,80,1)', .25 * pop);
      calendar(0, 0, 300, num, count > 0 && count < 1 ? (t * 9) % 1 : 0, t);
      ctx.restore();
    }
    plate(V.label, 540, 520, 56, back(prog(t, td - .05, td + .3)), C.red);
  },
  red(V, L, t, lt) {
    // five students in a row each shake their head; a red X over each, then the stamp
    K3.day();
    const tk = wordTime(L, V.stampAt, .55), tx = L.t0 + (L.t1 - L.t0) * .45;
    cam(540, 1200, 1.1, () => campus(t, { empty: true }));
    ctx.fillStyle = 'rgba(20,24,40,.35)'; ctx.fillRect(0, 0, W, H);
    K3.crowd.forEach((who, i) => {
      const x = 140 + i * 200, at = lerp(L.t0 - .1, tx, i / 4), k = prog(t, at, at + .25);
      const shake = k > 0 && k < 1 ? Math.sin(k * Math.PI * 4) * .12 : 0;
      figure(who, x, 1480, .44, t, { dir: i % 2 ? -1 : 1, expr: k > 0 ? 'displeased' : 'neutral', headTilt: shake,
        front: { sh: .1, el: .3 }, back: { sh: .1, el: .3 }, seed: 30 + i });
      cross(x, 880, 120, prog(t, at + .02, at + .25));
    });
    stamp('HAYIR', 540, 700, 110, prog(t, tk - .05, tk + .2));
    plate('Kabul eden: 0', 540, 520, 54, back(prog(t, tk - .1, tk + .2)), C.red);
  },
  zoo(V, L, t, lt) {
    // the small request right after: a zoo ticket held out, the trip in the student's thought
    K3.day();
    const ts = wordTime(L, V.small, .4), tr = wordTime(L, V.trip, .55), offer = eo(prog(t, ts - .2, ts + .3)), think = prog(t, tr - .2, tr + .5);
    k3Close(t, L, { sExpr: think > .5 ? 'warm' : 'calm', sTilt: .04 * think,
      rFront: offer > 0 ? { sh: lerp(.5, 1.3, offer), el: lerp(1.3, .5, offer), hand: 'hold', prop: () => zooTicket(0, 30, 150 * offer, t) } : undefined });
    thought(540, 800, 720, 420, 800, 1000, think);
    ctx.save(); ctx.translate(540, 800); ctx.scale(1.15, 1.15); zooThought(0, 0, think, t); ctx.restore();
    plate(V.label, 540, 520, 56, back(prog(t, ts - .05, ts + .3)), C.gold);
  },
  sonuc(V, L, t, lt) {
    // the 24 who said no to the big request first: 12 of them light up
    K3.day();
    cam(540, 1200, 1.1, () => campus(t, { empty: true }));
    ctx.fillStyle = 'rgba(10,12,24,.6)'; ctx.fillRect(0, 0, W, H);
    const k = eio(prog(t, L.t0 + .2, wordTime(L, V.key, .85)));
    ctx.save(); ctx.globalAlpha = clamp(prog(lt, 0, .3)); k3Group(540, .9, V.label, C.gold, V.lit, k, V.pct, t, 0); ctx.restore();
  },
  kontrol(V, L, t, lt) {
    // group A slides left; group B, who only heard the zoo trip, lights up 4 of 24
    K3.day();
    cam(540, 1200, 1.1, () => campus(t, { empty: true }));
    ctx.fillStyle = 'rgba(10,12,24,.6)'; ctx.fillRect(0, 0, W, H);
    const A = EP.lines[5].v, slide = eio(prog(lt, 0, .55)), k = eio(prog(t, L.t0 + .45, wordTime(L, V.key, .85)));
    k3Group(lerp(540, 280, slide), lerp(.9, .62, slide), A.label, C.gold, A.lit, 1, A.pct, t, 0);
    ctx.save(); ctx.globalAlpha = clamp(prog(lt, .3, .6));
    k3Group(800, .62, V.label, C.red, V.lit, k, V.pct, t, .45);
    ctx.strokeStyle = 'rgba(243,234,216,.25)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(540, 650); ctx.lineTo(540, 1300); ctx.stroke();
    ctx.restore();
  },
  neden(V, L, t, lt) {
    // why: the researcher takes a step back (a concession) and the student answers with a step forward
    K3.day();
    const tb = wordTime(L, V.back, .4), tf = wordTime(L, V.fwd, .7);
    const rb = prog(t, tb - .1, tb + .6), sf = prog(t, tf - .1, tf + .6);
    cam(540, 1250, 1.15, () => {
      campus(t);
      const rx = lerp(370, 230, eio(rb)), sx = lerp(740, 610, eio(sf));
      figure('arastirmaci', rx, 1500, .52, t, { expr: 'warm', legs: rb > 0 && rb < 1 ? 'walk' : 'stand', phase: -rb * 10, talk: talk(t, L) * .3,
        front: rb > .2 ? { sh: .35, el: 1.3, hand: 'palm' } : { sh: .12, el: .3 }, back: { sh: .1, el: .3 }, seed: 6 });
      figure(K3.ali, sx, 1500, .52, t, { dir: -1, expr: sf > .5 ? 'happy' : 'calm', legs: sf > 0 && sf < 1 ? 'walk' : 'stand', phase: sf * 10,
        front: sf > .3 ? { sh: .8, el: .7, hand: 'palm' } : { sh: .12, el: .3 }, back: { sh: .1, el: .3 }, seed: 9 });
      stepArrow(270, 1540, -1, prog(t, tb, tb + .4));
      stepArrow(680, 1540, -1, prog(t, tf, tf + .4));
    });
    plate('Taviz', 230, 1330, 44, back(prog(t, tb + .1, tb + .45)), C.gold);
    plate('Karşılık', 640, 1330, 44, back(prog(t, tf + .1, tf + .45)), C.gold);
    plate('Karşılıklı taviz', 540, 520, 56, back(prog(t, tf + .7, tf + 1.05)), C.gold);
    if (sf > .6) sparks(560, 980, t, 18, 160, prog(t, tf + .4, tf + 1.2));
  }
});
