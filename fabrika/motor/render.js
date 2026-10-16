// node render.js out.mp4 timings.json audio.mp3   -> video (motor.html with bolum.js next to it)
// node render.js --cover <line> out.jpg [timings.json] -> cover image
// node render.js --stills 1,5,9 outdir [timings.json] -> still_<t>.jpg
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  page.on('pageerror', e => { console.error('page error:', e.message); process.exitCode = 1; });
  await page.goto('file://' + path.join(__dirname, 'motor.html'));
  await page.evaluate(() => window.ready);
  const args = process.argv.slice(2);
  const timingsArg = args.find(a => a.endsWith('.json'));
  if (timingsArg) await page.evaluate(tm => window.setTimings(tm), JSON.parse(fs.readFileSync(timingsArg, 'utf8')));
  const shot = async () => Buffer.from((await page.evaluate(() => document.getElementById('c').toDataURL('image/jpeg', 0.92))).split(',')[1], 'base64');
  const grab = async t => { await page.evaluate(t => draw(t), t); return shot(); };
  if (args[0] === '--cover') {
    await page.evaluate(li => window.cover(li), +args[1]);
    fs.writeFileSync(args[2], await shot());
  } else if (args[0] === '--stills') {
    for (const t of args[1].split(',').map(Number)) fs.writeFileSync(path.join(args[2], `still_${t}.jpg`), await grab(t));
  } else {
    const fps = 30, out = args[0], audio = args.find(a => a.endsWith('.mp3') || a.endsWith('.wav'));
    const dur = await page.evaluate(() => window.DURATION());
    const ffArgs = ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-'];
    if (audio) ffArgs.push('-i', audio, '-c:a', 'aac', '-b:a', '192k', '-shortest');
    ffArgs.push('-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '19', '-preset', 'medium', '-movflags', '+faststart', out);
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
})();
