// Calibration probe for the PSA pop-report scraper. Throwaway — not part of the deploy.
//
// Goal: get past Cloudflare's managed challenge ONCE with a stealth browser, then capture
// everything we need to build psa-pop-scrape.js against ground truth:
//   - PSA's /pop/ URL taxonomy (so we can map card_catalog.set_name -> a set pop page)
//   - any XHR/fetch JSON the pop table renders from (preferred data path)
//   - the rendered table structure + grade column headers (DOM fallback)
//
// Persists cf_clearance in ./_psa-profile so re-runs reuse the cleared session.
// Artifacts written to ./_psa-probe-out/.
//
//   node _psa-probe.js [startUrl]
//   PSA_HEADLESS=true node _psa-probe.js     # headless 'new' (less reliable vs CF)

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-extra');
const Stealth = require('puppeteer-extra-plugin-stealth');
puppeteer.use(Stealth());

const START = process.argv[2] || 'https://www.psacard.com/pop/tcg-cards';
const HEADLESS = process.env.PSA_HEADLESS === 'true' ? 'new' : false;
const OUT = path.join(__dirname, '_psa-probe-out');
const PROFILE = path.join(__dirname, '_psa-profile');
const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

// Wait out the Cloudflare interstitial. The "Just a moment..." page runs JS, computes a token,
// and (if the browser looks legit) redirects to the real page. Poll the title until it changes
// or we time out.
async function clearChallenge(page, maxMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const title = await page.title().catch(() => '');
    const challenged = /just a moment|attention required|verifying/i.test(title);
    if (!challenged && title) return { cleared: true, ms: Date.now() - start, title };
    await sleep(2000);
  }
  return { cleared: false, ms: Date.now() - start, title: await page.title().catch(() => '?') };
}

(async () => {
  const browser = await puppeteer.launch({
    headless: HEADLESS,
    userDataDir: PROFILE,
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled', '--window-size=1366,900'],
  });
  const page = await browser.newPage();
  await page.setUserAgent(UA);
  await page.setViewport({ width: 1366, height: 900 });

  // Capture the request side of the data endpoint (method + params + postData) so we can
  // replicate the call directly instead of scraping the DOM.
  const requests = [];
  page.on('request', (req) => {
    const url = req.url();
    if (/Pop\/GetSetItems|\/Pop\/|GetSpecItems|population/i.test(url)) {
      requests.push({ method: req.method(), url, postData: req.postData() || null, headers: req.headers() });
    }
  });

  // Capture API-ish JSON responses — this is the data path we'd prefer over DOM scraping.
  const captured = [];
  page.on('response', async (res) => {
    try {
      const url = res.url();
      const ct = res.headers()['content-type'] || '';
      if (!/json/i.test(ct)) return;
      if (/google|doubleclick|analytics|gtm|facebook|hotjar|segment|sentry|cloudflare/i.test(url)) return;
      let bodySample = '';
      try {
        const txt = await res.text();
        bodySample = txt.slice(0, 600);
        // Save full body for anything that smells like population/spec/cert/search data.
        if (/pop|spec|cert|grade|population|search|card/i.test(url)) {
          const fname = 'xhr_' + captured.length + '_' + url.replace(/[^a-z0-9]+/gi, '_').slice(0, 80) + '.json';
          fs.writeFileSync(path.join(OUT, fname), txt);
        }
      } catch {}
      captured.push({ status: res.status(), ct, url, bodySample });
    } catch {}
  });

  console.log(`START=${START} headless=${HEADLESS}`);
  let status = 'n/a';
  try {
    const resp = await page.goto(START, { waitUntil: 'domcontentloaded', timeout: 60000 });
    status = resp ? resp.status() : 'no-response';
  } catch (e) {
    status = `goto-error: ${e.message}`;
  }

  const chal = await clearChallenge(page);
  await sleep(3000); // let post-challenge XHRs fire

  const finalUrl = page.url();
  const title = await page.title().catch(() => '?');
  console.log(`status=${status} cleared=${chal.cleared} in=${chal.ms}ms finalTitle="${title}" finalUrl=${finalUrl}`);

  // Save full rendered HTML + screenshot for offline inspection.
  const html = await page.content().catch(() => '');
  fs.writeFileSync(path.join(OUT, 'page.html'), html);
  await page.screenshot({ path: path.join(OUT, 'page.png'), fullPage: false }).catch(() => {});

  // Table structure: how many tables, and their header cells (grade columns live here).
  const tables = await page.evaluate(() =>
    Array.from(document.querySelectorAll('table')).map((t, i) => ({
      index: i,
      rows: t.querySelectorAll('tr').length,
      headers: Array.from(t.querySelectorAll('thead th, tr:first-child th, tr:first-child td'))
        .map((c) => c.textContent.trim())
        .filter(Boolean)
        .slice(0, 30),
    }))
  );

  // /pop/ link taxonomy — what do PSA set/category pop URLs look like?
  const popLinks = await page.evaluate(() =>
    [...new Set(
      Array.from(document.querySelectorAll('a[href]'))
        .map((a) => a.getAttribute('href'))
        .filter((h) => h && /\/pop\//.test(h))
    )].slice(0, 60)
  );

  console.log('\n=== captured JSON responses ===');
  for (const c of captured) console.log(`${c.status} ${c.url}`);
  console.log(`total_json=${captured.length}`);
  console.log('\n=== tables ===');
  console.log(JSON.stringify(tables, null, 2));
  console.log('\n=== /pop/ links ===');
  console.log(popLinks.join('\n'));

  fs.writeFileSync(
    path.join(OUT, 'summary.json'),
    JSON.stringify({ START, status, cleared: chal.cleared, finalUrl, title, tables, popLinks, captured, requests }, null, 2)
  );
  console.log('\n=== data-endpoint requests (method/url/postData) ===');
  for (const r of requests) console.log(`${r.method} ${r.url}\n  postData: ${r.postData}`);
  console.log(`\nartifacts -> ${OUT}`);

  await browser.close();
})().catch((e) => {
  console.error('fatal:', e);
  process.exit(1);
});
