// node brand.js [kinds]  -> brand/<kind>.png  (profil, banner, filigran, banner-guide, kapak)
const { chromium } = require('playwright');
const path = require('path'); const fs = require('fs');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  page.on('pageerror', e => console.log('ERR', e.message));
  await page.goto('file://' + path.join(__dirname, 'brand.html'));
  await page.evaluate(() => window.ready);
  fs.mkdirSync(path.join(__dirname, 'brand'), { recursive: true });
  for (const k of (process.argv[2] || 'profil,banner,filigran,banner-guide,kapak').split(',')) {
    const url = await page.evaluate(k => { brand(k); return document.getElementById('c').toDataURL('image/png'); }, k);
    fs.writeFileSync(path.join(__dirname, 'brand', k + '.png'), Buffer.from(url.split(',')[1], 'base64'));
  }
  await browser.close();
})();
