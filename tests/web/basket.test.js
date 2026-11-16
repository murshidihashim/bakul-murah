// Unit tests for web/js/basket.js.  Run: node --test tests/web/
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const B = require(path.join(__dirname, "../../web/js/basket.js"));

// 3 shops, 3 items. prices: {code: [[premiseIndex, price, daysOld]]}
const district = {
  premises: [[1, "A"], [2, "B"], [3, "C"]],
  prices: {
    10: [[0, 5.0, 1], [1, 4.0, 2], [2, 6.0, 0]],
    20: [[0, 2.0, 1], [1, 3.0, 2], [2, 2.5, 30]],
    30: [[0, 9.0, 1], [2, 8.0, 0]], // shop B lacks item 30
  },
};
const idx = B.indexDistrict(district);

test("rankStores puts full coverage first, then cheapest", () => {
  const rows = B.rankStores(idx, 3, [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }]);
  // A = 5 + 4 + 9 = 18 ; C = 6 + 5 + 8 = 19 ; B = 4 + 6 = 10 but missing one item
  assert.deepEqual(rows.map(r => r.pidx), [0, 2, 1]);
  assert.equal(rows[0].total, 18);
  assert.equal(rows[2].have, 2);
  assert.deepEqual(rows[2].missing, [30]);
  assert.equal(rows[1].maxDays, 30);
});

test("summarise compares against the median full-coverage shop", () => {
  const rows = B.rankStores(idx, 3, [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }]);
  const s = B.summarise(rows, 3);
  assert.equal(s.kind, "save");
  assert.equal(s.median, 18.5);
  assert.equal(s.save, 0.5);
  assert.equal(s.max, 19);
});

test("bestSplit picks the cheapest covering pair", () => {
  const basket = [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }];
  const rows = B.rankStores(idx, 3, basket);
  const sp = B.bestSplit(idx, 3, basket, rows);
  // Best pair: B for item 10 (4), A for item 20 (2x2=4), C for item 30 (8) -> needs 3 shops.
  // Pairs: A+B = 4+4+9 = 17 ; A+C = 5+4+8 = 17 ; B+C = 4+5+8 = 17. Tie -> the first pair tried
  // (candidates follow the ranking A, C, B, so A+C).
  assert.equal(sp.have, 3);
  assert.equal(sp.total, 17);
  assert.equal(sp.saving, 1);
  assert.equal(sp.kind, "save");
});

test("bestSplit reports cover when no single shop has everything", () => {
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1]], 2: [[1, 1]] } };
  const i = B.indexDistrict(d);
  const basket = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const rows = B.rankStores(i, 2, basket);
  assert.equal(B.summarise(rows, 2).kind, "none");
  assert.equal(B.bestSplit(i, 2, basket, rows).kind, "cover");
});

test("cheapestFor sorts by price", () => {
  assert.deepEqual(B.cheapestFor(idx, 10).map(x => x.pidx), [1, 0, 2]);
  assert.deepEqual(B.cheapestFor(idx, 99), []);
});

test("median", () => {
  assert.equal(B.median([3, 1, 2]), 2);
  assert.equal(B.median([4, 1, 2, 3]), 2.5);
  assert.equal(B.median([]), null);
});

// ---------- Phase 1: any brand, 1-3 stop plans, averages, pack sizes ----------

test("priceAt takes the cheapest accepted item when 'any brand' is on", () => {
  // item 40 is another brand of item 10, only sold at shop B and cheaper there.
  const d = { premises: [[1], [2]], prices: { 10: [[0, 5.0, 0], [1, 6.0, 0]], 40: [[1, 3.5, 4]] } };
  const i = B.indexDistrict(d);
  assert.deepEqual(B.priceAt(i, { code: 10, qty: 1 }, 1), { p: 6.0, d: 0, code: 10 });
  assert.deepEqual(B.priceAt(i, { code: 10, qty: 1, alt: [40] }, 1), { p: 3.5, d: 4, code: 40 });
  const rows = B.rankStores(i, 2, [{ code: 10, qty: 2, alt: [40] }]);
  assert.equal(rows[0].pidx, 1);
  assert.equal(rows[0].total, 7);
  assert.equal(rows[0].lines[10].code, 40);
});

// 4 shops, 3 items: each item is cheapest at a different shop.
const d4 = {
  premises: [[1], [2], [3], [4]],
  prices: {
    1: [[0, 10, 0], [1, 4, 0], [2, 10, 0], [3, 9, 0]],
    2: [[0, 10, 0], [1, 10, 0], [2, 4, 0], [3, 9, 0]],
    3: [[0, 10, 0], [1, 10, 0], [2, 10, 0], [3, 4, 0]],
  },
};
const i4 = B.indexDistrict(d4);
const bask3 = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }, { code: 3, qty: 1 }];

test("bestPlan finds the cheapest 3-shop combination", () => {
  const rows = B.rankStores(i4, 4, bask3);
  assert.equal(rows[0].pidx, 3);            // single best: 9 + 9 + 4 = 22
  assert.equal(rows[0].total, 22);
  const p3 = B.bestPlan(i4, 4, bask3, rows, 3);
  assert.deepEqual(p3.shops.slice().sort(), [1, 2, 3]);
  assert.equal(p3.total, 12);
  assert.deepEqual(p3.assign, { 1: 1, 2: 2, 3: 3 });
  const p2 = B.bestPlan(i4, 4, bask3, rows, 2);
  assert.equal(p2.total, 17);               // shop B or C (4) plus D (9 + 4)
});

test("planOptions recommends extra stops only when they beat the stop value", () => {
  const rows = B.rankStores(i4, 4, bask3);
  let po = B.planOptions(i4, 4, bask3, rows, 2);
  assert.deepEqual(po.options.map(o => [o.k, o.total, o.saving]), [[1, 22, 0], [2, 17, 5], [3, 12, 10]]);
  assert.equal(po.rec, 3);                   // 12 + 2x2 = 16 beats 17 + 2 and 22
  po = B.planOptions(i4, 4, bask3, rows, 5);
  assert.equal(po.rec, 1);                   // 22 vs 17 + 5 = 22 vs 12 + 10 = 22: ties keep fewer stops
  po = B.planOptions(i4, 4, bask3, rows, 6);
  assert.equal(po.rec, 1);                   // 22 vs 17 + 6 = 23 vs 12 + 12 = 24
});

test("planOptions prefers a plan that covers more items", () => {
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1, 0]], 2: [[1, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 50);
  assert.equal(po.rec, 2);
  assert.equal(po.options[1].saving, null);
});

test("planOptions drops a 2-stop plan that covers fewer items than 1 stop", () => {
  // Shop 0 sells items 1, 2, 3 at RM 1 each. Shop 1 sells item 1 at RM 2, shop 2 sells item 2 at RM 3.
  // The only 2-shop plan that uses both shops is 1 + 2: 2 items for RM 5. It used to be offered as "Save RM 0.00".
  const d = { premises: [[1], [2], [3]],
              prices: { 1: [[0, 1, 0], [1, 2, 0]], 2: [[0, 1, 0], [2, 3, 0]], 3: [[0, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }, { code: 3, qty: 1 }];
  const rows = B.rankStores(i, 3, bk);
  assert.deepEqual(B.bestPlan(i, 3, bk, rows, 2), { shops: [1, 2], total: 5, have: 2, assign: { 1: 1, 2: 2 } });
  const po = B.planOptions(i, 3, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 3, 3, 0]]);
  assert.equal(po.rec, 1);
});

test("planOptions drops an extra stop that saves nothing", () => {
  // Shop 0 has both items for RM 2. Shops 1 and 2 have one each for RM 1: the same 2 items for the same RM 2.
  const d = { premises: [[1], [2], [3]], prices: { 1: [[0, 1, 0], [1, 1, 0]], 2: [[0, 1, 0], [2, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const rows = B.rankStores(i, 3, bk);
  assert.equal(B.bestPlan(i, 3, bk, rows, 2).total, 2);
  const po = B.planOptions(i, 3, bk, rows, 0);
  assert.deepEqual(po.options.map(o => o.k), [1]);
  assert.equal(po.rec, 1);
});

test("planOptions keeps a same-coverage plan that is at least RM 0.01 cheaper", () => {
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1.0, 0], [1, 0.99, 0]], 2: [[0, 1.0, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 0);
  assert.deepEqual(po.options.map(o => [o.k, o.total, o.saving]), [[1, 2, 0], [2, 1.99, 0.01]]);
  assert.equal(po.rec, 1);                   // saves under RM 1, so one stop is still recommended
});

test("planOptions drops a 3-stop plan that covers fewer items than the 2-stop plan", () => {
  // Shop 0 has all 4 items at RM 1; shop 1 has item 1 at RM 0.50; shops 2-4 have one dear item each.
  // Best 3 shops that all get used: 1 + 2 + 3, only 3 items.
  const d = { prices: { 1: [[0, 1, 0], [1, 0.5, 0]], 2: [[0, 1, 0], [2, 3, 0]],
                        3: [[0, 1, 0], [3, 3, 0]], 4: [[0, 1, 0], [4, 3, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3, 4].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 5, bk);
  assert.equal(B.bestPlan(i, 5, bk, rows, 3).have, 3);
  const po = B.planOptions(i, 5, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 4, 4, 0], [2, 4, 3.5, 0.5]]);
  assert.equal(po.rec, 1);                   // RM 0.50 is under the RM 1 minimum for an extra stop
});

test("planOptions drops a 3-stop plan that costs the same as the 2-stop plan", () => {
  // 2 stops (shops 0 + 1) buy all 3 items for RM 5. Shops 1 + 3 + 4 also buy all 3 for RM 5.
  const d = { prices: { 1: [[0, 2, 0], [1, 1, 0], [2, 1, 0]], 2: [[0, 2, 0], [3, 2, 0]], 3: [[0, 2, 0], [4, 2, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 5, bk);
  assert.deepEqual(B.bestPlan(i, 5, bk, rows, 3).shops, [1, 3, 4]);
  assert.equal(B.bestPlan(i, 5, bk, rows, 3).total, 5);
  const po = B.planOptions(i, 5, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 3, 6, 0], [2, 3, 5, 1]]);
  assert.equal(po.rec, 2);
});

test("planOptions counts a RM 1.00 saving as RM 1 despite float maths", () => {
  // 8.11 - 7.11 is 0.9999999999999991 in floating point; the extra stop still saves RM 1.00.
  const d = { premises: [[1], [2]], prices: { 1: [[0, 3.61, 0], [1, 2.61, 0]], 2: [[0, 4.5, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 0);
  assert.deepEqual(po.options.map(o => [o.k, o.total, o.saving]), [[1, 8.11, 0], [2, 7.11, 1]]);
  assert.equal(po.rec, 2);
});

test("planOptions: each line is rounded to the sen, so a difference under a sen offers no extra stop", () => {
  // 0.3 kg of each item. Shops 1-3 each sell one item for RM 0.99 a kg, against RM 1 at shop 0.
  // Each line costs RM 0.30 at every shop once rounded to the sen (0.297 -> 0.30), so no extra stop saves anything.
  // (Before lines were rounded, the 3-stop plan "saved" RM 0.009, shown as RM 0.01, and the 2-stop plan was dropped.)
  const d = { prices: { 1: [[0, 1, 0], [1, 0.99, 0]], 2: [[0, 1, 0], [2, 0.99, 0]], 3: [[0, 1, 0], [3, 0.99, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3].map(code => ({ code, qty: 0.3 }));
  const rows = B.rankStores(i, 4, bk);
  assert.equal(rows[0].total, 0.9);
  const po = B.planOptions(i, 4, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 3, 0.9, 0]]);
  assert.equal(po.rec, 1);
});

test("every total is the sum of its lines rounded to the sen", () => {
  // 0.5 kg at RM 4.99 is RM 2.495: shown as RM 2.50 on its line, so the total must add RM 2.50, not RM 2.495.
  assert.equal(B.lineTotal(4.99, 0.5), 2.5);
  assert.equal(B.lineTotal(1.15, 0.5), 0.58);           // 57.5 sen rounds up, despite 1.15 being 1.1499... in floating point
  assert.equal(B.lineTotal(12.2, 3), 36.6);
  const d = { prices: { 1: [[0, 4.99, 0], [1, 5.01, 0]], 2: [[0, 1.15, 0], [1, 1.13, 0]], 3: [[0, 3.33, 0], [1, 3.35, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 0.5 }, { code: 2, qty: 0.5 }, { code: 3, qty: 1 }];
  const rows = B.rankStores(i, 2, bk);
  rows.forEach(r => {
    const sum = bk.reduce((s, b) => s + Math.round(B.lineTotal(r.lines[b.code].p, b.qty) * 100), 0) / 100;
    assert.equal(r.total, sum, "shop " + r.pidx);
  });
  assert.deepEqual(rows.map(r => [r.pidx, r.total]), [[0, 6.41], [1, 6.43]]);   // 2.50 + 0.58 + 3.33; 2.51 + 0.57 + 3.35
  const pl = B.bestPlan(i, 2, bk, rows, 2);
  assert.equal(pl.total, 6.4);                           // 2.50 (shop 0) + 0.57 (shop 1) + 3.33 (shop 0); 6.39 unrounded
  assert.deepEqual(pl.assign, { 1: 0, 2: 1, 3: 0 });
  assert.deepEqual(B.sharedTotals(i, bk, 0, 1), { n: 3, a: 6.41, b: 6.43 });
});

test("summarise: the typical total is rounded first, and the saving is the gap between the figures shown", () => {
  // Petaling Jaya case: 4 shops with every item. The typical total is the mean of the middle two, RM 147.865,
  // shown as RM 147.87; the saving shown must then be 147.87 - 140.52 = 7.35 (it used to say 7.34).
  const rows = [140.52, 143.37, 152.36, 155.87].map((t, p) => ({ pidx: p, total: t, have: 2, n: 2 }));
  const s = B.summarise(rows, 2);
  assert.equal(s.median, 147.87);
  assert.equal(s.save, 7.35);
  assert.equal(s.max, 155.87);
  assert.equal(B.round2(s.median - s.best.total), s.save);
});

// ---------- the plan card's message (planNote) ----------

const note = (po, k, c, n) => { const x = B.planNote(po, k, c, n); return x && [x.key, x.vars]; };

test("planNote words each option against the recommended plan", () => {
  const rows = B.rankStores(i4, 4, bask3);            // plans: 1 shop RM 22, 2 shops RM 17, 3 shops RM 12
  let po = B.planOptions(i4, 4, bask3, rows, 2);
  assert.equal(po.rec, 3);
  assert.deepEqual(note(po, 1, 2, 3), ["planFewer", { k: 3, x: 10 }]);
  assert.deepEqual(note(po, 2, 2, 3), ["planFewer", { k: 3, x: 5 }]);
  assert.deepEqual(note(po, 3, 2, 3), ["planSaveRec", { x: 10, c: 2 }]);
  po = B.planOptions(i4, 4, bask3, rows, 5);           // RM 5 a stop: every plan scores RM 22, so 1 shop wins the tie
  assert.equal(po.rec, 1);
  assert.deepEqual(note(po, 1, 5, 3), ["planOneLow", { x: 5, c: 5 }]);     // RM 5 is not more than RM 5
  assert.deepEqual(note(po, 2, 5, 3), ["planMoreNot", { x: 5, c: 5 }]);
  assert.deepEqual(note(po, 3, 5, 3), ["planMoreNot", { x: 10, c: 5 }]);   // RM 10 over 2 extra shops
  po = B.planOptions(i4, 4, bask3, rows, 0);
  assert.equal(po.rec, 3);
  assert.deepEqual(note(po, 3, 0, 3), ["planSaveRec0", { x: 10 }]);         // no "beats RM 0 per extra shop"
});

test("planNote explains the RM 1 minimum instead of 'less than RM 0'", () => {
  // 2 shops save RM 0.01. With RM 0 a stop, the hidden RM 1 minimum keeps 1 shop.
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1.0, 0], [1, 0.99, 0]], 2: [[0, 1.0, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 0);
  assert.equal(po.rec, 1);
  assert.deepEqual(note(po, 1, 0, 2), ["planOneTiny", { x: 0.01 }]);
  assert.deepEqual(note(po, 2, 0, 2), ["planMoreTiny", { x: 0.01 }]);
});

test("planNote with plans that cover more items", () => {
  // Shop 0 has items 1 and 2; shop 1 has item 3; shop 2 has item 4: 3 shops have all 4 items.
  let d = { prices: { 1: [[0, 1, 0]], 2: [[0, 1, 0]], 3: [[1, 1, 0]], 4: [[2, 1, 0]] } };
  let i = B.indexDistrict(d), bk = [1, 2, 3, 4].map(code => ({ code, qty: 1 }));
  let po = B.planOptions(i, 3, bk, B.rankStores(i, 3, bk), 5);
  assert.equal(po.rec, 3);
  assert.deepEqual(note(po, 1, 5, 4), ["planFewerCover", { k: 3, h: 4, n: 4 }]);   // not "One shop is the best plan"
  assert.deepEqual(note(po, 2, 5, 4), ["planFewerCover", { k: 3, h: 4, n: 4 }]);
  assert.deepEqual(note(po, 3, 5, 4), ["planCover", { h: 4, n: 4 }]);
  // 1 shop: 2 of 3 items for RM 10. 2 shops: all 3 for RM 11. 3 shops: all 3 for RM 8.
  d = { prices: { 1: [[0, 5, 0], [2, 2, 0]], 2: [[0, 5, 0]], 3: [[1, 1, 0]] } };
  i = B.indexDistrict(d); bk = [1, 2, 3].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 3, bk);
  po = B.planOptions(i, 3, bk, rows, 5);
  assert.equal(po.rec, 2);
  assert.deepEqual(note(po, 1, 5, 3), ["planFewerCover", { k: 2, h: 3, n: 3 }]);
  assert.deepEqual(note(po, 2, 5, 3), ["planCover", { h: 3, n: 3 }]);
  assert.deepEqual(note(po, 3, 5, 3), ["planMoreNot", { x: 3, c: 5 }]);
  po = B.planOptions(i, 3, bk, rows, 0);
  assert.equal(po.rec, 3);
  assert.deepEqual(note(po, 2, 0, 3), ["planFewer", { k: 3, x: 3 }]);
  assert.deepEqual(note(po, 3, 0, 3), ["planCover", { h: 3, n: 3 }]);
});

test("planNote looks plans up by k, and copes with odd input", () => {
  // A hand-made result with no 2-shop option: 1 shop recommended, 3 shops RM 0.50 cheaper.
  const po = { rec: 1, options: [{ k: 1, total: 10, have: 3 }, { k: 3, total: 9.5, have: 3 }] };
  assert.deepEqual(note(po, 1, 5, 3), ["planOneLow3", { x: 0.5, c: 5 }]);
  assert.deepEqual(note(po, 1, 0, 3), ["planOneTiny3", { x: 0.5 }]);
  assert.deepEqual(note(po, 3, 0, 3), ["planMoreTiny", { x: 0.5 }]);
  assert.deepEqual(note(po, 1, "<b>", 3), ["planOneLow3", { x: 0.5, c: 5 }]);   // a bad stop value counts as RM 5
  assert.equal(B.planNote(po, 2, 5, 3), null);
  assert.equal(B.planNote({ options: [], rec: 0 }, 1, 5, 3), null);
  assert.equal(B.planNote(null, 1, 5, 3), null);
  assert.deepEqual([B.stopValue(null), B.stopValue(""), B.stopValue("x"), B.stopValue(-2), B.stopValue("10"), B.stopValue(0)], [5, 5, 5, 5, 10, 0]);
});

// Checks one plan card message against the plan totals directly: right side of the recommendation, and
// every stated comparison true. Returns a problem, or "" when the message is honest.
function noteProblem(po, k, c, n) {
  const nt = B.planNote(po, k, c, n);
  const by = kk => po.options.find(o => o.k === kk), o = by(k), r = by(po.rec), one = po.options[0];
  const cs = Math.round(c * 100), sen = x => Math.round(x * 100), xs = nt && nt.vars.x != null ? sen(nt.vars.x) : null;
  if (!nt) return "no note";
  const key = nt.key, v = nt.vars;
  if (k < po.rec) {
    if (key === "planFewer") return v.k === po.rec && r.have === o.have && xs === sen(o.total) - sen(r.total) && xs > 0 ? "" : "planFewer wrong";
    if (key === "planFewerCover") return v.k === po.rec && r.have > o.have && v.h === r.have ? "" : "planFewerCover wrong";
    return "k < rec but " + key;
  }
  if (k > po.rec) {
    if (o.have !== r.have || xs !== sen(r.total) - sen(o.total) || xs <= 0) return key + ": wrong amount";
    if (key === "planMoreNot") return xs <= (k - po.rec) * cs ? "" : "planMoreNot: saving is more than the stop value";
    if (key === "planMoreTiny") return xs < 100 ? "" : "planMoreTiny: saving is RM 1 or more";
    return "k > rec but " + key;
  }
  if (k === 1) {
    const alt = by(2) || by(3);
    if (!alt) return key === "planOne" ? "" : "no alternative but " + key;
    if (xs !== sen(one.total) - sen(alt.total) || alt.have !== one.have) return key + ": wrong amount";
    if (/^planOneLow/.test(key)) return xs <= (alt.k - 1) * cs && (alt.k === 3) === /3$/.test(key) ? "" : key + ": not true";
    if (/^planOneTiny/.test(key)) return xs < 100 && (alt.k === 3) === /3$/.test(key) ? "" : key + ": not true";
    return "rec 1 but " + key;
  }
  if (key === "planCover") return o.have > one.have && v.h === o.have ? "" : "planCover wrong";
  if (key === "planSaveRec" || key === "planSaveRec0") {
    if (o.have !== one.have || xs !== sen(one.total) - sen(o.total)) return key + ": wrong amount";
    if (xs < 100 || xs <= (k - 1) * cs) return key + ": does not beat the stop value";
    return (key === "planSaveRec0") === (c === 0) ? "" : key + " with c " + c;
  }
  return "rec but " + key;
}

// Small seeded random numbers, so a failure can be repeated.
function rng(seed) { return () => { seed = (seed + 0x6D2B79F5) | 0; let x = Math.imul(seed ^ (seed >>> 15), 1 | seed); x ^= x + Math.imul(x ^ (x >>> 7), 61 | x); return ((x ^ (x >>> 14)) >>> 0) / 4294967296; }; }
const COSTS = [0, 2, 5, 10, 20];

test("planNote never contradicts the recommendation or states a false comparison (random lists)", () => {
  const rand = rng(7);
  let cards = 0;
  for (let trial = 0; trial < 400; trial++) {
    const nShops = 2 + Math.floor(rand() * 8), nItems = 2 + Math.floor(rand() * 6), prices = {};
    for (let c = 1; c <= nItems; c++) {
      prices[c] = [];
      for (let p = 0; p < nShops; p++) if (rand() < 0.75) prices[c].push([p, Math.round((1 + rand() * 20) * 100) / 100, 0]);
    }
    const i = B.indexDistrict({ prices });
    const bk = Array.from({ length: nItems }, (_, j) => ({ code: j + 1, qty: [0.5, 1, 1, 1.5, 2, 3][Math.floor(rand() * 6)] }));
    const rows = B.rankStores(i, nShops, bk);
    COSTS.forEach(c => {
      const po = B.planOptions(i, nShops, bk, rows, c);
      if (po.options.length < 2) return;
      cards++;
      po.options.forEach(o => assert.equal(noteProblem(po, o.k, c, bk.length), "", JSON.stringify({ trial, c, k: o.k, po })));
    });
  }
  assert.ok(cards > 300, cards + " plan cards");
});

// The same check on real district files, when a build is present (CI builds web/data before the tests).
// Set BAKUL_DATA to another build's data folder to use that instead.
const fs = require("fs");
const DATA = process.env.BAKUL_DATA || path.join(__dirname, "../../web/data");
const dFiles = fs.existsSync(path.join(DATA, "d")) ? fs.readdirSync(path.join(DATA, "d")).filter(f => f.endsWith(".json")).sort() : [];
test("planNote is honest on real district files (random lists)", { skip: dFiles.length ? false : "no built data in " + DATA }, () => {
  const rand = rng(11);
  let cards = 0, keys = {};
  const pick = dFiles.filter(() => rand() < Math.min(1, 40 / dFiles.length));
  pick.forEach(f => {
    const d = JSON.parse(fs.readFileSync(path.join(DATA, "d", f), "utf8")), i = B.indexDistrict(d);
    // Items priced at 3 or more shops, so plans exist.
    const codes = Object.keys(d.prices).filter(c => d.prices[c].length >= 3).map(Number);
    if (codes.length < 3) return;
    for (let trial = 0; trial < 3; trial++) {
      const n = 3 + Math.floor(rand() * 8), bk = [];
      while (bk.length < Math.min(n, codes.length)) {
        const c = codes[Math.floor(rand() * codes.length)];
        if (!bk.some(b => b.code === c)) bk.push({ code: c, qty: [0.5, 1, 1, 2, 3][Math.floor(rand() * 5)] });
      }
      const rows = B.rankStores(i, d.premises.length, bk);
      COSTS.forEach(c => {
        const po = B.planOptions(i, d.premises.length, bk, rows, c, 60);
        if (po.options.length < 2) return;
        cards++;
        po.options.forEach(o => {
          assert.equal(noteProblem(po, o.k, c, bk.length), "", JSON.stringify({ f, c, k: o.k, bk }));
          const key = B.planNote(po, o.k, c, bk.length).key; keys[key] = (keys[key] || 0) + 1;
        });
      });
    }
  });
  assert.ok(cards > 50, cards + " plan cards");
  // The scan reaches the messages that used to go wrong: fewer shops than recommended, and more.
  assert.ok(keys.planFewer && keys.planMoreNot, JSON.stringify(keys));
});

test("every plan message exists in English and BM with the values it is given", () => {
  global.window = {};
  require(path.join(__dirname, "../../web/js/i18n.js"));
  const I = global.window.I18N;
  // planCover also names where its shops are ({d}: "within 5 km of you", "in Petaling"): the page adds that value.
  const filled = { planOne: [], planOneLow: ["x", "c"], planOneLow3: ["x", "c"], planOneTiny: ["x"], planOneTiny3: ["x"],
                   planSaveRec: ["x", "c"], planSaveRec0: ["x"], planFewer: ["k", "x"], planFewerCover: ["k", "h", "n"],
                   planMoreNot: ["x", "c"], planMoreTiny: ["x"], planCover: ["h", "n", "d"] };
  ["en", "ms"].forEach(lang => Object.keys(filled).forEach(key => {
    const s = I[lang][key];
    assert.equal(typeof s, "string", lang + "." + key);
    const used = (s.match(/\{(\w+)\}/g) || []).map(m => m.slice(1, -1)).sort();
    assert.deepEqual(used, filled[key].slice().sort(), lang + "." + key + ": " + s);
    assert.ok(!/berharga|hentian|barangan/i.test(s), lang + "." + key);
  }));
});

test("planOptions keeps each plan that covers more items, with no saving figure", () => {
  // Shop 0 has items 1 and 2; shop 1 has item 3; shop 2 has item 4.
  const d = { prices: { 1: [[0, 1, 0]], 2: [[0, 1, 0]], 3: [[1, 1, 0]], 4: [[2, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3, 4].map(code => ({ code, qty: 1 }));
  const po = B.planOptions(i, 3, bk, B.rankStores(i, 3, bk), 5);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 2, 2, 0], [2, 3, 3, null], [3, 4, 4, null]]);
  assert.equal(po.rec, 3);                   // the most items wins, whatever one stop is worth
});

test("planOptions compares a 3-stop plan with a 2-stop plan that covers more than 1 stop", () => {
  // 1 stop (shop 0) buys 2 of the 3 items for RM 10; 2 stops (shops 0 + 1) buy all 3 for RM 11.
  // 3 stops also buy all 3, for RM 8 (item 1 at shop 2). Kept, and its saving is null: it covers more than 1 stop.
  const d = { prices: { 1: [[0, 5, 0], [2, 2, 0]], 2: [[0, 5, 0]], 3: [[1, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 3, bk);
  let po = B.planOptions(i, 3, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 2, 10, 0], [2, 3, 11, null], [3, 3, 8, null]]);
  assert.equal(po.rec, 3);                   // RM 8 beats RM 11
  assert.equal(B.planOptions(i, 3, bk, rows, 5).rec, 2);   // 11 + 5 = 16 beats 8 + 10 = 18
  // With item 1 at RM 4.50, 3 stops still beat 2 stops (RM 10.50 vs RM 11), but by under RM 1.
  const d2 = { prices: { 1: [[0, 5, 0], [2, 4.5, 0]], 2: [[0, 5, 0]], 3: [[1, 1, 0]] } };
  const i2 = B.indexDistrict(d2);
  po = B.planOptions(i2, 3, bk, B.rankStores(i2, 3, bk), 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total]), [[1, 2, 10], [2, 3, 11], [3, 3, 10.5]]);
  assert.equal(po.rec, 2);
});

test("planOptions with no rows or an empty basket", () => {
  assert.deepEqual(B.planOptions(idx, 3, [{ code: 10, qty: 1 }], [], 5), { options: [], rec: 0 });
  assert.deepEqual(B.planOptions(idx, 3, [], B.rankStores(idx, 3, []), 5), { options: [], rec: 0 });
});

test("sharedTotals compares two shops on the lines both of them price", () => {
  const basket = [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }];
  // Shop B (1) lacks item 30, so only items 10 and 20 are compared: A = 5 + 2x2, B = 4 + 2x3.
  assert.deepEqual(B.sharedTotals(idx, basket, 0, 1), { n: 2, a: 9, b: 10 });
  assert.deepEqual(B.sharedTotals(idx, basket, 1, 0), { n: 2, a: 10, b: 9 });
  assert.deepEqual(B.sharedTotals(idx, basket, 0, 2), { n: 3, a: 18, b: 19 });
  assert.deepEqual(B.sharedTotals(idx, basket, 2, 2), { n: 3, a: 19, b: 19 });
  assert.deepEqual(B.sharedTotals(idx, [{ code: 99, qty: 1 }], 0, 1), { n: 0, a: 0, b: 0 });
  assert.deepEqual(B.sharedTotals(idx, [], 0, 1), { n: 0, a: 0, b: 0 });
});

test("sharedTotals is any-brand aware and rounds to 2 decimals", () => {
  // Item 40 is another brand of item 10; shop 1 sells only item 40.
  const d = { premises: [[1], [2]], prices: { 10: [[0, 5.1, 0]], 40: [[1, 3.3, 2]], 20: [[0, 0.1, 0], [1, 0.2, 0]] } };
  const i = B.indexDistrict(d);
  assert.deepEqual(B.sharedTotals(i, [{ code: 10, qty: 1 }, { code: 20, qty: 3 }], 0, 1), { n: 1, a: 0.3, b: 0.6 });
  assert.deepEqual(B.sharedTotals(i, [{ code: 10, qty: 1, alt: [40] }, { code: 20, qty: 3 }], 0, 1),
                   { n: 2, a: 5.4, b: 3.9 });
});

test("averageRank uses each shop's monthly prices and counts cheapest months", () => {
  // Shop 0 is cheap now but dear before; shop 1 is steady.
  const sm = { 1: [[0, 9, 9, 3], [1, 5, 5, 5]], 2: [[0, 2, 2, 2], [1, 2, 2, null]] };
  const rows = B.averageRank(sm, 2, [{ code: 1, qty: 1 }, { code: 2, qty: 2 }], 3);
  assert.equal(rows[0].pidx, 1);             // 5 + 2x2 = 9
  assert.equal(rows[0].total, 9);
  assert.equal(rows[1].total, 11);           // 7 + 2x2
  assert.equal(rows[0].cheapestMonths, 2);   // months 1 and 2; month 3 shop 1 lacks item 2
  assert.equal(rows[0].fullMonths, 2);
  assert.equal(rows[1].cheapestMonths, 1);
  assert.equal(rows[1].fullMonths, 3);
});

test("parseUnit and unitPrice read PriceCatcher units", () => {
  assert.deepEqual(B.parseUnit("10 kg"), { amt: 10000, base: "g" });
  assert.deepEqual(B.parseUnit("1kg"), { amt: 1000, base: "g" });
  assert.deepEqual(B.parseUnit("+-350g"), { amt: 350, base: "g" });
  assert.deepEqual(B.parseUnit("1.5 liter"), { amt: 1500, base: "ml" });
  assert.deepEqual(B.parseUnit("946ML"), { amt: 946, base: "ml" });
  assert.deepEqual(B.parseUnit("5 X 79g"), { amt: 395, base: "g" });
  assert.deepEqual(B.parseUnit("30 biji"), { amt: 30, base: "pc" });
  assert.equal(B.parseUnit("S80, M60, L58, XL50"), null);
  assert.deepEqual(B.unitPrice(29.9, "5 kg"), { v: 5.98, per: "kg" });
  assert.equal(B.unitPrice(15, "30 biji").v, 0.5);
});

test("ageToday counts a price's age from today, not from the data date", () => {
  // days_old counts back from the data date. Opened 11 days after a 2026-11-05 build, a price that was
  // 1 day old then is 12 days old now ("prices this week" would be wrong).
  assert.equal(B.ageToday(1, "2026-11-05", "2026-11-16"), 12);
  assert.equal(B.ageToday(0, "2026-11-05", "2026-11-05"), 0);
  assert.equal(B.ageToday(17, "2026-11-05", "2026-11-15"), 27);
  assert.equal(B.ageToday(3, "2026-10-27", "2026-11-16"), 23);       // across a month end
  assert.equal(B.ageToday(5, "2026-12-30", "2027-01-02"), 8);        // across a year end
  // A phone clock set before the data date adds nothing; missing dates add nothing.
  assert.equal(B.ageToday(4, "2026-11-05", "2026-11-01"), 4);
  assert.equal(B.ageToday(4, null, "2026-11-16"), 4);
  assert.equal(B.ageToday(4, "2026-11-05", ""), 4);
  assert.equal(B.ageToday(4, "not a date", "2026-11-16"), 4);
});

test("every label exists in English and BM with the same placeholders, and BM avoids the banned words", () => {
  global.window = global.window || {};
  require(path.join(__dirname, "../../web/js/i18n.js"));
  const I = global.window.I18N, ph = s => (String(s).match(/\{\w+\}/g) || []).sort().join(",");
  assert.deepEqual(Object.keys(I.ms).sort(), Object.keys(I.en).sort());
  Object.keys(I.en).forEach(k => assert.equal(ph(I.ms[k]), ph(I.en[k]), "placeholders of " + k));
  // The whole BM text, not only what one screen shows (the glossary at the top of i18n.js).
  Object.keys(I.ms).forEach(k => assert.ok(!/berharga|hentian|barangan/i.test(I.ms[k]), "ms." + k + ": " + I.ms[k]));
  // An English singular label (key ending in "1") never says "1 shops", "1 items" and the like.
  Object.keys(I.en).filter(k => /1$/.test(k) && I.en[k.slice(0, -1)]).forEach(k => assert.ok(!/\b1 (shops|items|days|months|areas)\b/.test(I.en[k]), k));
});
