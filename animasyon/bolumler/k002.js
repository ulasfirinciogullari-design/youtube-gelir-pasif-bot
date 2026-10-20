// k002 Kapıya ayak koyma: Freedman & Fraser 1966, Palo Alto. A small window card first, two weeks later a huge
// lawn sign; the results as two streets of 25 houses; the self-image that explains it. Golden-hour 1960s street.
const K2 = {
  dusk() { setLight(-.75, -.45, { rim: '#ffd9a0', rimA: .55, warm: '#ffd9a0', cool: '#3a3260' }); },
  order: [...Array(25).keys()].sort((a, b) => rnd(a * 13 + 5) - rnd(b * 13 + 5))  // the order the signs appear in
};
// one street of 25 houses as a 5x5 grid centred on cx from y 700 down; n of them get the big sign as k runs 0..1,
// in a fixed shuffled order
function k2Grid(cx, sc, n, k, t, tone) {
  const step = 139 * sc;
  for (let i = 0; i < 25; i++) {
    const slot = K2.order.indexOf(i), on = slot < n ? clamp(k * n - slot) : 0;
    miniHouse(cx + ((i % 5) - 2) * step, 720 + Math.floor(i / 5) * step, sc * 1.24, on, t, tone);
  }
}
// the results block for one street: label, grid, the percentage under it (all above the subtitles at y 1450)
function k2Street(cx, sc, label, accent, n, k, pct, t, tone) {
  plate(label, cx, 610, 28 + 16 * (sc - .62) / .23, 1, accent, sc > .7 ? 900 : 470);
  k2Grid(cx, sc, n, k, t, tone);
  const bottom = 720 + 4 * 139 * sc + 30 * sc, size = 60 + 80 * sc;
  text('%' + Math.round(pct * k), cx, bottom + size * .62, size, accent === C.red ? C.red2 : C.gold, SANS, 900, 'center', 8);
  text('EVET DEDİ', cx, bottom + size * 1.3, 32, C.ivory, SANS, 900, 'center', 5, C.ink, 5);
}
Object.assign(SAHNE, {
  kapi(V, L, t, lt) {
    // the researcher walks up a Palo Alto front path and knocks; the door opens on the housewife
    K2.dusk();
    const tk = wordTime(L, V.knock, .3), td = wordTime(L, V.door, .55), ts = wordTime(L, V.small, .8);
    const walk = prog(t, L.t0, tk - .1), knock = prog(t, tk - .1, tk + .5), open = eo(prog(t, tk + .4, td));
    const close = t >= td, push = eio(prog(t, td - .1, td + .5));
    const cx = close ? lerp(560, 600, push) : lerp(540, 600, prog(lt, 0, 3)), cy = close ? lerp(1300, 1250, push) : 1250;
    const z = close ? lerp(1.5, 2.05, push) : lerp(1.15, 1.35, prog(lt, 0, tk - L.t0 + .5));
    cam(cx, cy, z, () => {
      suburb(t, { open });
      const rx = lerp(150, 480, eio(walk)), ry = lerp(1640, 1450, eio(walk));
      const rap = knock > 0 && knock < 1 ? Math.abs(Math.sin(knock * Math.PI * 3)) : 0;
      if (open > .3) {  // she appears in the doorway as the door swings in
        ctx.save(); ctx.globalAlpha = prog(open, .3, .7);
        figure('evsahibi', 650, 1400, .38, t, { dir: -1, expr: 'warm', front: { sh: .35, el: 1.1 }, back: { sh: .1, el: .2 }, seed: 5, noShadow: true });
        ctx.restore();
      }
      figure('arastirmaci', rx, ry, .4, t, { expr: close ? 'warm' : 'neutral', legs: walk < 1 ? 'walk' : 'stand', phase: walk * 14,
        front: walk >= 1 && knock < 1 ? { sh: 1.35, el: 1.5 - rap * .4, hand: 'fist' } : { sh: .45, el: 1.2, hand: 'hold', prop: () => clipboard(0, 50, 70, t, .2) },
        back: { sh: .1, el: .3 }, talk: close ? talk(t, L) : 0, seed: 6,
        ...(t >= ts ? { front: { sh: .75, el: 1.35, hand: 'hold', prop: () => stickerProp(0, 66, 56, t) } } : {}) });
    });
    plate(V.year, 540, 520, 50, back(prog(t, L.t0 + .1, L.t0 + .45)) * (1 - prog(t, td - .2, td)));
    if (close) plate('Küçük bir rica', 540, 520, 50, back(prog(t, ts - .05, ts + .3)), C.gold);
    flash(t, td);
  },
  etiket(V, L, t, lt) {
    // close on the picture window: she presses the little card onto the glass
    K2.dusk();
    const tk = wordTime(L, V.key, .7), press = prog(t, tk - .35, tk), stuck = prog(t, tk - .05, tk + .25);
    duskSky(t, 700);
    ctx.fillStyle = DUSK.stucco; ctx.fillRect(0, 640, W, 1280);
    const g = ctx.createLinearGradient(0, 640, 0, 760); g.addColorStop(0, 'rgba(40,20,30,.4)'); g.addColorStop(1, 'rgba(40,20,30,0)'); ctx.fillStyle = g; ctx.fillRect(0, 640, W, 120);
    pictureWindow(90, 760, 620, 480, t, { lit: .85, sticker: stuck, stickerW: 170 });
    [[160, 1330, 120], [420, 1340, 110], [660, 1330, 120]].forEach(([x, y, r], i) =>
      shape(() => { ctx.moveTo(x - r, y); ctx.bezierCurveTo(x - r, y - r * 1.1, x + r, y - r * 1.1, x + r, y); ctx.closePath(); }, i % 2 ? '#3f5a2e' : '#4a6634', { shade: 14, hi: 8, line: 6 }));
    // she reaches from the right, card in hand, then steps back pleased
    const reach = eio(press) * (1 - eo(prog(t, tk + .3, tk + .9)) * .6);
    figure('evsahibi', 900, 2330, 1.25, t, { dir: -1, legs: 'hide', expr: stuck > .5 ? 'happy' : { ...EXPR.warm, look: [.5, .2] }, lean: -.05 * reach,
      front: { sh: lerp(.4, 1.5, reach), el: lerp(1.0, 1.6, reach), hand: stuck > .3 ? 'palm' : 'hold', prop: stuck > .3 ? null : () => stickerProp(0, 70, 120, t) },
      back: { sh: .1, el: .3 }, seed: 5 });
    if (stuck > .5) sparks(600, 1150, t, 18, 140, prog(t, tk, tk + .8));
    plate(V.label, 540, 520, 56, back(prog(t, tk + .05, tk + .4)), C.gold);
  },
  tabela(V, L, t, lt) {
    // two weeks later another researcher, and a sign that swallows the front of the house
    K2.dusk();
    const tk = wordTime(L, V.key, .7), rise = prog(t, tk - .15, tk + .45);
    const z = lerp(1.25, 1.12, eio(prog(t, tk - .3, tk + .6)));
    cam(560, lerp(1320, 1280, rise), z, () => {
      suburb(t, { open: .8, sticker: 1, sign: rise });
      figure('evsahibi', rise > 0 ? 880 : 650, rise > 0 ? 1560 : 1400, rise > 0 ? .44 : .38, t, { dir: -1,
        expr: rise > .3 ? 'surprised' : 'warm', front: rise > .3 ? { sh: .9, el: 1.9, hand: 'palm' } : { sh: .3, el: .9 }, back: { sh: .1, el: .3 }, seed: 5 });
      figure('arastirmaci2', rise > 0 ? 190 : 470, rise > 0 ? 1620 : 1450, rise > 0 ? .46 : .4, t, { expr: rise > .3 ? 'sly' : 'warm', talk: talk(t, L),
        front: rise > .3 ? { sh: 1.2, el: .15, hand: 'point' } : { sh: .5, el: 1.2, hand: 'hold', prop: () => clipboard(0, 50, 70, t, .2) }, back: { sh: .1, el: .3 }, seed: 7 });
    });
    plate(V.later, 540, 520, 54, back(prog(t, L.t0 + .05, L.t0 + .4)) * (1 - prog(t, tk - .3, tk - .1)));
    plate(V.label, 540, 520, 58, back(prog(t, tk + .2, tk + .55)), C.red);
    flash(t, L.t0 + .02, .7); flash(t, tk - .15, .5);
  },
  sonuc(V, L, t, lt) {
    // street A: the houses that heard the small request first; signs go up across 76 in every 100
    K2.dusk(); duskSky(t, 1900);
    ctx.fillStyle = 'rgba(10,8,20,.35)'; ctx.fillRect(0, 0, W, H);
    const k = eio(prog(t, L.t0 + .2, wordTime(L, V.key, .85)));
    ctx.save(); ctx.globalAlpha = clamp(prog(lt, 0, .3)); k2Street(540, .85, V.label, C.gold, 19, k, V.pct, t, 0); ctx.restore();
  },
  kontrol(V, L, t, lt) {
    // street A stays; street B, never asked before, gets only a few signs
    K2.dusk(); duskSky(t, 1900);
    ctx.fillStyle = 'rgba(10,8,20,.35)'; ctx.fillRect(0, 0, W, H);
    const A = EP.lines[4].v, slide = eio(prog(lt, 0, .55)), k = eio(prog(t, L.t0 + .45, wordTime(L, V.key, .85)));
    k2Street(lerp(540, 285, slide), lerp(.85, .62, slide), A.label, C.gold, 19, 1, A.pct, t, 0);
    ctx.save(); ctx.globalAlpha = clamp(prog(lt, .3, .6));
    k2Street(795, .62, V.label, C.red, 4, k, V.pct, t, .45);
    ctx.strokeStyle = 'rgba(243,234,216,.25)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(540, 640); ctx.lineTo(540, 1300); ctx.stroke();
    ctx.restore();
  },
  kendilik(V, L, t, lt) {
    // why: she looks at her little card and sees herself differently; a gold pin appears on her dress
    K2.dusk();
    const tk = wordTime(L, V.key, .45), tq = wordTime(L, V.quote, .7), turn = eio(prog(t, tk - .2, tk + .4)), pin = prog(t, tq - .1, tq + .4);
    duskSky(t, 700);
    ctx.fillStyle = DUSK.stucco; ctx.fillRect(0, 640, W, 1280);
    pictureWindow(40, 800, 440, 420, t, { lit: .85, sticker: 1, stickerW: 130 });
    glow(330, 1180, 260, 'rgba(255,214,150,1)', .25 + .2 * turn);
    cam(560, 1000, lerp(1.0, 1.06, prog(lt, 0, 4)), () => {
      figure('evsahibi', 720, 2200, 1.3, t, { dir: -1, legs: 'hide', expr: pin > .4 ? 'happy' : (turn > .5 ? 'calm' : { ...EXPR.neutral, look: [.7, .3] }),
        headTilt: lerp(.06, -.04, turn), front: pin > .2 ? { sh: .5, el: 2.3, hand: 'palm' } : { sh: .15, el: .3 }, back: { sh: .1, el: .3 }, seed: 5 });
    });
    if (pin > 0) {  // a gold star pin on her dress
      ctx.save(); ctx.translate(655, 1365); ctx.scale(back(clamp(pin)), back(clamp(pin)));
      glow(0, 0, 90, 'rgba(255,214,120,1)', .5);
      ctx.fillStyle = C.gold; ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.beginPath();
      for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, r = i % 2 ? 18 : 42; ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r); }
      ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
      sparks(655, 1365, t, 22, 200, prog(t, tq, tq + 1));
    }
    plate('Ben iyi işlere', 540, 560, 54, back(prog(t, tq - .05, tq + .3)), C.gold);
    plate('destek olan biriyim', 540, 680, 54, back(prog(t, tq + .25, tq + .6)), C.gold);
  }
});
