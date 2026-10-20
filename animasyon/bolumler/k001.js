// k001 Ben Franklin etkisi: the 1737 assembly, the borrowed book, the rival's inner conflict, the handshake.
// Loaded after motor.html's helpers exist; VIS picks these up by name from SAHNE.
Object.assign(SAHNE, {
  meclis(V, L, t, lt) {
    const tB = wordTime(L, 'zengin', .22), tC = wordTime(L, V.who || 'Benjamin', .45), tD = wordTime(L, V.key || 'karşı', .7);
    const shout = { expr: 'shout', talk: talk(t, L) };
    if (t < tB) {  // the real building, a 1778 engraving
      paintingFull(IMG.meclis, t, L.t0 - .1, tB + .2, [.72, .42]);
      plate('Pensilvanya Meclisi', 540, 1240, 50, back(prog(t, L.t0 + .1, L.t0 + .45)));
      return;
    }
    if (t >= tC && t < tD) {  // the real Franklin
      setLight(-.8, -.6, { rim: '#9fc2ff', rimA: .6 });
      drawSoft(.1, () => assemblyRoom(t, .1), .55);
      const k = prog(t, tC - .05, tC + .35), z = lerp(1.0, 1.08, prog(t, tC, tD));
      gildedFrame(IMG.franklin, 540, 860, 520 * z, 650 * z, [.48, .3], 1.25, back(k));
      plate('Benjamin Franklin', 540, 1300, 52, back(prog(t, tC + .1, tC + .45)), C.gold);
      flash(t, tC);
      return;
    }
    // the cartoon assembly at night: the rival on his feet, pointing; Franklin seated across
    setLight(-.7, -.7, { rim: '#9fc2ff', rimA: .75, warm: '#ffd9a0', cool: '#1c1d3a' });
    const two = t >= tD, pan = two ? eio(prog(t, tD, tD + .9)) : 0;
    const cx = two ? lerp(700, 560, pan) : lerp(760, 720, prog(t, tB, tC)), z = two ? lerp(1.35, 1.12, pan) : lerp(1.45, 1.38, prog(t, tB, tC));
    cam(cx, 980, z, () => {
      assemblyRoom(t, .1);
      chandelier(560, 330, t, .9, .9);
      backMembers(t);
      // Franklin across the room, seated behind his table
      figure('franklin', 290, 1500, .66, t, { expr: two ? (prog(t, tD, tD + .6) > .5 ? 'displeased' : 'surprised') : 'neutral', legs: 'hide',
        front: { sh: .5, el: 1.3, hand: 'open' }, back: { sh: .4, el: 1.2 }, nod: two ? -.04 : 0, seed: 2 });
      baizeTable(300, 1170, 330, t, { candle: -.6, h: 330 });
      // the rival behind his own table, on his feet
      const jab = Math.max(0, Math.sin(t * 7)) * .08;
      figure('rakip', 800, 1530, .74, t, { dir: -1, ...shout, lean: .05 + jab * .3, nod: -.04 + jab * .4,
        front: { sh: 1.45 + jab, el: .05, hand: 'point' }, back: { sh: -.15, el: .7, hand: 'fist' }, seed: 1 });
      baizeTable(800, 1250, 360, t, { candle: .7, h: 300 });
      
    });
    plate(V.year, 540, 520, 42, back(prog(t, tB + .05, tB + .4)) * (1 - prog(t, tC - .2, tC)));
    if (two) plate('Franklin\'e karşı', 540, 520, 52, back(prog(t, tD + .1, tD + .45)), C.red);
    flash(t, tB); flash(t, tD);
  },
  yaranma(V, L, t, lt) {
    // what most people would do, shown as an old faded memory: bowing, a gift held out, and a cold shoulder
    setLight(-.7, -.7, { rim: '#ffd9a0', rimA: .4 });
    const k = eo(prog(lt, 0, .8)), kt = wordTime(L, V.key, .5);
    ctx.save();
    cam(560, 980, lerp(1.25, 1.32, prog(lt, 0, 3)), () => {
      assemblyRoom(t, .2);
      chandelier(560, 330, t, .9, .7);
      backMembers(t);
      figure('uye', 270, 1520, .72, t, { expr: 'warm', lean: .1 + .3 * k, nod: .12 * k, talk: talk(t, L) * .5,
        front: { sh: .55 + .15 * k, el: .9, hand: 'hold', prop: () => giftBox(0, 60, 130, t) }, back: { sh: .6 * k, el: 1.0 }, seed: 3 });
      figure('rakip', 810, 1520, .74, t, { dir: -1, expr: 'haughty', headTilt: -.12 - .06 * prog(t, kt, kt + .4), nod: 0,
        front: { sh: 1.2 * prog(t, kt - .2, kt + .3), el: .3, hand: 'palm' }, back: { sh: -.1, el: .4 }, seed: 1 });
    });
    sepiaTone(.75, .1);
    ctx.restore();
    // a faded film frame around the memory
    ctx.save(); ctx.strokeStyle = 'rgba(232,210,170,.5)'; ctx.lineWidth = 6; rr(60, 430, 960, 1000, 26); ctx.stroke(); ctx.restore();
    plate(V.label, 540, 520, 52, back(prog(lt, .1, .45)));
    cross(540, 1000, 520, prog(t, L.t1 - .25, L.t1 + .2));
  },
  mektup(V, L, t, lt) {
    // Franklin's study at night: he writes to the rival, the book glows in his thoughts, then the note itself
    setLight(.8, -.5, { rim: '#9fc2ff', rimA: .6, warm: '#ffd09a', cool: '#1a1630' });
    const tk = wordTime(L, V.key, .2), tb = wordTime(L, V.book, .45), ta = wordTime(L, V.ask, .75);
    const note = prog(t, ta - .15, ta + .35);
    cam(540, 960, lerp(1.18, 1.26, prog(lt, 0, 5)), () => {
      ctx.fillStyle = vlin(0, 0, 0, 1300, '#2a1e1a', '#3a2a22'); ctx.fillRect(0, 0, W, 1300);
      bookshelf(-40, 280, 380, 980, t, 4);
      window2(700, 380, 300, 420, t);
      fillRect(0, 1240, W, 700, '#1a110c');
      figure('franklin', 470, 1640, .82, t, { expr: t > tb ? 'sly' : 'neutral', legs: 'hide', nod: .1 - .1 * prog(t, tb - .3, tb), headTilt: Math.sin(t * 3) * .01,
        front: { sh: .95 + Math.sin(t * 9) * .04 * (1 - note), el: 1.35, hand: 'hold', prop: () => quill(0, 60, -.4, .5) }, back: { sh: .6, el: 1.4 }, seed: 2 });
      desk(540, 1240, 900, t);
      // the sheet lying on the desk, in perspective, lines of ink appearing as he writes
      ctx.save(); ctx.translate(560, 1222);
      shape(() => { ctx.moveTo(-120, 18); ctx.lineTo(120, 18); ctx.lineTo(96, -22); ctx.lineTo(-92, -22); ctx.closePath(); }, '#efe3c6', { shade: 6, hi: 4, noRim: true, line: 4 });
      const wk = prog(t, tk, ta);
      for (let i = 0; i < 4; i++) { const f = clamp(wk * 4 - i); if (f > 0) stroke('#3a2a1c', 3, () => { ctx.moveTo(-80 + i * 3, -12 + i * 8); ctx.lineTo(-80 + i * 3 + 150 * f, -12 + i * 8); }, .8); }
      ctx.restore();
      candleProp(860, 1225, t, 1.1);
    });
    plate('Tam tersi', 540, 520, 54, back(prog(t, tk, tk + .35)) * (1 - prog(t, tb - .2, tb)));
    const tbk = back(prog(t, tb - .1, tb + .35)) * (1 - note);
    if (tbk > 0) {
      thought(760, 660, 380, 300, 620, 860, tbk);
      rareBook(760, 650, 120 * clamp(tbk), t, { rot: -.08 });
      plate('Nadir kitap', 760, 870, 40, tbk, C.gold);
    }
    if (note > 0) {  // the note slides up, filling itself in
      ctx.save(); ctx.fillStyle = `rgba(0,0,0,${.5 * note})`; ctx.fillRect(0, 0, W, H); ctx.restore();
      letterSheet(540, lerp(1700, 960, eo(note)), 560, -.04, prog(t, ta, L.t1 + .1), ['Sayın Bay,', '', 'Nadir kitabınızı', 'birkaç günlüğüne', 'ödünç alabilir miyim?', '', '— B. Franklin'], t);
    }
  },
  gonder(V, L, t, lt) {
    // the rival's library: the note in his hand, a surprised look, and the book sent at once
    setLight(.8, -.4, { rim: '#ff9a50', rimA: .7, warm: '#ffc080', cool: '#1c1424' });
    const kt = wordTime(L, V.key, .4), send = eo(prog(t, kt - .1, kt + .3));
    cam(560, 960, lerp(1.25, 1.2, prog(lt, 0, 2)), () => {
      ctx.fillStyle = '#20140f'; ctx.fillRect(0, 0, W, H);
      bookshelf(-20, 200, 520, 1080, t, 11); bookshelf(560, 200, 540, 1080, t, 23);
      ctx.save(); ctx.globalCompositeOperation = 'lighter'; glow(1000, 1200, 700, 'rgba(255,120,40,1)', .22 + .04 * Math.sin(t * 9)); ctx.restore();
      fillRect(0, 1280, W, 700, '#1a100c');
      figure('rakip', 560, 1660, .9, t, { dir: -1, expr: send > .5 ? 'calm' : 'surprised', legs: 'hide', nod: -.05 * (1 - send),
        front: { sh: lerp(.9, 1.5, send), el: lerp(1.2, .1, send), hand: 'hold',
          prop: () => send < .6 ? letterSheet(0, 80, 110, .2, 1, ['...'], t) : null },
        back: { sh: .1, el: .3 }, seed: 1 });
    });
    if (send > .4) {  // the book flies off toward Franklin
      const f = prog(t, kt + .1, kt + .7);
      ctx.save(); ctx.globalAlpha = .35; for (let i = 1; i < 5; i++) rareBook(lerp(380, -200, eo(clamp(f - i * .05))), lerp(820, 640, clamp(f - i * .05)), 150, t, { rot: -.3, shine: 0 }); ctx.restore();
      rareBook(lerp(380, -200, eo(f)), lerp(820, 640, f), 150, t, { rot: -.3 + f * .4 });
    }
    plate('Hemen', 540, 520, 64, back(prog(t, kt, kt + .35)), C.gold);
  },
  celiski(V, L, t, lt) {
    // close on the rival: two thoughts that cannot both be true collide over his head
    setLight(.7, -.6, { rim: '#ff5a4f', rimA: .7, warm: '#ffd9a0', cool: '#18142e' });
    const tk = wordTime(L, V.key, .3), tn = wordTime(L, V.no, .55), ty = wordTime(L, V.yes, .8);
    const shake = prog(t, ty, ty + .2) > 0 ? Math.sin(t * 60) * 6 * (1 - prog(t, ty + .1, ty + .6)) : 0;
    ctx.save(); ctx.translate(shake, 0);
    ctx.fillStyle = '#100c16'; ctx.fillRect(0, 0, W, H);
    glow(160, 760, 760, 'rgba(210,35,47,1)', .35); glow(940, 900, 760, 'rgba(70,110,255,1)', .3);
    cam(560, 1000, lerp(1.0, 1.08, prog(lt, 0, 5)), () => {
      figure('rakip', 610, 2600, 1.6, t, { dir: -1, expr: 'confused', legs: 'hide', headTilt: -.06 + Math.sin(t * 1.4) * .03,
        front: { sh: .5, el: 2.45, hand: 'fist' }, back: { sh: .1, el: .3 }, seed: 1 });
    });
    plate('Çelişki', 540, 520, 56, back(prog(t, tk, tk + .35)) * (1 - prog(t, tn - .1, tn + .1)));
    const kn = back(prog(t, tn - .05, tn + .3)), ky = back(prog(t, ty - .05, ty + .3));
    if (kn > 0) { ctx.save(); ctx.translate(375, 480); ctx.rotate(-.06); plate('Onu sevmiyorum', 0, 0, 50, kn, C.red); ctx.restore(); }
    if (ky > 0) { ctx.save(); ctx.translate(650, 625); ctx.rotate(.05); plate('Ama yardım ettim', 0, 0, 50, ky, C.gold); ctx.restore(); }
    if (ky > .5) {  // a crackle between the two
      ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.strokeStyle = '#ffe2a0'; ctx.lineWidth = 5; ctx.shadowColor = '#ffb04a'; ctx.shadowBlur = 20;
      ctx.beginPath(); for (let i = 0; i <= 8; i++) { const px = lerp(470, 580, i / 8), py = lerp(530, 585, i / 8) + (i % 2 ? -1 : 1) * (10 + 14 * rnd(i + Math.floor(t * 20))); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
      ctx.stroke(); ctx.restore();
      text('?', 880 + Math.sin(t * 3) * 10, 1070, 140, C.ivory, SERIF, 900, 'center', 10);
    }
    ctx.restore();
  },
  cozum(V, L, t, lt) {
    // the red thought cracks and falls, the gold one becomes "I must like him", and his face softens
    setLight(.7, -.6, { rim: '#ffcf7a', rimA: .7, warm: '#ffd9a0', cool: '#20182a' });
    const tk = wordTime(L, V.key, .8), warm = eo(prog(lt, .2, 1.4)), love = prog(t, tk - .1, tk + .4);
    ctx.fillStyle = mixColor('#100c16', '#2a1a10', warm); ctx.fillRect(0, 0, W, H);
    glow(160, 760, 760, 'rgba(210,35,47,1)', .35 * (1 - warm)); glow(540, 820, 900, 'rgba(232,181,74,1)', .32 * warm);
    cam(560, 1000, lerp(1.08, 1.14, prog(lt, 0, 4)), () => {
      figure('rakip', 610, 2600, 1.6, t, { dir: -1, expr: love > .5 ? 'happy' : exprMix('confused', 'warm', warm), legs: 'hide', headTilt: lerp(-.06, .04, warm),
        front: { sh: lerp(.5, .06, warm), el: lerp(2.45, .3, warm), hand: warm > .5 ? 'open' : 'fist' }, back: { sh: .1, el: .3 }, seed: 1 });
    });
    // the red plate cracks and drops away
    const fall = prog(lt, .15, 1.0);
    if (fall < 1) {
      ctx.save(); ctx.translate(375 - fall * 80, 480 + fall * fall * 900); ctx.rotate(-.06 - fall * .9); ctx.globalAlpha = 1 - fall;
      plate('Onu sevmiyorum', 0, 0, 50, 1, C.red);
      ctx.strokeStyle = '#f3ead8'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(-40, -34); ctx.lineTo(-10, -4); ctx.lineTo(-30, 10); ctx.lineTo(6, 36); ctx.stroke();
      ctx.restore();
    }
    const gy = lerp(625, 540, eo(prog(lt, .3, 1.0))), gx = lerp(650, 540, eo(prog(lt, .3, 1.0)));
    ctx.save(); ctx.translate(gx, gy); ctx.rotate(lerp(.06, 0, warm));
    plate(love > .3 ? 'Demek ki onu seviyorum' : 'Ama yardım ettim', 0, 0, love > .3 ? 52 : 50, love > .3 ? back(prog(t, tk - .1, tk + .3)) : 1, C.gold);
    ctx.restore();
    sparks(540, 560, t, 26, 260, prog(t, tk, tk + 1));
    plate(V.label, 540, 1330, 50, back(prog(t, tk + .1, tk + .5)), C.gold);
  },
  dost(V, L, t, lt) {
    // the next sitting: the rival crosses the room, speaks to Franklin first, and they shake hands
    setLight(-.6, -.7, { rim: '#ffcf7a', rimA: .7, warm: '#ffd9a0', cool: '#2a1e2a' });
    const tw = wordTime(L, V.walk, .1), tt = wordTime(L, V.talk, .5), tk = wordTime(L, V.key, .75);
    const walk = eio(prog(t, L.t0, tt - .2)), shake = eo(prog(t, tk - .35, tk + .1));
    const z = lerp(1.18, 1.42, eio(prog(t, tk - .3, tk + .6)));
    cam(lerp(560, 565, shake), lerp(980, 1040, shake), z, () => {
      assemblyRoom(t, 1);
      chandelier(560, 330, t, .9, 1.2);
      backMembers(t, 1);
      baizeTable(140, 1180, 300, t, { candle: .5, h: 320, papers: true });
      const fx = 345, rx = lerp(1080, 785, walk), jig = shake > .9 ? Math.sin(t * 18) * .08 * (1 - prog(t, tk + .2, tk + .9)) : 0;
      figure('franklin', fx, 1450, .74, t, { expr: shake > .5 ? 'happy' : (t > tt ? 'surprised' : 'neutral'), nod: .08 * shake,
        front: { sh: lerp(.06, .15, shake), el: lerp(.3, 1.35, shake) + jig, hand: shake > .3 ? 'hold' : 'open' }, back: { sh: .05, el: .3 }, seed: 2 });
      figure('rakip', rx, 1450, .74, t, { dir: -1, expr: shake > .5 ? 'happy' : 'warm', talk: t > tt - .3 && t < tk ? talk(t, L) : 0,
        legs: walk < 1 ? 'walk' : 'stand', phase: walk * 14, bob: walk < 1 ? -Math.abs(Math.sin(walk * 14)) * 10 : 0, nod: .1 * shake,
        front: { sh: lerp(.06, .15, shake), el: lerp(.3, 1.35, shake) + jig, hand: shake > .3 ? 'hold' : 'open' }, back: { sh: .05, el: .3 }, seed: 1 });
      sparks(565, 1040, t, 30, 220, prog(t, tk, tk + 1));
    });
    plate(V.label, 540, 520, 56, back(prog(t, tk + .05, tk + .45)), C.gold);
    plate('İlk kez', 540, 520, 52, back(prog(t, tt - .3, tt)) * (1 - prog(t, tk - .1, tk + .05)));
  }
});
