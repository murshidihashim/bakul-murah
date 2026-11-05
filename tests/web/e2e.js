// Headless end-to-end check of web/index.html with Playwright.
// Run from grocery-prototype/:  node tests/web/e2e.js
// Opens the page straight from disk (file://) to prove no server is needed,
// builds a basket, checks the ranking against an independent calculation,
// and saves screenshots to screenshots/.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "../..");
const URL = "file://" + path.join(ROOT, "web/index.html");
const SHOTS = path.join(ROOT, "screenshots");
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail || "" });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`);
}

async function shot(page, name, opts) {
  await page.waitForFunction(() => !document.querySelector("#toast").classList.contains("on"));
  await page.waitForTimeout(250);
  await page.screenshot(Object.assign({ path: path.join(SHOTS, name) }, opts || {}));
}

async function noHScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}

async function buildBasket(page) {
  await page.waitForFunction(() => document.querySelectorAll("#districtSel option").length > 0);
  await page.selectOption("#stateSel", "Selangor");
  await page.waitForFunction(() => document.querySelector("#districtSel").value.startsWith("selangor"));
  await page.selectOption("#districtSel", "selangor--petaling");
  await page.waitForFunction(() => window.__bakul.d && window.__bakul.d.district === "Petaling");
  await page.click("#starterBtn");
  await page.fill("#searchIn", "manis pekat");
  await page.waitForSelector("#results [data-add]");
  // Starter list already has this one, so add something else too.
  await page.fill("#searchIn", "kicap");
  await page.waitForSelector("#results [data-add]");
  await page.click("#results [data-add] >> nth=0");
  await page.fill("#searchIn", "");
  await page.dispatchEvent("#searchIn", "input");
}

(async () => {
  const browser = await chromium.launch();
  const errors = [];

  // ---------- mobile ----------
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
                                          isMobile: true, hasTouch: true, colorScheme: "light" });
  const m = await mctx.newPage();
  m.on("pageerror", e => errors.push("pageerror: " + e.message));
  m.on("console", msg => { if (msg.type() === "error") errors.push("console: " + msg.text()); });
  await m.goto(URL);
  await m.waitForFunction(() => window.__bakul && window.__bakul.meta);
  check("page loads from file:// and reads meta", true);
  const isSynth = await m.evaluate(() => !!window.__bakul.meta.synthetic);
  check("sample-data banner matches the data (" + (isSynth ? "sample" : "real") + ")",
        (await m.isVisible("#synthBanner")) === isSynth);
  check("source attribution in footer", (await m.textContent(".foot")).includes("PriceCatcher, KPDN via OpenDOSM"));
  check("licence shown", (await m.textContent(".foot")).includes("CC BY 4.0"));
  await shot(m, "mobile-01-empty.png");

  await buildBasket(m);
  const nBasket = await m.$$eval("#basketList li", l => l.length);
  check("basket built (starter + search)", nBasket >= 10, nBasket + " items");
  const nRank = await m.$$eval("#rankList li", l => l.length);
  check("ranking renders", nRank > 0, nRank + " rows");
  check("summary visible", await m.isVisible("#summary"));
  check("split suggestion visible", await m.isVisible("#splitCard"));

  // Independent check: recompute the cheapest full-coverage shop from raw data.
  const indep = await m.evaluate(() => {
    const S = window.__bakul, d = S.d, n = S.basket.length;
    let best = null;
    d.premises.forEach((_, p) => {
      let tot = 0, have = 0;
      S.basket.forEach(b => {
        // A line accepts its item plus any "any brand" alternatives: take the cheapest at this shop.
        const codes = [b.code].concat(b.alt || []);
        let bestP = null;
        codes.forEach(c => { const r = (d.prices[c] || []).find(x => x[0] === p); if (r && (bestP == null || r[1] < bestP)) bestP = r[1]; });
        if (bestP != null) { tot += bestP * b.qty; have++; }
      });
      if (have === n && (!best || tot < best.tot)) best = { p, tot, name: d.premises[p][1] };
    });
    return best;
  });
  const firstName = await m.textContent("#rankList li:first-child .rname");
  const firstTot = await m.textContent("#rankList li:first-child .rtot b");
  check("top shop matches independent calculation", indep && firstName === indep.name &&
        firstTot.includes(indep.tot.toFixed(2)), `${firstName} ${firstTot} vs ${indep && indep.name} ${indep && indep.tot.toFixed(2)}`);
  check("no horizontal scroll (mobile)", await noHScroll(m));
  await m.evaluate(() => window.scrollTo(0, 0));
  await m.waitForTimeout(200);
  check("mobile result bar shows best shop", await m.isVisible("#resultBar") &&
        (await m.textContent("#rbShop")) === indep.name);
  await shot(m, "mobile-02-basket.png");
  await shot(m, "mobile-03-basket-full.png", { fullPage: true });

  // Quantity stepper changes the total.
  const before = await m.textContent("#rankList li:first-child .rtot b");
  await m.click("#basketList li:first-child [data-qty]:not([data-d^='-'])");
  const after = await m.textContent("#rankList li:first-child .rtot b");
  check("quantity change updates totals", before !== after, `${before} -> ${after}`);

  // History dialog.
  await m.click("#basketList li:first-child .bi-name");
  await m.waitForSelector("#itemDlg[open] svg.lc path.ln");
  const paths = await m.$$eval("#itemDlg svg.lc path.ln", p => p.filter(x => x.getAttribute("d").length > 10).length);
  check("price history chart draws 2 lines", paths === 2, paths + " lines");
  const box = await m.locator("#dlgChart svg").boundingBox();
  await m.touchscreen.tap(box.x + box.width * 0.6, box.y + box.height / 2);
  check("chart readout appears on tap", await m.isVisible("#dlgChart .tip"));
  await shot(m, "mobile-04-history.png");
  await m.click("#dlgClose");

  // Trends feed.
  await m.click("[data-tab='trends']");
  const nFeed = await m.$$eval("#feedList li .fi", l => l.length);
  check("price rises feed renders", nFeed > 0, nFeed + " rows");
  const firstPct = await m.textContent("#feedList li:first-child .pct");
  check("rises feed shows positive %", firstPct.startsWith("+"), firstPct);
  await shot(m, "mobile-05-rises.png");
  await m.click("[data-dir='falls']");
  const fallPct = await m.textContent("#feedList li:first-child .pct");
  check("falls toggle shows negative %", fallPct.startsWith("−"), fallPct);
  await m.click("[data-scope='national']");
  check("Malaysia scope renders", (await m.$$eval("#feedList li .fi", l => l.length)) > 0);
  await m.click("[data-dir='rises']");

  // Language toggle.
  await m.click("[data-lang='ms']");
  check("BM toggle changes labels", (await m.textContent("#tab-basket")) === "Bakul" &&
        (await m.textContent("#feedTitle")).includes("Perubahan"));
  await shot(m, "mobile-06-rises-bm.png");
  await m.click("[data-tab='basket']");

  // Dark mode toggle.
  await m.click("#themeBtn");
  const theme = await m.evaluate(() => document.documentElement.getAttribute("data-theme"));
  const bg = await m.evaluate(() => getComputedStyle(document.body).backgroundColor);
  check("dark mode toggle", theme === "dark" && bg !== "rgb(245, 242, 234)", `${theme} ${bg}`);
  await m.evaluate(() => window.scrollTo(0, 0));
  await shot(m, "mobile-07-dark-bm.png");

  // Persistence across reload.
  await m.reload();
  await m.waitForFunction(() => window.__bakul.d && window.__bakul.basket.length > 0);
  const persisted = await m.evaluate(() => ({ n: window.__bakul.basket.length, d: window.__bakul.d.district,
                                              lang: document.documentElement.lang }));
  check("basket, district and language persist on this device", persisted.n === nBasket &&
        persisted.d === "Petaling" && persisted.lang === "ms", JSON.stringify(persisted));
  await mctx.close();

  // ---------- list tools: paste, plans, saved lists, share link ----------
  const lctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
                                          isMobile: true, hasTouch: true, colorScheme: "light" });
  const l = await lctx.newPage();
  l.on("pageerror", e => errors.push("pageerror: " + e.message));
  l.on("console", msg => { if (msg.type() === "error") errors.push("console: " + msg.text()); });
  await l.goto(URL);
  await l.waitForFunction(() => window.__bakul && window.__bakul.meta);
  await l.selectOption("#stateSel", "Selangor");
  await l.selectOption("#districtSel", "selangor--petaling");
  await l.waitForFunction(() => window.__bakul.d && window.__bakul.d.district === "Petaling");
  await l.click("#pasteBtn2");
  await l.fill("#pasteIn", "beras 10kg\ntelur 2 papan\nminyak masak 5kg\nayam 2 ekor\n- bawang 500g, kangkung, susu pekat 2 tin\nroti gardenia\nikan");
  await shot(l, "mobile-09-paste.png");
  await l.click("#matchBtn");
  await l.waitForSelector("#reviewStep:not([hidden]) #matchList li");
  const nm = await l.$$eval("#matchList li", x => x.length);
  const nNone = await l.$$eval("#matchList li.none", x => x.length);
  const nGuess = await l.$$eval("#matchList li.guess", x => x.length);
  check("pasted list splits into 9 lines", nm === 9, nm + " lines");
  check("unknown item is marked not found", nNone === 1, nNone + " not found");
  check("vague line ('ikan') is marked as a guess", nGuess >= 1, nGuess + " guesses");
  const eggQty = await l.$eval("#matchList li:nth-child(2) input[type=number]", x => x.value);
  check("'telur 2 papan' reads as 2 trays of 30", eggQty === "2", "qty " + eggQty);
  const ayamQty = await l.$eval("#matchList li:nth-child(4) input[type=number]", x => x.value);
  check("'ayam 2 ekor' reads as 3 kg", ayamQty === "3", "qty " + ayamQty);
  await shot(l, "mobile-10-review.png", { fullPage: false });
  await l.click("#addMatchesBtn");
  await l.waitForFunction(() => !document.getElementById("pasteDlg").open);
  const nb = await l.$$eval("#basketList li.bi", x => x.length);
  check("matched items added to the list", nb === 8, nb + " items");
  check("rice line has an 'any brand' switch, on", await l.isVisible("#basketList .flex-tag.on"));
  const flexOk = await l.evaluate(() => {
    const S = window.__bakul, b = S.basket.find(x => S.items.get(x.code).item.startsWith("BERAS"));
    return b && b.alt && b.alt.length > 5;
  });
  check("'any brand' rice accepts many brands", flexOk);

  // Best plan card with 1 / 2 / 3 stops.
  const nOpt = await l.$$eval("#splitCard [data-k]", x => x.length);
  check("plan offers 1, 2 and 3 stops", nOpt === 3, nOpt + " options");
  await l.click("#splitCard [data-k='2']");
  const cols2 = await l.$$eval("#splitCard .split-col", x => x.length);
  check("2-stop plan shows 2 shops", cols2 === 2, cols2 + " columns");
  const planOk = await l.evaluate(() => {
    // Independent check of the 2-stop plan: no other pair of shops is cheaper for the same coverage.
    const S = window.__bakul, d = S.d, nP = d.premises.length;
    const price = (b, p) => { let v = null; [b.code].concat(b.alt || []).forEach(c => { const r = (d.prices[c] || []).find(x => x[0] === p); if (r && (v == null || r[1] < v)) v = r[1]; }); return v; };
    let best = { have: -1, tot: Infinity };
    for (let a = 0; a < nP; a++) for (let b2 = a + 1; b2 < nP; b2++) {
      let tot = 0, have = 0;
      S.basket.forEach(x => { const pa = price(x, a), pb = price(x, b2); const v = pa == null ? pb : pb == null ? pa : Math.min(pa, pb); if (v != null) { tot += v * x.qty; have++; } });
      if (have > best.have || (have === best.have && tot < best.tot)) best = { have, tot };
    }
    const shown = Number(document.querySelector("#splitCard .pill").textContent.replace(/[^0-9.]/g, ""));
    return { ok: Math.abs(shown - best.tot) < 0.011, shown, best: Math.round(best.tot * 100) / 100 };
  });
  check("2-stop total matches a brute-force check of every pair", planOk.ok, JSON.stringify(planOk));
  await l.selectOption("#stopCost", "20");
  const rec = await l.$eval("#splitCard [aria-pressed='true']", x => x.dataset.k);
  check("a high value per extra stop recommends 1 stop", rec === "1", "picked " + rec);
  const hasSM = await l.evaluate(() => !!window.__bakul.d.shop_months);
  check("'cheapest on average' card shows when monthly shop data exists", (await l.isVisible("#avgCard")) === hasSM, hasSM ? "data present" : "data not built yet, card hidden");
  await shot(l, "mobile-11-plan.png", { fullPage: true });

  // Saved lists.
  await l.click("#listsBtn");
  await l.fill("#listName", "Weekly shop");
  await l.click("#saveListBtn");
  check("list saved", (await l.textContent("#savedLists")).includes("Weekly shop"));
  const link = await l.inputValue("#shareOut");
  check("share link holds the list", /\?l=[0-9~._-]+$/.test(link), link.slice(-60));
  await shot(l, "mobile-12-lists.png");
  await l.click("#listsClose");
  await l.click("#clearBtn");
  await l.click("#listsBtn");
  await l.click("#savedLists [data-load='0']");
  const nb2 = await l.$$eval("#basketList li.bi", x => x.length);
  check("saved list loads back", nb2 === nb, nb2 + " items");

  // Open the share link in a fresh browser: the list is offered, not forced.
  const l2ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const l2 = await l2ctx.newPage();
  l2.on("pageerror", e => errors.push("pageerror: " + e.message));
  await l2.goto(link);
  await l2.waitForFunction(() => window.__bakul && window.__bakul.d);
  check("shared link shows a 'load it' bar", await l2.isVisible("#importBar"));
  await l2.click("#importYes");
  const shared = await l2.evaluate(() => window.__bakul.basket.length);
  check("shared list loads with the same items", shared === nb, shared + " items");
  await l2ctx.close();
  check("no horizontal scroll (list tools)", await noHScroll(l));
  await lctx.close();

  // ---------- near me ----------
  // Uses the real shop locations when the data has them; otherwise a temporary copy of the app
  // with made-up test locations around Petaling, so the feature is always tested end to end.
  const realGeo = await (async () => {
    const p0 = await browser.newPage(); await p0.goto(URL);
    await p0.waitForFunction(() => window.__bakul && window.__bakul.meta);
    const g = await p0.evaluate(() => !!window.__bakul.meta.geo); await p0.close(); return g;
  })();
  let NEAR_URL = URL, tmpWeb = null;
  if (!realGeo) {
    const os = require("os");
    tmpWeb = fs.mkdtempSync(path.join(os.tmpdir(), "bakul-geo-"));
    fs.cpSync(path.join(ROOT, "web"), tmpWeb, { recursive: true, filter: src => !src.includes("vendor") });
    const centres = { "selangor--petaling": [3.07, 101.53], "selangor--petaling-jaya": [3.11, 101.62],
                      "selangor--klang": [3.04, 101.45], "selangor--hulu-langat": [3.05, 101.80] };
    const read = f => JSON.parse(fs.readFileSync(f, "utf8"));
    const write = (f, key, obj) => {
      const txt = JSON.stringify(obj);
      fs.writeFileSync(f, txt); fs.writeFileSync(f.replace(/\.json$/, ".js"), `(window.PCDATA=window.PCDATA||{})[${JSON.stringify(key)}]=${txt};\n`);
    };
    const meta = read(path.join(tmpWeb, "data/meta.json"));
    const postcodes = {};
    Object.entries(centres).forEach(([slug, c]) => {
      const f = path.join(tmpWeb, "data/d", slug + ".json"), d = read(f);
      d.premises = d.premises.map((p, i) => {
        const lat = +(c[0] + ((i * 37) % 21 - 10) * 0.004).toFixed(5), lon = +(c[1] + ((i * 53) % 21 - 10) * 0.004).toFixed(5);
        const pc = (p[3].match(/\b(\d{5})\b/) || [])[1];
        if (pc && !postcodes[pc]) postcodes[pc] = [lat, lon];
        return [p[0], p[1], p[2], p[3], lat, lon, i % 9 === 8 ? "district" : "address"];
      });
      write(f, "d/" + slug, d);
      const st = Object.keys(meta.areas).find(k => meta.areas[k].some(a => a.slug === slug));
      meta.areas[st].find(a => a.slug === slug).geo = [c[0], c[1], 6];
    });
    meta.geo = { shops_located: 0, source: "test", source_url: "https://www.openstreetmap.org/copyright", postcodes };
    write(path.join(tmpWeb, "data/meta.json"), "meta", meta);
    NEAR_URL = "file://" + path.join(tmpWeb, "index.html");
  }
  const nctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
                                          geolocation: { latitude: 3.075, longitude: 101.535 }, permissions: ["geolocation"] });
  const n = await nctx.newPage();
  n.on("pageerror", e => errors.push("pageerror: " + e.message));
  n.on("console", msg => { if (msg.type() === "error") errors.push("console: " + msg.text()); });
  await n.goto(NEAR_URL);
  await n.waitForFunction(() => window.__bakul && window.__bakul.d);
  check("'Near me' switch shows when shop locations exist" + (realGeo ? "" : " (test locations)"), await n.isVisible("#modeSeg"));
  await n.click("[data-mode='near']");
  check("near mode asks for location or postcode first", (await n.textContent("#whereNote")).includes("postcode"));
  await n.click("#locBtn");
  await n.waitForFunction(() => window.__bakul.d && window.__bakul.d.near);
  const nearInfo = await n.evaluate(() => {
    const d = window.__bakul.d;
    return { shops: d.premises.length, maxKm: Math.max(...d.premises.map(p => p[7])), r: d.near.radius,
             districts: d.near.districts.length, note: document.getElementById("whereNote").textContent };
  });
  check("location finds shops within the radius", nearInfo.shops >= 3 && nearInfo.maxKm <= nearInfo.r, JSON.stringify(nearInfo));
  check("near note says the location is not saved", /not saved/.test(nearInfo.note), nearInfo.note.slice(0, 90));
  await n.click("#pasteBtn2");
  await n.fill("#pasteIn", "beras 10kg, telur 1 papan, minyak masak 5kg, gula 1kg, ayam 1kg");
  await n.click("#matchBtn");
  await n.waitForSelector("#reviewStep:not([hidden])");
  await n.click("#addMatchesBtn");
  await n.waitForSelector("#rankList li .dist");
  const dists = await n.$$eval("#rankList li .dist", x => x.map(e => e.textContent));
  check("ranked shops show distance", dists.length > 0 && dists.every(t => /km|around/.test(t)), dists.slice(0, 3).join(" | "));
  check("summary talks about shops near you", (await n.textContent("#summary")).includes("km of you"));
  const stored = await n.evaluate(() => JSON.stringify(Object.assign({}, localStorage)));
  check("location is not stored on the device", !/3\.07|101\.53|lat|lon/.test(stored), stored.slice(0, 120));
  await n.selectOption("#radiusSel", "10");
  await n.waitForFunction(() => window.__bakul.d.near && window.__bakul.d.near.radius === 10);
  const n10 = await n.evaluate(() => window.__bakul.d.premises.length);
  check("a wider radius includes more shops", n10 >= nearInfo.shops, `${nearInfo.shops} -> ${n10}`);
  await shot(n, "mobile-13-near.png", { fullPage: true });
  // Postcode instead of GPS.
  const pc = await n.evaluate(() => Object.keys(window.__bakul.meta.geo.postcodes)[0]);
  await n.fill("#pcIn", pc);
  await n.click("#pcForm button");
  await n.waitForFunction(p => (document.getElementById("whereNote").textContent || "").includes(p), pc);
  check("postcode works instead of GPS", (await n.evaluate(() => window.__bakul.d.premises.length)) > 0, "postcode " + pc);
  await n.click("[data-mode='district']");
  await n.waitForFunction(() => window.__bakul.d && !window.__bakul.d.near);
  check("back to district mode", !(await n.isVisible("#nearBox")));
  check("no horizontal scroll (near me)", await noHScroll(n));
  await nctx.close();
  if (tmpWeb) fs.rmSync(tmpWeb, { recursive: true, force: true });

  // ---------- screenshot reading (needs a web server, as on the real site) ----------
  const ocrPng = path.join(ROOT, "tests/web/fixtures/list-screenshot.png");
  if (fs.existsSync(ocrPng)) {
    const http = require("http");
    const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".gz": "application/gzip", ".wasm": "application/wasm" };
    const srv = http.createServer((req, res) => {
      const f = path.join(ROOT, "web", decodeURIComponent(req.url.split("?")[0]).replace(/\/$/, "/index.html"));
      if (!f.startsWith(path.join(ROOT, "web")) || !fs.existsSync(f)) { res.statusCode = 404; return res.end(); }
      res.setHeader("Content-Type", types[path.extname(f)] || "application/octet-stream");
      fs.createReadStream(f).pipe(res);
    });
    await new Promise(r => srv.listen(0, "127.0.0.1", r));
    const octx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const o = await octx.newPage();
    o.on("pageerror", e => errors.push("pageerror: " + e.message));
    await o.goto(`http://127.0.0.1:${srv.address().port}/`);
    await o.waitForFunction(() => window.__bakul && window.__bakul.d);
    await o.click("#pasteBtn2");
    await o.setInputFiles("#imgIn", ocrPng);
    await o.waitForFunction(() => /Read \d+ lines|No text|Could not/.test(document.getElementById("ocrStatus").textContent), null, { timeout: 120000 });
    const txt = (await o.inputValue("#pasteIn")).toLowerCase();
    check("screenshot read on the device (OCR)", txt.includes("beras") && txt.includes("telur"), JSON.stringify(txt.slice(0, 80)));
    await o.click("#matchBtn");
    await o.waitForSelector("#reviewStep:not([hidden]) #matchList li");
    const okLines = await o.$$eval("#matchList li:not(.none)", x => x.length);
    check("screenshot lines match items", okLines >= 3, okLines + " matched");
    await octx.close();
    srv.close();
  } else check("screenshot reading test image present", false, ocrPng);

  // ---------- desktop ----------
  const dctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });
  const d = await dctx.newPage();
  d.on("pageerror", e => errors.push("pageerror: " + e.message));
  d.on("console", msg => { if (msg.type() === "error") errors.push("console: " + msg.text()); });
  await d.goto(URL);
  await d.waitForFunction(() => window.__bakul && window.__bakul.meta);
  await buildBasket(d);
  check("desktop two-column layout", await d.evaluate(() => {
    const cols = document.querySelectorAll(".grid > .col");
    return cols[1].getBoundingClientRect().left > cols[0].getBoundingClientRect().right - 1;
  }));
  check("no horizontal scroll (desktop)", await noHScroll(d));
  check("result bar hidden on desktop", !(await d.isVisible("#resultBar")));
  await shot(d, "desktop-01-basket.png");
  await d.click("#rankList ~ #moreBtn");
  await d.evaluate(() => window.scrollTo(0, 0));
  check("tie or zero saving never shows 'RM 0.00 cheaper'", !(await d.textContent("#summary")).includes("RM 0.00"));
  await shot(d, "desktop-02-basket-full.png", { fullPage: true });
  await d.click("#basketList li:nth-child(2) .bi-name");
  await d.waitForSelector("#itemDlg[open] svg.lc");
  await d.mouse.move(640, 450);
  await shot(d, "desktop-03-history.png");
  await d.keyboard.press("Escape");
  await d.click("[data-tab='trends']");
  await shot(d, "desktop-04-rises.png");
  await dctx.close();

  // ---------- system dark mode, narrow phone ----------
  const sctx = await browser.newContext({ viewport: { width: 360, height: 740 }, colorScheme: "dark", isMobile: true, hasTouch: true });
  const s = await sctx.newPage();
  s.on("pageerror", e => errors.push("pageerror: " + e.message));
  await s.goto(URL);
  await s.waitForFunction(() => window.__bakul && window.__bakul.meta);
  await buildBasket(s);
  check("system dark mode applies without toggle", (await s.evaluate(() => getComputedStyle(document.body).backgroundColor)) === "rgb(17, 19, 18)");
  check("no horizontal scroll (360px)", await noHScroll(s));
  await shot(s, "mobile-08-system-dark-360.png", { fullPage: true });
  await sctx.close();

  check("no page or console errors", errors.length === 0, errors.join(" | "));
  await browser.close();

  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  fs.writeFileSync(path.join(SHOTS, "e2e-results.json"), JSON.stringify({ when: new Date().toISOString(), results }, null, 1));
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
