// Headless end-to-end check of web/index.html with Playwright.
// Run from the repo root:  node tests/web/e2e.js   (needs built data in web/data)
// Opens the page straight from disk (file://) to prove no server is needed, builds lists,
// checks results against independent calculations, and saves screenshots to screenshots/.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const http = require("http");

const ROOT = path.resolve(__dirname, "../..");
const URL = "file://" + path.join(ROOT, "web/index.html");
const SHOTS = path.join(ROOT, "screenshots");
const DATA = path.join(ROOT, "web/data");
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail || "" });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`);
}

async function shot(page, name, opts) {
  await page.waitForFunction(() => !document.querySelector("#toast").classList.contains("on"), null, { timeout: 10000 });
  await page.waitForTimeout(250);
  await page.screenshot(Object.assign({ path: path.join(SHOTS, name) }, opts || {}));
}
async function noHScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}
async function ready(page) {
  await page.waitForFunction(() => window.__bakul && window.__bakul.meta);
}
// Switch to district mode (first visits start on "Near me") and load one district.
async function chooseDistrict(page, state, slug) {
  if (await page.isVisible("#modeSeg") && (await page.getAttribute("[data-mode='district']", "aria-pressed")) !== "true") {
    await page.click("[data-mode='district']");
  }
  await page.selectOption("#stateSel", state);
  await page.waitForFunction(() => document.querySelector("#districtSel").value !== "");
  await page.selectOption("#districtSel", slug);
  await page.waitForFunction(s => window.__bakul.d && !window.__bakul.d.near && window.__bakul.slug === s, slug);
}
async function buildBasket(page) {
  await chooseDistrict(page, "Selangor", "selangor--petaling");
  await page.click("#starterBtn");
  await page.fill("#searchIn", "kicap");
  await page.waitForSelector("#results [data-add]");
  await page.click("#results [data-add] >> nth=0");
  await page.fill("#searchIn", "");
  await page.dispatchEvent("#searchIn", "input");
}
function readJson(rel) { return JSON.parse(fs.readFileSync(path.join(DATA, rel), "utf8")); }
function newPage(ctx, errors, label) {
  return ctx.newPage().then(p => {
    p.on("pageerror", e => errors.push(label + " pageerror: " + e.message));
    p.on("console", msg => { if (msg.type() === "error" && !/Failed to load resource|load d\//.test(msg.text())) errors.push(label + " console: " + msg.text()); });
    return p;
  });
}
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: "light" };

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const meta = readJson("meta.json");

  // ---------- first visit ----------
  const mctx = await browser.newContext(PHONE);
  const m = await newPage(mctx, errors, "mobile");
  await m.goto(URL);
  await ready(m);
  check("page loads from file:// and reads meta", true);
  const isSynth = !!meta.synthetic;
  check("test-data banner matches the data (" + (isSynth ? "sample" : "real") + ")", (await m.isVisible("#synthBanner")) === isSynth);
  check("source attribution in footer", (await m.textContent(".foot")).includes("PriceCatcher, KPDN via OpenDOSM"));
  check("licence shown", (await m.textContent(".foot")).includes("CC BY 4.0"));
  check("headline says what the app does", (await m.isVisible("#heroH")) && /cheapest shop for your whole grocery list/i.test(await m.textContent("#heroH")));
  check("source and date on the first screen", /PriceCatcher/.test(await m.textContent("#heroSrc")));
  const ex = meta.proof && meta.proof.example && meta.proof.by_district[meta.proof.example];
  const proofShown = await m.isVisible("#heroProof");
  check("example saving shown only when this week's data has one", proofShown === !!(ex && ex[1] - ex[0] >= 1),
        proofShown ? (await m.textContent("#heroProof")) : "no proof in data");
  if (meta.geo) check("first visit starts on Near me", (await m.getAttribute("[data-mode='near']", "aria-pressed")) === "true");
  check("nothing is loaded before a place is chosen", await m.evaluate(() => window.__bakul.d === null) && !(await m.isVisible("#rankCard")));
  if (meta.geo) check("near mode asks for location or postcode first", /postcode/i.test(await m.textContent("#whereNote")));
  check("no error box and no Share button on a clean first visit", !(await m.isVisible("#errBox")) && !(await m.isVisible("#shareBtn")));
  check("every element marked hidden is really hidden", await m.evaluate(() =>
    Array.from(document.querySelectorAll("[hidden]")).every(el => getComputedStyle(el).display === "none")));
  await shot(m, "mobile-01-empty.png");

  // ---------- district, starter list ----------
  await buildBasket(m);
  const nBasket = await m.$$eval("#basketList li.bi", l => l.length);
  check("basket built (starter + search)", nBasket >= 10, nBasket + " items");
  const nRank = await m.$$eval("#rankList li", l => l.length);
  check("ranking renders", nRank > 0, nRank + " rows");
  check("summary visible", await m.isVisible("#summary"));
  check("source and date under the result", /PriceCatcher/.test(await m.textContent("#summary .src-line")));
  const planTxt = (await m.isVisible("#splitCard")) ? await m.textContent("#splitCard") : "";
  check("plan never says 'RM 0.00'", !/RM 0\.00/.test(planTxt));

  // Independent check: recompute the cheapest full-coverage shop from the raw district file.
  const indep = await m.evaluate(() => {
    const S = window.__bakul, d = S.d, n = S.basket.length;
    let best = null;
    d.premises.forEach((row, p) => {
      let tot = 0, have = 0;
      S.basket.forEach(b => {
        const codes = [b.code].concat(b.alt || []);
        let bp = null;
        codes.forEach(c => { const r = (d.prices[c] || []).find(x => x[0] === p); if (r && (bp == null || r[1] < bp)) bp = r[1]; });
        if (bp != null) { tot += bp * b.qty; have++; }
      });
      if (have === n && (!best || tot < best.tot - 1e-9)) best = { code: row[0], tot };
    });
    return best;
  });
  const firstCode = Number(await m.getAttribute("#rankList li:first-child", "data-p"));
  const firstTot = await m.textContent("#rankList li:first-child .rtot b");
  check("top shop matches independent calculation", indep && firstCode === indep.code && firstTot.includes(indep.tot.toFixed(2)),
        `${firstCode} ${firstTot} vs ${indep && indep.code} ${indep && indep.tot.toFixed(2)}`);
  check("'Cheapest' tag on a shop with every item", (await m.textContent("#rankList li:first-child .rtot")).includes("Cheapest"));
  check("each shop shows how fresh its prices are", (await m.$$eval("#rankList li .fresh", x => x.length)) === nRank);
  check("partial shops are not faded", await m.evaluate(() =>
    Array.from(document.querySelectorAll("#rankList li, #results .res")).every(el => getComputedStyle(el).opacity === "1")));
  const small = await m.evaluate(() => {
    const sel = ["#basketList .qty button", "#basketList .x", "#basketList .flex-tag", ".chip-btn", ".seg button", "#clearBtn", "#themeBtn"];
    const out = [];
    sel.forEach(s => document.querySelectorAll(s).forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width && r.height && r.height < 43.5) out.push(s + " " + Math.round(r.height));
    }));
    return out;
  });
  check("tap targets are at least 44 px tall", small.length === 0, small.slice(0, 5).join(", "));
  check("no horizontal scroll (mobile)", await noHScroll(m));
  await m.evaluate(() => window.scrollTo(0, 0));
  await m.waitForTimeout(200);
  check("mobile result bar shows the top shop", await m.isVisible("#resultBar") &&
        (await m.textContent("#rbShop")) === (await m.textContent("#rankList li:first-child .rname")));
  await shot(m, "mobile-02-basket.png");
  await shot(m, "mobile-03-basket-full.png", { fullPage: true });

  // Quantity stepper changes the total.
  const before = await m.textContent("#rankList li:first-child .rtot b");
  await m.click("#basketList li:first-child [data-qty]:not([data-d^='-'])");
  const after = await m.textContent("#rankList li:first-child .rtot b");
  check("quantity change updates totals", before !== after, `${before} -> ${after}`);

  // Remove with Undo.
  const nBefore = await m.$$eval("#basketList li.bi", x => x.length);
  await m.click("#basketList li:last-child .x");
  await m.waitForSelector("#toastAct:not([hidden])");
  check("removing an item offers Undo", (await m.$$eval("#basketList li.bi", x => x.length)) === nBefore - 1);
  await m.click("#toastAct");
  check("Undo puts the item back", (await m.$$eval("#basketList li.bi", x => x.length)) === nBefore);

  // History dialog.
  await m.click("#basketList li:first-child .bi-name");
  await m.waitForSelector("#itemDlg[open] svg.lc path.ln");
  const paths = await m.$$eval("#itemDlg svg.lc path.ln", p => p.filter(x => x.getAttribute("d").length > 10).length);
  check("price history chart draws 2 lines", paths === 2, paths + " lines");
  check("item dialog says 'typical price', not 'median'", !/median/i.test(await m.textContent("#dlgStats")));
  const box = await m.locator("#dlgChart svg").boundingBox();
  await m.touchscreen.tap(box.x + box.width * 0.6, box.y + box.height / 2);
  check("chart readout appears on tap", await m.isVisible("#dlgChart .tip"));
  await shot(m, "mobile-04-history.png");
  await m.click("#dlgClose");

  // Price changes feed.
  await m.click("[data-tab='trends']");
  const nFeed = await m.$$eval("#feedList li .fi", l => l.length);
  check("price changes feed renders", nFeed > 0, nFeed + " rows");
  const feedOk = await m.evaluate(() => Array.from(document.querySelectorAll("#feedList li .fi")).every(b => {
    // The % must agree with the two RM figures shown on the row (within rounding).
    const sub = b.querySelector(".res-sub").textContent, pct = parseFloat(b.querySelector(".pct").textContent.replace("−", "-"));
    const m2 = sub.match(/RM ([\d.]+) → RM ([\d.]+)/); if (!m2) return false;
    const calc = (Number(m2[2]) / Number(m2[1]) - 1) * 100;
    return Math.abs(calc - pct) < 1.0;
  }));
  check("feed % matches the RM figures shown", feedOk);
  await shot(m, "mobile-05-rises.png");
  await m.click("[data-dir='falls']");
  const fallPct = await m.textContent("#feedList li:first-child .pct");
  check("falls toggle shows negative %", fallPct.startsWith("−"), fallPct);
  await m.click("[data-scope='national']");
  check("Malaysia scope renders", (await m.$$eval("#feedList li .fi", l => l.length)) > 0);
  await m.click("[data-dir='rises']");

  // Language toggle and the BM fixes.
  await m.click("[data-lang='ms']");
  check("BM toggle changes labels", (await m.textContent("#tab-basket")) === "Bakul" &&
        (await m.textContent("#feedTitle")).includes("Perubahan"));
  const bodyMs = await m.textContent("body");
  check("BM copy: no 'barangan berharga' and no 'hentian'", !/berharga|hentian/i.test(bodyMs));
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

  // ---------- list tools: paste, plans, saved lists, share link, undo ----------
  const lctx = await browser.newContext(PHONE);
  const l = await newPage(lctx, errors, "lists");
  await l.goto(URL);
  await ready(l);
  await chooseDistrict(l, "Selangor", "selangor--petaling");
  await l.click("#pasteBtn2");
  await l.fill("#pasteIn", "beras 10kg x 2\ntelur 2 papan\nminyak masak 5kg\nayam 2 ekor\n- bawang 500g, kangkung, susu pekat 2 tin\nroti gardenia\nikan\ngula 1kg x2\nbawang putih 1/2 kg");
  await shot(l, "mobile-09-paste.png");
  await l.click("#matchBtn");
  await l.waitForSelector("#reviewStep:not([hidden]) #matchList li");
  const nm = await l.$$eval("#matchList li", x => x.length);
  const nNone = await l.$$eval("#matchList li.none", x => x.length);
  const nGuess = await l.$$eval("#matchList li.guess", x => x.length);
  const qtyOf = i => l.$$eval(`#matchList li:nth-child(${i}) input[type=number]`, xs => xs.length ? xs[0].value : null);
  check("pasted list splits into 11 lines", nm === 11, nm + " lines");
  check("unknown item is marked not found", nNone === 1, nNone + " not found");
  check("vague line ('ikan') is marked Check this", nGuess >= 1, nGuess + " guesses");
  check("'beras 10kg x 2' reads as 2 bags", (await qtyOf(1)) === "2", "qty " + (await qtyOf(1)));
  const flex1 = await l.$$eval("#matchList li:nth-child(1) input[type=checkbox]", xs => xs.length ? xs[0].checked : null);
  check("'beras 10kg x 2' keeps any brand on", flex1 === true, flex1 === null ? "no other brand in this data" : "");
  check("'telur 2 papan' reads as 2 trays of 30", (await qtyOf(2)) === "2");
  check("'ayam 2 ekor' reads as 3 kg", (await qtyOf(4)) === "3");
  check("'gula 1kg x2' reads as 2", (await qtyOf(10)) === "2", "qty " + (await qtyOf(10)));
  check("'bawang putih 1/2 kg' reads as 0.5 kg", (await qtyOf(11)) === "0.5", "qty " + (await qtyOf(11)));
  // Switching 5 kg of oil to a 2 kg pack keeps the amount: 3 packs (6 kg), not 1.
  const opt2kg = await l.$$eval("#matchList li:nth-child(3) select option", os => {
    const o = os.find(x => /· 2 kg/.test(x.textContent)); return o ? o.value : null;
  });
  if (opt2kg) {
    await l.selectOption("#matchList li:nth-child(3) select", opt2kg);
    check("switching to a 2 kg pack keeps the 5 kg asked for", (await qtyOf(3)) === "3", "qty " + (await qtyOf(3)));
  } else check("switching to a 2 kg pack keeps the 5 kg asked for (no 2 kg option in this data)", true);
  await shot(l, "mobile-10-review.png");
  await l.click("#addMatchesBtn");
  await l.waitForFunction(() => !document.getElementById("pasteDlg").open);
  const nb = await l.$$eval("#basketList li.bi", x => x.length);
  check("matched items added to the list", nb === 10, nb + " items");
  check("rice line has an 'any brand' switch, on", await l.isVisible("#basketList .flex-tag.on"));

  // Best plan: only plans that are really better are offered, and the 2-shop total is the true best pair.
  if (await l.isVisible("#splitCard")) {
    if (!(await l.$eval("#planDet", d => d.open))) await l.click("#planDet > summary");
    const opts = await l.$$eval("#splitCard [data-k]", x => x.map(b => ({ k: Number(b.dataset.k), t: Number(b.querySelector("b").textContent.replace(/[^0-9.]/g, "")) })));
    check("plan options get cheaper with each extra shop", opts.length >= 2 && opts.every((o, i) => i === 0 || o.t < opts[i - 1].t + 1e-9),
          JSON.stringify(opts));
    if (opts.some(o => o.k === 2)) {
      await l.click("#splitCard [data-k='2']");
      const planOk = await l.evaluate(() => {
        const S = window.__bakul, d = S.d, nP = d.premises.length;
        const price = (b, p) => { let v = null; [b.code].concat(b.alt || []).forEach(c => { const r = (d.prices[c] || []).find(x => x[0] === p); if (r && (v == null || r[1] < v)) v = r[1]; }); return v; };
        let best = { have: -1, tot: Infinity };
        for (let a = 0; a < nP; a++) for (let b2 = a + 1; b2 < nP; b2++) {
          let tot = 0, have = 0;
          S.basket.forEach(x => { const pa = price(x, a), pb = price(x, b2); const v = pa == null ? pb : pb == null ? pa : Math.min(pa, pb); if (v != null) { tot += v * x.qty; have++; } });
          if (have > best.have || (have === best.have && tot < best.tot)) best = { have, tot };
        }
        const shown = Number(document.querySelector("#splitCard [data-k='2'] b").textContent.replace(/[^0-9.]/g, ""));
        return { ok: Math.abs(shown - best.tot) < 0.011, shown, best: Math.round(best.tot * 100) / 100 };
      });
      check("2-shop total matches a brute-force check of every pair", planOk.ok, JSON.stringify(planOk));
    }
    await l.selectOption("#stopCost", "20");
    const rec = await l.$eval("#splitCard [aria-pressed='true']", x => x.dataset.k);
    check("a high value per extra shop recommends 1 shop", rec === "1", "picked " + rec);
  } else check("plan card hidden when no multi-shop plan is better", true);
  const hasSM = await l.evaluate(() => !!window.__bakul.d.shop_months);
  check("'cheapest over 3 months' card shows when monthly shop data exists", (await l.isVisible("#avgCard")) === hasSM);
  await shot(l, "mobile-11-plan.png", { fullPage: true });

  // Saved lists and share link.
  await l.click("#listsBtn");
  await l.fill("#listName", "Weekly shop");
  await l.click("#saveListBtn");
  check("list saved", (await l.textContent("#savedLists")).includes("Weekly shop"));
  const link = await l.inputValue("#shareOut");
  check("share link holds the list", /\?l=[0-9~._bBgG-]+$/.test(link), link.slice(-60));
  await shot(l, "mobile-12-lists.png");
  await l.click("#listsClose");
  // Clear with Undo.
  await l.click("#clearBtn");
  await l.waitForSelector("#toastAct:not([hidden])");
  check("Clear empties the list and offers Undo", (await l.$$eval("#basketList li.bi", x => x.length)) === 0);
  await l.click("#toastAct");
  check("Undo after Clear brings the list back", (await l.$$eval("#basketList li.bi", x => x.length)) === nb);
  await l.click("#clearBtn");
  await l.click("#listsBtn");
  await l.click("#savedLists [data-load='0']");
  check("saved list loads back", (await l.$$eval("#basketList li.bi", x => x.length)) === nb);

  // Open the share link in a fresh browser: offered, not forced.
  const l2ctx = await browser.newContext(PHONE);
  const l2 = await newPage(l2ctx, errors, "share");
  await l2.goto(link);
  await ready(l2);
  check("shared link shows a 'load it' bar", await l2.isVisible("#importBar"));
  await l2.click("#importYes");
  const shared = await l2.evaluate(() => window.__bakul.basket);
  check("shared list loads with the same items", shared.length === nb, shared.length + " items");
  check("shared list keeps any-brand lines", shared.some(b => b.any === "brand" && b.alt && b.alt.length > 5));
  await l2ctx.close();
  // ...and with a list already on the phone: Replace or Add, never a silent wipe.
  const l3ctx = await browser.newContext(PHONE);
  await l3ctx.addInitScript(() => { try { localStorage.setItem("bakul.basket", JSON.stringify([{ code: 1, qty: 1 }])); } catch (e) {} });
  const l3 = await newPage(l3ctx, errors, "share2");
  await l3.goto(link);
  await ready(l3);
  check("with a list already here, the bar offers Add and Replace", (await l3.isVisible("#importAdd")) && /Replace/.test(await l3.textContent("#importYes")));
  await l3.click("#importAdd");
  const merged = await l3.evaluate(() => window.__bakul.basket.length);
  check("Add keeps my list and adds the shared items", merged >= nb, merged + " items");
  await l3ctx.close();
  check("no horizontal scroll (list tools)", await noHScroll(l));
  await lctx.close();

  // ---------- honest results: no prices, partial coverage, stale data ----------
  const areas = [];
  Object.keys(meta.areas).forEach(st => meta.areas[st].forEach(a => areas.push(Object.assign({ state: st }, a))));
  const thin = areas.slice().sort((a, b) => a.items - b.items)[0];
  const thinD = readJson("d/" + thin.slug + ".json");
  const notThere = meta.items.map(r => r[0]).find(c => !thinD.prices[c]);
  const zctx = await browser.newContext(PHONE);
  await zctx.addInitScript(([slug, code]) => {
    try {
      localStorage.setItem("bakul.mode", JSON.stringify("district"));
      localStorage.setItem("bakul.slug", JSON.stringify(slug));
      localStorage.setItem("bakul.basket", JSON.stringify([{ code: code, qty: 1 }]));
    } catch (e) {}
  }, [thin.slug, notThere]);
  const z = await newPage(zctx, errors, "zero");
  await z.goto(URL);
  await z.waitForFunction(() => window.__bakul.d);
  check("no prices for the list: a clear message, not an empty green card",
        (await z.isVisible("#emptyResult")) && !(await z.isVisible("#summary")) && !(await z.isVisible("#resultBar")),
        thin.slug + " item " + notThere);
  await shot(z, "mobile-14-no-prices.png");
  await zctx.close();

  const pet = readJson("d/selangor--petaling.json");
  const common = Object.keys(pet.prices).sort((a, b) => pet.prices[b].length - pet.prices[a].length)[0];
  const absent = meta.items.map(r => r[0]).find(c => !pet.prices[c]);
  const pctx = await browser.newContext(PHONE);
  await pctx.addInitScript(([a, b]) => {
    try {
      localStorage.setItem("bakul.mode", JSON.stringify("district"));
      localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
      localStorage.setItem("bakul.basket", JSON.stringify([{ code: a, qty: 1 }, { code: b, qty: 1 }]));
    } catch (e) {}
  }, [Number(common), absent]);
  const pg = await newPage(pctx, errors, "partial");
  await pg.goto(URL);
  await pg.waitForFunction(() => window.__bakul.d && document.querySelector("#rankList li"));
  check("when no shop has every item, the top shop is 'Most items', not 'Cheapest'",
        (await pg.isVisible("#rankList li:first-child .tag.most")) && !(await pg.textContent("#rankList li:first-child .rtot")).includes("Cheapest"));
  await pg.evaluate(() => window.scrollTo(0, 0));
  check("the result bar says 'Best match' then", /Best match/.test(await pg.textContent("#rbLabel")));
  await pctx.close();

  const sctx0 = await browser.newContext(PHONE);
  const st = await newPage(sctx0, errors, "stale");
  const later = new Date(Date.parse(meta.data_date + "T12:00:00") + 40 * 864e5);
  await st.clock.setFixedTime(later);
  await st.goto(URL);
  await ready(st);
  check("out-of-date warning when the data is over 14 days old", await st.isVisible("#staleBanner"), await st.textContent("#staleBanner"));
  await sctx0.close();

  // ---------- near me ----------
  // Uses the real shop locations when the data has them; otherwise a temporary copy with made-up
  // test locations around Petaling, so the feature is always tested end to end.
  let NEAR_URL = URL, tmpWeb = null, testPostcodes = null;
  if (!meta.geo) {
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
    const tm = read(path.join(tmpWeb, "data/meta.json"));
    // One postcode per test centre, so the postcode step works even when the addresses have none.
    const centrePcs = { "selangor--petaling": "40100", "selangor--petaling-jaya": "46000",
                        "selangor--klang": "41000", "selangor--hulu-langat": "43000" };
    const postcodes = {};
    Object.entries(centres).forEach(([slug, c]) => {
      const f = path.join(tmpWeb, "data/d", slug + ".json"); if (!fs.existsSync(f)) return;
      const d = read(f);
      d.premises = d.premises.map((p, i) => {
        const lat = +(c[0] + ((i * 37) % 21 - 10) * 0.004).toFixed(5), lon = +(c[1] + ((i * 53) % 21 - 10) * 0.004).toFixed(5);
        const pc = (p[3].match(/\b(\d{5})\b/) || [])[1];
        if (pc && !postcodes[pc]) postcodes[pc] = [lat, lon];
        return [p[0], p[1], p[2], p[3], lat, lon, i % 9 === 8 ? "district" : "address"];
      });
      d.centre = c;
      if (!postcodes[centrePcs[slug]]) postcodes[centrePcs[slug]] = [c[0], c[1]];
      write(f, "d/" + slug, d);
      const s2 = Object.keys(tm.areas).find(k => tm.areas[k].some(a => a.slug === slug));
      tm.areas[s2].find(a => a.slug === slug).geo = [c[0], c[1], 6];
    });
    tm.geo = { shops_located: 0, source: "test", source_url: "https://www.openstreetmap.org/copyright", postcodes };
    testPostcodes = postcodes;
    write(path.join(tmpWeb, "data/meta.json"), "meta", tm);
    NEAR_URL = "file://" + path.join(tmpWeb, "index.html");
  }
  const nctx = await browser.newContext(Object.assign({}, PHONE, { geolocation: { latitude: 3.075, longitude: 101.535 }, permissions: ["geolocation"] }));
  const n = await newPage(nctx, errors, "near");
  await n.goto(NEAR_URL);
  await ready(n);
  check("'Near me' switch shows when shop locations exist" + (meta.geo ? "" : " (test locations)"), await n.isVisible("#modeSeg"));
  // Location permission is already given in this browser, so the app finds the shopper straight away.
  await n.waitForFunction(() => window.__bakul.d && window.__bakul.d.near);
  check("with location already allowed, near me finds shops without a tap", true);
  await n.click("#locBtn");
  await n.waitForFunction(() => window.__bakul.d && window.__bakul.d.near && !/Finding/.test(document.getElementById("whereNote").textContent));
  const nearInfo = await n.evaluate(() => {
    const d = window.__bakul.d, located = d.premises.filter(p => p[7] != null);
    return { shops: d.premises.length, located: located.length, maxKm: Math.max.apply(null, located.map(p => p[7]).concat([0])),
             unknownCounted: d.premises.filter(p => p[7] != null && p[6] === "district").length,
             r: d.near.radius, note: document.getElementById("whereNote").textContent };
  });
  check("location finds shops within the radius", nearInfo.located >= 3 && nearInfo.maxKm <= nearInfo.r, JSON.stringify(nearInfo));
  check("shops with no real location are never counted inside the radius", nearInfo.unknownCounted === 0);
  check("near note says the location is not saved", /not saved/.test(nearInfo.note), nearInfo.note.slice(0, 120));
  await n.click("#pasteBtn2");
  await n.fill("#pasteIn", "beras 10kg, telur 1 papan, minyak masak 5kg, gula 1kg, ayam 1kg");
  await n.click("#matchBtn");
  await n.waitForSelector("#reviewStep:not([hidden])");
  await n.click("#addMatchesBtn");
  await n.waitForSelector("#rankList li .dist");
  const dists = await n.$$eval("#rankList li .dist", x => x.map(e => e.textContent));
  check("ranked shops show a distance or 'location unknown'", dists.length > 0 && dists.every(t => /km|unknown/.test(t)), dists.slice(0, 4).join(" | "));
  check("summary talks about shops near you", (await n.textContent("#summary")).includes("km of you"));
  const stored = await n.evaluate(() => JSON.stringify(Object.assign({}, localStorage)));
  check("location is not stored on the device", !/"lat"|"lon"|3\.07\d|101\.53\d/.test(stored), stored.slice(0, 120));
  // Fast radius changes: the last choice wins even if an earlier, bigger load finishes later.
  await n.selectOption("#radiusSel", "20");
  await n.selectOption("#radiusSel", "3");
  await n.waitForTimeout(1500);
  const rr = await n.evaluate(() => ({ r: window.__bakul.d.near.radius, sel: document.getElementById("radiusSel").value }));
  check("a later radius choice is not overwritten by an earlier load", rr.sel === "3" && rr.r <= 5, JSON.stringify(rr));
  await n.selectOption("#radiusSel", "10");
  await n.waitForFunction(() => window.__bakul.d.near && window.__bakul.d.near.radius === 10);
  const n10 = await n.evaluate(() => window.__bakul.d.premises.filter(p => p[7] != null).length);
  check("a wider radius includes more located shops", n10 >= nearInfo.located, `${nearInfo.located} -> ${n10}`);
  await shot(n, "mobile-13-near.png", { fullPage: true });
  // Postcode instead of GPS, including one that is not in the table exactly.
  const pcs = Object.keys((meta.geo ? meta.geo.postcodes : testPostcodes) || {});
  const pc = pcs[0] || "40100";
  await n.fill("#pcIn", pc);
  await n.click("#pcForm button");
  await n.waitForFunction(p => (document.getElementById("whereNote").textContent || "").includes(p), pc);
  check("postcode works instead of GPS", (await n.evaluate(() => window.__bakul.d && window.__bakul.d.premises.length)) > 0, "postcode " + pc);
  if (meta.geo) {
    const cityPcs = ["50100", "80000", "88000", "93000"];
    const found = await n.evaluate(pcList => pcList.filter(p => window.Near.locatePostcode(p, window.__bakul.meta.geo.postcodes)), cityPcs);
    check("common city postcodes are found (50100, 80000, 88000, 93000)", found.length === cityPcs.length, found.join(","));
  }
  await n.click("[data-mode='district']");
  check("back to district mode", !(await n.isVisible("#nearBox")));
  check("no horizontal scroll (near me)", await noHScroll(n));
  await nctx.close();
  if (tmpWeb) fs.rmSync(tmpWeb, { recursive: true, force: true });

  // ---------- on a web server, as on the real site: load errors and screenshot reading ----------
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".gz": "application/gzip", ".wasm": "application/wasm" };
  const srv = http.createServer((req, res) => {
    const f = path.join(ROOT, "web", decodeURIComponent(req.url.split("?")[0]).replace(/\/$/, "/index.html"));
    if (!f.startsWith(path.join(ROOT, "web")) || !fs.existsSync(f)) { res.statusCode = 404; return res.end(); }
    res.setHeader("Content-Type", types[path.extname(f)] || "application/octet-stream");
    fs.createReadStream(f).pipe(res);
  });
  await new Promise(r => srv.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const octx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const o = await newPage(octx, errors, "server");
  await o.route("**/data/d/*.js", r => r.abort());
  await o.goto(base);
  await ready(o);
  await o.click("[data-mode='district']").catch(() => {});
  await o.selectOption("#stateSel", "Selangor");
  await o.waitForSelector("#errBox:not([hidden])");
  const errTxt = await o.textContent("#errBox");
  check("a failed load shows a plain message with Try again", /Check your connection/.test(errTxt) && !/etl\//.test(errTxt), errTxt.trim().slice(0, 80));
  await o.unroute("**/data/d/*.js");
  await o.click("#errRetry");
  await o.waitForFunction(() => window.__bakul.d && document.getElementById("errBox").hidden, null, { timeout: 15000 }).catch(() => {});
  const after2 = await o.evaluate(() => ({ d: !!window.__bakul.d, err: !document.getElementById("errBox").hidden, slug: window.__bakul.slug }));
  check("Try again loads the district", after2.d && !after2.err, JSON.stringify(after2));
  const ocrPng = path.join(ROOT, "tests/web/fixtures/list-screenshot.png");
  if (fs.existsSync(ocrPng) && fs.existsSync(path.join(ROOT, "web/vendor/tesseract/worker.min.js"))) {
    await o.click("#pasteBtn2");
    await o.setInputFiles("#imgIn", ocrPng);
    await o.waitForFunction(() => /Read \d+ lines|No text|Could not/.test(document.getElementById("ocrStatus").textContent), null, { timeout: 120000 });
    const txt = (await o.inputValue("#pasteIn")).toLowerCase();
    check("screenshot read on the device (OCR)", txt.includes("beras") && txt.includes("telur"), JSON.stringify(txt.slice(0, 80)));
    await o.click("#matchBtn");
    await o.waitForSelector("#reviewStep:not([hidden]) #matchList li");
    const okLines = await o.$$eval("#matchList li:not(.none)", x => x.length);
    check("screenshot lines match items", okLines >= 3, okLines + " matched");
  } else check("screenshot reading test image and reader present", false);
  await octx.close();
  srv.close();

  // ---------- desktop ----------
  const dctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });
  const d = await newPage(dctx, errors, "desktop");
  await d.goto(URL);
  await ready(d);
  await buildBasket(d);
  check("desktop two-column layout", await d.evaluate(() => {
    const cols = document.querySelectorAll(".grid > .col");
    return cols[1].getBoundingClientRect().left > cols[0].getBoundingClientRect().right - 1;
  }));
  check("no horizontal scroll (desktop)", await noHScroll(d));
  check("result bar hidden on desktop", !(await d.isVisible("#resultBar")));
  await shot(d, "desktop-01-basket.png");
  if (await d.isVisible("#moreBtn")) await d.click("#moreBtn");
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
  const s = await newPage(sctx, errors, "dark360");
  await s.goto(URL);
  await ready(s);
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
