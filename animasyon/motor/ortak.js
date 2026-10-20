// Zirvenin Kanunu animation helpers. Canvas is 1080x1920; the look is a dark stage: near-black rooms, one warm
// light, silhouettes with gold or crimson rim light, film grain and a vignette on top.
const W = 1080, H = 1920, SERIF = 'Cinzel', SANS = 'Montserrat';
const cv = document.getElementById('c');
let ctx = cv.getContext('2d');  // let: drawSoft() points it at an offscreen canvas for a moment
// Canvas filters (blur, sepia) cost seconds per frame in headless Chrome without a GPU, so a softened layer is drawn
// small on an offscreen canvas and scaled up, tones use blend modes, and painting tones are baked once.
const SOFT = {};
function drawSoft(scale, fn, dark = 0) {  // fn draws in frame coordinates; the result lands under the current transform
  const w = Math.round(W * scale), h = Math.round(H * scale), key = w + 'x' + h;
  const c = SOFT[key] || (SOFT[key] = Object.assign(document.createElement('canvas'), { width: w, height: h })), o = c.getContext('2d');
  o.setTransform(1, 0, 0, 1, 0, 0); o.globalCompositeOperation = 'source-over'; o.globalAlpha = 1; o.clearRect(0, 0, w, h);
  o.setTransform(scale, 0, 0, scale, 0, 0);
  const keep = ctx; ctx = o;
  try { fn(); } finally { ctx = keep; }
  if (dark > 0) { o.setTransform(1, 0, 0, 1, 0, 0); o.globalCompositeOperation = 'source-atop'; o.fillStyle = `rgba(0,0,0,${dark})`; o.fillRect(0, 0, w, h); }
  ctx.save(); ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high'; ctx.drawImage(c, 0, 0, W, H); ctx.restore();
}
// an old-photo tone over everything drawn so far: a desaturation, then a warm brown colour, a and dim 0..1
function sepiaTone(a = .75, dim = .1) {
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = 'saturation'; ctx.globalAlpha = a; ctx.fillStyle = '#808080'; ctx.fillRect(0, 0, W, H);
  ctx.globalCompositeOperation = 'color'; ctx.globalAlpha = a * .7; ctx.fillStyle = '#a5845c'; ctx.fillRect(0, 0, W, H);
  ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = dim; ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  ctx.restore();
}
// a painting with CSS-like sepia(s) contrast(c) brightness(b) baked into a canvas once
const TONED = new Map();
function toned(img, s = .35, c = 1.05, b = .92) {
  if (TONED.has(img)) return TONED.get(img);
  const out = Object.assign(document.createElement('canvas'), { width: img.naturalWidth, height: img.naturalHeight }), o = out.getContext('2d');
  o.drawImage(img, 0, 0);
  const d = o.getImageData(0, 0, out.width, out.height), p = d.data, u = 1 - s;
  const m = [.393 + .607 * u, .769 - .769 * u, .189 - .189 * u, .349 - .349 * u, .686 + .314 * u, .168 - .168 * u,
             .272 - .272 * u, .534 - .534 * u, .131 + .869 * u];
  const f = v => ((v / 255 - .5) * c + .5) * b * 255;
  for (let i = 0; i < p.length; i += 4) {
    const r = p[i], g = p[i + 1], bl = p[i + 2];
    p[i] = f(m[0] * r + m[1] * g + m[2] * bl); p[i + 1] = f(m[3] * r + m[4] * g + m[5] * bl); p[i + 2] = f(m[6] * r + m[7] * g + m[8] * bl);
  }
  o.putImageData(d, 0, 0); TONED.set(img, out); return out;
}
const C = {
  black: '#060609', night: '#0c0b10', smoke: '#17151b', ink: '#030304',
  ivory: '#f3ead8', bone: '#cdbfa6', gold: '#e8b54a', gold2: '#ffd98a', goldDim: '#8a6a2c',
  red: '#d2232f', red2: '#ff5a4f', redDim: '#6e1218', candle: '#ffb766', steel: '#9aa3b5'
};
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const prog = (t, a, b) => clamp((t - a) / (b - a));
const eo = t => 1 - Math.pow(1 - t, 3);
const ei = t => t * t * t;
const eio = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const back = t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); };
const rnd = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const mixColor = (a, b, t) => {
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const A = p(a), B = p(b);
  return '#' + A.map((v, i) => Math.round(lerp(v, B[i], clamp(t))).toString(16).padStart(2, '0')).join('');
};
const rgba = (hex, a) => { const p = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)); return `rgba(${p.join(',')},${a})`; };

function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function circle(x, y, r, fill) { ctx.fillStyle = fill; ctx.beginPath(); ctx.arc(x, y, Math.max(0, r), 0, Math.PI * 2); ctx.fill(); }
function glow(x, y, r, color, a = .5) {
  if (r <= 0 || a <= 0) return;
  const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, color); g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.save(); ctx.globalAlpha *= a; ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2); ctx.restore();
}
function text(s, x, y, size, color, font = SANS, weight = 900, align = 'center', stroke = 0, strokeColor = C.ink, spacing = 0) {
  ctx.font = `${weight} ${size}px ${font}`; ctx.textAlign = align; ctx.textBaseline = 'middle';
  if ('letterSpacing' in ctx) ctx.letterSpacing = spacing ? `${spacing}px` : '0px';
  if (stroke) { ctx.lineJoin = 'round'; ctx.lineWidth = stroke; ctx.strokeStyle = strokeColor; ctx.strokeText(s, x, y); }
  ctx.fillStyle = color; ctx.fillText(s, x, y);
  if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';
}
function measure(s, size, font = SANS, weight = 900, spacing = 0) {
  ctx.font = `${weight} ${size}px ${font}`;
  if ('letterSpacing' in ctx) ctx.letterSpacing = spacing ? `${spacing}px` : '0px';
  const w = ctx.measureText(s).width;
  if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';
  return w;
}
const up = s => String(s || '').toLocaleUpperCase('tr');

// a label plate: dark glass, thin gold frame, ivory capitals
function plate(label, x, y, size, k = 1, accent = C.gold, maxW = 900) {
  if (k <= .01 || !label) return;
  label = up(label);
  let w = measure(label, size, SANS, 900, size * .08);
  if (w > maxW) { size = Math.floor(size * maxW / w); w = measure(label, size, SANS, 900, size * .08); }
  const pw = w + size * 1.4, ph = size * 1.75;
  ctx.save(); ctx.translate(x, y); ctx.scale(k, k);
  ctx.shadowColor = 'rgba(0,0,0,.7)'; ctx.shadowBlur = 30;
  ctx.fillStyle = 'rgba(8,7,10,.82)'; rr(-pw / 2, -ph / 2, pw, ph, 6); ctx.fill();
  ctx.shadowColor = rgba(accent, .55); ctx.shadowBlur = 18;
  ctx.strokeStyle = accent; ctx.lineWidth = 3; rr(-pw / 2, -ph / 2, pw, ph, 6); ctx.stroke();
  ctx.shadowColor = 'transparent';
  ctx.strokeStyle = rgba(accent, .35); ctx.lineWidth = 1.5; rr(-pw / 2 + 9, -ph / 2 + 9, pw - 18, ph - 18, 3); ctx.stroke();
  text(label, 0, size * .05, size, C.ivory, SANS, 900, 'center', 0, C.ink, size * .08);
  ctx.restore();
}

// ---------- light, air and film ----------
// a cone of light from (x, y0) widening to `spread` at y1, with dust drifting inside it
function beam(x, y0, y1, w0, spread, color, a, t, dust = 40) {
  ctx.save();
  const g = ctx.createLinearGradient(0, y0, 0, y1); g.addColorStop(0, rgba(color, a)); g.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = g; ctx.globalCompositeOperation = 'lighter';
  ctx.beginPath(); ctx.moveTo(x - w0 / 2, y0); ctx.lineTo(x + w0 / 2, y0); ctx.lineTo(x + spread / 2, y1); ctx.lineTo(x - spread / 2, y1); ctx.closePath(); ctx.fill();
  for (let i = 0; i < dust; i++) {
    const v = rnd(i + 40), yy = y0 + ((rnd(i) * (y1 - y0) + t * (12 + v * 22)) % (y1 - y0));
    const half = lerp(w0, spread, (yy - y0) / (y1 - y0)) / 2;
    const xx = x + (rnd(i + 9) - .5) * 2 * half * .9 + Math.sin(t * .7 + i) * 8;
    ctx.globalAlpha = (.15 + .5 * rnd(i + 3)) * (1 - (yy - y0) / (y1 - y0)) * (.6 + .4 * Math.sin(t * 2 + i));
    circle(xx, yy, 1.2 + rnd(i + 5) * 2.4, rgba(color, 1));
  }
  ctx.restore();
}
function embers(t, n = 26, x0 = 0, x1 = W, yBottom = 1500, h = 900, color = C.gold) {
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < n; i++) {
    const life = (t * (.08 + rnd(i + 2) * .1) + rnd(i)) % 1;
    const x = lerp(x0, x1, rnd(i + 7)) + Math.sin(t * 1.3 + i * 1.7) * 30;
    const y = yBottom - life * h;
    ctx.globalAlpha = Math.sin(life * Math.PI) * (.35 + .5 * rnd(i + 11));
    circle(x, y, 1.5 + rnd(i + 13) * 3, color);
  }
  ctx.restore();
}
function room(t, tone = C.night) {
  const g = ctx.createRadialGradient(540, 760, 80, 540, 900, 1250);
  g.addColorStop(0, mixColor(tone, '#2a2420', .35)); g.addColorStop(.55, tone); g.addColorStop(1, C.ink);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}
function floor(y, color = C.gold, a = .16, w = 760) {
  ctx.save(); ctx.translate(540, y); ctx.scale(1, .16);
  glow(0, 0, w / 2, rgba(color, 1), a);
  ctx.restore();
}
let GRAIN = null;
function grain(t) {
  if (!GRAIN) {
    GRAIN = [0, 1, 2, 3].map(k => {
      const c = document.createElement('canvas'); c.width = c.height = 256;
      const g = c.getContext('2d'), d = g.createImageData(256, 256);
      for (let i = 0; i < d.data.length; i += 4) {
        const v = rnd(i * .37 + k * 9973) * 255;
        d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255;
      }
      g.putImageData(d, 0, 0); return c;
    });
  }
  const c = GRAIN[Math.floor(t * 24) % 4];
  ctx.save(); ctx.globalCompositeOperation = 'overlay'; ctx.globalAlpha = .09;
  ctx.fillStyle = ctx.createPattern(c, 'repeat'); ctx.fillRect(0, 0, W, H);
  ctx.restore();
}
function vignette(a = .85) {
  const g = ctx.createRadialGradient(540, 860, 380, 540, 960, 1250);
  g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, `rgba(0,0,0,${a})`);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}
// low haze drifting across the floor, so even a still frame keeps moving
function fog(t, y = 1350, a = .06, color = '#8c8076') {
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 6; i++) {
    const x = ((rnd(i) * 1800 + t * (16 + rnd(i + 1) * 22)) % 1900) - 400, yy = y + (rnd(i + 2) - .5) * 280;
    ctx.save(); ctx.translate(x, yy); ctx.scale(1, .33);
    glow(0, 0, 360 + rnd(i + 3) * 220, rgba(color, 1), a * (.6 + .4 * Math.sin(t * .5 + i)));
    ctx.restore();
  }
  ctx.restore();
}
