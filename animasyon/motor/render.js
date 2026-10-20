// node render.js out.mp4 timings.json audio.mp3     -> video (motor.html with bolum.js next to it)
// node render.js --stills 1,5,9 outdir [timings.json] -> still_<t>.jpg
// node render.js --story outdir [timings.json]        -> story.jpg, one small frame per second in a grid
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  page.on('pageerror', e => { console.error('page error:', e.message); process.exitCode = 1; });
  // served over local http, not file://, so real paintings drawn on the canvas do not taint it
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.jpg': 'image/jpeg', '.png': 'image/png' };
  const server = http.createServer((req, res) => {
    const file = path.join(__dirname, decodeURIComponent(req.url.split('?')[0]));
    if (!file.startsWith(__dirname) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }); fs.createReadStream(file).pipe(res);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  await page.goto(`http://127.0.0.1:${server.address().port}/motor.html`);
  await page.evaluate(() => window.ready);
  const args = process.argv.slice(2);
  const timingsArg = args.find(a => a.endsWith('.json'));
  if (timingsArg) await page.evaluate(tm => window.setTimings(tm), JSON.parse(fs.readFileSync(timingsArg, 'utf8')));
  const shot = async () => Buffer.from((await page.evaluate(() => document.getElementById('c').toDataURL('image/jpeg', 0.92))).split(',')[1], 'base64');
  const grab = async t => { await page.evaluate(t => draw(t), t); return shot(); };
  const dur = await page.evaluate(() => window.DURATION());
  if (args[0] === '--stills') {
    for (const t of args[1].split(',').map(Number)) fs.writeFileSync(path.join(args[2], `still_${t}.jpg`), await grab(t));
  } else if (args[0] === '--story') {
    const dir = args[1], n = Math.ceil(dur);
    for (let i = 0; i < n; i++) fs.writeFileSync(path.join(dir, `f${String(i).padStart(3, '0')}.jpg`), await grab(i + .5));
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-pattern_type', 'glob', '-i', path.join(dir, 'f*.jpg'),
      '-vf', 'scale=270:480,tile=8x' + Math.ceil(n / 8), '-frames:v', '1', path.join(dir, 'story.jpg')]);
  } else {
    const fps = 30, out = args[0], audio = args.find(a => a.endsWith('.mp3') || a.endsWith('.wav'));
    const ffArgs = ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-'];
    if (audio) ffArgs.push('-i', audio, '-c:a', 'aac', '-b:a', '192k', '-shortest');
    ffArgs.push('-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium', '-movflags', '+faststart', out);
    const ff = spawn('ffmpeg', ffArgs, { stdio: ['pipe', 'inherit', 'inherit'] });
    const n = Math.ceil(dur * fps);
    for (let i = 0; i < n; i++) {
      const buf = await grab(i / fps);
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    }
    ff.stdin.end();
    const code = await new Promise(r => ff.on('close', r));
    if (code) { console.error('ffmpeg failed', code); process.exitCode = 1; }
    console.log('duration', dur.toFixed(2), 'frames', n);
  }
  await browser.close();
  server.close();
})();
