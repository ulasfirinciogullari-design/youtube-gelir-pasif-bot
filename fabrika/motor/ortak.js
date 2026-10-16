// YA OLSAYDI? shared drawing helpers (same look as the pilot). Canvas is 1080x1920.
const W = 1080, H = 1920, F = 'Nunito', EMOJI = '"Noto Color Emoji"';
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const C = {
  space1: '#070b24', space2: '#1a1650', cyan: '#3ee6ff', mag: '#ff4fd8', yellow: '#ffd23f',
  white: '#ffffff', ink: '#0b0f2a', skin: '#f6c9a3', coat: '#f4f6fb', ocean: '#1f6fe0', land: '#3ccf7a',
  moon: '#d8dbe6', red: '#ff5a5f', orange: '#ff9a3c', green: '#3ccf7a', purple: '#8b5cff'
};
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const prog = (t, a, b) => clamp((t - a) / (b - a));
const eo = t => 1 - Math.pow(1 - t, 3);
const eio = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const back = t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); };
const rnd = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function circle(x, y, r, fill) { ctx.fillStyle = fill; ctx.beginPath(); ctx.arc(x, y, Math.max(0, r), 0, Math.PI * 2); ctx.fill(); }
function glow(x, y, r, color, a = .5) { if (r <= 0) return; const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, color); g.addColorStop(1, 'rgba(0,0,0,0)'); ctx.globalAlpha *= a; ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2); ctx.globalAlpha /= a; }
function text(s, x, y, size, color, weight = 900, align = 'center', stroke = 0, strokeColor = C.ink) {
  ctx.font = `${weight} ${size}px ${F}`; ctx.textAlign = align; ctx.textBaseline = 'middle';
  if (stroke) { ctx.lineJoin = 'round'; ctx.lineWidth = stroke; ctx.strokeStyle = strokeColor; ctx.strokeText(s, x, y); }
  ctx.fillStyle = color; ctx.fillText(s, x, y);
}
// text that shrinks to fit maxW
function fitText(s, x, y, size, maxW, color, stroke = 0, weight = 900) {
  ctx.font = `${weight} ${size}px ${F}`; const w = ctx.measureText(s).width;
  text(s, x, y, w > maxW ? Math.floor(size * maxW / w) : size, color, weight, 'center', stroke);
}
function pill(label, x, y, size, bg, fg, scale = 1, maxW = 980) {
  if (scale <= 0.01 || !label) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(scale, scale);
  ctx.font = `900 ${size}px ${F}`; let w = ctx.measureText(label).width + size * 1.1;
  if (w > maxW) { size = Math.floor(size * maxW / w); ctx.font = `900 ${size}px ${F}`; w = ctx.measureText(label).width + size * 1.1; }
  ctx.shadowColor = 'rgba(0,0,0,.45)'; ctx.shadowBlur = 24; ctx.shadowOffsetY = 10;
  ctx.fillStyle = bg; rr(-w / 2, -size * .82, w, size * 1.64, size * .5); ctx.fill();
  ctx.shadowColor = 'transparent';
  text(label, 0, size * .04, size, fg);
  ctx.restore();
}
// a colour emoji (Noto, vector) centred at x,y with the given height
function emoji(ch, x, y, size, alpha = 1, rot = 0) {
  if (!ch || size < 2 || alpha <= 0) return;
  ctx.save(); ctx.globalAlpha *= alpha; ctx.translate(x, y); if (rot) ctx.rotate(rot);
  ctx.font = `${Math.round(size)}px ${EMOJI}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.shadowColor = 'rgba(0,0,0,.35)'; ctx.shadowBlur = size * .08; ctx.shadowOffsetY = size * .04;
  ctx.fillStyle = '#fff'; ctx.fillText(ch, 0, size * .04);
  ctx.restore();
}
function starfield(t, k = 1, n = 160, h = 1300) {
  for (let i = 0; i < n; i++) {
    const x = rnd(i) * W, y = rnd(i + 500) * h;
    const tw = .55 + .45 * Math.sin(t * (1 + rnd(i + 9) * 3) + i);
    ctx.globalAlpha = clamp(tw * k * (.35 + rnd(i + 3) * .65));
    circle(x, y, 1.2 + rnd(i + 7) * 2.6, C.white);
  }
  ctx.globalAlpha = 1;
}
function vgrad(top, bottom, y0 = 0, y1 = H) {
  const g = ctx.createLinearGradient(0, y0, 0, y1); g.addColorStop(0, top); g.addColorStop(1, bottom);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}
function blob(x, y, r, seed) {
  ctx.beginPath();
  for (let i = 0; i <= 10; i++) { const a = i / 10 * Math.PI * 2, r2 = r * (.7 + rnd(seed * 13 + i) * .45); const px = x + Math.cos(a) * r2, py = y + Math.sin(a) * r2 * .8; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
  ctx.closePath(); ctx.fill();
}
