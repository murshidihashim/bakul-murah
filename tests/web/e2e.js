// Headless end-to-end check of web/index.html with Playwright.
// Run from the repo root:  node tests/web/e2e.js   (needs built data in web/data)
// Opens the page straight from disk (file://) to prove no server is needed, builds lists,
// checks results against independent calculations, and saves screenshots to screenshots/.
// A check that needs something the data does not have is listed as SKIP, with the reason.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const http = require("http");

const ROOT = path.resolve(__dirname, "../..");
const URL = "file://" + path.join(ROOT, "web/index.html");
const SHOTS = path.join(ROOT, "screenshots");
const DATA = path.join(ROOT, "web/data");
// The live site. Screenshots show share links in this form, never the local folder the test runs from.
const SITE = "https://murshidihashim.github.io/bakul-murah/";
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, detail) {
  // The results file is committed with the screenshots: never write the local folder into it.
  detail = String(detail || "").split(ROOT).join("<repo>");
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`);
}
function skip(name, why) {
  results.push({ name, ok: true, skipped: true, detail: why });
  console.log(`SKIP  ${name}  (${why})`);
}

async function shot(page, name, opts) {
  await page.waitForFunction(() => !document.querySelector("#toast").classList.contains("on"), null, { timeout: 10000 });
  await page.waitForTimeout(250);
  await page.screenshot(Object.assign({ path: path.join(SHOTS, name) }, opts || {}));
}
// No sideways scroll: the page is no wider than the screen (documentElement.clientWidth). Not window.innerWidth:
// on a phone (isMobile) it grows to fit a page that is too wide, so a check against it can never fail.
async function noHScroll(page, name, note) {
  const w = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, screen: document.documentElement.clientWidth }));
  check(name, w.page <= w.screen + 1, `${w.page} px page, ${w.screen} px screen` + (note ? "; " + note : ""));
}
// The list lines fit the screen: no line, line name or figures under it wider than its box, none past the screen's
// edge, and no sideways scroll. Returns the problems found.
async function lineFit(page) {
  return page.evaluate(() => {
    const bad = [], screen = document.documentElement.clientWidth;
    document.querySelectorAll("#basketList li.bi").forEach(li => {
      const name = li.querySelector(".bi-name > span").textContent.slice(0, 30);
      [li.querySelector(".bi-row"), li.querySelector(".bi-name"), li.querySelector(".bi-name .res-sub")].forEach(el => {
        if (el && el.scrollWidth > el.clientWidth + 1) bad.push(`'${name}': ${el.className} is ${el.scrollWidth} px in a ${el.clientWidth} px box`);
      });
      if (li.getBoundingClientRect().right > screen + 1) bad.push(`'${name}' goes past the screen's edge`);
    });
    if (document.documentElement.scrollWidth > screen + 1) bad.push(`page ${document.documentElement.scrollWidth} px on a ${screen} px screen`);
    return bad;
  });
}
// After a dialog has closed: wait (up to 2 s) until keyboard focus is on a control again. When focus had dropped out
// of the dialog before Esc (its scroll box can stop scrolling when it is redrawn, and then takes no focus), the browser
// closes the dialog itself, and the page puts focus back once its 'close' event has run.
async function settled(page) {
  await page.waitForFunction(() => document.activeElement && document.activeElement !== document.body, null, { timeout: 2000 }).catch(() => {});
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

// Price changes list: on every row the % is the change between the two RM figures shown (allowing only for their
// rounding to the sen and the % to 0.1), with the sign of the list (rises +, falls −).
async function feedRows(page) {
  return page.evaluate(() => {
    const dir = window.__bakul.dir, bad = [];
    const rows = Array.from(document.querySelectorAll("#feedList li .fi"));
    rows.forEach(b => {
      const sub = b.querySelector(".res-sub").textContent, pt = b.querySelector(".pct").textContent;
      const m = sub.match(/RM ([\d.]+) → RM ([\d.]+)/), pct = parseFloat(pt.replace("−", "-"));
      if (!m || !isFinite(pct)) { bad.push(sub + " " + pt); return; }
      const a = Number(m[1]), z = Number(m[2]);
      const lo = ((z - 0.005) / (a + 0.005) - 1) * 100 - 0.05, hi = ((z + 0.005) / (a - 0.005) - 1) * 100 + 0.05;
      if (pct < lo - 1e-9 || pct > hi + 1e-9 || pt.charAt(0) !== (dir === "rises" ? "+" : "−")) bad.push(dir + ": RM " + m[1] + " → RM " + m[2] + " shown as " + pt);
    });
    return { n: rows.length, bad };
  });
}
// The plan card's message for the k-shop option, read against the option tagged 'Recommended' (rec). A plan with
// fewer shops points to the recommended one, a plan with more shops is compared with it, and only the recommended
// plan speaks for itself. Never 'One shop is ...' unless 1 shop is recommended, never an amount of RM 0.
// Returns the problem, or "" when the message fits.
function planMsgProblem(k, rec, msg) {
  if (!msg) return "no message";
  if (/RM\s0(\.00)?(?![.\d])|undefined|NaN|\{/.test(msg)) return "RM 0 or a value missing";
  const one = /One shop is/.test(msg), named = (msg.match(/We recommend (\d+) shops/) || [])[1], vsRec = /than the recommended plan/.test(msg);
  if (one && rec !== 1) return `'One shop' while ${rec} shops are recommended`;
  if (named && Number(named) !== rec) return `recommends ${named} shops, but ${rec} have the tag`;
  if (k < rec) return named ? "" : "does not point to the recommended plan";
  if (k > rec) return vsRec ? "" : "is not compared with the recommended plan";
  if (named || vsRec) return "the recommended plan argues against itself";
  return rec === 1 && !one ? "1 shop is recommended, but the message does not say so" : "";
}
// The 3-month card, against the monthly shop prices in the district file (district mode): it shows only when some
// shop had a price for every item in those months, lists only such shops (at most 3), names the first of them, and
// says a shop is 'cheapest today' only when today's top shop has every item.
async function avgCard(page) {
  return page.evaluate(() => {
    const S = window.__bakul, d = S.d, n = S.basket.length, M = (d.avg_months || []).length, sm = d.shop_months || {};
    const priced = (codes, p) => codes.some(c => (sm[c] || []).some(r => r[0] === p && r.slice(1, M + 1).some(v => v != null)));
    let full = 0;
    if (M && n) d.premises.forEach((row, p) => { if (S.basket.every(b => priced([b.code].concat(b.alt || []), p))) full++; });
    const card = document.getElementById("avgCard"), lis = Array.from(card.querySelectorAll(".avg-list li"));
    const first = lis.length && lis[0].querySelector(".rname") ? lis[0].querySelector(".rname").textContent : null;
    const head = card.querySelector("summary") ? card.querySelector("summary").textContent.trim() : "";
    const note = card.querySelector(".plan-msg"), top = document.querySelector("#rankList li[data-p]");
    return { full, shown: !card.hidden, rows: lis.length, head, note: note ? note.textContent : "",
             partial: lis.filter(li => li.classList.contains("partial") || /\b\d+ of \d+ items?\b/.test(li.textContent)).length,
             headOk: first != null && head.endsWith(": " + first), topFull: !!top && !top.classList.contains("partial") };
  });
}
function checkAvgCard(a, where) {
  check(`3-month card shows only when a shop had every item in those months (${where})`, a.shown === (a.full > 0),
        `${a.full} such shops, card ${a.shown ? "shown" : "hidden"}`);
  check(`3-month card never calls a shop without every item 'Cheapest' (${where})`,
        !a.shown || (a.partial === 0 && a.rows === Math.min(3, a.full) && a.headOk && (!a.note || a.topFull)),
        !a.shown ? "card hidden" : `${a.rows} rows, ${a.partial} without every item; '${a.head}'` +
          (a.note ? `; note: '${a.note}' (top shop today has every item: ${a.topFull})` : ""));
}
// Near me: the headline and the result bar may name only a shop inside the radius. That is the top ranked row,
// and it shows a distance ('1.2 km' or '~2 km'), not 'location unknown'. When no shop with a known place prices the
// list, the bar stays hidden and the headline must not claim 'km of you'.
// Returns the problem, "" when fine, or null when there are no results to check.
async function nearClaim(page) {
  return page.evaluate(() => {
    const S = window.__bakul, row = document.querySelector("#rankList li[data-p]"), sum = document.getElementById("summary");
    if (!S.d || !S.d.near || !row || sum.hidden) return null;
    if (!S.hasResult) return /km of you/.test(sum.textContent) ? "no shop with a known place, yet the headline says 'km of you'" : "";
    const name = row.querySelector(".rname").textContent, dist = row.querySelector(".dist") ? row.querySelector(".dist").textContent : "";
    const head = sum.querySelector(".shop"), bar = document.getElementById("rbShop").textContent;
    if (!/\bkm\b/.test(dist) || /unknown/i.test(dist)) return `top row '${name}' shows '${dist}'`;
    if (head && head.textContent !== name) return `headline names '${head.textContent}', the top row is '${name}'`;
    if (bar !== name) return `result bar names '${bar}', the top row is '${name}'`;
    return "";
  });
}

// Near me: a headline that counts shops with every item ('Only one shop ...', 'No shop ...', 'the cheaper of the 2
// shops ...', 'the priciest of the 3 shops ...') names where those shops are: 'within R km of you', or 'in <district>' when no
// shop with a known place prices the list. Its count matches the ranked list there, and shops with every item whose
// place is unknown (listed last) are counted in the line under it. So the list never contradicts the headline.
// Returns {problem ("" when fine), unknownFull}, or null when there are no results to check.
async function nearCounts(page) {
  return page.evaluate(() => {
    const S = window.__bakul, d = S.d, sum = document.getElementById("summary");
    if (!d || !d.near || sum.hidden || !sum.querySelector(".big")) return null;
    const n = S.basket.length, rows = window.Basket.rankStores(S.idx, d.premises.length, S.basket);
    const isLoc = p => d.premises[p][7] != null, anyLoc = rows.some(r => isLoc(r.pidx));
    const fullLoc = rows.filter(r => r.have === n && isLoc(r.pidx)).length, fullUnk = rows.filter(r => r.have === n && !isLoc(r.pidx)).length;
    const place = anyLoc ? "within " + d.near.radius + " km of you" : "in " + d.near.home, inPlace = anyLoc ? fullLoc : fullUnk;
    const big = sum.querySelector(".big").textContent, sub = (sum.querySelector(".muted") || {}).textContent || "";
    const also = sum.querySelector(".also-full"), wantAlso = anyLoc ? fullUnk : 0;
    const alsoN = also ? Number((also.textContent.match(/^(\d+)/) || [])[1]) : 0;
    let k = null, m;
    if (/^No shop /.test(big)) k = 0;
    else if (/^Only one shop /.test(big)) k = 1;
    else if ((m = (big + " " + sub).match(/ the (\d+) shops /))) k = Number(m[1]);
    let problem = "";
    if (!(big + " " + sub).includes(place)) problem = `'${big}' does not say '${place}'`;
    else if (k != null && k !== inPlace) problem = `'${big}' counts ${k}; the list has ${inPlace} ${place}`;
    else if (alsoN !== wantAlso) problem = `${wantAlso} shops with every item have no known place; the line under the headline says ` +
      (also ? `'${also.textContent}'` : "nothing");
    return { problem, unknownFull: wantAlso, head: big };
  });
}

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
  // The postcode is never kept, not even by the browser's form memory.
  check("postcode box is not kept by the browser (autocomplete off)", (await m.getAttribute("#pcIn", "autocomplete")) === "off");
  const ex = meta.proof && meta.proof.example && meta.proof.by_district[meta.proof.example];
  const proofShown = await m.isVisible("#heroProof");
  check("example saving shown only when the latest data has one", proofShown === !!(ex && ex[1] - ex[0] >= 1),
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

  // Independent check: recompute the cheapest full-coverage shop from the raw district file. Money is added up as
  // on screen: each line (price x amount) rounded to the sen.
  const indep = await m.evaluate(() => {
    const S = window.__bakul, d = S.d, n = S.basket.length;
    let best = null;
    d.premises.forEach((row, p) => {
      let sen = 0, have = 0;
      S.basket.forEach(b => {
        const codes = [b.code].concat(b.alt || []);
        let bp = null;
        codes.forEach(c => { const r = (d.prices[c] || []).find(x => x[0] === p); if (r && (bp == null || r[1] < bp)) bp = r[1]; });
        if (bp != null) { sen += Math.round(Math.round(bp * 100) * b.qty); have++; }
      });
      if (have === n && (!best || sen < best.sen)) best = { code: row[0], sen };
    });
    return best && { code: best.code, tot: (best.sen / 100).toFixed(2) };
  });
  const firstCode = Number(await m.getAttribute("#rankList li:first-child", "data-p"));
  const firstTot = await m.textContent("#rankList li:first-child .rtot b");
  check("top shop matches independent calculation", indep && firstCode === indep.code && firstTot === "RM " + indep.tot,
        `${firstCode} ${firstTot} vs ${indep && indep.code} RM ${indep && indep.tot}`);
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
  await noHScroll(m, "no horizontal scroll (mobile)");
  await m.evaluate(() => window.scrollTo(0, 0));
  await m.waitForFunction(() => !document.getElementById("resultBar").hidden, null, { timeout: 5000 }).catch(() => {});
  check("mobile result bar shows the top shop", await m.isVisible("#resultBar") &&
        (await m.textContent("#rbShop")) === (await m.textContent("#rankList li:first-child .rname")));
  await shot(m, "mobile-02-basket.png");
  await shot(m, "mobile-03-basket-full.png", { fullPage: true });
  // The bar hides itself once the results are on screen. With keyboard focus on it then, focus goes to the results
  // summary it points to, without scrolling the page (never to the page itself). The page is scrolled to the end of
  // the shop list, so the summary is above the screen: focusing it must not move the page there.
  await m.evaluate(() => window.scrollTo(0, 0));
  await m.waitForFunction(() => !document.getElementById("resultBar").hidden, null, { timeout: 5000 }).catch(() => {});
  await m.focus("#resultBar");
  const barAt = await m.evaluate(() => {
    const a = document.activeElement;
    document.getElementById("rankCard").scrollIntoView({ block: "end" });
    return { focus: a ? a.id || a.tagName : "none", y: window.scrollY, sumShown: document.getElementById("summary").getBoundingClientRect().bottom > 0 };
  });
  await m.waitForFunction(() => document.getElementById("resultBar").hidden, null, { timeout: 5000 }).catch(() => {});
  await m.waitForTimeout(300);
  const barGone = await m.evaluate(() => { const a = document.activeElement;
    return { hidden: document.getElementById("resultBar").hidden, focus: a ? a.id || a.tagName : "none", y: window.scrollY }; });
  check("the result bar hiding while it has keyboard focus moves focus to the results summary, without scrolling",
        barAt.focus === "resultBar" && barGone.hidden && barGone.focus === "summary" && barGone.y === barAt.y,
        `focus ${barAt.focus} -> ${barGone.focus}; bar hidden: ${barGone.hidden}; page at ${barAt.y} -> ${barGone.y} px ` +
        `(summary ${barAt.sumShown ? "on" : "above"} the screen)`);
  await m.evaluate(() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); });

  // A tap on '+' just above the result bar keeps the page still (the rebuilt button is focused again without
  // scrolling), so a quick second tap lands on the same '+'.
  const tapSel = "#basketList li.bi:nth-child(3) [data-qty]:not([data-d^='-'])";
  await m.evaluate(sel => { const r = document.querySelector(sel).getBoundingClientRect(); window.scrollBy(0, r.bottom - (window.innerHeight - 60)); }, tapSel);
  await m.waitForTimeout(400);
  const tapAt = await m.evaluate(sel => {
    const b = document.querySelector(sel), r = b.getBoundingClientRect(), code = Number(b.dataset.qty);
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, code, step: Number(b.dataset.d), q: window.__bakul.basket.filter(l => l.code === code)[0].qty, y0: window.scrollY };
  }, tapSel);
  await m.touchscreen.tap(tapAt.x, tapAt.y);
  await m.waitForTimeout(300);
  const tapY1 = await m.evaluate(() => window.scrollY);
  await m.touchscreen.tap(tapAt.x, tapAt.y);
  await m.waitForTimeout(300);
  const tapQ = await m.evaluate(code => window.__bakul.basket.filter(l => l.code === code)[0].qty, tapAt.code);
  check("a tap on '+' near the bottom of the screen keeps the page still, and a second tap adds again",
        tapY1 === tapAt.y0 && Math.abs(tapQ - (tapAt.q + 2 * tapAt.step)) < 1e-9,
        `page moved ${tapY1 - tapAt.y0} px; amount ${tapAt.q} -> ${tapQ}`);

  // Quantity stepper changes the total.
  const before = await m.textContent("#rankList li:first-child .rtot b");
  await m.click("#basketList li:first-child [data-qty]:not([data-d^='-'])");
  const after = await m.textContent("#rankList li:first-child .rtot b");
  check("quantity change updates totals", before !== after, `${before} -> ${after}`);
  // Screen readers hear the change: the status line names the shop and the total the result bar shows.
  const live = await m.waitForFunction(() => {
    const e = document.getElementById("resultLive"), t = e ? e.textContent : "";
    return !!t && t.includes(document.getElementById("rbShop").textContent) && t.includes(document.getElementById("rbTotal").textContent) && t;
  }, null, { timeout: 5000 }).then(h => h.jsonValue(), () => null);
  check("screen-reader status line names the top shop and its new total", !!live, live ||
        "status line: " + (await m.evaluate(() => { const e = document.getElementById("resultLive"); return e ? "'" + e.textContent + "'" : "missing"; })));

  // Remove with Undo.
  const nBefore = await m.$$eval("#basketList li.bi", x => x.length);
  await m.click("#basketList li:last-child .x");
  await m.waitForSelector("#toastAct:not([hidden])");
  check("removing an item offers Undo", (await m.$$eval("#basketList li.bi", x => x.length)) === nBefore - 1);
  const undoH = await m.$eval("#toastAct", b => b.getBoundingClientRect().height);
  check("Undo is at least 44 px tall", undoH >= 43.5, Math.round(undoH) + " px");
  await m.click("#toastAct");
  check("Undo puts the item back", (await m.$$eval("#basketList li.bi", x => x.length)) === nBefore);
  // The same by keyboard: Enter on Remove takes focus to Undo, and Tab from there to the next row's Remove.
  // Enter on Undo puts the item back, with focus on its Remove again.
  const firstRm = await m.getAttribute("#basketList li:first-child .x", "data-rm");
  await m.focus("#basketList li:first-child .x");
  await m.keyboard.press("Enter");
  await m.waitForSelector("#toastAct:not([hidden])");
  const onUndo = await m.evaluate(() => document.activeElement && document.activeElement.id);
  await m.keyboard.press("Tab");
  const onNext = await m.evaluate(() => document.activeElement === document.querySelector("#basketList li:first-child .x"));
  await m.focus("#toastAct");
  await m.keyboard.press("Enter");
  const kb = await m.evaluate(code => ({ n: document.querySelectorAll("#basketList li.bi").length,
    back: document.activeElement === document.querySelector('#basketList [data-rm="' + code + '"]') }), firstRm);
  check("keyboard: Remove moves focus to Undo, Tab to the next row, and Undo puts the item back with focus on it",
        onUndo === "toastAct" && onNext && kb.n === nBefore && kb.back,
        `focus after Remove: ${onUndo || "none"}; Tab to the next row's Remove: ${onNext}; ${kb.n} items; focus back on the row: ${kb.back}`);
  if (kb.n !== nBefore && await m.isVisible("#toastAct")) await m.click("#toastAct");   // the rest of the test needs the full list

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

  // Price changes feed: the % on each row is the change between its two RM figures, in rises and falls, here and
  // for Malaysia.
  await m.click("[data-tab='trends']");
  const nFeed = await m.$$eval("#feedList li .fi", l => l.length);
  check("price changes feed renders", nFeed > 0, nFeed + " rows");
  const feeds = [await feedRows(m)];
  await shot(m, "mobile-05-rises.png");
  await m.click("[data-dir='falls']");
  const fallPct = await m.textContent("#feedList li:first-child .pct");
  check("falls toggle shows negative %", fallPct.startsWith("−"), fallPct);
  feeds.push(await feedRows(m));
  await m.click("[data-scope='national']");
  check("Malaysia scope renders", (await m.$$eval("#feedList li .fi", l => l.length)) > 0);
  feeds.push(await feedRows(m));
  await m.click("[data-dir='rises']");
  feeds.push(await feedRows(m));
  const feedBad = [].concat(...feeds.map(f => f.bad));
  check("feed % equals the change between the two RM figures shown", feedBad.length === 0,
        feedBad.length ? feedBad.length + " rows, e.g. " + feedBad.slice(0, 2).join(" | ") : feeds.reduce((s, f) => s + f.n, 0) + " rows (rises, falls; my area, Malaysia)");

  // Language toggle and the BM fixes.
  await m.click("[data-lang='ms']");
  check("BM toggle changes labels", (await m.textContent("#tab-basket")) === "Bakul" &&
        (await m.textContent("#feedTitle")).includes("Perubahan"));
  const bodyMs = await m.textContent("body");
  check("BM copy: no 'barangan berharga' and no 'hentian'", !/berharga|hentian/i.test(bodyMs));
  // Every BM label, also the ones not on screen now. (Names in the data are not scanned: groups such as
  // 'BARANGAN KERING' are the survey's own words.)
  const bannedMs = await m.evaluate(() => Object.keys(window.I18N.ms).filter(k => /berharga|hentian|barangan/i.test(window.I18N.ms[k])));
  check("no BM label uses 'berharga', 'hentian' or 'barangan'", bannedMs.length === 0,
        bannedMs.length ? bannedMs.join(", ") : (await m.evaluate(() => Object.keys(window.I18N.ms).length)) + " labels");
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
        // A line's amount in sen at one shop, rounded line by line as on screen; null when the shop lacks it.
        const line = (b, p) => {
          let v = null;
          [b.code].concat(b.alt || []).forEach(c => { const r = (d.prices[c] || []).find(x => x[0] === p); if (r && (v == null || r[1] < v)) v = r[1]; });
          return v == null ? null : Math.round(Math.round(v * 100) * b.qty);
        };
        let best = { have: -1, sen: Infinity };
        for (let a = 0; a < nP; a++) for (let b2 = a + 1; b2 < nP; b2++) {
          let sen = 0, have = 0;
          S.basket.forEach(x => { const pa = line(x, a), pb = line(x, b2); const v = pa == null ? pb : pb == null ? pa : Math.min(pa, pb); if (v != null) { sen += v; have++; } });
          if (have > best.have || (have === best.have && sen < best.sen)) best = { have, sen };
        }
        const shown = Math.round(Number(document.querySelector("#splitCard [data-k='2'] b").textContent.replace(/[^0-9.]/g, "")) * 100);
        return { ok: shown === best.sen, shown: shown / 100, best: best.sen / 100 };
      });
      check("2-shop total matches a brute-force check of every pair", planOk.ok, JSON.stringify(planOk));
    }
    // Tap each option at each value of an extra shop: every message agrees with the 'Recommended' option.
    const planBad = [];
    let planTaps = 0;
    for (const cost of ["0", "2", "5", "10", "20"]) {
      await l.selectOption("#stopCost", cost);
      for (const k of await l.$$eval("#splitCard [data-k]", x => x.map(b => Number(b.dataset.k)))) {
        await l.click(`#splitCard [data-k='${k}']`);
        const p = await l.evaluate(() => ({
          recs: Array.from(document.querySelectorAll("#splitCard [data-k]")).filter(b => b.querySelector("i")).map(b => Number(b.dataset.k)),
          msg: (document.querySelector("#splitCard .plan-msg") || {}).textContent || "" }));
        planTaps++;
        const bad = p.recs.length !== 1 ? `${p.recs.length} options tagged 'Recommended'` : planMsgProblem(k, p.recs[0], p.msg);
        if (bad) planBad.push(`RM ${cost}, ${k} shop(s), ${p.recs.join("/") || "none"} recommended: ${bad}: '${p.msg}'`);
      }
    }
    check("each plan option's message agrees with the 'Recommended' option", planBad.length === 0,
          planBad.length ? planBad.slice(0, 2).join(" | ") : planTaps + " options at RM 0, 2, 5, 10 and 20");
    await l.selectOption("#stopCost", "20");
    const rec = await l.$eval("#splitCard [aria-pressed='true']", x => x.dataset.k);
    check("a high value per extra shop recommends 1 shop", rec === "1", "picked " + rec);
  } else check("plan card hidden when no multi-shop plan is better", true);
  checkAvgCard(await avgCard(l), "pasted list");
  await shot(l, "mobile-11-plan.png", { fullPage: true });

  // Saved lists and share link.
  await l.click("#listsBtn");
  await l.fill("#listName", "Weekly shop");
  await l.click("#saveListBtn");
  check("list saved", (await l.textContent("#savedLists")).includes("Weekly shop"));
  const link = await l.inputValue("#shareOut");
  check("share link holds the list", /\?l=[0-9~._bBgG-]+$/.test(link), link.slice(link.indexOf("?l=")).slice(-60));
  // The screenshot is published with the code: show the link as it reads on the live site, not the local folder
  // this test runs from (file:///home/<name>/...). The link itself was checked above.
  await l.$eval("#shareOut", (el, site) => { el.value = site + el.value.slice(el.value.indexOf("?l=")); }, SITE);
  await shot(l, "mobile-12-lists.png");
  check("lists screenshot shows the live-site link, not a local path", (await l.inputValue("#shareOut")).startsWith(SITE + "?l="));
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
  const linkQty1 = await l3.evaluate(() => { const l = (window.__bakul.shared || []).filter(x => x.code === 1)[0]; return l ? l.qty : null; });
  await l3.click("#importAdd");
  const merged = await l3.evaluate(() => window.__bakul.basket.length);
  check("Add keeps my list and adds the shared items", merged >= nb, merged + " items");
  // An item already on the list keeps the larger amount, and the message says so (never 'already has these items'
  // or only 'Added n items' when an amount went up).
  const addMsg = await l3.textContent("#toastMsg"), qty1 = await l3.evaluate(() => window.__bakul.basket.filter(b => b.code === 1)[0].qty);
  const raised = linkQty1 != null && linkQty1 > 1;
  check("Add says when it raised the amount of an item already on the list",
        raised ? qty1 === linkQty1 && /Raised the amount of 1 item/.test(addMsg) : qty1 === 1 && !/Raised/.test(addMsg),
        `amount on the phone 1, in the link ${linkQty1 == null ? "none" : linkQty1}, now ${qty1}; '${addMsg}'`);
  await l3ctx.close();
  // A long list comes back whole: saved values are checked when they are read, never cut short.
  const longCodes = meta.items.map(r => r[0]).slice(0, 90);
  const longName = "a list of more than 80 items comes back whole after a change and a reload";
  const longLinkName = "a shared link to a list of more than 80 items opens with every item, each once";
  if (longCodes.length <= 80) [longName, longLinkName].forEach(n => skip(n, `only ${longCodes.length} items in this data`));
  else {
    const llctx = await browser.newContext(PHONE);
    await llctx.addInitScript(cs => {
      try {
        if (sessionStorage.getItem("seeded")) return;
        sessionStorage.setItem("seeded", "1");
        const b = cs.map(c => ({ code: c, qty: 1 }));
        localStorage.setItem("bakul.basket", JSON.stringify(b));
        localStorage.setItem("bakul.lists", JSON.stringify([{ name: "Monthly", at: "2026-11-01", basket: b }]));
      } catch (e) {}
    }, longCodes);
    const ll = await newPage(llctx, errors, "long list");
    await ll.goto(URL);
    await ready(ll);
    await ll.click("#basketList li:first-child [data-qty]:not([data-d^='-'])");
    await ll.reload();
    await ready(ll);
    const llN = await ll.evaluate(() => ({ shown: document.querySelectorAll("#basketList li.bi").length,
      stored: JSON.parse(localStorage.getItem("bakul.basket")).length, saved: window.__bakul.lists[0].basket.length }));
    check(longName, llN.shown === longCodes.length && llN.stored === longCodes.length && llN.saved === longCodes.length,
          `${longCodes.length} items: ${JSON.stringify(llN)}`);
    // Its share link opens with every item too. The link is given one item a second time (another amount) and a code
    // not in the data: each item comes back once, with its first amount, and the unknown code is left out.
    await ll.click("#listsBtn");
    const longLink = await ll.inputValue("#shareOut"), qty0 = await ll.evaluate(() => window.__bakul.basket[0].qty);
    await ll.click("#listsClose");
    const unknown = Math.max.apply(null, meta.items.map(r => r[0])) + 1000;
    const slctx = await browser.newContext(PHONE);
    const sl = await newPage(slctx, errors, "long link");
    await sl.goto(`${longLink}_${longCodes[0]}~3_${unknown}~1`);
    await ready(sl);
    const slMsg = await sl.isVisible("#importBar") ? await sl.textContent("#importMsg") : "(no bar)";
    await sl.click("#importYes");
    const slGot = await sl.evaluate(() => ({ codes: window.__bakul.basket.map(b => b.code), qty0: window.__bakul.basket[0].qty,
      shown: document.querySelectorAll("#basketList li.bi").length }));
    check(longLinkName,
          slMsg.includes(`with ${longCodes.length} items`) && slGot.codes.join() === longCodes.join() && slGot.qty0 === qty0 && slGot.shown === longCodes.length,
          `${longCodes.length} items, a list part of ${longLink.length - longLink.indexOf("?l=")} characters: bar '${slMsg}'; loaded ${slGot.codes.length} (${slGot.shown} shown), first amount ${slGot.qty0} (was ${qty0})`);
    await slctx.close();
    await llctx.close();
  }
  // A list added before a place is chosen: once a place is chosen, a line with 'any brand' on is named after the brand
  // most shops there price, as when the list is read there (older data has no national shop counts to go by).
  const sfctx = await browser.newContext(PHONE);
  const sf = await newPage(sfctx, errors, "starter first");
  await sf.goto(URL);
  await ready(sf);
  await sf.click("#starterBtn");
  await chooseDistrict(sf, "Selangor", "selangor--petaling");
  const follow = await sf.evaluate(() => {
    const S = window.__bakul, cov = c => (S.idx.get(c) || new Map()).size, unit = c => S.items.get(c).unit.replace(/\s+/g, "").toLowerCase();
    const lines = S.basket.filter(b => b.any === "brand" && b.alt && b.alt.length);
    const bad = lines.filter(b => b.alt.some(c => S.items.has(c) && unit(c) === unit(b.code) && cov(c) > cov(b.code)))
      .map(b => S.items.get(b.code).item + " (" + cov(b.code) + " shops)");
    const notPriced = Array.from(document.querySelectorAll("#basketList li.bi")).filter(li => li.querySelector(".flex-tag.on") && /not priced here/.test(li.textContent)).length;
    return { lines: lines.length, bad, notPriced, names: lines.map(b => S.items.get(b.code).item + " (" + cov(b.code) + ")").slice(0, 3) };
  });
  const followName = "a list added before a place is chosen names each any-brand line after the brand most shops there price";
  if (!follow.lines) skip(followName, "no line with any brand on in this data");
  else check(followName, follow.bad.length === 0 && follow.notPriced === 0,
             follow.bad.length || follow.notPriced ? `named after less priced brands: ${follow.bad.join(", ")}; 'not priced here' with any brand on: ${follow.notPriced}`
                                                   : `${follow.lines} lines, e.g. ${follow.names.join(", ")}`);
  // The brands of that line in Petaling: the most priced one (the line's name there) and the least priced one.
  const kind = follow.lines ? await sf.evaluate(() => {
    const S = window.__bakul, b = S.basket.filter(x => x.any === "brand" && x.alt && x.alt.length)[0];
    const cov = c => (S.idx.get(c) || new Map()).size, all = [b.code].concat(b.alt);
    return { top: b.code, all, least: all.slice().sort((x, y) => cov(x) - cov(y))[0] };
  }) : null;
  // Two lines for the same kind of item (say one added from a shared link) are never renamed to the same item.
  const twinName = "two any-brand lines for the same kind of item keep different items after a place is chosen";
  const twin = follow.lines ? await sf.evaluate(() => {
    const S = window.__bakul, b = S.basket.filter(x => x.any === "brand" && x.alt && x.alt.length)[0];
    const least = b.alt.slice().sort((x, y) => (S.idx.get(x) || new Map()).size - (S.idx.get(y) || new Map()).size)[0];
    const second = { code: least, qty: 1, opts: [b.code].concat(b.alt.filter(c => c !== least)), alt: [b.code].concat(b.alt.filter(c => c !== least)), any: "brand" };
    localStorage.setItem("bakul.basket", JSON.stringify(S.basket.concat([second])));
    return S.basket.length + 1;
  }) : 0;
  if (!twin) skip(twinName, "no line with any brand on in this data");
  else {
    await sf.reload();
    await sf.waitForFunction(() => window.__bakul.d && document.querySelectorAll("#basketList li.bi").length);
    const codes = await sf.evaluate(() => window.__bakul.basket.map(b => b.code));
    check(twinName, codes.length === twin && new Set(codes).size === codes.length, codes.join(","));
  }
  // Keyboard focus stays on a line's 'Any brand OK' switch when a newly shown place renames the line (the place
  // finished loading after the shopper moved on to the list).
  const focusName = "keyboard focus stays on an any-brand line's switch when a newly shown place renames the line";
  await sfctx.close();
  if (!follow.lines) skip(focusName, "no line with any brand on in this data");
  else {
    const frctx = await browser.newContext(PHONE);
    const fr = await newPage(frctx, errors, "focus on rename");
    await fr.goto(URL);
    await ready(fr);
    await fr.click("#starterBtn");
    if (await fr.isVisible("#modeSeg")) await fr.click("[data-mode='district']");
    const at = await fr.evaluate(() => window.__bakul.basket.findIndex(b => b.any === "brand" && b.alt && b.alt.length));
    const was = await fr.evaluate(i => window.__bakul.basket[i].code, at);
    await fr.focus(`#basketList [data-flex="${was}"]`);
    // The state is chosen without moving focus, so the district loads while focus is on the switch.
    await fr.evaluate(() => { const s = document.getElementById("stateSel"); s.value = "Selangor"; s.dispatchEvent(new Event("change")); });
    await fr.waitForFunction(() => window.__bakul.d && !window.__bakul.d.near);
    const fo = await fr.evaluate(i => { const a = document.activeElement;
      return { now: window.__bakul.basket[i].code, focus: a && a.dataset && a.dataset.flex ? a.dataset.flex : a ? a.tagName : null, d: window.__bakul.d.district }; }, at);
    if (fo.now === was) skip(focusName, `the line keeps its item in ${fo.d}`);
    else check(focusName, fo.focus === String(fo.now), `in ${fo.d}, ${was} became ${fo.now}; focus on ${fo.focus}`);
    await frctx.close();
  }
  // A brand line next to an any-brand line of the same kind: removing the brand line and tapping Undo brings both lines
  // back as they were, also when another place was shown before Undo. Ticking 'Any brand OK' on a line for one brand
  // keeps keyboard focus on the switch and never leaves the line reading 'not priced here' while a brand it accepts is
  // priced here; a line whose brand is priced here keeps it; unticking gives back the shopper's brand. When the brand
  // line held the only priced brand, the any-brand line takes that brand's name once the brand line's Undo has gone
  // unused (Esc, timed out, or another message in its place), never when Undo is used, and never behind the line's
  // open price history. A line renamed behind that dialog gets keyboard focus back on its name when the dialog closes.
  const undoName = "Undo after removing a brand line next to an any-brand line of the same kind brings both lines back";
  const tickName = "ticking 'Any brand OK' keeps keyboard focus and never leaves 'not priced here' while a brand it accepts is priced here; unticking gives back the shopper's brand";
  const tickPricedName = "ticking 'Any brand OK' on a line whose brand is priced here keeps that brand and keyboard focus; unticking gives the same line back";
  const twinRmName = "removing the brand line that held the only priced brand: once its Undo has gone (Esc, or after 10 s) the any-brand line is named after that brand";
  const twinUndoName = "removing the brand line that held the only priced brand and tapping Undo brings both lines back as they were, with the same totals";
  const twinMsgName = "removing the brand line that held the only priced brand: when another message takes the place of its Undo, the any-brand line is named after that brand at once";
  const twinDlgName = "an Undo going unused while the any-brand line's price history is open: the dialog still says the item is in the list; on closing it the line is named after the freed brand, with keyboard focus on its name";
  const loadDlgName = "a place shown while a line's price history is open: the line keeps its item while the dialog shows it ('In your list'); closing the dialog names it after the brand most shops there price, with keyboard focus on its name";
  const countedBrandName = "an any-brand line whose own brand is not priced here, while a brand it accepts is (the totals count it), says 'not priced here · other brands are counted', in BM too, on one phone screen; with no brand it accepts priced here, only 'not priced here'";
  const others = st => (meta.areas[st] || []).map(a => a.slug).filter(s => s !== "selangor--petaling");
  if (!kind || kind.top === kind.least) {
    [undoName, tickName, tickPricedName, twinRmName, twinUndoName, twinMsgName, twinDlgName, loadDlgName, countedBrandName].forEach(n => skip(n, "no line with any brand on in this data"));
  } else {
    const seed = basket => async c => c.addInitScript(b => {
      try {
        if (sessionStorage.getItem("seeded")) return;
        sessionStorage.setItem("seeded", "1");
        localStorage.setItem("bakul.mode", JSON.stringify("district"));
        localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
        localStorage.setItem("bakul.basket", JSON.stringify(b));
      } catch (e) {}
    }, basket);
    const rest = kind.all.filter(c => c !== kind.least);
    const uctx = await browser.newContext(PHONE);
    await seed([{ code: kind.top, qty: 2 }, { code: kind.least, qty: 1, opts: rest, alt: rest, any: "brand" }])(uctx);
    const u = await newPage(uctx, errors, "undo brand line");
    await u.goto(URL);
    await u.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelectorAll("#basketList li.bi").length === 2);
    const lines = () => u.evaluate(() => window.__bakul.basket.map(b => b.code + "x" + b.qty + (b.alt && b.alt.length ? " any" : "")).join(", "));
    const start = await lines();
    const both = s => s.split(", ").length === 2 && s.split(", ").indexOf(kind.top + "x2") >= 0 && /\d+x1 any/.test(s) && new Set(s.split(", ").map(x => x.split("x")[0])).size === 2;
    await u.click(`#basketList [data-rm="${kind.top}"]`);
    const removed = await lines();
    await u.click("#toastAct");
    const back = await lines();
    // Again, with another district and then Petaling shown before Undo (each new place renames any-brand lines).
    let back2 = "not run (no other district in Selangor)";
    const other = others("Selangor")[0];
    if (other) {
      await u.click(`#basketList [data-rm="${kind.top}"]`);
      await u.selectOption("#districtSel", other);
      await u.waitForFunction(s => window.__bakul.d && window.__bakul.slug === s, other);
      await u.selectOption("#districtSel", "selangor--petaling");
      await u.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling");
      await u.click("#toastAct");
      back2 = await lines();
    }
    check(undoName, both(start) && both(back) && (!other || both(back2)),
          `start ${start}; after removing ${kind.top}: ${removed}; Undo: ${back}; Undo after ${other || "-"} and back: ${back2}`);
    await uctx.close();
    const tctx = await browser.newContext(PHONE);
    await seed([{ code: kind.least, qty: 1, opts: rest, any: "brand" }])(tctx);
    const tk = await newPage(tctx, errors, "tick any brand");
    await tk.goto(URL);
    await tk.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelector("#basketList [data-flex]"));
    // Shops in Petaling pricing each brand of the line.
    const cov = await tk.evaluate(cs => cs.map(c => (window.__bakul.idx.get(c) || new Map()).size), kind.all);
    const most = Math.max.apply(null, cov), leastCov = cov[kind.all.indexOf(kind.least)];
    const state = p => p.evaluate(() => {
      const S = window.__bakul, b = S.basket[0], a = document.activeElement;
      return { code: b.code, any: !!(b.alt && b.alt.length), shops: (S.idx.get(b.code) || new Map()).size,
               notHere: /not priced here/.test(document.querySelector("#basketList li.bi .bi-name").textContent),
               focus: a && a.dataset && a.dataset.flex ? "switch " + a.dataset.flex : a ? a.tagName : "none" };
    });
    const said = s => `${s.code}${s.any ? " any" : " one brand"} (${s.shops} shops${s.notHere ? ", 'not priced here'" : ""}), focus ${s.focus}`;
    await tk.focus("#basketList [data-flex]");
    await tk.keyboard.press("Space");
    const on = await state(tk);
    await tk.keyboard.press("Space");
    const off = await state(tk);
    // Ticked: its own brand when that is priced here, else a brand priced by the most shops here (when any is).
    const onOk = on.any && on.focus === "switch " + on.code && kind.all.indexOf(on.code) >= 0 &&
      (leastCov > 0 || !most ? on.code === kind.least : on.shops === most && !on.notHere);
    check(tickName, onOk && !off.any && off.code === kind.least && off.focus === "switch " + kind.least,
          `${kind.least} priced by ${leastCov} shops, the most by ${most}; tick: ${said(on)}; untick: ${said(off)}`);
    await tctx.close();
    // A line whose brand is priced here, by fewer shops than another brand it accepts: ticking keeps its brand (a
    // ticked line is renamed only when its brand is not priced here), and keyboard focus stays on its switch.
    const midAt = cov.map((n, i) => i).filter(i => cov[i] > 0 && cov[i] < most).sort((x, y) => cov[x] - cov[y] || x - y)[0];
    if (midAt === undefined) skip(tickPricedName, `no brand of the line is priced in Petaling by fewer shops than the most (${most})`);
    else {
      const mid = kind.all[midAt];
      const mctx = await browser.newContext(PHONE);
      await seed([{ code: mid, qty: 1, opts: kind.all.filter(c => c !== mid), any: "brand" }])(mctx);
      const mk = await newPage(mctx, errors, "tick a priced brand");
      await mk.goto(URL);
      await mk.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelector("#basketList [data-flex]"));
      await mk.focus("#basketList [data-flex]");
      await mk.keyboard.press("Space");
      const mOn = await state(mk);
      await mk.keyboard.press("Space");
      const mOff = await state(mk);
      check(tickPricedName, mOn.any && mOn.code === mid && !mOn.notHere && mOn.focus === "switch " + mid &&
            !mOff.any && mOff.code === mid && mOff.focus === "switch " + mid,
            `${mid} priced by ${cov[midAt]} shops, the most by ${most}; tick: ${said(mOn)}; untick: ${said(mOff)}`);
      await mctx.close();
    }
    // The brand line holds the only brand priced here among those the any-brand line accepts; the others are not
    // priced here. Removed by keyboard, then Esc on Undo; removed with a tap, then Undo left to time out; Undo tapped;
    // 'Saved' (My lists) taking the place of Undo; and Undo left to time out while the any-brand line's price history
    // is open.
    const unpriced = kind.all.filter((c, i) => cov[i] === 0 && c !== kind.top);
    if (!unpriced.length || !cov[kind.all.indexOf(kind.top)]) {
      [twinRmName, twinUndoName, twinMsgName, twinDlgName, countedBrandName].forEach(n => skip(n, `every brand of the line is priced in Petaling, or ${kind.top} is not`));
    } else {
      const twinOpts = [kind.top].concat(unpriced.slice(1));
      const twinBasket = [{ code: kind.top, qty: 2 }, { code: unpriced[0], qty: 1, opts: twinOpts, alt: twinOpts, any: "brand" }];
      const twinRun = async (label, removeAndLetUndoGo) => {
        const c = await browser.newContext(PHONE);
        await seed(twinBasket)(c);
        const p = await newPage(c, errors, label);
        await p.goto(URL);
        await p.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelectorAll("#basketList li.bi").length === 2);
        const st = () => p.evaluate(() => {
          const S = window.__bakul, a = document.activeElement, top = document.querySelector("#rankList li:first-child .rtot b");
          return { lines: S.basket.map(b => b.code + "x" + b.qty + (b.alt && b.alt.length ? " any" : "")).join(", "),
                   notHere: Array.from(document.querySelectorAll("#basketList li.bi")).filter(li => li.querySelector(".flex-tag.on") && /not priced here/.test(li.textContent)).length,
                   total: top ? top.textContent : "-", undo: document.getElementById("toast").classList.contains("on"),
                   focus: a ? (a.dataset && a.dataset.rm ? "remove " + a.dataset.rm : a.dataset && a.dataset.hist ? "name " + a.dataset.hist : a.id || a.tagName) : "none" };
        });
        const before = await st();
        const during = await removeAndLetUndoGo(p, st);       // while Undo is offered
        await p.waitForFunction(() => !document.getElementById("toast").classList.contains("on"), null, { timeout: 15000 });
        const after = await st();
        await c.close();
        return { before, during, after };
      };
      const esc = await twinRun("twin removal, Esc", async (p, st) => {
        await p.focus(`#basketList [data-rm="${kind.top}"]`);
        await p.keyboard.press("Enter");                     // a keyboard removal takes focus to Undo
        await p.waitForFunction(() => document.activeElement && document.activeElement.id === "toastAct");
        const during = await st();
        await p.keyboard.press("Escape");
        return during;
      });
      const timeout = await twinRun("twin removal, timeout", async (p, st) => {
        await p.click(`#basketList [data-rm="${kind.top}"]`);
        await p.waitForSelector("#toastAct:not([hidden])");
        return st();
      });
      // Both lines keep their own items at the start; afterwards one line, named after the priced brand, with keyboard
      // focus on its remove button (where it was, or where Esc on Undo goes back to). Only the name changes: the brands
      // the line accepts, and so the totals, stay as they were while Undo was offered.
      const renamed = r => r.before.lines === `${kind.top}x2, ${unpriced[0]}x1 any` && r.after.lines === `${kind.top}x1 any` &&
        r.after.notHere === 0 && !r.after.undo && r.after.focus === `remove ${kind.top}` && r.after.total === r.during.total;
      const show = r => `${r.before.lines} -> ${r.during.lines} (${r.during.notHere} 'not priced here' while Undo is offered, top total ${r.during.total}) -> ` +
        `${r.after.lines} (${r.after.notHere} 'not priced here', top total ${r.after.total}, focus ${r.after.focus})`;
      check(twinRmName, renamed(esc) && renamed(timeout), `Esc: ${show(esc)}; timeout: ${show(timeout)}`);
      // Undo tapped: both lines come back as they were (the any-brand line never takes the brand meanwhile), with the
      // starting totals, and keyboard focus on the brand line's remove button.
      const undone = await twinRun("twin removal, Undo", async (p, st) => {
        await p.click(`#basketList [data-rm="${kind.top}"]`);
        await p.waitForSelector("#toastAct:not([hidden])");
        const during = await st();
        await p.click("#toastAct");
        return during;
      });
      check(twinUndoName, undone.before.lines === `${kind.top}x2, ${unpriced[0]}x1 any` && undone.after.lines === undone.before.lines &&
            undone.after.total === undone.before.total && undone.after.notHere === undone.before.notHere && undone.after.focus === `remove ${kind.top}`,
            `${show(undone)}; at the start: top total ${undone.before.total}, ${undone.before.notHere} 'not priced here'`);
      // Another message in the place of Undo ('Saved', from My lists, draws nothing else): the line is renamed then.
      const msg = await twinRun("twin removal, another message", async (p, st) => {
        await p.click(`#basketList [data-rm="${kind.top}"]`);
        await p.waitForSelector("#toastAct:not([hidden])");
        const during = await st();
        await p.click("#listsBtn");
        await p.waitForFunction(() => document.getElementById("listsDlg").open);
        await p.fill("#listName", "twin");
        await p.click("#saveListBtn");
        await p.waitForFunction(() => document.getElementById("toast").classList.contains("on") && document.getElementById("toastAct").hidden);
        during.saved = await st();
        await p.click("#listsClose");
        return during;
      });
      check(twinMsgName, msg.before.lines === `${kind.top}x2, ${unpriced[0]}x1 any` && msg.during.saved.lines === `${kind.top}x1 any` &&
            msg.during.saved.notHere === 0 && msg.after.lines === `${kind.top}x1 any` && msg.after.notHere === 0 && msg.after.total === msg.during.total,
            `${show(msg)}; when 'Saved' showed: ${msg.during.saved.lines} (${msg.during.saved.notHere} 'not priced here')`);
      // Keyboard only: the brand line removed (focus on Undo), then the any-brand line's price history opened, and Undo
      // left to time out there. The dialog keeps saying the line's item is in the list; Esc closes it, the line is
      // named after the freed brand, and keyboard focus is back on the line's name.
      const dlgBtn = p => p.evaluate(() => { const b = document.getElementById("dlgAdd");
        return `'${b.textContent}'${b.getAttribute("aria-disabled") === "true" ? " (off)" : ""}`; });
      const dlg = await twinRun("twin removal, price history open", async (p, st) => {
        await p.focus(`#basketList [data-rm="${kind.top}"]`);
        await p.keyboard.press("Enter");
        await p.waitForFunction(() => document.activeElement && document.activeElement.id === "toastAct");
        const during = await st();
        await p.focus(`#basketList [data-hist="${unpriced[0]}"]`);
        await p.keyboard.press("Enter");
        await p.waitForFunction(() => document.getElementById("itemDlg").open);
        during.btnOpen = await dlgBtn(p);
        await p.waitForFunction(() => !document.getElementById("toast").classList.contains("on"), null, { timeout: 15000 });
        during.btnGone = await dlgBtn(p);
        await p.keyboard.press("Escape");
        await p.waitForFunction(() => !document.getElementById("itemDlg").open);
        return during;
      });
      check(twinDlgName, dlg.before.lines === `${kind.top}x2, ${unpriced[0]}x1 any` && /\(off\)$/.test(dlg.during.btnOpen) && dlg.during.btnGone === dlg.during.btnOpen &&
            dlg.after.lines === `${kind.top}x1 any` && dlg.after.notHere === 0 && dlg.after.total === dlg.during.total && dlg.after.focus === `name ${kind.top}`,
            `${show(dlg)}; the dialog's button ${dlg.during.btnOpen} when opened, ${dlg.during.btnGone} once Undo had gone`);
      // The wording of the any-brand line next to the brand line: its own brand is not priced in Petaling, but the brand
      // line's brand, which it accepts, is, and every total counts it for the line. With its switch off (it accepts no
      // other brand), or when no brand it accepts is priced here, nothing is counted for it: only 'not priced here'.
      // Each list in a fresh browser: the figures under each line in English, and in BM when asked, with the top total.
      const wording = async (basket, label, bm) => {
        const wc = await browser.newContext(PHONE);
        await seed(basket)(wc);
        const w = await newPage(wc, errors, label);
        await w.goto(URL);
        await w.waitForFunction(k => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelectorAll("#basketList li.bi").length === k, basket.length);
        const subs = () => w.evaluate(() => Array.from(document.querySelectorAll("#basketList li.bi .bi-name .res-sub")).map(s => s.textContent));
        const r = { subs: await subs(), fit: await lineFit(w),
                    top: await w.evaluate(() => { const b = document.querySelector("#rankList li:first-child .rtot b"); return b ? b.textContent : "-"; }) };
        if (bm) { await w.click("[data-lang='ms']"); r.ms = await subs(); r.fit = r.fit.concat(await lineFit(w)); }
        await wc.close();
        return r;
      };
      const wOn = await wording(twinBasket, "counted wording, brands", true);
      const wOff = (await wording([twinBasket[0], { code: unpriced[0], qty: 1, opts: twinOpts }], "counted wording, switch off")).subs[1];
      const wNone = unpriced.length > 1 ? (await wording([{ code: unpriced[0], qty: 1, opts: unpriced.slice(1), alt: unpriced.slice(1), any: "brand" }], "counted wording, nothing priced")).subs[0] : null;
      // The top shop's total, from the district file: both lines at the brand line's brand there (2 + 1 packs).
      const pet = readJson("d/selangor--petaling.json"), lowSen = Math.min.apply(null, (pet.prices[kind.top] || []).map(r => Math.round(r[1] * 100)));
      const wantTop = "RM " + (3 * lowSen / 100).toFixed(2);
      const ends = (s, tail) => typeof s === "string" && s.slice(-tail.length - 2) === "· " + tail;
      check(countedBrandName, ends(wOn.subs[1], "not priced here · other brands are counted") && wOn.top === wantTop &&
            ends(wOn.ms[1], "tiada harga di sini · jenama lain diambil kira") && !wOn.fit.length &&
            ends(wOff, "not priced here") && !/counted/.test(wOff) && (wNone == null || (ends(wNone, "not priced here") && !/counted/.test(wNone))),
            `EN '${wOn.subs[1]}', top total ${wOn.top} (from the data: ${wantTop}); BM '${wOn.ms[1]}'; ` +
            `phone (390 px): ${wOn.fit.join("; ") || "fits"}; switch off: '${wOff}'; ` +
            (wNone == null ? "no second unpriced brand to check" : `no brand it accepts priced here: '${wNone}'`));
    }
    // A place shown while a line's price history is open (it finished loading after the dialog was opened): the line
    // keeps its item while the dialog shows it, so the dialog still says it is in the list. Closing the dialog names
    // the line after the brand most shops there price, and puts keyboard focus back on its name.
    const ldctx = await browser.newContext(PHONE);
    await ldctx.addInitScript(b => {
      try {
        if (sessionStorage.getItem("seeded")) return;
        sessionStorage.setItem("seeded", "1");
        localStorage.setItem("bakul.mode", JSON.stringify("district"));
        localStorage.setItem("bakul.basket", JSON.stringify(b));
      } catch (e) {}
    }, [{ code: kind.least, qty: 1, opts: rest, alt: rest, any: "brand" }]);
    const ld = await newPage(ldctx, errors, "place shown behind price history");
    await ld.goto(URL);
    await ready(ld);
    await ld.waitForSelector(`#basketList [data-hist="${kind.least}"]`);
    await ld.focus(`#basketList [data-hist="${kind.least}"]`);
    await ld.keyboard.press("Enter");
    await ld.waitForFunction(() => document.getElementById("itemDlg").open);
    await ld.evaluate(() => {
      const s = document.getElementById("stateSel"), d = document.getElementById("districtSel");
      s.value = "Selangor"; s.dispatchEvent(new Event("change"));
      d.value = "selangor--petaling"; d.dispatchEvent(new Event("change"));
    });
    await ld.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling");
    const ldMid = await ld.evaluate(() => { const b = document.getElementById("dlgAdd");
      return { code: window.__bakul.basket[0].code, open: document.getElementById("itemDlg").open,
               btn: `'${b.textContent}'${b.getAttribute("aria-disabled") === "true" ? " (off)" : ""}` }; });
    await ld.keyboard.press("Escape");
    await ld.waitForFunction(() => !document.getElementById("itemDlg").open);
    await settled(ld);
    const ldEnd = await ld.evaluate(() => { const a = document.activeElement;
      return { code: window.__bakul.basket[0].code, focus: a ? (a.dataset && a.dataset.hist ? "name " + a.dataset.hist : a.id || a.tagName) : "none" }; });
    // The brands of the line (of its size) that most shops in Petaling price, from the district file.
    const ldPet = readJson("d/selangor--petaling.json"), ldCov = c => new Set((ldPet.prices[c] || []).map(r => r[0])).size;
    const ldUnit = c => String((meta.items.find(r => r[0] === c) || [])[2] || "").replace(/\s+/g, "").toLowerCase();
    const ldMost = Math.max.apply(null, kind.all.filter(c => ldUnit(c) === ldUnit(kind.least)).map(ldCov));
    if (ldCov(kind.least) >= ldMost) skip(loadDlgName, `${kind.least} is priced in Petaling by as many shops as any brand of the line`);
    else check(loadDlgName, ldMid.open && ldMid.code === kind.least && /\(off\)$/.test(ldMid.btn) &&
               ldEnd.code !== kind.least && ldCov(ldEnd.code) === ldMost && ldUnit(ldEnd.code) === ldUnit(kind.least) && ldEnd.focus === `name ${ldEnd.code}`,
               `with the dialog open in Petaling: line ${ldMid.code}, the dialog's button ${ldMid.btn}; after Esc: line ${ldEnd.code} ` +
               `(${ldCov(ldEnd.code)} shops in Petaling; the most for a brand of the line: ${ldMost}), focus on ${ldEnd.focus}`);
    await ldctx.close();
    // The same for a line whose own brand is priced in Petaling, by fewer shops than another brand it accepts: its name
    // is not stale there, but the place is new to the line, so the line follows it, also only once the dialog has
    // closed. A change of the shopper's own after that ('+' on the line) keeps the line's new name.
    const ldPricedName = "a place shown while the price history of a line whose own brand is priced there (by fewer shops than another brand it accepts) is open: the line keeps its item while the dialog shows it; closing the dialog names it after the brand most shops there price, with keyboard focus on its name, and a later '+' keeps that name";
    const ldOwn = kind.all.filter(c => ldUnit(c) === ldUnit(kind.least) && ldCov(c) > 0 && ldCov(c) < ldMost).sort((x, y) => ldCov(x) - ldCov(y) || x - y)[0];
    if (ldOwn === undefined) skip(ldPricedName, `no brand of the line is priced in Petaling by fewer shops than the most (${ldMost})`);
    else {
      const lpOpts = kind.all.filter(c => c !== ldOwn);
      const lpctx = await browser.newContext(PHONE);
      await lpctx.addInitScript(b => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("district"));
          localStorage.setItem("bakul.basket", JSON.stringify(b));
        } catch (e) {}
      }, [{ code: ldOwn, qty: 1, opts: lpOpts, alt: lpOpts, any: "brand" }]);
      const lpp = await newPage(lpctx, errors, "priced brand shown behind price history");
      await lpp.goto(URL);
      await ready(lpp);
      await lpp.waitForSelector(`#basketList [data-hist="${ldOwn}"]`);
      await lpp.focus(`#basketList [data-hist="${ldOwn}"]`);
      await lpp.keyboard.press("Enter");
      await lpp.waitForFunction(() => document.getElementById("itemDlg").open);
      await lpp.evaluate(() => {
        const s = document.getElementById("stateSel"), d = document.getElementById("districtSel");
        s.value = "Selangor"; s.dispatchEvent(new Event("change"));
        d.value = "selangor--petaling"; d.dispatchEvent(new Event("change"));
      });
      await lpp.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling");
      const lpMid = await lpp.evaluate(() => { const b = document.getElementById("dlgAdd");
        return { code: window.__bakul.basket[0].code, open: document.getElementById("itemDlg").open,
                 btn: `'${b.textContent}'${b.getAttribute("aria-disabled") === "true" ? " (off)" : ""}` }; });
      await lpp.keyboard.press("Escape");
      await lpp.waitForFunction(() => !document.getElementById("itemDlg").open);
      await settled(lpp);
      const lpEnd = await lpp.evaluate(() => { const a = document.activeElement;
        return { code: window.__bakul.basket[0].code, focus: a ? (a.dataset && a.dataset.hist ? "name " + a.dataset.hist : a.id || a.tagName) : "none" }; });
      await lpp.focus(`#basketList [data-qty="${lpEnd.code}"]:not([data-d^="-"])`);
      await lpp.keyboard.press("Enter");
      await lpp.waitForFunction(() => window.__bakul.basket[0].qty === 2, null, { timeout: 3000 }).catch(() => {});
      const lpPlus = await lpp.evaluate(() => ({ code: window.__bakul.basket[0].code, qty: window.__bakul.basket[0].qty }));
      check(ldPricedName, lpMid.open && lpMid.code === ldOwn && /\(off\)$/.test(lpMid.btn) &&
            lpEnd.code !== ldOwn && ldCov(lpEnd.code) === ldMost && ldUnit(lpEnd.code) === ldUnit(ldOwn) && lpEnd.focus === `name ${lpEnd.code}` &&
            lpPlus.code === lpEnd.code && lpPlus.qty === 2,
            `${ldOwn} (${ldCov(ldOwn)} shops in Petaling; the most for a brand of the line: ${ldMost}); with the dialog open: line ${lpMid.code}, ` +
            `the dialog's button ${lpMid.btn}; after Esc: line ${lpEnd.code} (${ldCov(lpEnd.code)} shops), focus on ${lpEnd.focus}; ` +
            `after '+': line ${lpPlus.code} x${lpPlus.qty}`);
      await lpctx.close();
    }
  }
  // The same from a row of the price changes (Malaysia): a list line of that row's item keeps it while the dialog shows
  // it ('In your list'); the place shown meanwhile renames the line once the dialog closes, and keyboard focus goes back
  // on the row (a line's new item is for its own controls only).
  const feedDlgName = "closing a price history opened from the price changes puts keyboard focus back on that row; a list line of the row's item keeps it while the dialog is open and follows a place shown meanwhile once it closes";
  {
    const pet = readJson("d/selangor--petaling.json"), pcov = c => new Set((pet.prices[c] || []).map(r => r[0])).size;
    const kinds = new Map(meta.items.map(r => [r[0], r[2].replace(/\s+/g, "").toLowerCase() + "|" + r[4]])), kindOf = c => kinds.get(c) || "";
    const fdctx = await browser.newContext(PHONE);
    await fdctx.addInitScript(() => {
      try {
        if (sessionStorage.getItem("seeded")) return;
        sessionStorage.setItem("seeded", "1");
        localStorage.setItem("bakul.mode", JSON.stringify("district"));
      } catch (e) {}
    });
    const fd = await newPage(fdctx, errors, "price changes row behind price history");
    await fd.goto(URL);
    await ready(fd);
    await fd.click("[data-tab='trends']");
    const rows = await fd.evaluate(() => Array.from(document.querySelectorAll("#feedList [data-hist]")).map(b => Number(b.dataset.hist)));
    // A row's item, and an item of the same size and kind that more shops in Petaling price (a line of the first,
    // with the second accepted, is named after the second there).
    let pair = null;
    rows.some(x => {
      const ys = meta.items.map(r => r[0]).filter(c => c !== x && kindOf(c) === kindOf(x) && pcov(c) > pcov(x)).sort((a, b) => pcov(b) - pcov(a) || a - b);
      if (ys.length) pair = { x, y: ys[0] };
      return !!pair;
    });
    await fdctx.close();
    if (!pair) skip(feedDlgName, "no price-changes row with an item of the same kind that more shops in Petaling price");
    else {
      // The list is set in a fresh browser before the page starts: a list stored just before a reload is not always
      // read back by the reloaded page on a busy machine.
      const fsctx = await browser.newContext(PHONE);
      await fsctx.addInitScript(b => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("district"));
          localStorage.setItem("bakul.basket", JSON.stringify(b));
        } catch (e) {}
      }, [{ code: pair.x, qty: 1, opts: [pair.y], alt: [pair.y], any: "brand" }]);
      const fdp = await newPage(fsctx, errors, "price changes row behind price history");
      await fdp.goto(URL);
      await ready(fdp);
      const seeded = await fdp.waitForFunction(c => window.__bakul.basket.length === 1 && window.__bakul.basket[0].code === c, pair.x, { timeout: 5000 })
        .then(() => true, () => false);
      if (!seeded) check(feedDlgName, false, `the list with ${pair.x} was not there when the page started: ${await fdp.evaluate(() => JSON.stringify(window.__bakul.basket))}`);
      else {
        await fdp.click("[data-tab='trends']");
        await fdp.click("[data-scope='national']");
        await fdp.focus(`#feedList [data-hist="${pair.x}"]`);
        await fdp.keyboard.press("Enter");
        await fdp.waitForFunction(() => document.getElementById("itemDlg").open);
        await fdp.evaluate(() => {
          const s = document.getElementById("stateSel"), d = document.getElementById("districtSel");
          s.value = "Selangor"; s.dispatchEvent(new Event("change"));
          d.value = "selangor--petaling"; d.dispatchEvent(new Event("change"));
        });
        await fdp.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling");
        const fdMid = await fdp.evaluate(() => { const b = document.getElementById("dlgAdd"), l = window.__bakul.basket[0];
          return { code: l ? l.code : "none", open: document.getElementById("itemDlg").open,
                   btn: `'${b.textContent}'${b.getAttribute("aria-disabled") === "true" ? " (off)" : ""}` }; });
        await fdp.keyboard.press("Escape");
        await fdp.waitForFunction(() => !document.getElementById("itemDlg").open);
        await settled(fdp);
        const fdEnd = await fdp.evaluate(() => { const a = document.activeElement;
          return !a ? "none" : a.dataset && a.dataset.hist ? (a.closest("#feedList") ? "price-changes row " : "list name ") + a.dataset.hist : a.id || a.tagName; });
        const fdCode = await fdp.evaluate(() => { const l = window.__bakul.basket[0]; return l ? l.code : "none"; });
        check(feedDlgName, fdMid.open && fdMid.code === pair.x && /\(off\)$/.test(fdMid.btn) && fdCode === pair.y && fdEnd === `price-changes row ${pair.x}`,
              `row ${pair.x} (${pcov(pair.x)} shops in Petaling); with the dialog open the list line is ${fdMid.code}, the dialog's button ${fdMid.btn}; ` +
              `after Esc the line is ${fdCode} (${pcov(pair.y)} shops), focus on ${fdEnd}`);
      }
      await fsctx.close();
    }
  }
  // Undo inside a dialog. A line removed by keyboard (focus goes to Undo), then another line's price history opened:
  // the toast with Undo moves into the dialog, and the place in the list that Undo goes back to is behind the dialog
  // (it takes no focus). Tab and Shift+Tab on Undo move on in the dialog, as from its other controls, and Undo used
  // there puts the line back with keyboard focus still in the dialog. The same with My lists open.
  const udTabName = "Tab and Shift+Tab on Undo inside a price history move focus on in the dialog (the list behind it takes no focus)";
  const udUseName = "Undo used inside a price history or My lists puts the removed line back and keeps keyboard focus in the dialog";
  {
    const pet = readJson("d/selangor--petaling.json"), pshops = c => new Set((pet.prices[c] || []).map(r => r[0])).size;
    const three = meta.items.map(r => r[0]).filter(c => pshops(c) > 0).sort((a, b) => pshops(b) - pshops(a) || a - b).slice(0, 3);
    if (three.length < 3) [udTabName, udUseName].forEach(n => skip(n, "fewer than 3 items priced in Petaling"));
    else {
      const udctx = await browser.newContext(PHONE);
      await udctx.addInitScript(b => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("district"));
          localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
          localStorage.setItem("bakul.lang", JSON.stringify("en"));
          localStorage.setItem("bakul.basket", JSON.stringify(b));
        } catch (e) {}
      }, three.map(c => ({ code: c, qty: 1 })));
      const ud = await newPage(udctx, errors, "undo inside a dialog");
      await ud.goto(URL);
      await ud.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelectorAll("#basketList li.bi").length === 3);
      const where = () => ud.evaluate(() => {
        const a = document.activeElement, dl = a && a.closest ? a.closest("dialog") : null;
        if (!a || a === document.body) return "the page";
        return (a.id ? "#" + a.id : a.tagName + (a.dataset.rm ? " remove " + a.dataset.rm : a.dataset.hist ? " name " + a.dataset.hist : "")) + (dl ? " in " + dl.id : "");
      });
      const codes = () => ud.evaluate(() => window.__bakul.basket.map(b => b.code).join(","));
      // Remove the second line by keyboard, Tab from Undo back to the list, then open the first line's price history.
      const removeSecond = async () => {
        await ud.focus(`#basketList [data-rm="${three[1]}"]`);
        await ud.keyboard.press("Enter");
        await ud.waitForFunction(() => document.activeElement && document.activeElement.id === "toastAct");
        await ud.keyboard.press("Tab");
      };
      await removeSecond();
      await ud.focus(`#basketList [data-hist="${three[0]}"]`);
      await ud.keyboard.press("Enter");
      await ud.waitForFunction(() => document.getElementById("itemDlg").open && document.getElementById("toast").parentNode.id === "itemDlg");
      await ud.focus("#toastAct");
      await ud.keyboard.press("Shift+Tab");
      const shiftTab = await where();
      await ud.focus("#toastAct");
      await ud.keyboard.press("Tab");
      const tab1 = await where();
      await ud.keyboard.press("Tab");
      const tab2 = await where();
      check(udTabName, shiftTab === "#dlgAdd in itemDlg" && tab1 !== "#toastAct in itemDlg" && / in itemDlg$/.test(tab2) && tab2 !== "#toastAct in itemDlg",
            `Shift+Tab on Undo: focus on ${shiftTab}; Tab on Undo: ${tab1}, then ${tab2}`);
      // Undo used in the price history (Enter), then Esc; then the same removal with My lists open.
      await ud.focus("#toastAct");
      await ud.keyboard.press("Enter");
      await ud.waitForFunction(() => window.__bakul.basket.length === 3);
      const inItem = { focus: await where(), codes: await codes(), open: await ud.evaluate(() => document.getElementById("itemDlg").open) };
      await ud.keyboard.press("Escape");
      await ud.waitForFunction(() => !document.getElementById("itemDlg").open);
      inItem.closed = await where();
      await removeSecond();
      await ud.focus("#listsBtn");
      await ud.keyboard.press("Enter");
      await ud.waitForFunction(() => document.getElementById("listsDlg").open && document.getElementById("toast").parentNode.id === "listsDlg");
      await ud.focus("#toastAct");
      await ud.keyboard.press("Enter");
      await ud.waitForFunction(() => window.__bakul.basket.length === 3);
      const inLists = { focus: await where(), codes: await codes(), open: await ud.evaluate(() => document.getElementById("listsDlg").open) };
      await ud.keyboard.press("Escape");
      await ud.waitForFunction(() => !document.getElementById("listsDlg").open);
      inLists.closed = await where();
      const want = three.join(",");
      const inDlg = (r, id) => r.open && r.codes === want && new RegExp(" in " + id + "$").test(r.focus) && r.focus.indexOf("#toastAct") !== 0;
      check(udUseName, inDlg(inItem, "itemDlg") && inItem.closed === `BUTTON name ${three[0]}` && inDlg(inLists, "listsDlg") && inLists.closed === "#listsBtn",
            `price history: lines ${inItem.codes} (were ${want}), dialog ${inItem.open ? "open" : "closed"}, focus on ${inItem.focus}; after Esc, ${inItem.closed}. ` +
            `My lists: lines ${inLists.codes}, dialog ${inLists.open ? "open" : "closed"}, focus on ${inLists.focus}; after Esc, ${inLists.closed}`);
      await udctx.close();
    }
  }
  // The price history opened by keyboard from a list line: focus on its title, which screen readers read as the item's
  // name (the dialog is named by it), not as one block of the whole dialog's text. On a phone with a place (a long
  // dialog), a desktop screen with a place, and a phone with none (short dialogs). Tab goes on to its Close button, and
  // Esc goes back to the line.
  const dlgOpenName = "the price history opens by keyboard with focus on its title, read as the item's name (not the whole dialog), on a phone and a desktop screen, with a place and without; Tab goes on to Close and Esc back to the line";
  {
    const pet = readJson("d/selangor--petaling.json"), pshops = c => new Set((pet.prices[c] || []).map(r => r[0])).size;
    const top = meta.items.map(r => r[0]).filter(c => pshops(c) > 0).sort((a, b) => pshops(b) - pshops(a) || a - b)[0];
    if (top == null) skip(dlgOpenName, "no item priced in Petaling");
    else {
      const seen = [];
      for (const [label, o, slug] of [["phone, Petaling", PHONE, "selangor--petaling"], ["desktop, Petaling", { viewport: { width: 1280, height: 900 } }, "selangor--petaling"],
                                      ["phone, no place", PHONE, null]]) {
        const octx = await browser.newContext(o);
        await octx.addInitScript(([c, s]) => {
          try {
            if (sessionStorage.getItem("seeded")) return;
            sessionStorage.setItem("seeded", "1");
            localStorage.setItem("bakul.mode", JSON.stringify("district"));
            if (s) localStorage.setItem("bakul.slug", JSON.stringify(s));
            localStorage.setItem("bakul.lang", JSON.stringify("en"));
            localStorage.setItem("bakul.basket", JSON.stringify([{ code: c, qty: 1 }]));
          } catch (e) {}
        }, [top, slug]);
        const op = await newPage(octx, errors, "price history opened by keyboard");
        await op.goto(URL);
        await op.waitForFunction(s => window.__bakul && window.__bakul.meta && document.querySelector("#basketList [data-hist]") &&
          (s ? !!window.__bakul.d && window.__bakul.slug === s : !window.__bakul.d), slug);
        const at = () => op.evaluate(() => { const a = document.activeElement;
          return !a || a === document.body ? "the page" : a.id ? "#" + a.id : a.classList.contains("sheet-inner") ? "the scroll box" : a.tagName + (a.dataset.hist ? " name " + a.dataset.hist : ""); });
        await op.focus(`#basketList [data-hist="${top}"]`);
        await op.keyboard.press("Enter");
        await op.waitForFunction(() => document.getElementById("itemDlg").open);
        const focus = await at(), title = (await op.textContent("#dlgTitle")).replace(/\s+/g, " ").trim();
        const long = await op.evaluate(() => { const s = document.querySelector("#itemDlg .sheet-inner"); return s.scrollHeight > s.clientHeight; });
        // What the browser gives screen readers for the element with focus (the page itself also counts as focused).
        const cdp = await octx.newCDPSession(op);
        await cdp.send("Accessibility.enable");
        const { nodes } = await cdp.send("Accessibility.getFullAXTree");
        const ax = nodes.filter(n => (n.properties || []).some(p => p.name === "focused" && p.value && p.value.value) && !(n.role && n.role.value === "RootWebArea"))
          .map(n => `${n.role ? n.role.value : "?"} '${(n.name ? n.name.value : "").replace(/\s+/g, " ").trim()}'`);
        await op.keyboard.press("Tab");
        const tab = await at();
        await op.keyboard.press("Escape");
        await op.waitForFunction(() => !document.getElementById("itemDlg").open);
        await settled(op);
        seen.push({ label, focus, title, long, ax, tab, esc: await at() });
        await octx.close();
      }
      check(dlgOpenName, seen.every(r => r.focus === "#dlgTitle" && r.ax.length === 1 && r.ax[0] === `heading '${r.title}'` && r.tab === "#dlgClose" && r.esc === `BUTTON name ${top}`),
            seen.map(r => `${r.label} (${r.long ? "a long" : "a short"} dialog): focus on ${r.focus}, read as ${r.ax.join(" | ") || "nothing"}; Tab: ${r.tab}; Esc: ${r.esc}`).join("; "));
    }
  }
  // An 'Other grades or sizes OK' line (eggs, as the list reader reads 'telur 1 papan'), with the switch on, in the first
  // district whose shops price none of its own grade but another grade or size it accepts (the totals count that), and
  // in the first district whose shops price none of them (nothing is counted: only 'not priced here').
  const countedGradeName = "an 'Other grades or sizes OK' line whose own grade is not priced here, while another it accepts is, says 'not priced here · other grades or sizes are counted', in BM too, on one phone screen; with none priced, only 'not priced here'";
  {
    const egg = await l.evaluate(() => { const m = window.__bakul.cat.matchText("telur 1 papan")[0];
      return m && m.code != null && m.alt && m.alt.length && !m.flex ? { code: m.code, alt: m.alt } : null; });
    const slugs = [].concat.apply([], Object.keys(meta.areas).map(st => meta.areas[st].map(a => a.slug)));
    let gradeAt = null, noneAt = null;
    if (egg) slugs.some(s => {
      const d = readJson("d/" + s + ".json"), shops = c => new Set((d.prices[c] || []).map(r => r[0])).size;
      if (shops(egg.code)) return false;
      if (egg.alt.some(c => shops(c))) { if (!gradeAt) gradeAt = { slug: s, d }; }
      else if (!noneAt) noneAt = s;
      return !!(gradeAt && noneAt);
    });
    if (!gradeAt) skip(countedGradeName, egg ? `no district prices another grade or size of ${egg.code} without ${egg.code} itself` : "no 'Other grades or sizes' line for 'telur 1 papan' in this data");
    else {
      // The line in one district, in a fresh browser: the figures under it in English, and in BM when asked.
      const gWording = async (slug, label, bm) => {
        const gctx = await browser.newContext(PHONE);
        await gctx.addInitScript(([b, s]) => {
          try {
            if (sessionStorage.getItem("seeded")) return;
            sessionStorage.setItem("seeded", "1");
            localStorage.setItem("bakul.mode", JSON.stringify("district"));
            localStorage.setItem("bakul.slug", JSON.stringify(s));
            localStorage.setItem("bakul.lang", JSON.stringify("en"));
            localStorage.setItem("bakul.basket", JSON.stringify(b));
          } catch (e) {}
        }, [[{ code: egg.code, qty: 1, opts: egg.alt, alt: egg.alt }], slug]);
        const g = await newPage(gctx, errors, label);
        await g.goto(URL);
        await g.waitForFunction(s => window.__bakul.d && window.__bakul.slug === s && document.querySelector("#basketList li.bi"), slug);
        const sub = () => g.evaluate(() => document.querySelector("#basketList li.bi .bi-name .res-sub").textContent);
        const r = { sub: await sub(), fit: await lineFit(g), sw: await g.evaluate(() => document.querySelector("#basketList [data-flex]").textContent),
                    top: await g.evaluate(() => { const b = document.querySelector("#rankList li:first-child .rtot b"); return b ? b.textContent : "-"; }) };
        if (bm) { await g.click("[data-lang='ms']"); r.ms = await sub(); r.fit = r.fit.concat(await lineFit(g)); }
        await gctx.close();
        return r;
      };
      const gEn = await gWording(gradeAt.slug, "counted wording, grades", true);
      const gNone = noneAt ? (await gWording(noneAt, "counted wording, no grade priced")).sub : null;
      // The top shop's total, from the district file: the line at its cheapest accepted grade or size in each shop.
      const best = new Map();
      [egg.code].concat(egg.alt).forEach(c => (gradeAt.d.prices[c] || []).forEach(r => {
        const sen = Math.round(r[1] * 100); if (!best.has(r[0]) || sen < best.get(r[0])) best.set(r[0], sen);
      }));
      const wantTop = "RM " + (Math.min.apply(null, Array.from(best.values())) / 100).toFixed(2);
      const ends = (s, tail) => typeof s === "string" && s.slice(-tail.length - 2) === "· " + tail;
      check(countedGradeName, /Other grades or sizes OK/.test(gEn.sw) && ends(gEn.sub, "not priced here · other grades or sizes are counted") && gEn.top === wantTop &&
            ends(gEn.ms, "tiada harga di sini · gred atau saiz lain diambil kira") && !gEn.fit.length &&
            (gNone == null || (ends(gNone, "not priced here") && !/counted/.test(gNone))),
            `${egg.code} with ${egg.alt.join(", ")} accepted, in ${gradeAt.slug}: EN '${gEn.sub}', top total ${gEn.top} (from the data: ${wantTop}); BM '${gEn.ms}'; ` +
            `phone (390 px): ${gEn.fit.join("; ") || "fits"}; ` + (gNone == null ? "no district without any of them" : `in ${noneAt} (none priced): '${gNone}'`));
    }
  }
  await noHScroll(l, "no horizontal scroll (list tools)");
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
  if (absent == null) skip("3-month card with an item no shop here prices", "every item is priced in Petaling in this data");
  else checkAvgCard(await avgCard(pg), "an item no shop here prices");
  await pctx.close();

  const sctx0 = await browser.newContext(PHONE);
  const st = await newPage(sctx0, errors, "stale");
  const later = new Date(Date.parse(meta.data_date + "T12:00:00") + 40 * 864e5);
  await st.clock.setFixedTime(later);
  await st.goto(URL);
  await ready(st);
  check("out-of-date warning when the data is over 14 days old", await st.isVisible("#staleBanner"), await st.textContent("#staleBanner"));
  await sctx0.close();

  // Prices age from the survey date to today: 10 days after it (before the out-of-date warning shows), no shop's
  // prices are 'this week', and the item dialog counts the days to today.
  const fctx = await browser.newContext(PHONE);
  await fctx.addInitScript(code => {
    try {
      localStorage.setItem("bakul.mode", JSON.stringify("district"));
      localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
      localStorage.setItem("bakul.basket", JSON.stringify([{ code: code, qty: 1 }]));
    } catch (e) {}
  }, Number(common));
  const fr = await newPage(fctx, errors, "fresh");
  await fr.clock.setFixedTime(new Date(Date.parse(meta.data_date + "T12:00:00") + 10 * 864e5));
  await fr.goto(URL);
  await fr.waitForFunction(() => window.__bakul.d && document.querySelector("#rankList li .fresh"));
  if (await fr.isVisible("#moreBtn")) await fr.click("#moreBtn");
  const fresh = await fr.$$eval("#rankList li .fresh", x => x.map(e => e.textContent));
  const tooNew = fresh.filter(s => !(Number((s.match(/^prices up to (\d+) days old$/) || [])[1]) >= 10));
  check("10 days after the survey date, no shop says 'prices this week'", fresh.length > 0 && tooNew.length === 0,
        `${fresh.length} shops` + (tooNew.length ? ", e.g. '" + tooNew[0] + "'" : ", e.g. '" + fresh[0] + "'"));
  await fr.click("#basketList li:first-child .bi-name");
  await fr.waitForSelector("#itemDlg[open] #dlgShops li");
  const dlgAges = await fr.$$eval("#dlgShops li small", x => x.map(e => e.textContent));
  const dlgNew = dlgAges.filter(s => !(Number((s.match(/(\d+) days ago$/) || [])[1]) >= 10));
  check("10 days after the survey date, the item dialog counts the days to today", dlgAges.length > 0 && dlgNew.length === 0,
        `${dlgAges.length} shops` + (dlgNew.length ? ", e.g. '" + dlgNew[0] + "'" : ", e.g. '" + dlgAges[0] + "'"));
  await fctx.close();

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
  const claims = [["GPS", await nearClaim(n)]], counts = [["GPS", await nearCounts(n)]];
  // An item's figures in Near me count shops with a known place only, like the headline: the number of shops on its
  // line, and the item dialog's cheapest shops near you (else they name the home district).
  const item = await n.evaluate(() => {
    const S = window.__bakul, d = S.d, b = S.basket[0], m = S.idx.get(b.code) || new Map();
    let loc = 0; m.forEach((v, p) => { if (d.premises[p][7] != null) loc++; });
    return { code: b.code, loc, all: m.size, home: d.near.home, line: document.querySelector("#basketList li.bi .res-sub").textContent };
  });
  await n.click(`#basketList [data-hist="${item.code}"]`);
  await n.waitForSelector("#itemDlg[open]");
  const dlgNear = await n.evaluate(() => ({ head: document.getElementById("cheapHead").textContent,
    shops: Array.from(document.querySelectorAll("#dlgShops li")).map(li => li.textContent) }));
  await n.click("#dlgClose");
  const lineN = Number((item.line.match(/· (\d+) shops?\b/) || [])[1]), lineIn = / shops? in /.test(item.line);
  const itemOk = item.loc ? lineN === item.loc && !lineIn && dlgNear.head === "Cheapest near you now" && dlgNear.shops.every(s => !/unknown/.test(s))
    : !item.all || (lineN === item.all && item.line.includes(" in " + item.home) && dlgNear.head === "Cheapest in " + item.home + " now");
  check("Near me: an item's shop count and cheapest shops count only shops with a known place", itemOk,
        `${item.loc} of ${item.all} shops pricing it have a known place; line '${item.line}'; dialog '${dlgNear.head}': ${dlgNear.shops.slice(0, 2).join(" | ")}`);
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
    // ...and placed in their city: a postcode that is not in the table is placed where most postcodes sharing its
    // first digits are, never between far-apart ones (80000 once landed in the sea between Johor Bahru and Sabah).
    const cities = [["50100", /^w-p-kuala-lumpur--/], ["80000", /^johor--johor-bahru$/], ["88000", /^sabah--kota-kinabalu$/], ["93000", /^sarawak--kuching$/]];
    const placed = await n.evaluate(pcList => pcList.map(p => {
      const S = window.__bakul, h = window.Near.locatePostcode(p, S.meta.geo.postcodes);
      const ds = h ? window.Near.districtsWithin(S.meta.areas, h.lat, h.lon, 20) : [];
      return p + " " + (ds.length ? ds[0].slug + " " + ds[0].km.toFixed(1) + " km" : "no district within 20 km");
    }), cities.map(c => c[0]));
    check("common city postcodes are placed in their city (nearest district centre)", placed.every((s, i) => cities[i][1].test(s.split(" ")[1])),
          placed.join("; "));
  }
  // Postcode 40100 at 5 km: it has shops whose place is unknown (listed for the home district), which must never
  // be named as the cheapest near you.
  await n.selectOption("#radiusSel", "5");
  await n.fill("#pcIn", "40100");
  await n.click("#pcForm button");
  const at40100 = await n.waitForFunction(() => { const S = window.__bakul;
    return S.d && S.d.near && !S.nearLoading && /40100/.test(document.getElementById("whereNote").textContent); }, null, { timeout: 15000 }).then(() => true, () => false);
  claims.push(["postcode 40100", at40100 ? await nearClaim(n) : null]);
  counts.push(["postcode 40100", at40100 ? await nearCounts(n) : null]);
  // More postcodes, until one has shops with every item whose place is unknown (the case where a headline that does
  // not name the radius would be contradicted by the list under it).
  for (const p of pcs.slice(0, 40)) {
    if (counts.filter(c => c[1]).length >= 8 && counts.some(c => c[1] && c[1].unknownFull)) break;
    await n.fill("#pcIn", p);
    await n.click("#pcForm button");
    const ok = await n.waitForFunction(q => { const S = window.__bakul; return !S.nearLoading && S.near && S.near.pc === q && S.d && S.d.near; }, p, { timeout: 15000 }).then(() => true, () => false);
    if (ok) counts.push(["postcode " + p, await nearCounts(n)]);
  }
  const claimed = claims.filter(c => c[1] != null), claimBad = claimed.filter(c => c[1]);
  if (!claimed.length) skip("Near me: the headline and the result bar name a shop with a distance, not 'location unknown'", "no Near me results to check");
  else check("Near me: the headline and the result bar name a shop with a distance, not 'location unknown'", claimBad.length === 0,
             claimBad.length ? claimBad.map(c => c[0] + ": " + c[1]).join(" | ") : claimed.map(c => c[0]).join(", "));
  const counted = counts.filter(c => c[1]), countBad = counted.filter(c => c[1].problem), withUnk = counted.filter(c => c[1].unknownFull);
  const countName = "Near me: a headline that counts shops with every item names the radius and matches the list under it";
  if (!counted.length) skip(countName, "no Near me results to check");
  else check(countName, countBad.length === 0, countBad.length ? countBad.map(c => c[0] + ": " + c[1].problem).join(" | ")
    : `${counted.length} searches; ${withUnk.length} with shops of unknown place that have every item` + (withUnk.length ? ", e.g. " + withUnk[0][0] + ": '" + withUnk[0][1].head + "'" : ""));
  await noHScroll(n, "no horizontal scroll (near me)");
  await n.click("[data-mode='district']");
  check("back to district mode", !(await n.isVisible("#nearBox")));
  await nctx.close();

  // Near me ranks items by the shops inside the radius first: shops whose place is unknown (outside it) only break
  // ties. So the starter list never names an any-brand line after a brand no shop within the radius prices while
  // another of its brands is priced there, and a search lists items priced within the radius first.
  const rankPcs = meta.geo ? ["12085", "11570", "15817", "50100"].filter(p => meta.geo.postcodes[p]) : Object.keys(testPostcodes || {}).slice(0, 4);
  const rctx = await browser.newContext(PHONE);
  await rctx.addInitScript(() => {
    try {
      if (sessionStorage.getItem("seeded")) return;
      sessionStorage.setItem("seeded", "1");
      localStorage.setItem("bakul.mode", JSON.stringify("near"));
      localStorage.setItem("bakul.radius", JSON.stringify(5));
    } catch (e) {}
  });
  const rk = await newPage(rctx, errors, "near ranking");
  await rk.goto(NEAR_URL);
  await ready(rk);
  const rankLines = [], rankBad = [], searchBad = [];
  let searched = 0;
  for (const p of rankPcs) {
    await rk.fill("#pcIn", p);
    await rk.click("#pcForm button");
    const ok = await rk.waitForFunction(q => { const S = window.__bakul; return !S.nearLoading && S.near && S.near.pc === q && S.d && S.d.near; }, p, { timeout: 15000 }).then(() => true, () => false);
    if (!ok) continue;
    if (await rk.isVisible("#clearBtn")) await rk.click("#clearBtn");
    await rk.click("#starterBtn");
    const r = await rk.evaluate(() => {
      const S = window.__bakul, d = S.d, loc = c => { let k = 0; (S.idx.get(c) || new Map()).forEach((v, q) => { if (d.premises[q][7] != null) k++; }); return k; };
      const unit = c => S.items.get(c).unit.replace(/\s+/g, "").toLowerCase();
      return S.basket.filter(b => b.any === "brand" && b.alt && b.alt.length).map(b => {
        const mains = S.basket.filter(x => x !== b).map(x => x.code);
        const priced = [b.code].concat(b.alt).filter(c => S.items.has(c) && unit(c) === unit(b.code) && mains.indexOf(c) < 0 && loc(c) > 0);
        return { name: S.items.get(b.code).item, loc: loc(b.code), priced: priced.length };
      });
    });
    r.forEach(x => { rankLines.push(x); if (!x.loc && x.priced) rankBad.push(`${p}: ${x.name} (0 shops within 5 km; ${x.priced} of its brands are priced there)`); });
    await rk.fill("#searchIn", "beras");
    const order = await rk.evaluate(() => {
      const S = window.__bakul, d = S.d, loc = c => { let k = 0; (S.idx.get(c) || new Map()).forEach((v, q) => { if (d.premises[q][7] != null) k++; }); return k; };
      return Array.from(document.querySelectorAll("#results [data-add]")).map(b => Number(b.dataset.add))
        .filter(c => /^BERAS /.test(S.items.get(c).item)).map(c => loc(c));
    });
    await rk.fill("#searchIn", "");
    await rk.dispatchEvent("#searchIn", "input");
    if (order.length > 1) searched++;
    const firstUnpriced = order.findIndex(k => !k);
    if (firstUnpriced >= 0 && order.slice(firstUnpriced).some(k => k > 0)) searchBad.push(`${p}: shops within 5 km by search order ${order.join(",")}`);
  }
  await rctx.close();
  const rankName = "Near me: an any-brand line is never named after a brand no shop within the radius prices while another of its brands is priced there";
  if (!rankLines.length) skip(rankName, "no line with any brand on in this data");
  else check(rankName, rankBad.length === 0, rankBad.length ? rankBad.slice(0, 4).join(" | ") : `${rankLines.length} lines at ${rankPcs.join(", ")}`);
  const searchName = "Near me: a search lists items priced within the radius before items priced only outside it";
  if (!searched) skip(searchName, "no search with more than one rice item in this data");
  else check(searchName, searchBad.length === 0, searchBad.length ? searchBad.join(" | ") : `${searched} postcodes`);

  // Near me: an 'Other grades or sizes OK' line whose own item no shop near prices, while one it accepts is priced
  // near only by shops whose place is unknown. The headline and the totals count the shops within the radius whenever
  // one of them prices something on the list: nothing then counts the line, and it says only 'not priced here'. With
  // nothing else on the list (no shop within the radius prices any of it), they count the home district's shops and
  // name that district: then the line says the others are counted. One priced within the radius is counted too.
  const nearCountedName = "Near me: a line says 'not priced here · other grades or sizes are counted' only when shops the headline counts price one it accepts (within the radius, or the home district's when the headline names it), else only 'not priced here'";
  {
    const NEARJS = require(path.join(ROOT, "web/js/near.js"));
    const nearDir = tmpWeb ? path.join(tmpWeb, "data") : DATA;
    const nearMeta = JSON.parse(fs.readFileSync(path.join(nearDir, "meta.json"), "utf8"));
    const nfiles = new Map(), nfile = s => { if (!nfiles.has(s)) nfiles.set(s, JSON.parse(fs.readFileSync(path.join(nearDir, "d", s + ".json"), "utf8"))); return nfiles.get(s); };
    // The shops the page shows for a postcode, within 5 km: the same files, merge and widening as the page (loadNear).
    const nearAt = pc => {
      const h = NEARJS.locatePostcode(pc, nearMeta.geo.postcodes); if (!h) return null;
      const steps = [5, 10, 20];
      for (let i = 0; i < steps.length; i++) {
        const list = NEARJS.districtsWithin(nearMeta.areas, h.lat, h.lon, steps[i]).slice(0, 30);
        const d = list.length ? NEARJS.mergeNear(list.map(x => nfile(x.slug)), h.lat, h.lon, steps[i]) : null;
        if ((d ? d.near.located : 0) < 3 && i + 1 < steps.length) continue;
        return d;
      }
      return null;
    };
    // The list reader's own lines with other grades or sizes (beef, chicken, eggs).
    const ncctx = await browser.newContext(PHONE);
    const ncp = await newPage(ncctx, errors, "near counted wording");
    await ncp.goto(NEAR_URL);
    await ready(ncp);
    const gradeLines = await ncp.evaluate(() => ["daging lembu 1kg", "ayam 1kg", "telur 1 papan"].map(x => window.__bakul.cat.matchText(x)[0])
      .filter(m => m && m.code != null && m.alt && m.alt.length && !m.flex).map(m => ({ code: m.code, alt: m.alt })));
    await ncctx.close();
    // U: a postcode where the line's own item is priced by no shop near, and one it accepts only by shops whose place
    // is unknown; with the item most shops within the radius price there as a second line. L: one it accepts is priced
    // within the radius.
    let U = null, L = null;
    const pcs = Object.keys(nearMeta.geo.postcodes);
    for (let i = 0; i < pcs.length && !(U && L); i++) {
      const d = nearAt(pcs[i]); if (!d || !d.premises.length) continue;
      const unloc = p => d.premises[p][7] == null, at = c => (d.prices[c] || []).map(r => r[0]);
      gradeLines.forEach(g => {
        if (at(g.code).length) return;
        const inR = g.alt.filter(c => at(c).some(p => !unloc(p))), outR = g.alt.filter(c => at(c).length && at(c).every(unloc));
        const other = () => Object.keys(d.prices).map(Number).filter(c => c !== g.code && g.alt.indexOf(c) < 0)
          .map(c => [c, new Set(at(c).filter(p => !unloc(p))).size]).sort((a, b) => b[1] - a[1] || a[0] - b[0])[0];
        if (!U && !inR.length && outR.length) { const y = other(); if (y && y[1]) U = { pc: pcs[i], g, outR, y: y[0], home: d.near.home, r: d.near.radius }; }
        if (!L && inR.length) { const y = other(); if (y && y[1]) L = { pc: pcs[i], g, inR, y: y[0], r: d.near.radius }; }
      });
    }
    // One list, seeded in a fresh browser in Near me (5 km), the postcode typed in: the figures under the first line,
    // and the headline.
    const nearLine = async (pc, basket, label) => {
      const c = await browser.newContext(PHONE);
      await c.addInitScript(b => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("near"));
          localStorage.setItem("bakul.radius", JSON.stringify(5));
          localStorage.setItem("bakul.lang", JSON.stringify("en"));
          localStorage.setItem("bakul.basket", JSON.stringify(b));
        } catch (e) {}
      }, basket);
      const p = await newPage(c, errors, label);
      await p.goto(NEAR_URL);
      await ready(p);
      await p.fill("#pcIn", pc);
      await p.click("#pcForm button");
      const ok = await p.waitForFunction(q => { const S = window.__bakul; return !S.nearLoading && S.near && S.near.pc === q && S.d && S.d.near &&
        document.querySelectorAll("#basketList li.bi").length && !document.getElementById("summary").hidden; }, pc, { timeout: 15000 }).then(() => true, () => false);
      const r = ok ? await p.evaluate(() => ({ sub: document.querySelector("#basketList li.bi .bi-name .res-sub").textContent,
        head: document.getElementById("summary").textContent.replace(/\s+/g, " ").trim().slice(0, 200), radius: window.__bakul.d.near.radius })) : null;
      await c.close();
      return r;
    };
    if (!U) skip(nearCountedName, "no postcode where a grade or size a line accepts is priced near only by shops whose place is unknown");
    else {
      const line = g => ({ code: g.code, qty: 1, opts: g.alt, alt: g.alt });
      const mixed = await nearLine(U.pc, [line(U.g), { code: U.y, qty: 1 }], "near counted wording, unknown place only");
      const alone = await nearLine(U.pc, [line(U.g)], "near counted wording, home district");
      const inR = L ? await nearLine(L.pc, [line(L.g), { code: L.y, qty: 1 }], "near counted wording, within the radius") : null;
      const counted = "· not priced here · other grades or sizes are counted", bare = "· not priced here";
      const ends = (r, tail) => !!r && r.sub.slice(-tail.length) === tail;
      check(nearCountedName, ends(mixed, bare) && !/counted/.test(mixed.sub) && /km of you/.test(mixed.head) &&
            ends(alone, counted) && alone.head.indexOf("in " + U.home) >= 0 && (!L || ends(inR, counted)),
            `${U.g.code} (accepts ${U.g.alt.join(", ")}) at ${U.pc}, ${U.r} km, where ${U.outR.join(", ")} is priced only by shops in ${U.home} whose place is unknown: ` +
            `with ${U.y} on the list: '${mixed ? mixed.sub : "no result"}' (headline '${mixed ? mixed.head.slice(0, 90) : "-"}'); ` +
            `alone: '${alone ? alone.sub : "no result"}' (headline '${alone ? alone.head.slice(0, 90) : "-"}'); ` +
            (L ? `${L.g.code} at ${L.pc}, where ${L.inR.join(", ")} is priced within ${L.r} km: '${inR ? inR.sub : "no result"}'` : "no postcode where one it accepts is priced within the radius"));
    }
  }

  // Narrow phone in BM, Near me, every shop shown: the widest rows (BM tags on shops without every item).
  // On the real data, the six items and postcode 40100 give such rows; on other data, the starter list.
  const six = [1, 1110, 2066, 1589, 1601, 1440];
  const sixHere = six.every(c => meta.items.some(r => r[0] === c));
  const bctx = await browser.newContext({ viewport: { width: 360, height: 740 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
  await bctx.addInitScript(basket => {
    try {
      localStorage.setItem("bakul.lang", JSON.stringify("ms"));
      localStorage.setItem("bakul.mode", JSON.stringify("near"));
      if (basket) localStorage.setItem("bakul.basket", JSON.stringify(basket));
    } catch (e) {}
  }, sixHere ? six.map(c => ({ code: c, qty: 1 })) : null);
  const bm = await newPage(bctx, errors, "near360ms");
  await bm.goto(NEAR_URL);
  await ready(bm);
  if (!sixHere) await bm.click("#starterBtn");
  await bm.fill("#pcIn", "40100");
  await bm.click("#pcForm button");
  const near360 = "no horizontal scroll (360 px, BM, Near me, every shop shown)";
  const lastUnknown = "Near me lists shops whose place is unknown last, with no 'Cheapest' or price-comparison tags";
  const got = await bm.waitForFunction(() => window.__bakul.d && window.__bakul.d.near && !window.__bakul.nearLoading &&
    document.querySelector("#rankList li[data-p]"), null, { timeout: 15000 }).then(() => true, () => false);
  if (!got) { skip(near360, "no Near me results for postcode 40100 in this data"); skip(lastUnknown, "no Near me results for postcode 40100 in this data"); }
  else {
    if (await bm.isVisible("#moreBtn")) await bm.click("#moreBtn");
    const wide = await bm.evaluate(() => {
      const S = window.__bakul, byCode = {};
      S.d.premises.forEach(p => { byCode[p[0]] = p; });
      const lis = Array.from(document.querySelectorAll("#rankList li[data-p]"));
      const unk = lis.map(li => (byCode[Number(li.dataset.p)] || [])[7] == null);
      const firstUnk = unk.indexOf(true), lastKnown = unk.lastIndexOf(false);
      return { lang: document.documentElement.lang, rows: lis.length, unknown: unk.filter(Boolean).length,
               partial: lis.filter(li => li.classList.contains("partial") && li.querySelector(".diff")).length,
               ordered: firstUnk < 0 || lastKnown < firstUnk,
               tagged: lis.filter((li, i) => unk[i] && li.querySelector(".tag.good, .tag.most, .diff")).length };
    });
    await noHScroll(bm, near360, `${wide.lang}, ${wide.rows} shops, ${wide.partial} without every item` + (wide.partial ? "" : ": none in this data"));
    if (wide.unknown) check(lastUnknown, wide.ordered && wide.tagged === 0, `${wide.unknown} of ${wide.rows} shops; after the others: ${wide.ordered}; tagged: ${wide.tagged}`);
    else skip(lastUnknown, "no shop with an unknown place near 40100 in this data");
    // The 'Location unknown, in X' line above those shops is a heading to look at, not a shop: screen readers count
    // only the shops in the list (each of those rows says 'location unknown' itself).
    const snap = await bm.locator("#rankList").ariaSnapshot();
    const items = snap.split("\n").filter(l => /^\s*- listitem\b/.test(l)).length;
    const subName = "screen readers count only the shops in the ranked list, not the 'location unknown' heading";
    if (wide.unknown) check(subName, items === wide.rows, `${items} list items for ${wide.rows} shops`);
    else skip(subName, "no shop with an unknown place near 40100 in this data");
  }
  await bctx.close();
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

  // A place that finishes loading while a price history is open (its file arrives late: held back here until the
  // dialog is open). From a row of the price changes: when that row is no longer listed for the new place, closing the
  // dialog puts keyboard focus on the price changes heading, never on the page, and Tab goes on from there.
  const lateFeedName = "closing a price history whose price-changes row is gone for a place that loaded meanwhile puts keyboard focus on the price changes heading";
  // While it is open, focus stays in the dialog. Opened by keyboard, focus is on its title. Focus on its scroll box (where
  // a tap on its figures puts it) stays there when the new place leaves the dialog less to show (no prices there for its
  // item: the scroll box then has nothing to scroll).
  const lateFocusName = "a place that loads while a price history is open keeps focus in the dialog, also on its scroll box when the dialog then has less to show";
  {
    const fctx = await browser.newContext(PHONE);
    await fctx.addInitScript(() => {
      try {
        if (sessionStorage.getItem("seeded")) return;
        sessionStorage.setItem("seeded", "1");
        localStorage.setItem("bakul.mode", JSON.stringify("district"));
        localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
        localStorage.setItem("bakul.lang", JSON.stringify("en"));
      } catch (e) {}
    });
    const fp = await newPage(fctx, errors, "late place, price changes");
    let open; const gate = new Promise(r => { open = r; });
    await fp.route("**/data/d/*.js", async r => { if (!/selangor--petaling\.js/.test(r.request().url())) await gate; await r.continue(); });
    await fp.goto(base);
    await fp.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && document.querySelector("#feedList [data-hist]"));
    const x = await fp.evaluate(() => Number(document.querySelector("#feedList [data-hist]").dataset.hist));
    // A district whose rises do not list the first row of Petaling's (another one in Selangor when there is one).
    const allSlugs = [].concat.apply([], Object.keys(meta.areas).map(st => meta.areas[st].map(a => ({ st, slug: a.slug, name: a.district }))));
    const cands = allSlugs.filter(a => a.st === "Selangor").concat(allSlugs.filter(a => a.st !== "Selangor")).filter(a => a.slug !== "selangor--petaling");
    // Preferably one whose shops do not price that item at all (the dialog then has the least to show).
    const dfs = new Map(), df = a => { if (!dfs.has(a.slug)) dfs.set(a.slug, readJson("d/" + a.slug + ".json")); return dfs.get(a.slug); };
    const listsX = a => ((df(a).feed || {}).rises || []).some(r => r[0] === x);
    const pricesX = a => !!(df(a).prices || {})[x];
    const to = cands.find(a => !listsX(a) && !pricesX(a)) || cands.find(a => !listsX(a));
    if (!to) [lateFeedName, lateFocusName].forEach(n => skip(n, `every other district lists row ${x} in its price rises`));
    else {
      if (to.st !== "Selangor") await fp.selectOption("#stateSel", to.st);
      await fp.selectOption("#districtSel", to.slug);
      await fp.focus("#tab-basket");
      await fp.keyboard.press("ArrowRight");                 // the Price changes tab, by keyboard
      await fp.waitForFunction(() => window.__bakul.tab === "trends");
      const at = () => fp.evaluate(() => { const a = document.activeElement;
        return !a || a === document.body ? "the page" : (a.id ? "#" + a.id : a.classList.contains("sheet-inner") ? "scroll box" : a.tagName + (a.dataset.dir ? " " + a.dataset.dir : "")) +
          (a.closest("#panel-trends") ? " (price changes)" : a.closest("dialog") ? " (in " + a.closest("dialog").id + ")" : ""); });
      const scrolls = () => fp.evaluate(() => { const s = document.querySelector("#itemDlg .sheet-inner"); return s.scrollHeight > s.clientHeight ? "scrolls" : "has nothing to scroll"; });
      await fp.focus(`#feedList [data-hist="${x}"]`);
      await fp.keyboard.press("Enter");
      await fp.waitForFunction(() => document.getElementById("itemDlg").open);
      const opened = await at();
      await fp.focus("#itemDlg .sheet-inner");
      const onBox = await at(), before = await scrolls();
      open();
      // The place chosen is set at once; its prices are shown once its file has arrived.
      await fp.waitForFunction(n => window.__bakul.d && window.__bakul.d.district === n, to.name);
      const listed = await fp.evaluate(c => !!document.querySelector(`#feedList [data-hist="${c}"]`), x);
      // The browser moves focus off a control that can no longer take it a moment after the page is drawn.
      await fp.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 100)))));
      const inDlg = await at(), xPriced = pricesX(to), after = await scrolls();
      check(lateFocusName, opened === "#dlgTitle (in itemDlg)" && onBox === "scroll box (in itemDlg)" && inDlg === "scroll box (in itemDlg)",
            `row ${x} of Petaling opened by Enter: focus on ${opened}; then on its scroll box: ${onBox} (it ${before}); ` +
            `${to.slug} loaded behind the dialog (${xPriced ? "it prices" : "no prices there for"} ${x}; the scroll box now ${after}); focus on ${inDlg}`);
      await fp.keyboard.press("Escape");
      await fp.waitForFunction(() => !document.getElementById("itemDlg").open);
      await settled(fp);
      const afterEsc = await at();
      await fp.keyboard.press("Tab");
      const afterTab = await at();
      if (listed) skip(lateFeedName, `row ${x} is still listed in ${to.slug}`);
      else check(lateFeedName, afterEsc === "#feedTitle (price changes)" && / \(price changes\)$/.test(afterTab),
                 `row ${x} of Petaling opened, ${to.slug} loaded behind the dialog (row gone); after Esc, focus on ${afterEsc}; then Tab: ${afterTab}`);
    }
    await fctx.close();
  }
  // With the price changes shown (the list is not): the shared-list bar's 'Ignore', and Undo of a removal used there,
  // put keyboard focus on the price changes heading, never on the page. And screen readers hear the figures under a
  // list line's name (its typical price, or 'not priced here ...') as the description of the line's button, in BM too.
  const tabFocusName = "with the price changes shown, 'Ignore' on the shared-list bar and Undo of a removal put keyboard focus on its heading";
  const lineDescName = "screen readers hear the figures under a list line's name as the description of its button, in EN and BM";
  // Tab, Shift+Tab and Esc on a removal's Undo there go to that heading too (the line's place is on the list, which is
  // not shown); Esc also closes the toast, and the line stays removed.
  const undoKeysName = "with the price changes shown, Tab, Shift+Tab and Esc on a removal's Undo put keyboard focus on its heading, and Esc closes the toast without putting the line back";
  // The shared-list bar's other buttons there: by keyboard, 'Replace my list' and 'Add to my list' take focus to Undo,
  // and Tab there goes to that heading; a tap on them, or 'Load it' into an empty list, puts focus on the heading.
  const importTrendsName = "with the price changes shown, 'Replace my list' and 'Add to my list' on the shared-list bar put keyboard focus on Undo and Tab there goes to its heading; a tap on them, or 'Load it' into an empty list, puts focus on that heading";
  {
    const pet = readJson("d/selangor--petaling.json"), pshops = c => new Set((pet.prices[c] || []).map(r => r[0])).size;
    const three = meta.items.map(r => r[0]).filter(c => pshops(c) > 0).sort((a, b) => pshops(b) - pshops(a) || a - b).slice(0, 3);
    if (three.length < 3) [tabFocusName, lineDescName, undoKeysName, importTrendsName].forEach(n => skip(n, "fewer than 3 items priced in Petaling"));
    else {
      const tfctx = await browser.newContext(PHONE);
      await tfctx.addInitScript(b => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("district"));
          localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
          localStorage.setItem("bakul.lang", JSON.stringify("en"));
          localStorage.setItem("bakul.basket", JSON.stringify(b));
        } catch (e) {}
      }, three.slice(0, 2).map(c => ({ code: c, qty: 1 })));
      const tf = await newPage(tfctx, errors, "price changes tab focus");
      await tf.goto(URL + "?l=" + three[2] + "~1");
      await tf.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && !document.getElementById("importBar").hidden &&
        document.querySelectorAll("#basketList li.bi").length === 2);
      // The accessibility tree as the browser gives it to screen readers: each line button's name and description.
      const cdp = await tfctx.newCDPSession(tf);
      await cdp.send("Accessibility.enable");
      const described = async () => {
        const { nodes } = await cdp.send("Accessibility.getFullAXTree");
        const ax = nodes.filter(n => n.role && n.role.value === "button" && n.name && /^(Price history|Sejarah harga): /.test(n.name.value))
          .map(n => ({ name: n.name.value, desc: ((n.description && n.description.value) || "").replace(/\s+/g, " ").trim() }));
        const subs = await tf.evaluate(() => Array.from(document.querySelectorAll("#basketList li.bi .bi-name .res-sub")).map(x => x.textContent.replace(/\s+/g, " ").trim()));
        return { ax, subs, ok: ax.length === subs.length && subs.length > 0 && ax.every((a, i) => a.desc === subs[i]) };
      };
      const dEn = await described();
      await tf.click("[data-lang='ms']");
      const dMs = await described();
      await tf.click("[data-lang='en']");
      check(lineDescName, dEn.ok && dMs.ok && /^Sejarah harga: /.test((dMs.ax[0] || {}).name || ""),
            `EN: ${dEn.ax.map(a => `'${a.name}' described as '${a.desc}'`).join("; ")} (figures shown: ${dEn.subs.join(" | ")}); ` +
            `BM: ${dMs.ax.map(a => `'${a.name}' described as '${a.desc}'`).join("; ")}`);
      const at = () => tf.evaluate(() => { const a = document.activeElement;
        return !a || a === document.body ? "the page" : a.id ? "#" + a.id : a.tagName + (a.dataset.rm ? " remove " + a.dataset.rm : ""); });
      // 'Ignore' on the bar, with the price changes tab chosen by keyboard.
      await tf.focus("#tab-basket");
      await tf.keyboard.press("ArrowRight");
      await tf.waitForFunction(() => window.__bakul.tab === "trends");
      await tf.focus("#importNo");
      await tf.keyboard.press("Enter");
      const afterNo = await at();
      // A line removed by keyboard (focus goes to Undo), the price changes tab chosen, then Undo used there by keyboard.
      await tf.focus("#tab-trends");
      await tf.keyboard.press("ArrowLeft");
      await tf.waitForFunction(() => window.__bakul.tab === "basket");
      await tf.focus(`#basketList [data-rm="${three[1]}"]`);
      await tf.keyboard.press("Enter");
      const onUndo = await at();
      await tf.focus("#tab-basket");
      await tf.keyboard.press("ArrowRight");
      await tf.waitForFunction(() => window.__bakul.tab === "trends");
      await tf.focus("#toastAct");
      await tf.keyboard.press("Enter");
      const afterUndo = await at(), tfLines = await tf.evaluate(() => window.__bakul.basket.map(b => b.code).join(","));
      check(tabFocusName, afterNo === "#feedTitle" && onUndo === "#toastAct" && afterUndo === "#feedTitle" && tfLines === three.slice(0, 2).join(","),
            `'Ignore': focus on ${afterNo}; ${three[1]} removed (focus on ${onUndo}), then Undo used on the price changes: lines ${tfLines}, focus on ${afterUndo}`);
      // The same line removed again by keyboard, the price changes tab chosen, then Shift+Tab, Tab and Esc on its Undo.
      await tf.focus("#tab-trends");
      await tf.keyboard.press("ArrowLeft");
      await tf.waitForFunction(() => window.__bakul.tab === "basket");
      await tf.focus(`#basketList [data-rm="${three[1]}"]`);
      await tf.keyboard.press("Enter");
      const onUndo2 = await at();
      await tf.focus("#tab-basket");
      await tf.keyboard.press("ArrowRight");
      await tf.waitForFunction(() => window.__bakul.tab === "trends");
      const keyTo = [];
      for (const k of ["Shift+Tab", "Tab", "Escape"]) {
        await tf.focus("#toastAct");
        await tf.keyboard.press(k);
        keyTo.push([k, await at()]);
      }
      const undoEnd = await tf.evaluate(() => ({ toast: document.getElementById("toast").classList.contains("on"), lines: window.__bakul.basket.map(b => b.code).join(",") }));
      check(undoKeysName, onUndo2 === "#toastAct" && keyTo.every(k => k[1] === "#feedTitle") && !undoEnd.toast && undoEnd.lines === String(three[0]),
            `${three[1]} removed (focus on ${onUndo2}); on the price changes: ${keyTo.map(k => `${k[0]} on Undo: focus on ${k[1]}`).join("; ")}; ` +
            `then the toast is ${undoEnd.toast ? "still shown" : "closed"}, lines ${undoEnd.lines}`);
      // The shared-list bar used with the price changes shown, each time in a fresh page (the list as seeded, or empty).
      const ectx = await browser.newContext(PHONE);
      await ectx.addInitScript(() => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("district"));
          localStorage.setItem("bakul.slug", JSON.stringify("selangor--petaling"));
          localStorage.setItem("bakul.lang", JSON.stringify("en"));
          localStorage.setItem("bakul.basket", JSON.stringify([]));
        } catch (e) {}
      });
      const bar = [], all3 = three.join(",");
      for (const [bctx2, btn, how, toUndo, want] of [[tfctx, "#importYes", "Enter", true, String(three[2])], [tfctx, "#importAdd", "Enter", true, all3],
                                                      [tfctx, "#importYes", "tap", false, String(three[2])], [tfctx, "#importAdd", "tap", false, all3],
                                                      [ectx, "#importYes", "Enter", false, String(three[2])]]) {
        const p = await newPage(bctx2, errors, "shared-list bar on the price changes");
        await p.goto(URL + "?l=" + three[2] + "~1");
        await p.waitForFunction(() => window.__bakul.d && window.__bakul.slug === "selangor--petaling" && !document.getElementById("importBar").hidden);
        if (how === "Enter") { await p.focus("#tab-basket"); await p.keyboard.press("ArrowRight"); } else await p.tap("#tab-trends");
        await p.waitForFunction(() => window.__bakul.tab === "trends");
        const label = await p.textContent(btn);
        const pat = () => p.evaluate(() => { const a = document.activeElement; return !a || a === document.body ? "the page" : a.id ? "#" + a.id : a.tagName; });
        if (how === "Enter") { await p.focus(btn); await p.keyboard.press("Enter"); } else await p.tap(btn);
        const r = { what: `'${label}' (${how === "Enter" ? "Enter" : "a tap"}${bctx2 === ectx ? ", empty list" : ""})`, focus: await pat(),
                    lines: await p.evaluate(() => window.__bakul.basket.map(b => b.code).join(",")) };
        if (toUndo) { await p.keyboard.press("Tab"); r.tab = await pat(); }
        r.ok = r.lines === want && (toUndo ? r.focus === "#toastAct" && r.tab === "#feedTitle" : r.focus === "#feedTitle");
        bar.push(r);
        await p.close();
      }
      await ectx.close();
      check(importTrendsName, bar.length === 5 && bar.every(r => r.ok),
            bar.map(r => `${r.what}: lines ${r.lines}, focus on ${r.focus}` + (r.tab ? `, then Tab: ${r.tab}` : "")).join("; "));
      await tfctx.close();
    }
  }
  // From an any-brand list line, with no place shown yet: the line keeps its item while the dialog shows it (the dialog
  // keeps 'In your list', and its button adds nothing, by Enter or a tap); another any-brand line follows the place at
  // once. Closing the dialog names the line after the brand most shops there price, with keyboard focus on its name.
  const lateLineName = "a place loading while an any-brand line's price history is open: the dialog keeps 'In your list' and adds no line; on closing, the line follows the place, with keyboard focus on its name";
  if (!kind || kind.top === kind.least) skip(lateLineName, "no line with any brand on in this data");
  else {
    const pet = readJson("d/selangor--petaling.json"), pshops = c => new Set((pet.prices[c] || []).map(r => r[0])).size;
    const unitOf = c => String((meta.items.find(r => r[0] === c) || [])[2] || "").replace(/\s+/g, "").toLowerCase();
    // A family of brands: its least priced one in Petaling, and how many shops price its most priced one (same size).
    const fam = all => {
      const u = unitOf(all[0]), same = all.filter(c => unitOf(c) === u), most = Math.max.apply(null, same.map(pshops));
      const least = same.slice().sort((a, b) => pshops(a) - pshops(b) || a - b)[0];
      return { all, least, rest: all.filter(c => c !== least), most, renames: pshops(least) < most };
    };
    const one = fam(kind.all);
    // Another any-brand line of the starter list (a different kind of item), when one is renamed in Petaling.
    const sctx2 = await browser.newContext(PHONE);
    const sp = await newPage(sctx2, errors, "starter families");
    await sp.goto(base);
    await ready(sp);
    await sp.click("#starterBtn");
    const fams = await sp.evaluate(() => window.__bakul.basket.filter(b => b.any === "brand" && b.alt && b.alt.length).map(b => [b.code].concat(b.alt)));
    await sctx2.close();
    const two = fams.map(fam).filter(f => f.renames && !f.all.some(c => kind.all.indexOf(c) >= 0))[0] || null;
    if (!one.renames) skip(lateLineName, `every brand of the line is priced in Petaling by as many shops (${one.most})`);
    else {
      const seedLines = [{ code: one.least, qty: 1, opts: one.rest, alt: one.rest, any: "brand" }].concat(two ? [{ code: two.least, qty: 1, opts: two.rest, alt: two.rest, any: "brand" }] : []);
      const lc = await browser.newContext(PHONE);
      await lc.addInitScript(b => {
        try {
          if (sessionStorage.getItem("seeded")) return;
          sessionStorage.setItem("seeded", "1");
          localStorage.setItem("bakul.mode", JSON.stringify("district"));
          localStorage.setItem("bakul.lang", JSON.stringify("en"));
          localStorage.setItem("bakul.basket", JSON.stringify(b));
        } catch (e) {}
      }, seedLines);
      const lp = await newPage(lc, errors, "late place, list line");
      let open; const gate = new Promise(r => { open = r; });
      await lp.route("**/data/d/*.js", async r => { await gate; await r.continue(); });
      await lp.goto(base);
      await ready(lp);
      await lp.waitForSelector(`#basketList [data-hist="${one.least}"]`);
      await lp.selectOption("#stateSel", "Selangor");
      await lp.selectOption("#districtSel", "selangor--petaling");
      await lp.focus(`#basketList [data-hist="${one.least}"]`);
      await lp.keyboard.press("Enter");
      await lp.waitForFunction(() => document.getElementById("itemDlg").open);
      open();
      const petName = ((meta.areas.Selangor || []).find(a => a.slug === "selangor--petaling") || {}).district;
      await lp.waitForFunction(n => window.__bakul.d && window.__bakul.d.district === n, petName);
      const state = () => lp.evaluate(() => {
        const S = window.__bakul, b = document.getElementById("dlgAdd"), a = document.activeElement;
        return { codes: S.basket.map(x => x.code), open: document.getElementById("itemDlg").open,
                 btn: `'${b.textContent}'${b.getAttribute("aria-disabled") === "true" ? " (off)" : ""}`,
                 focus: !a || a === document.body ? "the page" : a.dataset && a.dataset.hist ? "name " + a.dataset.hist : a.id || a.tagName };
      });
      const mid = await state();
      await lp.focus("#dlgAdd");
      await lp.keyboard.press("Enter");
      await lp.tap("#dlgAdd", { force: true });              // a real tap, also on a button marked aria-disabled
      const pressed = await state();
      await lp.keyboard.press("Escape");
      await lp.waitForFunction(() => !document.getElementById("itemDlg").open);
      await settled(lp);
      const end = await state();
      const n = seedLines.length, top = (f, c) => f.all.indexOf(c) >= 0 && unitOf(c) === unitOf(f.least) && pshops(c) === f.most;
      const ok = mid.open && mid.codes[0] === one.least && /^'In your list' \(off\)$/.test(mid.btn) && (!two || top(two, mid.codes[1])) &&
        pressed.codes.join() === mid.codes.join() &&
        end.codes.length === n && new Set(end.codes).size === n && top(one, end.codes[0]) && end.focus === `name ${end.codes[0]}`;
      check(lateLineName, ok,
            `with the dialog open in Petaling: lines ${mid.codes.join(", ")} (the dialog's line was ${one.least}, ${pshops(one.least)} shops` +
            (two ? `; the other line was ${two.least}, ${pshops(two.least)} shops, now ${mid.codes[1]}, ${pshops(mid.codes[1])} shops, the most ${two.most}` : "; no other any-brand line to check") +
            `), the dialog's button ${mid.btn}; Enter and a tap on it: lines ${pressed.codes.join(", ")}; ` +
            `after Esc: lines ${end.codes.join(", ")} (${pshops(end.codes[0])} shops, the most ${one.most}), focus on ${end.focus}`);
      await lc.close();
    }
  }
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
  await noHScroll(d, "no horizontal scroll (desktop)");
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
  await noHScroll(s, "no horizontal scroll (360px)");
  await shot(s, "mobile-08-system-dark-360.png", { fullPage: true });
  await sctx.close();

  check("no page or console errors", errors.length === 0, errors.join(" | "));
  await browser.close();

  const failed = results.filter(r => !r.ok).length, skipped = results.filter(r => r.skipped).length;
  console.log(`\n${results.length - failed - skipped} passed, ${failed} failed, ${skipped} skipped`);
  fs.writeFileSync(path.join(SHOTS, "e2e-results.json"), JSON.stringify({ when: new Date().toISOString(), results }, null, 1));
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
