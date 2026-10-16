// YA OLSAYDI? backgrounds and celestial bodies, drawn with Canvas 2D on ortak.js's ctx.
// Load after ortak.js as a plain <script>. It defines two globals:
//   BG.<name>(t)                 paints the full 1080x1920 frame; t (seconds) only drives slow ambient motion
//   BODY.<name>(x, y, r, t, opt) a body centred at x,y with radius r; draws nothing when r < 0.5
// The still scenery of each background is painted once into an offscreen canvas (it never depends on t)
// and blitted every frame; only the ambient motion is drawn live, so a frame costs a few ms.
// Everything is deterministic (rnd, never Math.random or Date), keeps the caller's transform and
// globalAlpha (so cross-fades work) and restores every context setting it changes.
(() => {
  const TAU = Math.PI * 2;
  let BA = 1; // caller's globalAlpha when the current BG/BODY call started
  const al = a => { ctx.globalAlpha = BA * a; };
  const rgba = (rgb, a) => `rgba(${rgb},${a})`;
  const wrap = (v, m) => ((v % m) + m) % m;
  const nz = (x, s = 0) => { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return lerp(rnd(i + s * 71.3), rnd(i + 1 + s * 71.3), u); };
  const hexRgb = h => { const n = parseInt(String(h).replace('#', '').replace(/^(.)(.)(.)$/, '$1$1$2$2$3$3'), 16); return `${n >> 16 & 255},${n >> 8 & 255},${n & 255}`; };
  const mixRgb = (rgb, k, to = 255) => rgb.split(',').map(v => Math.round(lerp(+v, to, k))).join(',');
  // how far x,y sits inside the calm centre where the engine puts its big visual (1 = middle, 0 = outside)
  const calm = (x, y) => { const dx = (x - 540) / 470, dy = (y - 770) / 520; return clamp(1 - dx * dx - dy * dy); };

  // ---------- drawing helpers that take a context (static layers paint into an offscreen canvas) ----------
  function vg(g, stops, y0 = 0, y1 = H, x = 0, y = 0, w = W, h = H) {
    const gr = g.createLinearGradient(0, y0, 0, y1);
    for (const [p, c] of stops) gr.addColorStop(p, c);
    g.fillStyle = gr; g.fillRect(x, y, w, h);
  }
  // soft round glow that fades to the same hue (no grey fringe)
  function gl(g, x, y, r, rgb, a = 1) {
    if (!(r > 0) || !(a > 0)) return;
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, rgba(rgb, a)); gr.addColorStop(.35, rgba(rgb, a * .5)); gr.addColorStop(.7, rgba(rgb, a * .14)); gr.addColorStop(1, rgba(rgb, 0));
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
  }
  // glow ring from r0 outwards to r1; leaves the inside (covered by the body) unpainted
  function halo(g, x, y, r0, r1, rgb, a) {
    if (!(r1 > r0) || !(a > 0)) return;
    const gr = g.createRadialGradient(x, y, r0, x, y, r1);
    gr.addColorStop(0, rgba(rgb, a)); gr.addColorStop(.3, rgba(rgb, a * .45)); gr.addColorStop(.65, rgba(rgb, a * .12)); gr.addColorStop(1, rgba(rgb, 0));
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r1, 0, TAU); g.arc(x, y, r0 * .97, 0, TAU, true); g.fill();
  }
  function ci(g, x, y, r, fill) { g.fillStyle = fill; g.beginPath(); g.arc(x, y, Math.max(0, r), 0, TAU); g.fill(); }
  function el(g, x, y, rx, ry, rot, fill) { g.fillStyle = fill; g.beginPath(); g.ellipse(x, y, Math.max(0, rx), Math.max(0, ry), rot, 0, TAU); g.fill(); }
  function rrp(g, x, y, w, h, r) { r = Math.max(0, Math.min(r, w / 2, h / 2)); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
  function rrf(g, x, y, w, h, r, fill) { g.fillStyle = fill; g.beginPath(); rrp(g, x, y, w, h, r); g.fill(); }
  function poly(g, P, fill) { g.fillStyle = fill; g.beginPath(); P.forEach(([px, py], i) => i ? g.lineTo(px, py) : g.moveTo(px, py)); g.closePath(); g.fill(); }
  function smoothClosed(g, P) {
    const n = P.length, a = P[n - 1], b = P[0];
    g.moveTo((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    for (let i = 0; i < n; i++) { const p = P[i], q = P[(i + 1) % n]; g.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2); }
    g.closePath();
  }
  function smoothOpen(g, P) {
    g.moveTo(P[0][0], P[0][1]);
    for (let i = 1; i < P.length - 1; i++) { const p = P[i], q = P[i + 1]; g.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2); }
    const l = P[P.length - 1]; g.lineTo(l[0], l[1]);
  }
  // the pilot's blob outline, smoothed so it stays round at any size
  function blobPts(x, y, r, seed, n = 10, k = .45, sy = .8) {
    const P = [];
    for (let i = 0; i < n; i++) { const a = i / n * TAU, r2 = r * (.7 + rnd(seed * 13 + i) * k); P.push([x + Math.cos(a) * r2, y + Math.sin(a) * r2 * sy]); }
    return P;
  }
  function sblob(g, x, y, r, seed, fill, n, k, sy) { g.fillStyle = fill; g.beginPath(); smoothClosed(g, blobPts(x, y, r, seed, n, k, sy)); g.fill(); }
  // a landscape layer: smooth top edge fy(x), filled down to the bottom of the frame
  function ridge(g, fill, fy, step = 36) {
    const P = []; for (let x = -step; x <= W + step; x += step) P.push([x, fy(x)]);
    g.beginPath(); smoothOpen(g, P); g.lineTo(W + step, H + 10); g.lineTo(-step, H + 10); g.closePath(); g.fillStyle = fill; g.fill();
  }
  function jagged(g, fill, fy, step = 24) {
    g.beginPath(); g.moveTo(-10, H + 10); for (let x = -10; x <= W + step; x += step) g.lineTo(x, fy(x)); g.lineTo(W + 10, H + 10); g.closePath(); g.fillStyle = fill; g.fill();
  }
  // flat-bottomed cartoon cloud; x = centre, y = flat bottom, w = width
  function cloudPath(g, x, y, w, v = 0) {
    const h = w * .27; g.beginPath(); rrp(g, x - w / 2, y - h, w, h, h / 2);
    const B = [[-.22, -.27, .17], [.03, -.35, .23], [.27, -.23, .15]];
    for (let i = 0; i < 3; i++) { const [bx, by, br] = B[i], s = 1 + (rnd(v * 5 + i) - .5) * .25; g.moveTo(x + bx * w + br * w * s, y + by * w); g.arc(x + bx * w, y + by * w, br * w * s, 0, TAU); }
  }
  function cloud(g, x, y, w, col, shade, v = 0) {
    if (shade) { cloudPath(g, x + w * .025, y + w * .03, w, v); g.fillStyle = shade; g.fill(); }
    cloudPath(g, x, y, w, v); g.fillStyle = col; g.fill();
  }
  function tree(g, x, y, s, col, trunk) {
    rrf(g, x - s * .07, y - s * .55, s * .14, s * .58, s * .04, trunk);
    g.fillStyle = col; g.beginPath();
    for (const [dx, dy, r] of [[0, -.66, .3], [-.22, -.5, .23], [.22, -.52, .24], [0, -.42, .22]]) { g.moveTo(x + dx * s + r * s, y + dy * s); g.arc(x + dx * s, y + dy * s, r * s, 0, TAU); }
    g.fill();
  }
  function pine(g, x, y, h, col, trunk) {
    if (trunk) rrf(g, x - h * .045, y - h * .2, h * .09, h * .22, h * .02, trunk);
    g.fillStyle = col; g.beginPath();
    for (let i = 0; i < 3; i++) { const top = y - h + i * h * .24, bot = y - h * .12 - (2 - i) * h * .17, w = h * (.2 + i * .1); g.moveTo(x, top); g.lineTo(x + w, bot); g.quadraticCurveTo(x, bot - h * .06, x - w, bot); g.closePath(); }
    g.fill();
  }
  function sparkle(g, x, y, s) {
    g.beginPath(); g.moveTo(x, y - s); g.quadraticCurveTo(x, y, x + s, y); g.quadraticCurveTo(x, y, x, y + s); g.quadraticCurveTo(x, y, x - s, y); g.quadraticCurveTo(x, y, x, y - s); g.fill();
  }
  // static dust of tiny stars, some of them crowded along a band (x0,y0)->(x1,y1)
  function dust(g, n, seed, band, spread = 240, y1 = H) {
    for (let i = 0; i < n; i++) {
      const s = seed * 1000 + i * 3.7; let x, y;
      if (band && i < n * .45) { const u = rnd(s); x = lerp(band[0], band[2], u) + (rnd(s + 1) - .5) * spread; y = lerp(band[1], band[3], u) + (rnd(s + 2) - .5) * spread; }
      else { x = rnd(s + 3) * W; y = rnd(s + 4) * y1; }
      g.globalAlpha = (.15 + rnd(s + 5) * .45) * (1 - calm(x, y) * .5); ci(g, x, y, .6 + rnd(s + 6) * 1.2, '#fff');
    }
    g.globalAlpha = 1;
  }

  // ---------- live (animated) pieces, drawn on ctx ----------
  function twinkle(t, n, k = 1, y0 = 0, y1 = H, seed = 0) {
    for (let i = 0; i < n; i++) {
      const s = seed * 977 + i * 7.13, x = rnd(s) * W, y = y0 + rnd(s + 1.7) * (y1 - y0);
      const tw = .55 + .45 * Math.sin(t * (1 + rnd(s + 2.3) * 3) + i * 1.7);
      al(clamp(tw * k * (.35 + rnd(s + 3.1) * .65) * (1 - calm(x, y) * .55)));
      circle(x, y, 1.1 + rnd(s + 4.9) * 2.4, '#fff');
    }
    al(1);
  }
  function sparkles(t, list, col = '#fff') {
    ctx.fillStyle = col;
    list.forEach(([x, y, s], i) => { const k = .75 + .25 * Math.sin(t * 1.6 + i * 2.1); al(.55 + .45 * k); sparkle(ctx, x, y, s * k); });
    al(1);
  }
  // a shooting star now and then (some periods have none)
  function meteor(t, period, seed, y0 = 290, y1 = 470) {
    const k = Math.floor(t / period), p = (t - k * period) / 1.1;
    if (p >= 1 || rnd(k * 3.1 + seed) < .3) return;
    const x0 = 420 + rnd(k + seed * 7) * 600, yy = y0 + rnd(k + seed * 7 + 1) * (y1 - y0), ang = Math.PI * (.82 + rnd(k + seed) * .08);
    const dx = Math.cos(ang), dy = Math.sin(ang), e = eo(p), hx = x0 + dx * e * 520, hy = yy + dy * e * 520, len = 30 + 190 * Math.sin(p * Math.PI);
    const gr = ctx.createLinearGradient(hx, hy, hx - dx * len, hy - dy * len);
    gr.addColorStop(0, 'rgba(255,255,255,.95)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    al(Math.sin(p * Math.PI)); ctx.strokeStyle = gr; ctx.lineWidth = 3.5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(hx - dx * len, hy - dy * len); ctx.stroke();
    circle(hx, hy, 3.2, '#fff'); al(1);
  }
  function driftClouds(t, list, col, shade, a = 1) {
    al(a);
    list.forEach(([x0, y, w, v], i) => { const x = wrap(x0 + t * v + w * .6, W + w * 1.2) - w * .6; cloud(ctx, x, y, w, col, shade, i + 1); });
    al(1);
  }

  // ---------- background registry with a small cache of static layers ----------
  const cache = new Map();
  function layer(key, paint) {
    let c = cache.get(key);
    if (c) { cache.delete(key); cache.set(key, c); }
    else {
      c = document.createElement('canvas'); c.width = W; c.height = H;
      paint(c.getContext('2d'));
      cache.set(key, c);
      if (cache.size > 6) cache.delete(cache.keys().next().value);
    }
    ctx.drawImage(c, 0, 0);
  }
  const BG = {};
  function defBG(name, paint, anim) {
    BG[name] = function (t) {
      t = +t || 0;
      ctx.save(); const pa = BA; BA = ctx.globalAlpha;
      try {
        ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.filter = 'none'; ctx.setLineDash([]);
        layer(name, paint);
        if (anim) anim(t);
      } finally { BA = pa; ctx.restore(); }
    };
  }

  // =====================================================================================
  // SPACE: the pilot's navy/magenta/cyan sky, with a faint milky band and sparkles at the edges
  defBG('space', g => {
    vg(g, [[0, '#070b24'], [.5, '#100f38'], [1, '#1a1650']]);
    gl(g, 130, 470, 640, '255,79,216', .2);
    gl(g, 1010, 240, 500, '139,92,255', .22);
    gl(g, 1040, 1230, 720, '62,230,255', .13);
    gl(g, 30, 1560, 580, '139,92,255', .16);
    for (let i = 0; i <= 10; i++) { const u = i / 10; gl(g, lerp(-60, 1140, u), lerp(1260, -20, u) + Math.sin(u * 7) * 50, 200 + rnd(i + 40) * 110, '175,160,255', .055); }
    dust(g, 620, 1, [-60, 1260, 1140, -20], 250);
  }, t => {
    twinkle(t, 150, 1);
    sparkles(t, [[110, 330, 17], [972, 440, 13], [80, 1140, 12], [1004, 960, 15], [820, 200, 10], [240, 1070, 9], [660, 290, 7], [930, 1180, 8]]);
    meteor(t, 9, 1);
  });

  // SKY: clear day, cartoon clouds, rolling green hills
  const skyH = [x => 1075 + Math.sin(x * .0062 + 1) * 36 + nz(x * .012, 1) * 26, x => 1150 + Math.sin(x * .0047 + 2.4) * 50,
    x => 1268 + Math.sin(x * .0042 + .3) * 64, x => 1430 + Math.sin(x * .0052 + 3) * 56];
  defBG('sky', g => {
    vg(g, [[0, '#1a55c2'], [.45, '#2f7ddc'], [.82, '#559fe9'], [1, '#86c4f3']], 0, 1110);
    gl(g, 1010, 150, 700, '255,248,210', .5);
    ci(g, 1010, 150, 118, '#fff3b8'); ci(g, 1010, 150, 96, '#fff9dc');
    ridge(g, '#6cc593', skyH[0]);
    for (const x of [70, 160, 905, 980, 1050]) tree(g, x, skyH[0](x) + 6, 46 + rnd(x) * 14, '#55b47f', '#4a9a6e');
    ridge(g, '#45b277', skyH[1]);
    for (const x of [40, 120, 210, 860, 960, 1040]) tree(g, x, skyH[1](x) + 8, 86 + rnd(x + 1) * 30, '#2f9a5f', '#2a6f4c');
    // a little red-roofed house on the far right hill
    { const hx = 810, hy = skyH[1](hx) + 6; rrf(g, hx - 34, hy - 46, 68, 48, 4, '#f4f1ea'); poly(g, [[hx - 44, hy - 44], [hx, hy - 82], [hx + 44, hy - 44]], '#ff5a5f'); rrf(g, hx - 9, hy - 30, 18, 30, 3, '#8a5a44'); }
    ridge(g, '#2d9a60', skyH[2]);
    for (let i = 0; i < 40; i++) { const x = rnd(i + 200) * W, y = skyH[2](x) + 30 + rnd(i + 201) * 120; ci(g, x, y, 5 + rnd(i + 202) * 4, ['#ffffff', '#ffd23f', '#ff8fc8'][i % 3]); }
    ridge(g, '#21844f', skyH[3]);
  }, t => {
    driftClouds(t, [[150, 360, 280, 7], [820, 420, 210, 5], [30, 640, 150, 3], [980, 720, 170, 4], [330, 960, 230, 6], [760, 1010, 160, 5]], '#ffffff', '#cfe4fa');
  });

  // SUNSET: rose and orange sky, sun sinking behind purple hills, birds
  const sunH = [x => 1120 + Math.sin(x * .005 + .5) * 30 + nz(x * .01, 4) * 22, x => 1190 + Math.sin(x * .0045 + 2) * 46,
    x => 1300 + Math.sin(x * .004 + 4) * 60, x => 1450 + Math.sin(x * .005 + 1) * 50];
  defBG('sunset', g => {
    vg(g, [[0, '#1a1243'], [.26, '#3a1a63'], [.48, '#7d2a70'], [.66, '#c9466e'], [.8, '#f47a5e'], [.92, '#ffb56a'], [1, '#ffd68a']], 0, 1160);
    gl(g, 800, 1130, 760, '255,150,90', .45);
    gl(g, 800, 1130, 330, '255,220,140', .6);
    { const gr = g.createLinearGradient(0, 960, 0, 1130); gr.addColorStop(0, '#fff3b0'); gr.addColorStop(1, '#ff9a4a'); g.fillStyle = gr; g.beginPath(); g.arc(800, 1130, 170, 0, TAU); g.fill(); }
    // retro gaps across the lower sun
    for (let i = 0; i < 4; i++) { const y = 1062 + i * 20; g.fillStyle = i % 2 ? '#ff9f5a' : '#ffa862'; g.fillRect(620, y, 360, 4 + i * 2); }
    // thin cloud streaks
    for (const [x, y, w, c, a] of [[60, 420, 330, '#ff8fb0', .3], [760, 520, 380, '#ff9fb8', .25], [-40, 760, 300, '#ffb08a', .22], [700, 880, 420, '#ffc08a', .3], [140, 960, 260, '#ffd08f', .28]]) { g.globalAlpha = a; rrf(g, x, y, w, 16, 8, c); rrf(g, x + w * .2, y + 22, w * .6, 11, 6, c); }
    g.globalAlpha = 1;
    dust(g, 120, 2, null, 0, 560);
    ridge(g, '#8a3470', sunH[0]);
    ridge(g, '#5e215f', sunH[1]);
    // lone tree and cottage silhouettes
    tree(g, 940, sunH[1](940) + 10, 150, '#3e1650', '#3e1650');
    { const hx = 130, hy = sunH[1](hx) + 8; poly(g, [[hx - 50, hy], [hx - 50, hy - 60], [hx, hy - 100], [hx + 50, hy - 60], [hx + 50, hy]], '#3e1650'); rrf(g, hx - 12, hy - 52, 24, 26, 4, '#ffcf6b'); gl(g, hx, hy - 40, 70, '255,200,100', .35); }
    ridge(g, '#3a1650', sunH[2]);
    ridge(g, '#230d36', sunH[3]);
  }, t => {
    twinkle(t, 40, .7, 0, 560, 3);
    // birds gliding across, flapping slowly
    ctx.strokeStyle = '#2a1036'; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (const [x0, y0, s, v, ph] of [[100, 560, 22, 16, 0], [160, 600, 16, 16, 1.3], [60, 620, 14, 16, 2.1], [700, 380, 18, 11, .7], [760, 410, 13, 11, 2.6]]) {
      const x = wrap(x0 + t * v, W + 160) - 80, y = y0 + Math.sin(t * .7 + ph) * 12, f = Math.sin(t * 5 + ph);
      ctx.lineWidth = Math.max(2, s * .16); ctx.beginPath();
      ctx.moveTo(x - s, y - s * (.15 + .35 * f)); ctx.quadraticCurveTo(x - s * .4, y - s * (.45 + .1 * f), x, y);
      ctx.quadraticCurveTo(x + s * .4, y - s * (.45 + .1 * f), x + s, y - s * (.15 + .35 * f)); ctx.stroke();
    }
  });

  // NIGHT: starry sky over a small sleeping town (the pilot's town, cosier)
  const nightHouses = [];
  for (let i = 0; i < 9; i++) nightHouses.push({ x: 60 + i * 115, y: 1120 + Math.sin(i * 1.3) * 30, w: 80 + rnd(i) * 30, h: 70 + rnd(i + 2) * 60, lit: rnd(i + 30) > .3, chim: i % 3 === 1 });
  defBG('night', g => {
    vg(g, [[0, '#040720'], [.5, '#0a1140'], [.82, '#18225e'], [1, '#283070']], 0, 1150);
    gl(g, 540, 1200, 720, '255,160,90', .1);
    gl(g, 110, 380, 540, '139,92,255', .13);
    gl(g, 990, 720, 520, '62,230,255', .06);
    for (let i = 0; i <= 8; i++) { const u = i / 8; gl(g, lerp(1140, -40, u), lerp(900, 80, u), 190 + rnd(i + 70) * 90, '175,160,255', .05); }
    dust(g, 380, 3, [1140, 900, -40, 80], 220, 1100);
    ridge(g, '#111947', x => 1050 + Math.sin(x * .005 + 2) * 46 + nz(x * .01, 3) * 30);
    for (const x of [30, 90, 1000, 1060]) pine(g, x, 1100 + rnd(x) * 30, 120 + rnd(x + 1) * 50, '#0c1238');
    g.fillStyle = '#0c1238'; g.beginPath(); g.moveTo(0, 1150); g.quadraticCurveTo(300, 1000, 600, 1110); g.quadraticCurveTo(850, 1190, 1080, 1060); g.lineTo(1080, H); g.lineTo(0, H); g.fill();
    for (const hs of nightHouses) {
      const { x, y, w, h } = hs;
      if (hs.chim) { g.fillStyle = '#070a26'; g.fillRect(x + w * .66, y - h - 34, 16, 40); }
      g.fillStyle = '#0a0f34'; g.fillRect(x, y - h, w, h + 300);
      poly(g, [[x - 8, y - h], [x + w / 2, y - h - 40], [x + w + 8, y - h]], '#161d52');
      rrf(g, x + w * .62, y - 34, 16, 34, 3, '#060920');
    }
    const fg = x => 1168 + Math.sin(x * .006 + 1) * 18 + nz(x * .01, 13) * 14;
    ridge(g, '#070a26', fg);
    // street lamps on the front hill
    for (const lx of [205, 655, 905]) { const ly = fg(lx) + 6; g.fillStyle = '#05081c'; g.fillRect(lx - 3, ly - 112, 6, 112); rrf(g, lx - 12, ly - 124, 24, 16, 6, '#05081c'); gl(g, lx, ly - 112, 90, '255,200,110', .35); ci(g, lx, ly - 112, 6, '#ffe2a0'); }
  }, t => {
    twinkle(t, 200, 1.1, 0, 1050, 4);
    meteor(t, 11, 4);
    // lit windows, an occasional one switching on or off
    const ep = Math.floor(t / 4.5);
    nightHouses.forEach((hs, i) => {
      const flip = rnd(ep * 17 + i) < .12, on = hs.lit !== flip;
      const wx = hs.x + hs.w * .3, wy = hs.y - hs.h + 20;
      if (on) { glow(wx + 11, wy + 13, 46, 'rgba(255,200,90,.55)', .6); rr(wx, wy, 22, 26, 4); ctx.fillStyle = '#ffd25a'; ctx.fill(); ctx.fillStyle = 'rgba(8,12,44,.55)'; ctx.fillRect(wx + 10, wy, 2, 26); ctx.fillRect(wx, wy + 12, 22, 2); }
      else { rr(wx, wy, 22, 26, 4); ctx.fillStyle = '#141a48'; ctx.fill(); }
      if (hs.chim) for (let k = 0; k < 4; k++) { const p = wrap(t * .22 + k / 4, 1); al((1 - p) * .28); circle(hs.x + hs.w * .66 + 8 + p * 30 + Math.sin(t + k) * 4, hs.y - hs.h - 40 - p * 110, 8 + p * 16, '#8f97c9'); }
      al(1);
    });
  });

  // OCEAN: sea surface from just above the water, horizon, sun glitter and a small sailboat
  const HY = 1010;
  defBG('ocean', g => {
    vg(g, [[0, '#1c58c6'], [.55, '#3d92ea'], [.9, '#8ccffb'], [1, '#c4ecff']], 0, HY);
    gl(g, 990, 190, 620, '255,246,210', .5);
    ci(g, 990, 190, 96, '#fff4c2'); ci(g, 990, 190, 78, '#fffbe6');
    { const gr = g.createLinearGradient(0, HY, 0, H); gr.addColorStop(0, '#3a9bf0'); gr.addColorStop(.12, '#2479e0'); gr.addColorStop(.38, '#1757c2'); gr.addColorStop(1, '#0a2a78'); g.fillStyle = gr; g.fillRect(0, HY, W, H - HY); }
    // sun column on the water
    g.save(); g.translate(990, HY); g.scale(.28, 1); gl(g, 0, 0, 560, '255,250,220', .4); g.restore();
    g.fillStyle = 'rgba(230,248,255,.6)'; g.fillRect(0, HY - 1, W, 3);
    // little island on the horizon, left
    { g.fillStyle = '#f3d48a'; g.beginPath(); g.ellipse(120, HY + 4, 130, 16, 0, Math.PI, TAU); g.fill(); g.fillStyle = '#2fae6a'; g.beginPath(); g.ellipse(100, HY - 4, 90, 34, 0, Math.PI, TAU); g.fill();
      g.strokeStyle = '#7a5a3a'; g.lineWidth = 7; g.lineCap = 'round'; g.beginPath(); g.moveTo(170, HY - 6); g.quadraticCurveTo(178, HY - 60, 166, HY - 100); g.stroke();
      g.fillStyle = '#24a05e'; for (const a of [-2.6, -2, -1.2, -.5, .1]) { g.beginPath(); g.ellipse(166 + Math.cos(a) * 30, HY - 100 + Math.sin(a) * 14 + 6, 34, 9, a, 0, TAU); g.fill(); } }
  }, t => {
    driftClouds(t, [[120, 330, 240, 5], [760, 470, 180, 4], [400, 860, 150, 3]], '#ffffff', '#cfe4fa', .95);
    // wave marks, smaller and slower toward the horizon
    ctx.lineCap = 'round';
    for (let j = 0; j < 22; j++) {
      const u = (j + .5) / 22, y = HY + 8 + (H - HY) * Math.pow(u, 1.7), s = .22 + u * 1.5, dir = j % 2 ? 1 : -1;
      ctx.lineWidth = Math.max(1.5, 4 * s); ctx.strokeStyle = 'rgba(210,240,255,1)';
      for (let i = 0; i < 6; i++) {
        const w = (38 + rnd(j * 9 + i) * 40) * s, x = wrap(rnd(j * 13 + i) * W + t * dir * 10 * s, W + 200) - 100, yy = y + Math.sin(t * 1.4 + i + j) * 4 * s;
        al((.18 + .3 * (1 - u)) * (.6 + .4 * Math.sin(t * .9 + i * 2 + j)));
        ctx.beginPath(); ctx.moveTo(x - w / 2, yy); ctx.quadraticCurveTo(x, yy - 9 * s, x + w / 2, yy); ctx.stroke();
      }
    }
    // glitter under the sun
    ctx.fillStyle = '#fffbe6';
    for (let i = 0; i < 36; i++) { const u = rnd(i + 400), y = HY + 6 + Math.pow(u, 1.4) * 480, x = 990 + (rnd(i + 401) - .5) * (60 + u * 200), f = Math.sin(t * 3 + i * 2.3); if (f > .2) { al(f * .9); ctx.fillRect(x - 6 - u * 10, y, 12 + u * 20, 2 + u * 3); } }
    al(1);
    // sailboat bobbing on the right
    { const bx = 820 + Math.sin(t * .25) * 10, by = HY + 14 + Math.sin(t * 1.3) * 3; ctx.save(); ctx.translate(bx, by); ctx.rotate(Math.sin(t * 1.1) * .05);
      ctx.fillStyle = '#ffffff'; ctx.beginPath(); ctx.moveTo(0, -12); ctx.lineTo(0, -92); ctx.lineTo(42, -16); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#ff5a5f'; ctx.beginPath(); ctx.moveTo(-4, -14); ctx.lineTo(-4, -76); ctx.lineTo(-34, -14); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#0b2d6b'; ctx.beginPath(); ctx.moveTo(-50, -10); ctx.lineTo(56, -10); ctx.lineTo(40, 6); ctx.lineTo(-38, 6); ctx.closePath(); ctx.fill(); ctx.restore(); }
  });

  // UNDERWATER: deep blue, light rays, swaying seaweed, coral at the sides, bubbles and fish
  function coralG(g, x, y, s, col, seed) {
    g.save(); g.translate(x, y); g.scale(s, s); g.strokeStyle = col; g.fillStyle = col; g.lineCap = 'round';
    const branch = (len, ang, w, d) => {
      if (d > 3) return; g.save(); g.rotate(ang); g.lineWidth = w; g.beginPath(); g.moveTo(0, 0); g.lineTo(0, -len); g.stroke(); g.translate(0, -len);
      g.beginPath(); g.arc(0, 0, w * .55, 0, TAU); g.fill();
      branch(len * .72, -.45 - rnd(seed + d) * .2, w * .72, d + 1); branch(len * .72, .45 + rnd(seed + d + 4) * .2, w * .72, d + 1); g.restore();
    };
    branch(110, 0, 26, 0); g.restore();
  }
  defBG('underwater', g => {
    vg(g, [[0, '#1a93cc'], [.1, '#1176b4'], [.4, '#084585'], [.72, '#052a5e'], [1, '#03122e']]);
    gl(g, 540, -160, 900, '170,240,255', .35);
    // surface seen from below
    g.fillStyle = 'rgba(190,240,255,.18)'; g.beginPath(); g.moveTo(0, 0); for (let x = 0; x <= W; x += 30) g.lineTo(x, 70 + Math.sin(x * .02) * 14 + Math.sin(x * .053) * 8); g.lineTo(W, 0); g.fill();
    // sea floor
    ridge(g, '#0d3f73', x => 1200 + Math.sin(x * .006 + 1) * 40 + nz(x * .012, 6) * 30);
    ridge(g, '#c9a467', x => 1330 + Math.sin(x * .005 + 2.2) * 34);
    ridge(g, '#b48d55', x => 1470 + Math.sin(x * .006 + .5) * 30);
    for (let i = 0; i < 30; i++) ci(g, rnd(i + 50) * W, 1360 + rnd(i + 51) * 200, 3 + rnd(i + 52) * 4, '#a8824c');
    // rocks and coral at the sides
    sblob(g, 70, 1300, 120, 7, '#1f4a78', 12, .35, .7); sblob(g, 1010, 1290, 130, 8, '#1f4a78', 12, .35, .7);
    const cols = ['#ff6fa8', '#ff9a3c', '#c86bff', '#ff5a7a'];
    [[40, 1290, .9], [150, 1320, 1.15], [250, 1350, .7], [850, 1340, .8], [950, 1310, 1.2], [1060, 1290, .9]].forEach(([x, y, s], i) => coralG(g, x, y, s, cols[i % 4], i * 3));
    // brain coral and sea stars on the sand
    for (const [x, y, r, c] of [[330, 1380, 44, '#ffb15c'], [760, 1395, 38, '#ff7bb4']]) { el(g, x, y, r, r * .7, 0, c); g.strokeStyle = 'rgba(0,0,0,.18)'; g.lineWidth = 4; for (let k = -1; k <= 1; k++) { g.beginPath(); g.ellipse(x, y + k * r * .18, r * .75, r * .2, 0, Math.PI, TAU); g.stroke(); } }
  }, t => {
    // light rays
    ctx.fillStyle = '#e8fbff';
    for (let i = 0; i < 6; i++) {
      const x0 = 80 + i * 190, sw = Math.sin(t * .5 + i * 1.7) * 60;
      al(.05 + .025 * Math.sin(t * .8 + i)); ctx.beginPath(); ctx.moveTo(x0 - 30, 0); ctx.lineTo(x0 + 40, 0); ctx.lineTo(x0 + 110 + sw, 1300); ctx.lineTo(x0 - 40 + sw, 1300); ctx.closePath(); ctx.fill();
    }
    al(1);
    // seaweed swaying at the sides
    ctx.lineCap = 'round';
    for (const [x, h, c, ph] of [[95, 360, '#2fbf8a', 0], [200, 270, '#27a679', 1.3], [880, 300, '#2fbf8a', 2.2], [990, 410, '#27a679', .6], [1045, 250, '#3ccf9a', 1.9], [30, 240, '#3ccf9a', 2.8]]) {
      ctx.strokeStyle = c; ctx.lineWidth = 16; ctx.beginPath(); const y0 = 1330; ctx.moveTo(x, y0);
      for (let k = 1; k <= 8; k++) { const u = k / 8; ctx.lineTo(x + Math.sin(t * 1.1 + ph + u * 3) * 26 * u, y0 - h * u); }
      ctx.stroke();
    }
    // fish
    for (const [y, s, v, c, x0] of [[420, 36, 34, '#ffd23f', 0], [470, 26, 30, '#ffd23f', 60], [1080, 40, -26, '#ff8a5c', 300], [610, 22, 22, '#7ff0ff', 700], [1150, 28, 18, '#ff6fa8', 900]]) {
      const x = wrap(x0 + t * v, W + 240) - 120, yy = y + Math.sin(t * 1.3 + x0) * 10, d = Math.sign(v);
      al(.9); ctx.save(); ctx.translate(x, yy); ctx.scale(d, 1);
      ctx.fillStyle = c; ctx.beginPath(); ctx.ellipse(0, 0, s, s * .55, 0, 0, TAU); ctx.fill();
      ctx.beginPath(); ctx.moveTo(-s * .8, 0); ctx.lineTo(-s * 1.5, -s * .5 + Math.sin(t * 8 + x0) * s * .1); ctx.lineTo(-s * 1.5, s * .5 + Math.sin(t * 8 + x0) * s * .1); ctx.closePath(); ctx.fill();
      circle(s * .5, -s * .1, s * .14, '#0b0f2a'); ctx.restore();
    }
    // bubbles rising along the sides, marine snow everywhere
    ctx.lineWidth = 2.5;
    for (let i = 0; i < 26; i++) {
      const p = wrap(t * (.05 + rnd(i + 80) * .04) + rnd(i + 81), 1), side = i % 2 ? 60 + rnd(i + 82) * 200 : 820 + rnd(i + 82) * 200;
      const x = side + Math.sin(t * 2 + i) * 10, y = 1300 - p * 1250, r = 4 + rnd(i + 83) * 10;
      al(.55 * clamp((1 - p) * 4)); ctx.strokeStyle = '#d8f6ff'; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.stroke(); circle(x - r * .35, y - r * .35, r * .25, '#ffffff');
    }
    for (let i = 0; i < 40; i++) { const x = wrap(rnd(i + 90) * W + Math.sin(t * .3 + i) * 20, W), y = wrap(rnd(i + 91) * H - t * 6, 1300); al(.25 * (1 - calm(x, y) * .6)); circle(x, y, 1.5 + rnd(i + 92) * 2, '#cdefff'); }
    al(1);
  });

  // DESERT: blue sky, hot sun, layered dunes, cacti at the edges
  const desH = [x => 1128 + Math.sin(x * .006 + .8) * 24, x => 1205 + Math.sin(x * .0042 + 2.6) * 50 + nz(x * .01, 8) * 20,
    x => 1325 + Math.sin(x * .0048 + .2) * 66, x => 1475 + Math.sin(x * .005 + 2) * 48];
  function cactus(g, x, y, h, col, dark) {
    const w = h * .17;
    rrf(g, x - w / 2, y - h, w, h + 10, w / 2, col);
    rrf(g, x - w * 2.1, y - h * .62, w * .8, h * .3, w * .4, col); rrf(g, x - w * 2.1, y - h * .42, w * 1.7, w * .8, w * .4, col);
    rrf(g, x + w * 1.3, y - h * .8, w * .8, h * .34, w * .4, col); rrf(g, x + w * .4, y - h * .55, w * 1.7, w * .8, w * .4, col);
    g.strokeStyle = dark; g.lineWidth = Math.max(2, w * .1); g.lineCap = 'round';
    g.beginPath(); g.moveTo(x, y - h + w * .6); g.lineTo(x, y); g.stroke();
  }
  defBG('desert', g => {
    vg(g, [[0, '#1b58c0'], [.55, '#3a86dc'], [.88, '#7dbbe8'], [1, '#c4e2f2']], 0, 1140);
    gl(g, 905, 285, 560, '255,240,180', .55);
    ci(g, 905, 285, 92, '#fff1a6'); ci(g, 905, 285, 74, '#fff8d2');
    // far mesas in the haze
    poly(g, [[-20, 1140], [-20, 1010], [60, 1010], [80, 990], [210, 990], [240, 1020], [300, 1030], [330, 1140]], '#c98462');
    poly(g, [[210, 990], [240, 1020], [300, 1030], [330, 1140], [190, 1140]], '#b06e52');
    poly(g, [[820, 1140], [860, 1050], [930, 1050], [950, 1065], [1010, 1065], [1050, 1140]], '#cf8c6a');
    ridge(g, '#f0a65a', desH[0]);
    { const gr = g.createLinearGradient(0, 1150, 0, 1330); gr.addColorStop(0, '#e48b44'); gr.addColorStop(1, '#d6773a'); ridge(g, gr, desH[1]); }
    cactus(g, 270, desH[1](270) + 14, 90, '#3f9a5c', '#2f7b48');
    cactus(g, 840, desH[1](840) + 12, 70, '#3f9a5c', '#2f7b48');
    { const gr = g.createLinearGradient(0, 1250, 0, 1480); gr.addColorStop(0, '#c9662f'); gr.addColorStop(1, '#b65a2a'); ridge(g, gr, desH[2]); }
    cactus(g, 110, desH[2](110) + 30, 250, '#2f8a50', '#23703f');
    cactus(g, 985, desH[2](985) + 30, 200, '#2f8a50', '#23703f');
    for (const [x, r] of [[330, 26], [370, 16], [720, 22]]) el(g, x, desH[2](x) + 24, r, r * .6, 0, '#8f4322');
    ridge(g, '#9a4724', desH[3]);
  }, t => {
    driftClouds(t, [[220, 350, 190, 4], [700, 560, 130, 3]], '#ffffff', '#d6e8f8', .9);
    // heat shimmer above the dunes
    ctx.strokeStyle = '#fff2d0'; ctx.lineWidth = 3; ctx.lineCap = 'round';
    for (let i = 0; i < 7; i++) {
      const y = 1080 + i * 14, x0 = wrap(rnd(i + 300) * W + t * 18 * (i % 2 ? 1 : -1), W + 400) - 300;
      al(.16 + .1 * Math.sin(t * 2 + i)); ctx.beginPath();
      for (let k = 0; k <= 10; k++) { const x = x0 + k * 22; ctx.lineTo(x, y + Math.sin(k * 1.4 + t * 4 + i) * 3); }
      ctx.stroke();
    }
    // sand blowing low
    for (let i = 0; i < 30; i++) { const x = wrap(rnd(i + 310) * W + t * (60 + rnd(i + 311) * 50), W + 20) - 10, y = 1220 + rnd(i + 312) * 240 + Math.sin(t * 2 + i) * 6; al(.35); circle(x, y, 1.5 + rnd(i + 313) * 2, '#fff0c8'); }
    al(1);
  });

  // FOREST: deep teal woods, light from the upper left, fireflies and drifting leaves
  defBG('forest', g => {
    vg(g, [[0, '#0b3043'], [.45, '#155560'], [.82, '#3a9478'], [1, '#78c992']], 0, 1150);
    gl(g, 160, 80, 820, '255,240,180', .22);
    // far canopy: a band of soft round crowns
    g.fillStyle = '#2d7a6d'; g.beginPath();
    for (let x = -40; x <= W + 40; x += 46) { const r = 50 + rnd(x + 3) * 40, y = 1060 - rnd(x + 4) * 50; g.moveTo(x + r, y); g.arc(x, y, r, 0, TAU); }
    g.rect(-40, 1060, W + 80, 200); g.fill();
    for (let i = 0; i < 13; i++) { const x = 20 + i * 86 + rnd(i + 20) * 30; pine(g, x, 1190, 220 + rnd(i + 21) * 180, '#1e5c52'); }
    ridge(g, '#164a40', x => 1180 + Math.sin(x * .006 + 1) * 24);
    // tall trees framing both sides
    for (const [x, h] of [[215, 600], [875, 640]]) { rrf(g, x - 14, 1380 - h * .4, 28, h * .4 + 100, 8, '#123f37'); pine(g, x, 1400 - h * .3, h, '#185249'); }
    for (const [x, h] of [[35, 1000], [1045, 980]]) {
      rrf(g, x - 22, 1400 - h * .5, 44, h * .5 + 100, 10, '#0b2a26'); pine(g, x, 1420 - h * .32, h, '#0d3530');
      g.save(); g.beginPath(); g.rect(x - h, 0, h, H); g.clip(); pine(g, x, 1420 - h * .32, h, '#154a41'); g.restore();
    }
    ridge(g, '#0f362f', x => 1330 + Math.sin(x * .007 + 3) * 30);
    // ferns and mushrooms at the bottom corners
    for (const [x, y, s, f] of [[60, 1340, 80, 1], [300, 1360, 60, -1], [780, 1355, 66, 1], [1020, 1335, 84, -1]]) {
      g.fillStyle = '#1d6b4f'; for (let k = 0; k < 5; k++) { const a = -Math.PI / 2 + (k - 2) * .45 * f; g.beginPath(); g.ellipse(x + Math.cos(a) * s * .5, y + Math.sin(a) * s * .5, s * .55, s * .13, a, 0, TAU); g.fill(); }
    }
    for (const [x, y, s] of [[150, 1352, 34], [196, 1360, 22], [900, 1356, 30]]) { rrf(g, x - s * .22, y - s * .7, s * .44, s * .7, s * .15, '#f3e6d0'); g.fillStyle = '#ff5a5f'; g.beginPath(); g.ellipse(x, y - s * .7, s * .62, s * .42, 0, Math.PI, TAU); g.fill(); ci(g, x - s * .25, y - s * .86, s * .09, '#fff'); ci(g, x + s * .18, y - s * .95, s * .07, '#fff'); ci(g, x + s * .02, y - s * .78, s * .06, '#fff'); }
  }, t => {
    // god rays from the upper left
    ctx.fillStyle = '#fff6d0';
    for (let i = 0; i < 5; i++) {
      const a = .5 + i * .16 + Math.sin(t * .3 + i) * .02, w = .045 + rnd(i + 500) * .04;
      al(.032 + .014 * Math.sin(t * .7 + i * 2)); ctx.beginPath(); ctx.moveTo(-60, -60); ctx.lineTo(-60 + Math.cos(a - w) * 2200, -60 + Math.sin(a - w) * 2200); ctx.lineTo(-60 + Math.cos(a + w) * 2200, -60 + Math.sin(a + w) * 2200); ctx.closePath(); ctx.fill();
    }
    // fireflies / pollen
    for (let i = 0; i < 36; i++) {
      const x = wrap(rnd(i + 520) * W + Math.sin(t * .4 + i) * 40 + t * 4, W + 40) - 20, y = 380 + rnd(i + 521) * 1000 + Math.sin(t * .6 + i * 2) * 30;
      const f = .5 + .5 * Math.sin(t * 2.2 + i * 1.9), c = 1 - calm(x, y) * .6;
      al(f * .5 * c); glow(x, y, 18, 'rgba(255,240,140,.9)', .6); al(f * .9 * c); circle(x, y, 2.6, '#fff6b0');
    }
    // falling leaves
    for (let i = 0; i < 6; i++) {
      const p = wrap(t * .045 + rnd(i + 540), 1), x = (i % 2 ? 80 : 820) + rnd(i + 541) * 180 + Math.sin(t * 1.2 + i) * 50, y = 200 + p * 1150;
      al(clamp((1 - p) * 5) * .9); ctx.save(); ctx.translate(x, y); ctx.rotate(t * 1.5 + i); ctx.fillStyle = i % 3 ? '#e9a13b' : '#d8613a';
      ctx.beginPath(); ctx.ellipse(0, 0, 14, 6, 0, 0, TAU); ctx.fill(); ctx.restore();
    }
    al(1);
  });

  // ICE: polar night with aurora, faceted ice mountains and a snowfield
  function peaks(g, base, list, light, shade, cap) {
    for (const [x, w, h] of list) {
      const top = base - h;
      poly(g, [[x - w, base], [x, top], [x + w * .12, base]], light);
      poly(g, [[x + w * .12, base], [x, top], [x + w, base]], shade);
      if (cap) {
        const k = .3, lx = x - w * k, rx = x + w * k, cy = top + h * k;
        poly(g, [[x, top], [lx, cy], [lx + w * .12, cy - h * .06], [x - w * .06, cy + h * .02], [x + w * .1, cy - h * .05], [rx, cy]], cap);
      }
    }
  }
  defBG('ice', g => {
    vg(g, [[0, '#050f2e'], [.45, '#0a2556'], [.78, '#15528a'], [1, '#3a8fb8']], 0, 1100);
    gl(g, 540, 1140, 700, '120,230,255', .14);
    dust(g, 260, 5, null, 0, 900);
    peaks(g, 1110, [[60, 200, 230], [300, 170, 150], [520, 220, 110], [760, 190, 160], [1010, 230, 250]], '#3f7fb0', '#2c6293', null);
    peaks(g, 1170, [[-10, 260, 380], [210, 170, 210], [880, 190, 240], [1090, 250, 360]], '#a6cde8', '#6c9dc8', '#e8f5fd');
    ridge(g, '#8fbfe2', x => 1170 + Math.sin(x * .005 + 1) * 22 + nz(x * .015, 9) * 18);
    ridge(g, '#77aad6', x => 1290 + Math.sin(x * .006 + 2.5) * 30);
    // cracked ice floes
    for (const [x, y, w] of [[160, 1250, 170], [860, 1262, 200]]) { el(g, x, y, w, 20, 0, '#5f93c4'); el(g, x, y - 4, w * .94, 15, 0, '#b4daf2'); }
    ridge(g, '#5f94c6', x => 1420 + Math.sin(x * .005 + .4) * 34);
  }, t => {
    // aurora ribbons
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    [[330, '80,255,190', 1, 0], [430, '62,230,255', .75, 2], [250, '210,120,255', .5, 4]].forEach(([base, rgb, a, ph]) => {
      const P = []; for (let x = -60; x <= W + 60; x += 40) P.push([x, base + Math.sin(x * .006 + t * .35 + ph) * 60 + Math.sin(x * .013 - t * .25 + ph * 2) * 24]);
      for (const [lw, aa] of [[150, .05], [70, .08], [22, .16]]) { ctx.lineWidth = lw; ctx.strokeStyle = rgba(rgb, 1); al(aa * a); ctx.beginPath(); P.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke(); }
      // the curtain: a soft sheet rising from the ribbon
      const cg = ctx.createLinearGradient(0, base + 70, 0, base - 230); cg.addColorStop(0, rgba(rgb, .2 * a)); cg.addColorStop(1, rgba(rgb, 0));
      al(1); ctx.fillStyle = cg; ctx.beginPath(); P.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
      for (let i = P.length - 1; i >= 0; i--) { const [x, y] = P[i]; ctx.lineTo(x, y - 110 - 80 * Math.sin(x * .011 + t * .5 + ph)); }
      ctx.closePath(); ctx.fill();
    });
    // snowfall
    for (let i = 0; i < 90; i++) {
      const x = wrap(rnd(i + 600) * W + Math.sin(t * .6 + i) * 30 + t * 8, W + 12) - 6, y = wrap(rnd(i + 601) * H + t * (35 + rnd(i + 602) * 45), H + 12) - 6;
      al((.45 + rnd(i + 603) * .45) * (1 - calm(x, y) * .5)); circle(x, y, 1.6 + rnd(i + 604) * 3.2, '#ffffff');
    }
    al(1);
  });

  // LAB: cosy lab wall, warm lamps, shelves of glassware, a counter at the bottom
  const labGlass = [ // kind, x, base y, size, liquid rgb
    ['e', 60, 400, 120, '62,230,255'], ['r', 155, 400, 104, '255,79,216'], ['e', 1015, 440, 112, '255,210,63'], ['r', 925, 440, 98, '60,207,122'],
    ['b', 255, 660, 70, '255,210,63'], ['b', 830, 700, 66, '255,79,216'], ['r', 1005, 700, 92, '62,230,255'],
    ['e', 935, 1172, 210, '60,207,122'], ['b', 1040, 1172, 76, '62,230,255']];
  function glass(g, kind, x, y, s, rgb) {
    g.beginPath();
    if (kind === 'e') { g.moveTo(x - s * .11, y - s); g.lineTo(x - s * .11, y - s * .62); g.lineTo(x - s * .4, y - s * .07); g.quadraticCurveTo(x - s * .43, y, x - s * .34, y); g.lineTo(x + s * .34, y); g.quadraticCurveTo(x + s * .43, y, x + s * .4, y - s * .07); g.lineTo(x + s * .11, y - s * .62); g.lineTo(x + s * .11, y - s); g.closePath(); }
    else if (kind === 'r') { const R = s * .36, cy = y - R; g.moveTo(x - s * .1, y - s); g.lineTo(x - s * .1, cy - R * .92); g.arc(x, cy, R, -Math.PI / 2 - .28, Math.PI * 1.5 + .28, true); g.lineTo(x + s * .1, y - s); g.closePath(); }
    else { rrp(g, x - s * .42, y - s, s * .84, s, s * .08); }
    g.save(); g.fillStyle = 'rgba(170,200,255,.12)'; g.fill(); g.clip();
    const lv = kind === 'b' ? .55 : .42; g.fillStyle = rgba(rgb, .85); g.fillRect(x - s, y - s * lv, s * 2, s * lv);
    g.fillStyle = 'rgba(255,255,255,.25)'; g.fillRect(x - s, y - s * lv, s * 2, Math.max(2, s * .03)); g.restore();
    g.strokeStyle = 'rgba(200,220,255,.6)'; g.lineWidth = Math.max(2, s * .03); g.lineJoin = 'round'; g.stroke();
    rrf(g, x - s * (kind === 'b' ? .46 : .15), y - s - s * .03, s * (kind === 'b' ? .92 : .3), s * .06, s * .03, 'rgba(200,220,255,.75)');
    gl(g, x, y - s * .2, s * .9, rgb, .16);
  }
  function shelf(g, x0, x1, y) {
    rrf(g, x0, y, x1 - x0, 20, 4, '#6e4a58'); g.fillStyle = '#4c3044'; g.fillRect(x0, y + 16, x1 - x0, 6);
    for (const bx of [x0 + 40, x1 - 50]) poly(g, [[bx, y + 22], [bx + 12, y + 22], [bx + 12, y + 70]], '#4c3044');
  }
  defBG('lab', g => {
    vg(g, [[0, '#151944'], [.55, '#1e2358'], [1, '#191d4c']]);
    for (let x = 0; x < W; x += 180) { g.fillStyle = 'rgba(255,255,255,.022)'; g.fillRect(x, 0, 90, 1120); }
    gl(g, 120, 210, 520, '255,200,120', .16); gl(g, 960, 210, 520, '255,200,120', .16);
    // dado rail and lower panelling
    g.fillStyle = '#141737'; g.fillRect(0, 1110, W, H - 1110); g.fillStyle = '#2b3170'; g.fillRect(0, 1100, W, 12);
    // pendant lamps
    for (const lx of [120, 960]) { g.strokeStyle = '#0d1030'; g.lineWidth = 4; g.beginPath(); g.moveTo(lx, 0); g.lineTo(lx, 120); g.stroke(); g.fillStyle = '#ff4fd8'; g.beginPath(); g.arc(lx, 160, 48, Math.PI, TAU); g.fill(); el(g, lx, 162, 30, 9, 0, '#fff2c4'); gl(g, lx, 175, 150, '255,226,160', .45); }
    shelf(g, 0, 300, 400); shelf(g, 0, 300, 660); shelf(g, 780, W, 440); shelf(g, 780, W, 700);
    // books
    let bx = 205; for (const [w, h, c] of [[20, 100, '#ff5a5f'], [24, 116, '#3ee6ff'], [18, 92, '#ffd23f'], [26, 110, '#8b5cff']]) { rrf(g, bx, 400 - h, w, h, 3, c); g.fillStyle = 'rgba(0,0,0,.18)'; g.fillRect(bx + 3, 400 - h + 14, w - 6, 5); bx += w + 3; }
    bx = 795; for (const [w, h, c] of [[22, 104, '#3ccf7a'], [26, 120, '#ff9a3c'], [20, 96, '#ff4fd8']]) { rrf(g, bx, 440 - h, w, h, 3, c); bx += w + 3; }
    // potted plant and test tube rack
    rrf(g, 34, 610, 56, 50, 8, '#ff9a3c'); for (const [dx, a] of [[-14, -.5], [0, 0], [14, .5], [-6, -.2], [8, .3]]) el(g, 62 + dx + Math.sin(a) * 26, 602 - Math.cos(a) * 26, 9, 24, a, '#3ccf7a');
    rrf(g, 120, 630, 100, 12, 4, '#4c3044'); ['#ff4fd8', '#3ee6ff', '#ffd23f', '#3ccf7a'].forEach((c, i) => { const tx = 134 + i * 24; rrf(g, tx - 8, 570, 16, 82, 8, 'rgba(200,220,255,.25)'); rrf(g, tx - 6, 610, 12, 40, 6, c); });
    rrf(g, 890, 630, 62, 70, 10, 'rgba(200,220,255,.18)'); el(g, 921, 630, 31, 7, 0, '#8b5cff');
    // atom poster and wall clock
    rrf(g, 50, 748, 170, 166, 14, '#232a6e'); g.strokeStyle = '#ffd23f'; g.lineWidth = 6; g.beginPath(); rrp(g, 50, 748, 170, 166, 14); g.stroke();
    g.strokeStyle = '#3ee6ff'; g.lineWidth = 5; for (const a of [0, Math.PI / 3, -Math.PI / 3]) { g.beginPath(); g.ellipse(135, 831, 60, 20, a, 0, TAU); g.stroke(); }
    ci(g, 135, 831, 11, '#ff4fd8');
    ci(g, 935, 858, 66, '#2b3170'); ci(g, 935, 858, 55, '#e9edf7'); for (let i = 0; i < 12; i++) { const a = i / 12 * TAU; ci(g, 935 + Math.cos(a) * 44, 858 + Math.sin(a) * 44, i % 3 ? 3 : 5, '#0b0f2a'); }
    // counter
    rrf(g, -10, 1172, W + 20, 34, 8, '#3a3170'); g.fillStyle = '#4b418a'; g.fillRect(0, 1172, W, 6); g.fillStyle = '#1b1842'; g.fillRect(0, 1206, W, H - 1206);
    for (let x = 30; x < W; x += 270) { g.strokeStyle = '#2a2560'; g.lineWidth = 4; g.beginPath(); rrp(g, x, 1236, 230, 300, 14); g.stroke(); ci(g, x + 115, 1270, 7, '#2a2560'); }
    // microscope on the left of the counter
    { const mx = 150, my = 1172; rrf(g, mx - 70, my - 22, 140, 22, 8, '#d5dbec'); rrf(g, mx + 10, my - 170, 26, 150, 10, '#e9edf7'); g.save(); g.translate(mx - 10, my - 150); g.rotate(-.5); rrf(g, -16, -60, 32, 110, 10, '#e9edf7'); rrf(g, -10, 46, 20, 24, 4, '#9aa3c4'); rrf(g, -12, -84, 24, 28, 6, '#2b2f4a'); g.restore(); rrf(g, mx - 50, my - 82, 70, 12, 4, '#9aa3c4'); }
    // stand and burner under the big flask
    rrf(g, 900, 1150, 70, 22, 6, '#2b2f4a');
    for (const [k, x, y, s, rgb] of labGlass) glass(g, k, x, y, s, rgb);
  }, t => {
    // bubbles in the glassware
    labGlass.forEach(([k, x, y, s, rgb], j) => {
      const n = s > 150 ? 6 : 3;
      for (let i = 0; i < n; i++) { const p = wrap(t * (.35 + rnd(j * 7 + i) * .3) + rnd(j * 7 + i + 1), 1), lv = k === 'b' ? .55 : .42; al(.7 * (1 - p)); circle(x + (rnd(j * 7 + i + 2) - .5) * s * .3 + Math.sin(t * 3 + i) * 2, y - s * .06 - p * s * (lv - .08), Math.max(1.5, s * .03), '#ffffff'); }
      al(.12 + .08 * Math.sin(t * 2 + j)); glow(x, y - s * .25, s * .7, rgba(rgb, .9), 1);
    });
    // clock hands and a blinking light on the microscope
    al(1); ctx.strokeStyle = '#0b0f2a'; ctx.lineCap = 'round';
    for (const [len, w, a] of [[30, 7, t * TAU / 3600 - 1.2], [42, 5, t * TAU / 60 + .5]]) { ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(935, 858); ctx.lineTo(935 + Math.sin(a) * len, 858 - Math.cos(a) * len); ctx.stroke(); }
    ctx.strokeStyle = '#ff5a5f'; ctx.lineWidth = 2.5; { const a = t * TAU / 60 * 6; ctx.beginPath(); ctx.moveTo(935, 858); ctx.lineTo(935 + Math.sin(a) * 46, 858 - Math.cos(a) * 46); ctx.stroke(); }
    circle(935, 858, 6, '#ff5a5f');
    al(.4 + .6 * (Math.sin(t * 4) > 0)); circle(105, 1162, 6, '#3ccf7a'); al(1);
  });

  // BODY: inside a blood vessel, warm pink tunnel with blood cells floating toward the viewer
  function rbc(x, y, s, rot, sq, a) {
    if (s < .5 || a <= 0) return;
    ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(1, sq); al(a);
    circle(0, 0, s, '#e4323f'); circle(0, 0, s * .6, '#b51d31');
    ctx.strokeStyle = '#ff8a92'; ctx.lineWidth = s * .13; ctx.lineCap = 'round'; ctx.beginPath(); ctx.arc(0, 0, s * .8, Math.PI * 1.05, Math.PI * 1.55); ctx.stroke();
    ctx.restore();
  }
  defBG('body', g => {
    vg(g, [[0, '#2c0413'], [.5, '#470a21'], [1, '#2c0413']]);
    const gr = g.createRadialGradient(540, 760, 150, 540, 760, 1150);
    gr.addColorStop(0, 'rgba(120,10,40,0)'); gr.addColorStop(.35, 'rgba(160,24,56,.28)'); gr.addColorStop(.7, 'rgba(210,62,88,.62)'); gr.addColorStop(1, 'rgba(242,120,132,.9)');
    g.fillStyle = gr; g.fillRect(0, 0, W, H);
    // folds of the vessel wall
    for (let k = 0; k < 9; k++) {
      const R = 150 * Math.pow(1.36, k), a = .03 + k * .02;
      g.lineWidth = R * .06; g.strokeStyle = rgba('255,160,170', a); g.beginPath(); g.ellipse(540, 770, R * .95, R * 1.22, 0, 0, TAU); g.stroke();
      g.lineWidth = R * .03; g.strokeStyle = rgba('90,0,25', a * 1.3); g.beginPath(); g.ellipse(540, 770, R * .95 - R * .05, R * 1.22 - R * .05, 0, 0, TAU); g.stroke();
    }
    // little capillaries at the edges
    g.strokeStyle = 'rgba(120,8,36,.45)'; g.lineCap = 'round';
    for (let i = 0; i < 14; i++) { const a = rnd(i + 700) * TAU, r0 = 640 + rnd(i + 701) * 260; g.lineWidth = 3 + rnd(i + 702) * 4; g.beginPath(); let px = 540 + Math.cos(a) * r0, py = 770 + Math.sin(a) * r0 * 1.25; g.moveTo(px, py); for (let k = 0; k < 5; k++) { px += Math.cos(a + (rnd(i * 9 + k) - .5) * 1.6) * 60; py += Math.sin(a + (rnd(i * 9 + k + 3) - .5) * 1.6) * 70; g.lineTo(px, py); } g.stroke(); }
    gl(g, 540, 770, 420, '24,0,10', .45);
  }, t => {
    // cells drift out of the dark centre toward the viewer: small and faint in the middle, big at the edges
    const N = 30, list = [];
    for (let i = 0; i < N; i++) { const z = wrap(t * .055 + i / N + rnd(i + 720) * .03, 1); list.push([z, i]); }
    list.sort((a, b) => a[0] - b[0]);
    for (const [z, i] of list) {
      const a = rnd(i + 721) * TAU + z * .6, d = lerp(40, 1050, z * z), s = lerp(4, 100, Math.pow(z, 1.6));
      const x = 540 + Math.cos(a) * d * .95, y = 770 + Math.sin(a) * d * 1.22, fade = clamp(z * 2.2) * clamp((1 - z) * 6);
      if (i % 11 === 5) { al(fade * .85); ctx.fillStyle = '#f6d9e6'; ctx.beginPath(); smoothClosed(ctx, blobPts(x, y, s * .9, i, 9, .25, 1)); ctx.fill(); al(fade * .6); circle(x + s * .1, y, s * .35, '#c49ad8'); continue; }
      rbc(x, y, s, rnd(i + 722) * TAU + t * .3 * (rnd(i + 723) - .5), .45 + .45 * Math.abs(Math.sin(t * .4 + i)), fade * .95);
    }
    // a few big out-of-focus cells drifting across the corners
    for (const [x0, y0, s, v, ph] of [[50, 360, 120, 9, 0], [1040, 1190, 130, -7, 2]]) rbc(x0 + Math.sin(t * .2 + ph) * 40, y0 + Math.sin(t * .15 + ph) * v * 5, s, ph + t * .05, .7, .4);
    al(1);
  });

  // CITY: blue-hour skyline, lit windows, blinking aviation lights
  defBG('city', g => {
    vg(g, [[0, '#0a0f36'], [.4, '#211f68'], [.7, '#56308a'], [.88, '#c2577f'], [1, '#ff9a7a']], 0, 1170);
    gl(g, 540, 1180, 640, '255,140,120', .25);
    dust(g, 160, 6, null, 0, 700);
    // far skyline
    for (let x = -20, i = 0; x < W + 20; i++) { const w = 50 + rnd(i + 800) * 60, h = 90 + rnd(i + 801) * 190; rrf(g, x, 1170 - h, w - 4, h + 20, 3, '#3a2d78'); for (let k = 0; k < 6; k++) if (rnd(i * 13 + k) > .55) { g.fillStyle = 'rgba(255,215,140,.35)'; g.fillRect(x + 8 + (k % 2) * (w * .4), 1170 - h + 16 + Math.floor(k / 2) * 26, 8, 10); } x += w; }
    // mid skyline: tall at the edges, low in the middle
    for (let x = -10, i = 0; x < W + 10; i++) {
      const w = 90 + rnd(i + 820) * 70, edge = Math.pow(Math.abs(x + w / 2 - 540) / 540, 1.6), h = 160 + edge * 560 + rnd(i + 821) * 120, top = 1200 - h;
      g.fillStyle = '#231a58'; g.fillRect(x, top, w - 6, h + 40);
      if (rnd(i + 822) > .5) { g.fillRect(x + w * .3, top - 30, w * .3, 30); }
      for (let wy = top + 24; wy < 1180; wy += 34) for (let wx = x + 12; wx < x + w - 24; wx += 24) { const r = rnd(wx * .37 + wy * 1.31); if (r > .62) { g.fillStyle = r > .9 ? '#9ff0ff' : '#ffd36b'; g.globalAlpha = .75; g.fillRect(wx, wy, 11, 15); g.globalAlpha = 1; } }
      x += w;
    }
    // near towers framing the sides
    for (const [x, w, top] of [[-20, 170, 360], [130, 120, 560], [820, 130, 520], [930, 170, 400]]) {
      g.fillStyle = '#140f3a'; g.fillRect(x, top, w, H - top);
      g.fillRect(x + w / 2 - 3, top - 80, 6, 80);
      for (let wy = top + 30; wy < 1300; wy += 44) for (let wx = x + 16; wx < x + w - 26; wx += 32) { const r = rnd(wx * .71 + wy * .53); g.fillStyle = r > .55 ? 'rgba(255,211,107,.9)' : 'rgba(80,70,150,.5)'; g.fillRect(wx, wy, 16, 22); }
    }
    // street
    g.fillStyle = '#0d0a2a'; g.fillRect(0, 1240, W, H - 1240); g.fillStyle = '#2a2266'; g.fillRect(0, 1236, W, 6);
  }, t => {
    twinkle(t, 50, .8, 0, 650, 6);
    // aviation lights
    for (const [x, y, ph] of [[60, 280, 0], [190, 480, 1.1], [885, 440, 2], [1015, 320, .5]]) { const on = Math.sin(t * 2.4 + ph * 2) > .55; if (on) { al(.6); glow(x, y, 26, 'rgba(255,60,60,1)', 1); al(1); circle(x, y, 5, '#ff4a4a'); } else { al(.5); circle(x, y, 4, '#7a2030'); al(1); } }
    // car lights sliding along the street
    for (let i = 0; i < 6; i++) { const dir = i % 2 ? 1 : -1, x = wrap(rnd(i + 860) * W + dir * t * (70 + rnd(i + 861) * 40), W + 120) - 60, y = 1258 + (i % 2) * 14; al(.85); ctx.fillStyle = dir > 0 ? '#fff4c8' : '#ff5a5f'; rr(x - 9, y - 3, 18, 6, 3); ctx.fill(); al(.35); glow(x, y, 30, dir > 0 ? 'rgba(255,240,190,1)' : 'rgba(255,80,90,1)', 1); }
    al(1);
  });

  // VOLCANO: smouldering sky, a wide cone with lava rivers, plume and embers
  const lava = [[[742, 1004], [726, 1060], [690, 1112], [662, 1170], [612, 1232], [574, 1300]], [[800, 1004], [826, 1066], [872, 1128], [896, 1196], [946, 1268], [968, 1320]]];
  defBG('volcano', g => {
    vg(g, [[0, '#120820'], [.42, '#2c0f2e'], [.72, '#651b2e'], [1, '#bb3c2c']], 0, 1150);
    gl(g, 770, 1000, 620, '255,110,40', .3);
    dust(g, 90, 7, null, 0, 600);
    // distant volcano
    g.fillStyle = '#3a1430'; g.beginPath(); g.moveTo(-40, 1150); g.quadraticCurveTo(150, 1000, 200, 960); g.lineTo(250, 960); g.quadraticCurveTo(310, 1010, 470, 1150); g.fill();
    ridge(g, '#2e1026', x => 1145 + Math.sin(x * .008 + 1) * 10 + nz(x * .015, 14) * 22);
    // main cone
    g.fillStyle = '#2a0f20'; g.beginPath(); g.moveTo(60, 1340); g.quadraticCurveTo(470, 1170, 682, 1006); g.quadraticCurveTo(700, 990, 720, 994); g.lineTo(830, 994); g.quadraticCurveTo(852, 990, 866, 1006); g.quadraticCurveTo(1010, 1150, 1200, 1250); g.lineTo(1200, H); g.lineTo(60, H); g.fill();
    // crater glow
    gl(g, 776, 1000, 170, '255,170,60', .5); el(g, 776, 1000, 62, 12, 0, '#ffb347'); el(g, 776, 1000, 40, 7, 0, '#fff0a0');
    // lava rivers
    g.lineCap = 'round'; g.lineJoin = 'round';
    for (const P of lava) {
      for (const [px, py] of P) gl(g, px, py, 60, '255,120,40', .22);
      g.strokeStyle = '#ff5a1a'; g.lineWidth = 18; g.beginPath(); smoothOpen(g, P); g.stroke();
      g.strokeStyle = '#ffb03a'; g.lineWidth = 8; g.beginPath(); smoothOpen(g, P); g.stroke();
    }
    // dark foreground rocks with a lava pool
    jagged(g, '#170812', x => 1350 + nz(x * .02, 11) * 60 + Math.sin(x * .004) * 30);
    gl(g, 330, 1440, 180, '255,100,40', .25); el(g, 330, 1440, 120, 20, 0, '#ff6a1a'); el(g, 330, 1438, 80, 10, 0, '#ffb347');
  }, t => {
    // crater pulse
    al(.25 + .12 * Math.sin(t * 2.1)); glow(776, 996, 150, 'rgba(255,190,90,1)', 1);
    // lava flowing down the rivers
    ctx.setLineDash([16, 30]); ctx.lineDashOffset = -t * 34; ctx.strokeStyle = '#fff1a0'; ctx.lineWidth = 4; ctx.lineCap = 'round'; al(.75);
    for (const P of lava) { ctx.beginPath(); smoothOpen(ctx, P); ctx.stroke(); }
    ctx.setLineDash([]);
    // smoke plume drifting up and to the right
    for (let i = 13; i >= 0; i--) {
      const p = wrap(t * .04 + i / 14, 1), x = 776 + p * 260 + Math.sin(p * 6 + i) * 24 * p, y = 980 - p * 820, r = 34 + p * 150;
      al((1 - p) * .55 * clamp(p * 8)); ctx.fillStyle = i % 2 ? '#3a2434' : '#45293a'; ctx.beginPath(); smoothClosed(ctx, blobPts(x, y, r, i, 9, .3, .9)); ctx.fill();
    }
    // embers
    for (let i = 0; i < 40; i++) {
      const p = wrap(t * (.1 + rnd(i + 900) * .08) + rnd(i + 901), 1), side = i % 4 === 0;
      const x = side ? rnd(i + 902) * W : 776 + (rnd(i + 902) - .5) * 220 + p * 140 + Math.sin(t * 2 + i) * 14, y = side ? 1400 - p * 600 : 990 - p * 650;
      al((1 - p) * (.6 + .4 * Math.sin(t * 9 + i))); circle(x, y, 2 + rnd(i + 903) * 2.5, i % 3 ? '#ffb347' : '#ffe58a');
    }
    al(1);
  });

  // STORM: heavy clouds, rain, lightning over dark hills with one warm window
  defBG('storm', g => {
    vg(g, [[0, '#0a0d1f'], [.45, '#1a2040'], [.8, '#283252'], [1, '#384264']], 0, 1150);
    gl(g, 540, 1150, 620, '140,160,210', .12);
    // a heavy cloud ceiling with a scalloped underside, hanging lower at the sides
    for (const [col, rim, y0, amp, seed] of [['#2f3860', '#3d4775', 300, 250, 1], ['#232a4c', '#2f3760', 230, 230, 2], ['#181d3c', '#222a50', 150, 210, 3]]) {
      const yb = x => y0 + Math.pow(Math.abs(x - 540) / 540, 2) * amp + nz(x * .008, seed) * 60;
      for (const [c, dy] of [[rim, 9], [col, 0]]) {
        g.fillStyle = c; g.beginPath(); g.moveTo(-90, -20); g.lineTo(W + 90, -20); let x = W + 90; g.lineTo(x, yb(x) + dy);
        while (x > -90) { const w = 80 + rnd(x * .37 + seed) * 70, nx = x - w; g.quadraticCurveTo(x - w / 2, Math.max(yb(x), yb(nx)) + w * .5 + dy, nx, yb(nx) + dy); x = nx; }
        g.closePath(); g.fill();
      }
    }
    ridge(g, '#1a2038', x => 1080 + Math.sin(x * .005 + 1.5) * 40 + nz(x * .012, 12) * 30);
    ridge(g, '#11152a', x => 1190 + Math.sin(x * .0045 + 3.5) * 50);
    // cottage with a warm window on the left, wind-bent trees on the right
    { const hx = 190, hy = 1180; g.fillStyle = '#0b0e20'; g.fillRect(hx - 60, hy - 70, 120, 90); poly(g, [[hx - 72, hy - 68], [hx, hy - 120], [hx + 72, hy - 68]], '#0b0e20'); gl(g, hx + 20, hy - 40, 70, '255,200,100', .4); rrf(g, hx + 6, hy - 52, 28, 26, 4, '#ffcf6b'); }
    for (const [x, s] of [[860, 150], [960, 120]]) { const y = 1210; g.strokeStyle = '#0b0e20'; g.lineWidth = s * .09; g.lineCap = 'round'; g.beginPath(); g.moveTo(x, y); g.quadraticCurveTo(x + s * .05, y - s * .5, x + s * .3, y - s * .8); g.stroke(); g.fillStyle = '#0b0e20'; g.beginPath(); g.ellipse(x + s * .38, y - s * .82, s * .42, s * .22, -.35, 0, TAU); g.fill(); }
    ridge(g, '#0a0d1e', x => 1370 + Math.sin(x * .006 + .7) * 40);
  }, t => {
    // lighter cloud puffs sliding along the cloud base
    for (let i = 0; i < 8; i++) { const x = wrap(rnd(i + 950) * W + t * (12 + rnd(i + 951) * 10), W + 400) - 200, e = Math.min(1, Math.pow(Math.abs(x - 540) / 540, 2)), y = 230 + e * 220 + rnd(i + 952) * 40; al(.4); ctx.fillStyle = i % 2 ? '#2a3359' : '#222a4c'; ctx.beginPath(); smoothClosed(ctx, blobPts(x, y, 80 + rnd(i + 953) * 40, i + 30, 9, .3, .55)); ctx.fill(); }
    // lightning every few seconds, alternating sides
    const P = 5.3, tt = t + 2.6, k = Math.floor(tt / P), p = tt - k * P; // first strike at t = 2.7 s, not on the opening frame
    if (p < .55) {
      const f = Math.max(0, 1 - p / .12) + .7 * Math.max(0, 1 - Math.abs(p - .25) / .08);
      if (f > 0) {
        al(f * .16); ctx.fillStyle = '#c8d6ff'; ctx.fillRect(0, 0, W, H);
        const sx = (k % 2 ? 900 : 160) + (rnd(k + 960) - .5) * 120; let x = sx, y = 400; const pts = [[x, y]];
        while (y < 1080) { y += 60 + rnd(k * 31 + y) * 50; x += (rnd(k * 17 + y) - .5) * 110; pts.push([x, y]); }
        ctx.lineJoin = 'round'; ctx.lineCap = 'round';
        for (const [lw, c, a] of [[26, '#9fb8ff', .25], [11, '#dfe7ff', .8], [4.5, '#ffffff', 1]]) { al(f * a); ctx.strokeStyle = c; ctx.lineWidth = lw; ctx.beginPath(); pts.forEach(([px, py], i) => i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)); ctx.moveTo(pts[3][0], pts[3][1]); ctx.lineTo(pts[3][0] + 70 * (k % 2 ? -1 : 1), pts[3][1] + 120); ctx.stroke(); }
      }
    }
    // rain
    ctx.strokeStyle = '#a9c3ff'; ctx.lineWidth = 2.2; ctx.lineCap = 'round'; al(.24); ctx.beginPath();
    for (let i = 0; i < 160; i++) { const v = 650 + rnd(i + 970) * 300, x = wrap(rnd(i + 971) * W - t * v * .22, W + 60) - 30, y = wrap(rnd(i + 972) * H + t * v, H + 80) - 40; ctx.moveTo(x, y); ctx.lineTo(x + 11, y - 44); }
    ctx.stroke(); al(1);
  });

  // =====================================================================================
  // BODIES
  const BODY = {};
  function defBody(name, fn) {
    BODY[name] = function (x, y, r, t, opt) {
      if (!(r >= .5) || !isFinite(x) || !isFinite(y)) return;
      opt = opt || {};
      ctx.save(); const pa = BA;
      try {
        if (opt.alpha != null) { if (!(opt.alpha > 0)) return; ctx.globalAlpha *= opt.alpha; }
        BA = ctx.globalAlpha;
        ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.filter = 'none'; ctx.setLineDash([]);
        fn(x, y, r, +t || 0, opt);
      } finally { BA = pa; ctx.restore(); }
    };
  }
  const disc = (x, y, r) => { ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); };
  const lw = (r, k, lo = .6, hi = 6) => clamp(r * k, lo, hi);
  // Big soft gradients are the slow part here (~4 ns per pixel in software canvas), so large glows, shading
  // and fills come from 512 px sprites painted once and blitted without smoothing (~0.8 ns per pixel).
  // Their content is smooth, so the nearest-neighbour blocks don't show; a disc clip keeps rims antialiased.
  const sprites = new Map();
  function sprite(key, paint) {
    let c = sprites.get(key);
    if (!c) { c = document.createElement('canvas'); c.width = c.height = 512; paint(c.getContext('2d'), 256); sprites.set(key, c); }
    return c;
  }
  function blit(c, x, y, w, h) { ctx.imageSmoothingEnabled = false; ctx.drawImage(c, x, y, w, h); ctx.imageSmoothingEnabled = true; }
  const BIG = 160; // radius above which a sprite is cheaper than a gradient
  function ringG(g, x, y, r0, r1, stops) {
    const gr = g.createRadialGradient(x, y, r0, x, y, r1); for (const [p, c] of stops) gr.addColorStop(p, c);
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r1, 0, TAU); g.arc(x, y, r0 * .97, 0, TAU, true); g.fill();
  }
  function bring(name, x, y, r0, r1, stops) {
    if (r1 < BIG * 1.4) return ringG(ctx, x, y, r0, r1, stops);
    const k = Math.round(r0 / r1 * 400) / 400;
    blit(sprite(`r${name}|${k}`, (g, R) => ringG(g, R, R, R * k, R, stops)), x - r1, y - r1, 2 * r1, 2 * r1);
  }
  function bhalo(x, y, r0, r1, rgb, a) {
    if (!(r1 > r0) || !(a > 0)) return;
    if (r1 < BIG * 1.4) return halo(ctx, x, y, r0, r1, rgb, a);
    const k = Math.round(r0 / r1 * 400) / 400;
    blit(sprite(`h${rgb}|${a}|${k}`, (g, R) => halo(g, R, R, R * k, R, rgb, a)), x - r1, y - r1, 2 * r1, 2 * r1);
  }
  function bglow(x, y, r, rgb, a) {
    if (r < BIG * 1.4) return gl(ctx, x, y, r, rgb, a);
    blit(sprite(`g${rgb}|${a}`, (g, R) => gl(g, R, R, R, rgb, a)), x - r, y - r, 2 * r, 2 * r);
  }
  // fill the square x-r..x+r with a gradient made by mk(g, cx, cy, R); call with a disc clip active
  function gfill(key, x, y, r, mk) {
    if (r < BIG) { ctx.fillStyle = mk(ctx, x, y, r); ctx.fillRect(x - r, y - r, 2 * r, 2 * r); return; }
    blit(sprite(key, (g, R) => { g.fillStyle = mk(g, R, R, R); g.fillRect(0, 0, 2 * R, 2 * R); }), x - r, y - r, 2 * r, 2 * r);
  }
  // light from the upper left, night side lower right (same lighting as the pilot)
  function shade(x, y, r, hi = .22, dark = .62) {
    gfill(`s${hi}|${dark}`, x, y, r, (g, cx, cy, R) => {
      const gr = g.createRadialGradient(cx - R * .42, cy - R * .45, 0, cx - R * .42, cy - R * .45, R * 2);
      gr.addColorStop(0, `rgba(255,255,255,${hi})`); gr.addColorStop(.3, 'rgba(255,255,255,0)'); gr.addColorStop(.5, 'rgba(6,8,30,0)'); gr.addColorStop(.84, `rgba(6,8,30,${dark})`); gr.addColorStop(1, `rgba(6,8,30,${dark})`);
      return gr;
    });
  }
  // a shaded ball: base colour, surface painter clipped to the disc, lighting, rim
  function planet(x, y, r, o) {
    if (o.halo) bhalo(x, y, r, r * (o.haloK || 1.4), o.halo, o.haloA ?? .22);
    ctx.save(); disc(x, y, r); ctx.fillStyle = o.base; ctx.fill(); ctx.clip();
    if (o.paint) o.paint();
    shade(x, y, r, o.hi ?? .22, o.dark ?? .62);
    ctx.restore();
    if (o.rim) { ctx.strokeStyle = o.rim; ctx.lineWidth = lw(r, .014, .5, 4); disc(x, y, r); ctx.stroke(); }
  }
  // foreshortened craters: darker bowl plus a lit lower-right wall
  function craters(x, y, r, n, seed, dark, light, kmin = .04, kmax = .16) {
    for (let i = 0; i < n; i++) {
      const a = rnd(seed * 31 + i) * TAU, d = Math.sqrt(rnd(seed * 31 + i + .5)) * .86 * r, cr = r * (kmin + Math.pow(rnd(seed * 31 + i + .7), 2) * (kmax - kmin));
      const cx = x + Math.cos(a) * d, cy = y + Math.sin(a) * d, f = Math.max(.3, Math.sqrt(1 - Math.pow(d / r, 2)));
      ctx.fillStyle = dark; ctx.beginPath(); ctx.ellipse(cx, cy, cr * f, cr, a, 0, TAU); ctx.fill();
      if (light && cr > 2) { const m = Math.PI / 4 - a; ctx.strokeStyle = light; ctx.lineWidth = cr * .2; ctx.beginPath(); ctx.ellipse(cx, cy, cr * f * .85, cr * .85, a, m - 1, m + 1); ctx.stroke(); }
    }
  }
  // soft flame-like corona that slowly breathes and turns
  function corona(x, y, r, t, n, k, rgb, a, ph) {
    const P = [];
    for (let i = 0; i < n * 2; i++) { const ang = i / (n * 2) * TAU + t * .05 + ph, rr2 = i % 2 ? r * 1.02 : r * (k + .08 * Math.sin(t * 1.4 + i * 1.7 + ph)); P.push([x + Math.cos(ang) * rr2, y + Math.sin(ang) * rr2]); }
    ctx.fillStyle = rgba(rgb, a); ctx.beginPath(); smoothClosed(ctx, P); ctx.fill();
  }
  // half of a flat ring system (back = far half, front = near half), in a frame rotated by tilt and squashed by open
  function halfRing(R0, R1, front) { const a0 = front ? 0 : Math.PI, a1 = a0 + Math.PI; ctx.beginPath(); ctx.arc(0, 0, R1, a0, a1); ctx.arc(0, 0, R0, a1, a0, true); ctx.closePath(); }
  function rings(x, y, r, tilt, open, bands, front) {
    ctx.save(); ctx.translate(x, y); ctx.rotate(tilt); ctx.scale(1, open);
    for (const [a, b, c] of bands) { ctx.fillStyle = c; halfRing(a * r, b * r, front); ctx.fill(); }
    ctx.restore();
  }

  defBody('earth', (x, y, r, t, o) => {
    const tilt = o.tilt ?? .41;
    bhalo(x, y, r, r * 1.6, '62,180,255', .3);
    ctx.save(); disc(x, y, r); ctx.fillStyle = '#1546a8'; ctx.fill(); ctx.clip();
    gfill('earthSea', x, y, r, (g, cx, cy, R) => { const gr = g.createRadialGradient(cx - R * .35, cy - R * .35, R * .1, cx, cy, R); gr.addColorStop(0, '#4aa3ff'); gr.addColorStop(1, '#1546a8'); return gr; });
    ctx.translate(x, y); ctx.rotate(tilt);
    // drifting continents (period 2r so they wrap without popping); each layer is one path
    const off = wrap(t * r * .095, r * 2), land = [], dark = [];
    for (let k = -1; k <= 1; k++) {
      const ox = k * r * 2 - off + r;
      for (const [dx, dy, s, seed] of [[-.3, -.35, .45, 1], [.35, .15, .38, 2], [-.1, .55, .25, 3]]) {
        const bx = ox + dx * r, by = dy * r, br = s * r; if (Math.abs(bx) - br * 1.2 > r) continue;
        land.push(blobPts(bx, by, br, seed)); if (r > 40) dark.push(blobPts(bx + br * .12, by + br * .1, br * .45, seed + 7));
      }
    }
    ctx.beginPath(); for (const P of land) smoothClosed(ctx, P); ctx.fillStyle = C.land; ctx.fill();
    if (dark.length) { ctx.beginPath(); for (const P of dark) smoothClosed(ctx, P); ctx.fillStyle = 'rgba(30,150,85,.45)'; ctx.fill(); }
    // polar ice
    ctx.beginPath(); ctx.ellipse(0, -r * 1.02, r * .52, r * .2, 0, 0, TAU); ctx.moveTo(r * .42, r * 1.03); ctx.ellipse(0, r * 1.03, r * .42, r * .15, 0, 0, TAU); ctx.fillStyle = 'rgba(240,248,255,.92)'; ctx.fill();
    // clouds
    ctx.beginPath();
    for (let k = 0; k < 6; k++) { const cx = wrap(rnd(k) * r * 2 + t * r * .152, r * 3) - r * 1.8, cy = (rnd(k + 4) - .5) * r * 1.6; rrp(ctx, cx, cy, r * (.25 + rnd(k + 8) * .3), r * .07, r * .035); }
    ctx.fillStyle = 'rgba(255,255,255,.75)'; ctx.fill();
    // night side (lit from the left, like the pilot)
    ctx.rotate(-tilt); ctx.translate(-x, -y);
    gfill('earthNight', x, y, r, (g, cx, cy, R) => { const gr = g.createLinearGradient(cx - R, 0, cx + R, 0); gr.addColorStop(.45, 'rgba(5,8,30,0)'); gr.addColorStop(1, 'rgba(5,8,30,.6)'); return gr; });
    ctx.restore();
    ctx.strokeStyle = 'rgba(160,220,255,.6)'; ctx.lineWidth = lw(r, .0175, .6, 5); disc(x, y, r); ctx.stroke();
  });

  defBody('moon', (x, y, r) => {
    bhalo(x, y, r, r * 2.2, '230,235,255', .22);
    ctx.save(); disc(x, y, r); ctx.fillStyle = '#a9aec4'; ctx.fill(); ctx.clip();
    gfill('moon', x, y, r, (g, cx, cy, R) => { const gr = g.createRadialGradient(cx - R * .3, cy - R * .3, R * .1, cx, cy, R); gr.addColorStop(0, '#f4f5fb'); gr.addColorStop(1, '#a9aec4'); return gr; });
    ctx.restore();
    for (const [dx, dy, k] of [[-.35, -.2, .22], [.3, .25, .16], [.1, -.45, .12], [-.2, .4, .1], [.45, -.2, .09]]) {
      ctx.fillStyle = 'rgba(120,125,150,.45)'; disc(x + dx * r, y + dy * r, k * r); ctx.fill();
      if (r > 30) { ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = k * r * .16; ctx.beginPath(); ctx.arc(x + dx * r, y + dy * r, k * r * .9, -.2, 1.9); ctx.stroke(); }
    }
  });

  defBody('sun', (x, y, r, t) => {
    bhalo(x, y, r, r * 3.4, '255,180,60', .5);
    bhalo(x, y, r, r * 1.8, '255,232,140', .55);
    corona(x, y, r, t, 14, 1.34, '255,206,84', .3, 0);
    corona(x, y, r, -t * .8, 11, 1.17, '255,236,160', .45, 1.7);
    ctx.save(); disc(x, y, r); ctx.fillStyle = '#ffc23a'; ctx.fill(); ctx.clip();
    gfill('sun', x, y, r, (g, cx, cy, R) => { const gr = g.createRadialGradient(cx - R * .3, cy - R * .3, R * .05, cx, cy, R); gr.addColorStop(0, '#fffbe0'); gr.addColorStop(.45, '#ffe25a'); gr.addColorStop(.82, '#ffc23a'); gr.addColorStop(1, '#ffa23c'); return gr; });
    ctx.restore();
    if (r > 20) { ctx.strokeStyle = 'rgba(255,255,255,.45)'; ctx.lineWidth = r * .05; ctx.lineCap = 'round'; ctx.beginPath(); ctx.arc(x, y, r * .78, Math.PI * 1.1, Math.PI * 1.4); ctx.stroke(); }
  });

  defBody('mercury', (x, y, r) => planet(x, y, r, {
    base: '#a69c94', halo: '210,200,190', haloA: .1, haloK: 1.3, paint: () => {
      sblob(ctx, x - r * .3, y + r * .1, r * .5, 21, 'rgba(120,108,100,.4)', 11, .4, .8); sblob(ctx, x + r * .4, y - r * .35, r * .35, 22, 'rgba(190,180,170,.35)');
      craters(x, y, r, 16, 4, 'rgba(84,74,68,.45)', 'rgba(255,250,240,.3)');
    }
  }));

  defBody('venus', (x, y, r, t) => planet(x, y, r, {
    base: '#e7bf78', halo: '255,214,150', haloA: .3, haloK: 1.45, hi: .26, paint: () => {
      ctx.lineCap = 'round';
      for (let k = 0; k < 7; k++) {
        const oy = (k / 6 - .5) * r * 1.7; ctx.strokeStyle = k % 2 ? 'rgba(255,238,196,.45)' : 'rgba(205,150,80,.32)'; ctx.lineWidth = r * (.1 + rnd(k + 60) * .08);
        ctx.beginPath(); for (let i = 0; i <= 8; i++) { const px = x - r * 1.1 + i * r * .275; ctx.lineTo(px, y + oy + Math.sin(i * .9 + k * 1.7 + t * .12) * r * .07); } ctx.stroke();
      }
    }
  }));

  defBody('mars', (x, y, r) => planet(x, y, r, {
    base: '#d4623b', halo: '255,150,110', haloA: .16, haloK: 1.25, paint: () => {
      sblob(ctx, x - r * .28, y + r * .1, r * .5, 31, 'rgba(130,42,28,.45)', 11, .45, .6);
      sblob(ctx, x + r * .42, y + r * .38, r * .32, 32, 'rgba(130,42,28,.4)'); sblob(ctx, x + r * .15, y - r * .38, r * .22, 33, 'rgba(240,150,100,.35)');
      ctx.strokeStyle = 'rgba(110,32,22,.5)'; ctx.lineWidth = lw(r, .022, .4, 14); ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(x - r * .55, y - r * .02); ctx.quadraticCurveTo(x - r * .1, y + r * .12, x + r * .35, y + r * .06); ctx.stroke();
      craters(x, y, r, 7, 9, 'rgba(120,40,26,.35)', 'rgba(255,200,170,.25)', .03, .09);
      el(ctx, x - r * .08, y - r * .93, r * .46, r * .17, -.1, '#fbf1ec'); el(ctx, x + r * .1, y + r * .97, r * .32, r * .1, 0, 'rgba(251,241,236,.85)');
    }
  }));

  defBody('jupiter', (x, y, r, t) => planet(x, y, r, {
    base: '#ecd6b2', halo: '255,220,170', haloA: .14, haloK: 1.3, hi: .2, paint: () => {
      const bands = [[-1.05, -.78, '#c9a07a'], [-.62, -.48, '#bd8a58'], [-.36, -.13, '#c4834e'], [.05, .18, '#d8a571'], [.27, .47, '#b9774a'], [.6, .72, '#c99d72'], [.84, 1.05, '#b88d68']];
      bands.forEach(([a, b, c], k) => {
        ctx.fillStyle = c; ctx.beginPath(); const ph = t * .25 * (k % 2 ? 1 : -1) + k;
        for (let i = 0; i <= 12; i++) { const px = x - r + i * r / 6; ctx.lineTo(px, y + a * r + Math.sin(i * 1.3 + ph) * r * .018); }
        for (let i = 12; i >= 0; i--) { const px = x - r + i * r / 6; ctx.lineTo(px, y + b * r + Math.sin(i * 1.1 + ph + 2) * r * .018); }
        ctx.fill();
      });
      ctx.strokeStyle = 'rgba(255,248,230,.45)'; ctx.lineWidth = lw(r, .014, .4, 8);
      for (const f of [-.7, -.05, .55]) { ctx.beginPath(); ctx.moveTo(x - r, y + f * r); ctx.bezierCurveTo(x - r * .3, y + f * r - r * .03, x + r * .3, y + f * r + r * .03, x + r, y + f * r); ctx.stroke(); }
      const sx = x + r * (.26 + .05 * Math.sin(t * .15)), sy = y + r * .36;
      el(ctx, sx, sy, r * .27, r * .15, 0, '#e8b48e'); el(ctx, sx, sy, r * .21, r * .11, 0, '#c4513a'); el(ctx, sx - r * .03, sy - r * .01, r * .11, r * .055, 0, '#d9705a');
    }
  }));

  defBody('saturn', (x, y, r, t, o) => {
    const tilt = o.tilt ?? -.38, open = o.open ?? .3;
    const B = [[1.22, 1.36, 'rgba(205,185,145,.35)'], [1.36, 1.72, '#e8d3a2'], [1.72, 1.8, 'rgba(60,45,35,.3)'], [1.8, 2.06, '#d4b680'], [2.06, 2.16, 'rgba(212,182,128,.5)']];
    bhalo(x, y, r, r * 1.35, '255,230,170', .12);
    rings(x, y, r, tilt, open, B, false);
    planet(x, y, r, {
      base: '#ebd49d', hi: .24, paint: () => {
        ctx.save(); ctx.translate(x, y); ctx.rotate(tilt);
        for (const [a, b, c] of [[-1.05, -.7, '#d9b77d'], [-.45, -.3, '#e2c48e'], [-.1, .06, '#d6b47c'], [.3, .5, '#cfa96f'], [.72, 1.05, '#c7a06a']]) { ctx.fillStyle = c; ctx.fillRect(-r * 1.1, a * r, r * 2.2, (b - a) * r); }
        // shadow of the rings across the globe
        ctx.scale(1, open); ctx.strokeStyle = 'rgba(40,30,20,.28)'; ctx.lineWidth = r * .35; ctx.beginPath(); ctx.arc(0, r * .32, r * 1.55, 0, Math.PI); ctx.stroke();
        ctx.restore();
      }
    });
    rings(x, y, r, tilt, open, B, true);
  });

  defBody('uranus', (x, y, r, t, o) => {
    const tilt = o.tilt ?? 1.32, open = o.open ?? .24, B = [[1.5, 1.54, 'rgba(210,245,255,.45)'], [1.62, 1.7, 'rgba(210,245,255,.6)'], [1.78, 1.8, 'rgba(210,245,255,.35)']];
    rings(x, y, r, tilt, open, B, false);
    planet(x, y, r, {
      base: '#a5e4ea', halo: '160,240,255', haloA: .2, haloK: 1.35, hi: .3, dark: .55, paint: () => {
        ctx.save(); ctx.translate(x, y); ctx.rotate(tilt - Math.PI / 2);
        for (const [a, b, c] of [[-.6, -.45, 'rgba(255,255,255,.14)'], [-.1, .1, 'rgba(70,170,190,.14)'], [.4, .55, 'rgba(255,255,255,.12)']]) { ctx.fillStyle = c; ctx.fillRect(-r * 1.1, a * r, r * 2.2, (b - a) * r); }
        ctx.restore(); bglow(x + Math.cos(tilt - Math.PI / 2) * r * .2, y + Math.sin(tilt - Math.PI / 2) * r * .2, r * .6, '230,255,255', .25);
      }
    });
    rings(x, y, r, tilt, open, B, true);
  });

  defBody('neptune', (x, y, r, t) => planet(x, y, r, {
    base: '#3a6be2', halo: '90,150,255', haloA: .26, haloK: 1.35, paint: () => {
      for (const [a, b, c] of [[-.75, -.55, 'rgba(110,160,255,.4)'], [-.25, -.1, 'rgba(30,60,170,.35)'], [.35, .55, 'rgba(110,160,255,.3)'], [.75, .9, 'rgba(30,60,170,.35)']]) { ctx.fillStyle = c; ctx.fillRect(x - r, y + a * r, 2 * r, (b - a) * r); }
      const sx = x - r * (.18 + .04 * Math.sin(t * .2));
      el(ctx, sx, y + r * .18, r * .2, r * .11, -.1, '#21409c');
      for (const [dx, dy, w] of [[-.12, .36, .3], [.25, -.28, .34], [.05, -.42, .18]]) el(ctx, sx + (dx + .18) * r, y + dy * r, w * r / 2, Math.max(.6, r * .026), -.06, 'rgba(240,248,255,.85)');
    }
  }));

  defBody('pluto', (x, y, r) => planet(x, y, r, {
    base: '#d6b590', halo: '230,210,190', haloA: .1, haloK: 1.25, paint: () => {
      sblob(ctx, x - r * .5, y + r * .25, r * .55, 41, 'rgba(105,64,44,.6)', 11, .4, .55);
      sblob(ctx, x + r * .55, y - r * .45, r * .3, 42, 'rgba(150,100,70,.35)');
      craters(x, y, r, 6, 12, 'rgba(110,80,60,.3)', null, .03, .07);
      // Tombaugh Regio, the heart
      ctx.save(); ctx.translate(x + r * .22, y + r * .12); ctx.rotate(-.25); const s = r * .78;
      ctx.fillStyle = '#f5e9d6'; ctx.beginPath(); ctx.moveTo(0, s * .42);
      ctx.bezierCurveTo(-s * .2, s * .26, -s * .58, s * .02, -s * .5, -s * .22); ctx.bezierCurveTo(-s * .42, -s * .44, -s * .1, -s * .46, 0, -s * .24);
      ctx.bezierCurveTo(s * .08, -s * .44, s * .4, -s * .46, s * .48, -s * .22); ctx.bezierCurveTo(s * .56, s * .02, s * .2, s * .26, 0, s * .42); ctx.fill(); ctx.restore();
    }
  }));

  defBody('blackhole', (x, y, r, t, o) => {
    const tilt = o.tilt ?? -.16, open = o.open ?? .26;
    bhalo(x, y, r, r * 3.4, '255,110,40', .32);
    bhalo(x, y, r, r * 2.1, '255,180,90', .45);
    const diskHalf = front => {
      ctx.save(); ctx.translate(x, y); ctx.rotate(tilt); ctx.scale(1, open);
      const gr = ctx.createRadialGradient(0, 0, r * 1.25, 0, 0, r * 2.7);
      gr.addColorStop(0, '#fff7d8'); gr.addColorStop(.12, '#ffd27a'); gr.addColorStop(.38, '#ff9a3c'); gr.addColorStop(.7, 'rgba(255,90,30,.5)'); gr.addColorStop(1, 'rgba(255,60,20,0)');
      ctx.fillStyle = gr; halfRing(r * 1.25, r * 2.7, front); ctx.fill();
      // swirling streaks
      ctx.lineCap = 'round'; const a0 = front ? 0 : Math.PI;
      for (let k = 0; k < 4; k++) { const R = r * (1.45 + k * .35); ctx.setLineDash([R * .5, R * .7]); ctx.lineDashOffset = -t * r * (1.6 - k * .25) + (front ? 0 : R * Math.PI); ctx.strokeStyle = `rgba(255,246,220,${.42 - k * .08})`; ctx.lineWidth = r * .07; ctx.beginPath(); ctx.arc(0, 0, R, a0, a0 + Math.PI); ctx.stroke(); }
      ctx.setLineDash([]);
      // the approaching (left) side is brighter
      if (front) { ctx.fillStyle = 'rgba(255,250,225,.22)'; ctx.beginPath(); ctx.ellipse(-r * 1.95, 0, r * .75, r * .9, 0, 0, TAU); ctx.fill(); }
      ctx.restore();
    };
    diskHalf(false);
    // light from the far side of the disc bent around the shadow
    bring('lens', x, y, r, r * 1.6, [[0, '#fff3cf'], [.2, 'rgba(255,190,90,.9)'], [.55, 'rgba(255,120,40,.35)'], [1, 'rgba(255,90,30,0)']]);
    ctx.fillStyle = '#000'; disc(x, y, r); ctx.fill();
    ctx.strokeStyle = 'rgba(255,244,214,.95)'; ctx.lineWidth = lw(r, .035, .6, 8); disc(x, y, r * 1.03); ctx.stroke();
    diskHalf(true);
  });

  defBody('asteroid', (x, y, r, t, o) => {
    const seed = o.seed ?? 1, rot = (o.spin === false ? 0 : t * .25) + seed * 1.7;
    const P = []; for (let i = 0; i < 13; i++) { const a = i / 13 * TAU, k = .74 + rnd(seed * 17 + i) * .3 + Math.sin(a * 2 + seed) * .06; P.push([Math.cos(a) * r * k, Math.sin(a) * r * k * .86]); }
    ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
    ctx.beginPath(); smoothClosed(ctx, P); ctx.fillStyle = '#8c7c6e'; ctx.fill(); ctx.clip();
    sblob(ctx, r * .2, r * .15, r * .45, seed + 50, 'rgba(110,96,84,.6)');
    craters(0, 0, r * .85, 6, seed + 3, 'rgba(70,58,50,.55)', 'rgba(255,240,220,.3)', .07, .2);
    for (let i = 0; i < 10; i++) circle((rnd(seed + i + 60) - .5) * r * 1.4, (rnd(seed + i + 61) - .5) * r * 1.2, Math.max(.4, r * .025), 'rgba(200,185,170,.5)');
    ctx.rotate(-rot);
    gfill('rock', 0, 0, r * 1.05, (g, cx, cy, R) => { const gr = g.createRadialGradient(cx - R * .43, cy - R * .48, 0, cx - R * .43, cy - R * .48, R * 1.8); gr.addColorStop(0, 'rgba(255,245,230,.28)'); gr.addColorStop(.3, 'rgba(255,245,230,0)'); gr.addColorStop(.52, 'rgba(6,8,30,0)'); gr.addColorStop(.9, 'rgba(6,8,30,.65)'); gr.addColorStop(1, 'rgba(6,8,30,.65)'); return gr; });
    ctx.restore();
  });

  defBody('comet', (x, y, r, t, o) => {
    const ang = o.angle ?? -2.4, L = r * (o.len ?? 9), dx = Math.cos(ang), dy = Math.sin(ang), nx = -dy, ny = dx;
    // each tail is three nested layers so its edges stay soft
    const tail = (len, W0, W1, bend, rgb, A, a2 = 0, layers) => {
      const ca = Math.cos(ang + a2), sa = Math.sin(ang + a2), nx = -sa, ny = ca;
      for (const [k, ka] of (layers || (r > 120 ? [[1, .42], [.5, .6]] : [[1, .3], [.66, .4], [.36, .55]]))) {
        const w0 = W0 * k, w1 = W1 * k, l = len * (.75 + .25 * k), ex = x + ca * l, ey = y + sa * l, mx = x + ca * l * .5 + nx * l * bend, my = y + sa * l * .5 + ny * l * bend;
        const gr = ctx.createLinearGradient(x, y, ex, ey); gr.addColorStop(0, rgba(rgb, A * ka)); gr.addColorStop(.6, rgba(rgb, A * ka * .35)); gr.addColorStop(1, rgba(rgb, 0));
        ctx.fillStyle = gr; ctx.beginPath(); ctx.moveTo(x + nx * w0, y + ny * w0);
        ctx.quadraticCurveTo(mx + nx * w1 * .8, my + ny * w1 * .8, ex + nx * w1, ey + ny * w1); ctx.lineTo(ex - nx * w1, ey - ny * w1);
        ctx.quadraticCurveTo(mx - nx * w1 * .8, my - ny * w1 * .8, x - nx * w0, y - ny * w0);
        ctx.arc(x, y, w0, ang + a2 - Math.PI / 2, ang + a2 + Math.PI / 2, true); ctx.fill();
      }
    };
    tail(L, r * .85, r * 1.9, .08, '255,236,200', .75);
    tail(L * 1.15, r * .45, r * 1, 0, '110,210,255', .8, .1, r > 120 ? [[.8, .75]] : null);
    // streaks streaming away from the head
    ctx.lineCap = 'round'; ctx.strokeStyle = '#ffffff';
    for (let i = 0; i < 10; i++) {
      const p = wrap(t * .3 + rnd(i + 70), 1), off = (rnd(i + 71) - .5) * r * 1.8 * (.3 + p), sx = x + dx * p * L * .85 + nx * off, sy = y + dy * p * L * .85 + ny * off;
      al((1 - p) * .45); ctx.lineWidth = lw(r, .07, .5, 6); ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + dx * r * 1.4, sy + dy * r * 1.4); ctx.stroke();
    }
    al(1);
    bhalo(x, y, r * .3, r * 2.6, '160,225,255', .5);
    const cg = ctx.createRadialGradient(x, y, 0, x, y, r); cg.addColorStop(0, 'rgba(240,252,255,1)'); cg.addColorStop(.45, 'rgba(190,235,255,.75)'); cg.addColorStop(1, 'rgba(140,210,255,0)');
    ctx.fillStyle = cg; disc(x, y, r); ctx.fill();
    circle(x, y, r * .3, '#ffffff');
  });

  defBody('star', (x, y, r, t, o) => {
    const rgb = o.color ? hexRgb(o.color) : '255,226,150';
    bhalo(x, y, r, r * 2.8, rgb, .5);
    bhalo(x, y, r, r * 1.5, mixRgb(rgb, .5), .5);
    const k = 1 + .07 * Math.sin(t * 2.3);
    ctx.fillStyle = 'rgba(255,255,255,.38)'; sparkle(ctx, x, y, r * 1.95 * k);
    ctx.save(); ctx.translate(x, y); ctx.rotate(Math.PI / 4); ctx.fillStyle = 'rgba(255,255,255,.2)'; sparkle(ctx, 0, 0, r * 1.3 * k); ctx.restore();
    corona(x, y, r, t, 12, 1.16, rgb, .35, .4);
    ctx.save(); disc(x, y, r); ctx.fillStyle = rgba(rgb, 1); ctx.fill(); ctx.clip();
    gfill('star' + rgb, x, y, r, (g, cx, cy, R) => { const gr = g.createRadialGradient(cx - R * .25, cy - R * .25, 0, cx, cy, R); gr.addColorStop(0, '#ffffff'); gr.addColorStop(.5, rgba(mixRgb(rgb, .55), 1)); gr.addColorStop(1, rgba(rgb, 1)); return gr; });
    ctx.restore();
  });

  defBody('galaxy', (x, y, r, t, o) => {
    const tilt = o.tilt ?? .55, ang = o.angle ?? -.35, arms = o.arms ?? 2, spin = -t * .05, TH = Math.PI * 2.4, b = Math.log(10) / TH;
    ctx.save(); ctx.translate(x, y); ctx.rotate(ang); ctx.scale(1, tilt);
    gl(ctx, 0, 0, r * 1.2, '120,100,255', .4);
    gl(ctx, 0, 0, r * .8, '255,120,230', .22);
    const at = (th, k) => { const R = r * .1 * Math.exp(b * th), a = th + k * TAU / arms + spin; return [Math.cos(a) * R, Math.sin(a) * R]; };
    const dots = new Map(), dot = (c, lv, px, py, sz) => { const key = c + '|' + lv; let P = dots.get(key); if (!P) dots.set(key, P = []); P.push(px, py, sz); };
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (let k = 0; k < arms; k++) {
      for (const [w, c] of [[.34, 'rgba(150,130,255,.07)'], [.2, 'rgba(140,190,255,.15)'], [.08, 'rgba(210,235,255,.28)']]) { ctx.lineWidth = r * w; ctx.strokeStyle = c; ctx.beginPath(); for (let i = 0; i <= 40; i++) { const [px, py] = at(i / 40 * TH, k); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); } ctx.stroke(); }
      // stars along the arm, batched into one path per colour and brightness
      const nDots = Math.round(clamp(r * .9, 40, 170));
      if (r > 12) for (let i = 0; i < nDots; i++) {
        const u = rnd(k * 300 + i + .3), th = u * TH, [px, py] = at(th, k), j = (rnd(k * 300 + i + .6) - .5) * r * .16 * (.4 + u), jj = (rnd(k * 300 + i + .9) - .5) * r * .1;
        const c = u < .22 ? '#fff0c8' : rnd(k * 300 + i + 1.2) < .2 ? '#ff9fe6' : rnd(k * 300 + i + 1.5) < .6 ? '#bfe9ff' : '#ffffff';
        dot(c, Math.floor(rnd(k * 300 + i + 1.8) * 3), px + j, py + jj, Math.max(.4, r * (.006 + rnd(k * 300 + i + 2.1) * .014) * (1.25 - u * .5)));
      }
    }
    if (r > 12) for (let i = 0; i < 70; i++) { const a = rnd(i + 1500) * TAU + spin, d = Math.sqrt(rnd(i + 1501)) * r; dot('#e8f0ff', Math.floor(rnd(i + 1502) * 2), Math.cos(a) * d, Math.sin(a) * d, Math.max(.4, r * .007)); }
    for (const [key, P] of dots) { const [c, lv] = key.split('|'); al([.5, .75, 1][lv]); ctx.fillStyle = c; ctx.beginPath(); for (let i = 0; i < P.length; i += 3) { ctx.moveTo(P[i] + P[i + 2], P[i + 1]); ctx.arc(P[i], P[i + 1], P[i + 2], 0, TAU); } ctx.fill(); }
    al(1);
    gl(ctx, 0, 0, r * .5, '255,236,190', 1);
    circle(0, 0, Math.max(.6, r * .07), '#ffffff');
    ctx.restore();
  });

  window.BG = BG;
  window.BODY = BODY;
})();
